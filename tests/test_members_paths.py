"""Containment tests for jlab.members.paths.

Covers t8's three acceptance criteria:

1. The output path resolves from the repo root by walking up from
   ``__file__``, never from the caller's current working directory.
2. The ``.gitignore`` rule actually covers the output path (asserted with
   ``git check-ignore``), landing in the same commit as this containment
   code so no report can ever be generated before the ignore rule exists.
3. When no repo root can be resolved (the wheel-install case), the module
   refuses to write rather than falling back to some other location.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jlab.cli._errors import EXIT_ENV_ERROR, CliError
from jlab.members import paths as members_paths

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_find_repo_root_locates_this_checkout() -> None:
    root = members_paths.find_repo_root()
    assert root is not None
    assert root == _REPO_ROOT
    assert (root / "culture.yaml").is_file()


def test_resolution_ignores_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Changing CWD to an unrelated directory must not change the result.

    The module anchors on ``__file__``, not the caller's working directory —
    this is the whole point of copying whoami.py's pattern. Prove it by
    chdir-ing somewhere with no culture.yaml at all and confirming the
    resolved repo root (and report path) are unaffected.
    """
    unrelated = tmp_path / "unrelated-checkout"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    root = members_paths.find_repo_root()
    assert root == _REPO_ROOT

    report_path = members_paths.members_report_path("run-cwd", "cwd-independence.html")
    assert report_path == (
        _REPO_ROOT / "data" / "reports" / "members" / "run-cwd" / "cwd-independence.html"
    )
    assert _REPO_ROOT in report_path.parents


def test_members_reports_dir_is_inside_repo_and_created() -> None:
    out_dir = members_paths.members_reports_dir()
    assert out_dir == _REPO_ROOT / "data" / "reports" / "members"
    assert out_dir.is_dir()


def test_gitignore_covers_the_members_report_path() -> None:
    """The ignore rule must actually shadow files under the output path."""
    candidate = _REPO_ROOT / "data" / "reports" / "members" / "example-report.html"
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(candidate)],
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, "data/reports/members/ must be gitignored"


def test_gitignore_rule_is_present_in_the_file() -> None:
    gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/reports/members/" in gitignore


def test_refuses_to_write_when_no_repo_root_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the wheel-install case: no culture.yaml anywhere above us."""
    monkeypatch.setattr(members_paths, "_REPO_MARKER", "no-such-marker-file.yaml")

    assert members_paths.find_repo_root() is None

    with pytest.raises(CliError) as exc_info:
        members_paths.members_reports_dir()
    err = exc_info.value
    assert err.code == EXIT_ENV_ERROR
    assert err.remediation

    with pytest.raises(CliError):
        members_paths.members_report_path("run-x")

    with pytest.raises(CliError):
        members_paths.members_run_dir("run-x")


def test_report_filename_cannot_escape_the_contained_directory() -> None:
    """A hostile filename must be refused, not normalised (c21/h30).

    Regression: `members_report_path("../../../../tmp/x.html")` resolved to a
    path outside the repository entirely, and therefore outside the .gitignore
    rule that contains person-level report data.
    """
    for hostile in (
        "../../../../tmp/ESCAPED.html",
        "../x.html",
        "sub/dir.html",
        "/etc/passwd",
        "..",
        ".",
        "",
    ):
        with pytest.raises(CliError) as exc:
            members_paths.members_report_path("run-1", hostile)
        assert exc.value.code == 2
        assert exc.value.remediation


def test_report_filename_accepts_a_bare_name() -> None:
    """The legitimate override still works, inside the run directory."""
    path = members_paths.members_report_path("run-1", "other.html")
    assert path.name == "other.html"
    assert path.parent == members_paths.members_run_dir("run-1")


# --- per-run subdirectory layout (t15) -----------------------------------


def test_run_dir_is_a_child_of_the_reports_dir() -> None:
    """Each run owns a subdirectory; the shared reports dir is only its parent."""
    run_dir = members_paths.members_run_dir("20260905T101112Z-abcdef01")
    assert run_dir.parent == members_paths.members_reports_dir()
    assert run_dir.name == "20260905T101112Z-abcdef01"


def test_run_dir_is_not_created_eagerly() -> None:
    """The run directory is the destination of an atomic swap, not a mkdir target.

    ``write_artifact_set`` renames a fully-written temp directory onto this
    path. Pre-creating it would push the swap into the two-rename branch and
    weaken the guarantee, so resolving the path must not create it.
    """
    run_dir = members_paths.members_run_dir("never-created-by-path-resolution")
    assert not run_dir.exists()


def test_new_run_id_is_unique_and_a_safe_bare_segment() -> None:
    ids = {members_paths.new_run_id() for _ in range(200)}
    assert len(ids) == 200
    for run_id in ids:
        assert run_id == Path(run_id).name
        assert ".." not in run_id
        # Resolving it must stay inside the reports directory.
        assert members_paths.members_run_dir(run_id).parent == members_paths.members_reports_dir()


# --- symlink containment (finding 7) --------------------------------------


def test_reports_dir_rejects_a_symlinked_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing symlink above the reports dir must not be followed.

    Simulates ``data/reports`` (an ancestor of the fixed repo-relative path)
    already being a symlink pointing outside the intended repo root. This
    must be refused rather than silently followed, or person-level Discord
    data would land outside the gitignored boundary.
    """
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (fake_root / "data").mkdir()
    (fake_root / "data" / "reports").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(members_paths, "find_repo_root", lambda: fake_root)

    with pytest.raises(CliError) as exc:
        members_paths.members_reports_dir()
    assert exc.value.code == EXIT_ENV_ERROR
    assert exc.value.remediation
    # Nothing must have been created inside the outside directory.
    assert not (outside / "members").exists()


def test_reports_dir_rejects_being_itself_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final component (``links``/``members``) itself may be the symlink."""
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    outside = tmp_path / "outside-2"
    outside.mkdir()
    (fake_root / "data" / "reports").mkdir(parents=True)
    (fake_root / "data" / "reports" / "members").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(members_paths, "find_repo_root", lambda: fake_root)

    with pytest.raises(CliError) as exc:
        members_paths.members_reports_dir()
    assert exc.value.code == EXIT_ENV_ERROR
    assert exc.value.remediation


def test_run_dir_rejects_a_preexisting_symlinked_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run-id directory that is already a symlink out of the repo is refused."""
    fake_root = tmp_path / "fake-repo"
    fake_root.mkdir()
    (fake_root / "culture.yaml").write_text("suffix: fake\n", encoding="utf-8")
    outside = tmp_path / "outside-3"
    outside.mkdir()
    reports_dir = fake_root / "data" / "reports" / "members"
    reports_dir.mkdir(parents=True)
    (reports_dir / "hostile-run").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(members_paths, "find_repo_root", lambda: fake_root)

    with pytest.raises(CliError) as exc:
        members_paths.members_run_dir("hostile-run")
    assert exc.value.code == EXIT_ENV_ERROR
    assert exc.value.remediation


def test_run_id_cannot_escape_the_contained_directory() -> None:
    """The traversal guard applies to RUN IDS too, not only to filenames."""
    reports_dir = members_paths.members_reports_dir().resolve()
    for hostile in (
        "../../../../tmp/ESCAPED",
        "../sibling",
        "sub/dir",
        "/etc",
        "..",
        ".",
        "",
    ):
        with pytest.raises(CliError) as exc:
            members_paths.members_run_dir(hostile)
        assert exc.value.code == EXIT_ENV_ERROR
        assert exc.value.remediation

        with pytest.raises(CliError):
            members_paths.members_report_path(hostile)

    # And nothing hostile ever produced a path outside the reports dir.
    assert members_paths.members_run_dir("safe").resolve().parent == reports_dir

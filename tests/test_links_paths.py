"""Containment tests for jlab.links.paths.

Covers t3's three acceptance criteria:

1. The output path resolves from the repo root by walking up from
   ``__file__``, never from the caller's current working directory, and
   the module refuses to write when no repo root can be resolved.
2. A hostile filename (path separators, ``..``) cannot escape the report
   directory.
3. ``git check-ignore`` succeeds on every path the links verb will write
   AND on the members CSV paths — proven by enumerating actual filenames
   under each report directory, not by asserting a hardcoded directory
   string.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jlab.cli._errors import EXIT_ENV_ERROR, CliError
from jlab.links import paths as links_paths
from jlab.members import paths as members_paths

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Filenames the links verb will eventually write (t8): the HTML report plus
# the flat per-share and deduped per-URL summary CSVs. Enumerated here as
# plain filenames under links_reports_dir() rather than as a hardcoded
# directory string, so this test proves the *files* are ignored, not just
# that some directory string appears in .gitignore.
_LINKS_ARTIFACT_FILENAMES = (
    "links-report.html",
    "links-flat.csv",
    "links-summary.csv",
)

# Filenames the members verb will write (t7 adds CSVs alongside the existing
# HTML report). Same enumeration approach for the sibling report directory.
_MEMBERS_ARTIFACT_FILENAMES = (
    "members-report.html",
    "members-report.csv",
)


def _assert_git_ignores(path: Path) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{path} must be gitignored"


def test_find_repo_root_locates_this_checkout() -> None:
    root = links_paths.find_repo_root()
    assert root is not None
    assert root == _REPO_ROOT
    assert (root / "culture.yaml").is_file()


def test_resolution_ignores_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Changing CWD to an unrelated directory must not change the result.

    The module anchors on ``__file__``, not the caller's working directory.
    Prove it by chdir-ing somewhere with no culture.yaml at all and
    confirming the resolved repo root (and report path) are unaffected.
    """
    unrelated = tmp_path / "unrelated-checkout"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    root = links_paths.find_repo_root()
    assert root == _REPO_ROOT

    report_path = links_paths.links_report_path("run-cwd", "cwd-independence.html")
    assert report_path == (
        _REPO_ROOT / "data" / "reports" / "links" / "run-cwd" / "cwd-independence.html"
    )
    assert _REPO_ROOT in report_path.parents


def test_links_reports_dir_is_inside_repo_and_created() -> None:
    out_dir = links_paths.links_reports_dir()
    assert out_dir == _REPO_ROOT / "data" / "reports" / "links"
    assert out_dir.is_dir()


def test_gitignore_covers_every_links_artifact_filename() -> None:
    """Every file the links verb will write must be shadowed by the ignore rule.

    Asserted under the per-run subdirectory layout (t15): the artifacts live
    at ``data/reports/links/<run-id>/<filename>``, so the ignore rule has to
    shadow that deeper path, not just the flat one.
    """
    run_dir = links_paths.links_run_dir(links_paths.new_run_id())
    for filename in _LINKS_ARTIFACT_FILENAMES:
        _assert_git_ignores(run_dir / filename)


def test_gitignore_covers_every_members_csv_filename() -> None:
    """The members report's CSVs (added by t7) must also be gitignored.

    This is the sibling-directory half of t3: the pre-existing
    ``/data/reports/members/`` rule already shadows anything written under
    that directory, but this task adds both rules explicitly per the build
    plan, so assert directly against filenames rather than trusting the
    directory rule alone.
    """
    run_dir = members_paths.members_run_dir(members_paths.new_run_id())
    for filename in _MEMBERS_ARTIFACT_FILENAMES:
        _assert_git_ignores(run_dir / filename)


def test_gitignore_rules_are_present_in_the_file() -> None:
    gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/reports/links/" in gitignore
    assert "data/reports/members/*.csv" in gitignore


def test_refuses_to_write_when_no_repo_root_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the wheel-install case: no culture.yaml anywhere above us."""
    monkeypatch.setattr(links_paths, "_REPO_MARKER", "no-such-marker-file.yaml")

    assert links_paths.find_repo_root() is None

    with pytest.raises(CliError) as exc_info:
        links_paths.links_reports_dir()
    err = exc_info.value
    assert err.code == EXIT_ENV_ERROR
    assert err.remediation

    with pytest.raises(CliError):
        links_paths.links_report_path("run-x")

    with pytest.raises(CliError):
        links_paths.links_run_dir("run-x")


def test_report_filename_cannot_escape_the_contained_directory() -> None:
    """A hostile filename must be refused, not normalised (c5/h22, c9/h26)."""
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
            links_paths.links_report_path("run-1", hostile)
        assert exc.value.code == 2
        assert exc.value.remediation


def test_report_filename_accepts_a_bare_name() -> None:
    """The legitimate override still works, inside the run directory."""
    path = links_paths.links_report_path("run-1", "other.html")
    assert path.name == "other.html"
    assert path.parent == links_paths.links_run_dir("run-1")


# --- per-run subdirectory layout (t15) -----------------------------------


def test_run_dir_is_a_child_of_the_reports_dir() -> None:
    run_dir = links_paths.links_run_dir("20260905T101112Z-abcdef01")
    assert run_dir.parent == links_paths.links_reports_dir()
    assert run_dir.name == "20260905T101112Z-abcdef01"


def test_run_dir_is_not_created_eagerly() -> None:
    """The run directory is an atomic-swap destination, not a mkdir target."""
    run_dir = links_paths.links_run_dir("never-created-by-path-resolution")
    assert not run_dir.exists()


def test_new_run_id_is_unique_and_a_safe_bare_segment() -> None:
    ids = {links_paths.new_run_id() for _ in range(200)}
    assert len(ids) == 200
    for run_id in ids:
        assert run_id == Path(run_id).name
        assert ".." not in run_id
        assert links_paths.links_run_dir(run_id).parent == links_paths.links_reports_dir()


def test_run_id_cannot_escape_the_contained_directory() -> None:
    """The traversal guard applies to RUN IDS too, not only to filenames."""
    reports_dir = links_paths.links_reports_dir().resolve()
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
            links_paths.links_run_dir(hostile)
        assert exc.value.code == EXIT_ENV_ERROR
        assert exc.value.remediation

        with pytest.raises(CliError):
            links_paths.links_report_path(hostile)

    assert links_paths.links_run_dir("safe").resolve().parent == reports_dir

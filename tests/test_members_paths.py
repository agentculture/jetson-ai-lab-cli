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

    report_path = members_paths.members_report_path("cwd-independence.html")
    assert report_path == _REPO_ROOT / "data" / "reports" / "members" / "cwd-independence.html"
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
        members_paths.members_report_path()

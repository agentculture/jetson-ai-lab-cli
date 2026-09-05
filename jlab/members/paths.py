"""Repo-anchored output path for member-report data (containment boundary).

The members report (``docs/specs/2026-09-04-jlab-discord-members-report.md``)
contains person-level data about real Discord community members. It must
never be committed and must never land outside this repo's gitignored output
directory — including when the command is invoked from an unrelated working
directory.

Resolving the output path from the caller's current working directory would
let someone run ``jlab discord members`` from a different checkout entirely
and write person-level data somewhere with no ignore rule, silently
defeating that containment. So this module copies the pattern
``jlab/cli/_commands/whoami.py::find_culture_yaml`` uses to locate its own
``culture.yaml``: walk up from ``__file__`` — never the caller's CWD — to
find this checkout's repo root, and refuse to write at all when no repo root
can be found (e.g. a wheel install with no ``culture.yaml`` alongside the
package), rather than falling back to some other location.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from jlab.cli._errors import EXIT_ENV_ERROR, CliError

# Same marker file whoami.py uses to find its own repo root.
_REPO_MARKER = "culture.yaml"

# Gitignored, relative to the repo root. Kept as a tuple of path segments so
# callers never need to worry about separator handling.
_REPORTS_SUBDIR = ("data", "reports", "members")

_DEFAULT_REPORT_FILENAME = "members-report.html"


def find_repo_root() -> Path | None:
    """Locate this checkout's repo root by walking up from this module.

    Mirrors ``whoami.find_culture_yaml``: the anchor is this package's own
    location on disk, never the caller's current working directory, so
    invoking the CLI from an unrelated directory cannot redirect where
    person-level data gets written. Returns ``None`` when no repo root can
    be found — e.g. a wheel install with no ``culture.yaml`` shipped
    alongside the package.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / _REPO_MARKER).is_file():
            return parent
    return None


def members_reports_dir() -> Path:
    """Return the gitignored PARENT directory that holds one dir per run.

    This directory is shared across runs; it never holds a run's artifacts
    directly. Each run writes into its own child of it — see
    :func:`members_run_dir` for why that layout is load-bearing rather than
    cosmetic. Creates the directory (and its parents) if it does not exist
    yet.
    Raises :class:`CliError` with :data:`~jlab.cli._errors.EXIT_ENV_ERROR`
    instead of writing anywhere else when no repo root can be resolved —
    person-level data must never be written outside this repo's ignored
    path, so refusing to write is the only acceptable fallback.
    """
    root = find_repo_root()
    if root is None:
        raise CliError(
            EXIT_ENV_ERROR,
            "cannot resolve the jetson-ai-lab-cli repo root for the members report output path",
            "run this from an editable/source checkout of jetson-ai-lab-cli "
            "(a wheel install has no culture.yaml to anchor on); the members "
            "report refuses to write outside its gitignored repo-relative "
            "path rather than falling back to the current directory",
        )
    out_dir = root.joinpath(*_REPORTS_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def new_run_id(now: datetime | None = None) -> str:
    """Mint a fresh, collision-resistant, filesystem-safe run id.

    Shape: ``20260905T101112Z-1a2b3c4d`` — a UTC timestamp (sortable, so a
    listing of the report directory reads chronologically) plus eight hex
    characters of entropy. The random suffix is what makes concurrent runs
    safe: two runs starting inside the same second — routine under
    ``pytest -n auto`` — still get distinct directories, so neither can swap
    over the other's artifacts.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _require_bare_segment(value: str, kind: str, example: str) -> None:
    """Refuse anything that is not a single, non-traversing path segment."""
    if not isinstance(value, str) or value != Path(value).name or value in {"", ".", ".."}:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{kind} must be a bare path segment, got {value!r}",
            remediation=(
                f"pass a plain name like {example!r} — the report path is fixed "
                "inside this repository and cannot be redirected"
            ),
        )


def members_run_dir(run_id: str) -> Path:
    """Return the per-run subdirectory that holds ONE run's whole artifact set.

    Layout is ``data/reports/members/<run-id>/<artifact>`` — one directory per
    run, never a shared flat directory. That matters for more than tidiness:
    :func:`jlab.atomic_writeset.write_artifact_set` replaces its *entire*
    destination directory with exactly the file set it is handed. Pointed at
    a shared directory holding independently-named reports, that swap would
    silently delete every sibling report, and two concurrent runs would race
    on it. Pointed at a directory that belongs to a single run, the same
    primitive is exactly right: the run's whole set lands with one
    ``os.replace`` onto a path nothing else owns.

    The directory is deliberately **not** created here. It is the destination
    of that rename, and pre-creating it would push the swap into the
    two-rename (rename-aside-then-rename-in) branch, weakening the guarantee
    for no reason. Only the shared parent is created (by
    :func:`members_reports_dir`).

    *run_id* is validated as a bare path segment for the same reason
    filenames are: a run id carrying a separator or ``..`` would place the
    run's artifacts outside the gitignored report directory. Hostile run ids
    are refused, never normalised.
    """
    reports_dir = members_reports_dir()
    _require_bare_segment(run_id, "run id", "20260905T101112Z-1a2b3c4d")
    return reports_dir / run_id


def members_report_path(run_id: str, filename: str = _DEFAULT_REPORT_FILENAME) -> Path:
    """Return the path of one artifact inside *run_id*'s own run directory.

    Both *run_id* and *filename* are constrained to bare path segments: the
    report carries person-level data (c21/h30), containment is the point, and a
    name with any path separator, a parent reference, or an absolute root
    would escape the gitignored directory — ``members_report_path(run, "../../x.html")``
    resolved outside the repo entirely before this guard existed — so those
    are refused rather than normalised.
    """
    _require_bare_segment(filename, "report filename", _DEFAULT_REPORT_FILENAME)
    return members_run_dir(run_id) / filename

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
    """Return the gitignored directory for member-report output.

    Creates the directory (and its parents) if it does not exist yet.
    Raises :class:`CliError` with :data:`~jlab.cli._errors.EXIT_ENV_ERROR`
    instead of writing anywhere else when no repo root can be resolved —
    person-level data must never be written outside this repo's ignored
    path, so refusing to write is the only acceptable fallback.
    """
    root = find_repo_root()
    if root is None:
        raise CliError(
            EXIT_ENV_ERROR,
            "cannot resolve the jetson-ai-lab-cli repo root for the members " "report output path",
            "run this from an editable/source checkout of jetson-ai-lab-cli "
            "(a wheel install has no culture.yaml to anchor on); the members "
            "report refuses to write outside its gitignored repo-relative "
            "path rather than falling back to the current directory",
        )
    out_dir = root.joinpath(*_REPORTS_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def members_report_path(filename: str = _DEFAULT_REPORT_FILENAME) -> Path:
    """Return the fixed, repo-anchored path for the generated members report.

    ``filename`` may be overridden by callers that need a distinct name
    (e.g. tests), but is constrained to a bare filename: the report carries
    person-level data and containment is the point (c21/h30). A name with any
    path separator, a parent reference, or an absolute root would escape the
    gitignored directory — ``members_report_path("../../x.html")`` resolved
    outside the repo entirely before this guard existed — so those are refused
    rather than normalised.
    """
    if filename != Path(filename).name or filename in {"", ".", ".."}:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"report filename must be a bare filename, got {filename!r}",
            remediation=(
                "pass a plain name like 'members-report.html' — the report "
                "path is fixed inside this repository and cannot be redirected"
            ),
        )
    return members_reports_dir() / filename

"""Repo-anchored output path for the links-report data (containment boundary).

The links report (``docs/plans/2026-09-05-jlab-discord-links-report.md``)
contains URLs and author ids scraped from the Jetson AI Lab Discord's
public channels. Like the members report, it must never be committed and
must never land outside this repo's gitignored output directory —
including when the command is invoked from an unrelated working directory.

Resolving the output path from the caller's current working directory
would let someone run ``jlab discord links`` from a different checkout
entirely and write scraped data somewhere with no ignore rule, silently
defeating that containment. So this module copies the pattern
``jlab/members/paths.py`` uses (itself copied from
``jlab/cli/_commands/whoami.py::find_culture_yaml``): walk up from
``__file__`` — never the caller's CWD — to find this checkout's repo
root, and refuse to write at all when no repo root can be found (e.g. a
wheel install with no ``culture.yaml`` alongside the package), rather
than falling back to some other location.
"""

from __future__ import annotations

from pathlib import Path

from jlab.cli._errors import EXIT_ENV_ERROR, CliError

# Same marker file whoami.py (and jlab/members/paths.py) use to find their
# own repo root.
_REPO_MARKER = "culture.yaml"

# Gitignored, relative to the repo root. Kept as a tuple of path segments so
# callers never need to worry about separator handling.
_REPORTS_SUBDIR = ("data", "reports", "links")

_DEFAULT_REPORT_FILENAME = "links-report.html"


def find_repo_root() -> Path | None:
    """Locate this checkout's repo root by walking up from this module.

    Mirrors ``jlab.members.paths.find_repo_root`` (itself mirroring
    ``whoami.find_culture_yaml``): the anchor is this package's own
    location on disk, never the caller's current working directory, so
    invoking the CLI from an unrelated directory cannot redirect where
    scraped data gets written. Returns ``None`` when no repo root can be
    found — e.g. a wheel install with no ``culture.yaml`` shipped
    alongside the package.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / _REPO_MARKER).is_file():
            return parent
    return None


def links_reports_dir() -> Path:
    """Return the gitignored directory for links-report output.

    Creates the directory (and its parents) if it does not exist yet.
    Raises :class:`CliError` with :data:`~jlab.cli._errors.EXIT_ENV_ERROR`
    instead of writing anywhere else when no repo root can be resolved —
    scraped data must never be written outside this repo's ignored path,
    so refusing to write is the only acceptable fallback.
    """
    root = find_repo_root()
    if root is None:
        raise CliError(
            EXIT_ENV_ERROR,
            "cannot resolve the jetson-ai-lab-cli repo root for the links report output path",
            "run this from an editable/source checkout of jetson-ai-lab-cli "
            "(a wheel install has no culture.yaml to anchor on); the links "
            "report refuses to write outside its gitignored repo-relative "
            "path rather than falling back to the current directory",
        )
    out_dir = root.joinpath(*_REPORTS_SUBDIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def links_report_path(filename: str = _DEFAULT_REPORT_FILENAME) -> Path:
    """Return the fixed, repo-anchored path for a generated links artifact.

    ``filename`` may be overridden by callers that need a distinct name
    (e.g. the flat/summary CSVs, or tests), but is constrained to a bare
    filename: the report carries scraped URLs and author ids, and
    containment is the point (c5/h22, c9/h26). A name with any path
    separator, a parent reference, or an absolute root would escape the
    gitignored directory, so those are refused rather than normalised.
    """
    if filename != Path(filename).name or filename in {"", ".", ".."}:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"report filename must be a bare filename, got {filename!r}",
            remediation=(
                "pass a plain name like 'links-report.html' — the report "
                "path is fixed inside this repository and cannot be redirected"
            ),
        )
    return links_reports_dir() / filename

"""Per-user and per-channel deletion across the cache AND its derived reports.

Why this module exists (t12, obligation o15). Discord's Developer Terms require
API Data to be deleted when the applicable user asks, when Discord asks, and
once keeping it is no longer necessary; the published privacy policy promises
to honour deletion requests including "backups and derived indexes associated
with deleted data". A purge that clears jlab-mongodb but leaves the person in a
rendered report has not honoured that promise, so every purge here covers both.

What "derived reports" means, and how they are purged
-----------------------------------------------------
``jlab discord members`` and ``jlab discord links`` write per-run artifact sets
under ``data/reports/{members,links}/<run-id>/`` (plus a sibling
``<run-id>-cache`` for links). Those artifacts attribute rows to people by
**author id** — never by name alone — and the links artifacts carry jump URLs
that embed the **channel id**. A purge scans every file of every run directory
for the target id (matched on digit boundaries, so ``42`` does not match
``1420``) and **removes the whole run directory** when it is found.

Whole-run removal, not row surgery, is deliberate:

* a run is one rendered, internally consistent artifact set (HTML + flat CSV +
  derived summary CSV + coverage); deleting rows from rendered HTML reliably is
  not possible, and editing only the CSVs would leave totals and the HTML still
  describing the person;
* the artifacts are local, gitignored and regenerable by re-running the verb —
  over-deleting a derived report costs a re-run, under-deleting breaks a legal
  promise, so the sweep errs toward removal;
* a run directory is exactly the unit :mod:`jlab.atomic_writeset` writes, so
  removing it can never leave a half-set behind.

Known limit, stated rather than hidden: the members report records per-channel
*counts* only, never channel ids, so a **channel** purge cannot identify which
members runs that channel contributed to; those runs hold no content or
channel-identifying data from it. An **author** purge has no such gap — author
ids are written verbatim into every artifact that attributes anything to a
person.

Safety design
-------------
* Targets must be a bare Discord snowflake (digits only). Empty strings,
  whitespace, ``*``, ``all``, regex/SQL wildcards and negative numbers are
  refused with exit code 1 **before** any collection or file is touched.
* The retention bound (``--older-than DAYS``) must be a positive integer.
* The CLI verb previews by default (dry run) and deletes only with ``--yes``;
  every run reports exactly what matched and what was removed.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Iterator

from jlab import cache as _cache
from jlab import mongo as _mongo
from jlab.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

_SNOWFLAKE = re.compile(r"[0-9]{1,25}")
_RUN_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")
_RUN_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def validate_target(value: Any, label: str) -> str:
    """Return *value* as a bare snowflake string, or raise code 1.

    This is the anti-accident guard: nothing that could widen a targeted
    delete (empty, whitespace, wildcard, pattern, list) gets past it.
    """
    text = "" if value is None else str(value).strip()
    if not _SNOWFLAKE.fullmatch(text):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=(
                f"refusing to purge: {label} must be one explicit Discord id "
                f"(digits only), got {value!r}"
            ),
            remediation=(
                "pass the numeric id exactly, e.g. --author 123456789012345678; "
                "empty, wildcard and pattern targets are never accepted"
            ),
        )
    return text


def _report_roots() -> list[Path]:
    """The real derived-report parents (members and links)."""
    from jlab.links import paths as _links_paths
    from jlab.members import paths as _members_paths

    return [_members_paths.members_reports_dir(), _links_paths.links_reports_dir()]


def _run_dirs(report_dirs: Iterable[Path]) -> Iterator[tuple[Path, Path]]:
    for root in report_dirs:
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                yield root, child


def _run_mentions(run_dir: Path, pattern: re.Pattern[str]) -> bool:
    for path in run_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        if pattern.search(text):
            return True
    return False


def _remove_run(root: Path, run_dir: Path) -> None:
    # Containment: only ever remove a direct child of a report root.
    if run_dir.resolve().parent != root.resolve():
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to remove {run_dir}: it is not inside {root}",
            remediation="remove any symlink in the report directory and re-run",
        )
    shutil.rmtree(run_dir)


def _sweep(
    report_dirs: Iterable[Path] | None,
    predicate: Any,
    dry_run: bool,
) -> dict[str, Any]:
    roots = _report_roots() if report_dirs is None else list(report_dirs)
    scanned = 0
    matched: list[str] = []
    removed: list[str] = []
    for root, run_dir in _run_dirs(roots):
        scanned += 1
        if not predicate(run_dir):
            continue
        matched.append(str(run_dir))
        if not dry_run:
            _remove_run(root, run_dir)
            removed.append(str(run_dir))
    return {"runs_scanned": scanned, "runs_matched": matched, "runs_removed": removed}


def sweep_reports(
    target_id: str,
    *,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove every derived-report run directory that mentions *target_id*."""
    target = validate_target(target_id, "target id")
    pattern = re.compile(rf"(?<![0-9]){re.escape(target)}(?![0-9])")
    return _sweep(report_dirs, lambda run: _run_mentions(run, pattern), dry_run)


def _result(kind: str, value: str, dry_run: bool, cache: dict, reports: dict) -> dict[str, Any]:
    return {
        "target": {"kind": kind, "value": value},
        "dry_run": dry_run,
        "cache": cache,
        "reports": reports,
    }


def _with_collection(collection: Any, fn: Any) -> dict[str, Any]:
    if collection is None:
        with _mongo.message_collection() as col:
            return fn(col)
    return fn(collection)


def purge_author(
    author_id: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete one author's messages from the cache and every report naming them."""
    target = validate_target(author_id, "author id")
    cache = _with_collection(
        collection, lambda col: _cache.delete_by_author(target, collection=col, dry_run=dry_run)
    )
    reports = sweep_reports(target, report_dirs=report_dirs, dry_run=dry_run)
    return _result("author", target, dry_run, cache, reports)


def purge_channel(
    channel_id: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete one channel's messages from the cache and every report carrying its id."""
    target = validate_target(channel_id, "channel id")
    cache = _with_collection(
        collection, lambda col: _cache.delete_by_channel(target, collection=col, dry_run=dry_run)
    )
    reports = sweep_reports(target, report_dirs=report_dirs, dry_run=dry_run)
    return _result("channel", target, dry_run, cache, reports)


def _run_older_than(cutoff: dt.datetime) -> Any:
    def _predicate(run_dir: Path) -> bool:
        match = _RUN_STAMP.match(run_dir.name)
        if not match:
            return False  # an unrecognised directory is never guessed at
        stamp = dt.datetime.strptime(match.group(1), _RUN_STAMP_FORMAT)
        return stamp.replace(tzinfo=dt.timezone.utc) < cutoff

    return _predicate


def purge_older_than(
    days: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Enforce the retention bound: drop cached messages and report runs older than *days*."""
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=(
                "refusing to purge: --older-than must be a positive number of days, "
                f"got {days!r}"
            ),
            remediation="pass a whole number of days greater than zero, e.g. --older-than 365",
        )
    cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)
    cache = _with_collection(
        collection, lambda col: _cache.delete_older_than(cutoff, collection=col, dry_run=dry_run)
    )
    reports = _sweep(report_dirs, _run_older_than(cutoff), dry_run)
    result = _result("older_than", str(days), dry_run, cache, reports)
    result["cutoff"] = cutoff.isoformat()
    return result

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
  every run reports exactly what matched and what was removed. A dry run
  changes nothing at all: no delete, no coverage change, no suppression
  record, no lock file.

Coverage, locking and suppression (wave-3 integration)
------------------------------------------------------
Deleting cached messages without touching :mod:`jlab.coverage` would leave
coverage claiming spans whose messages are gone, so a later read reports a
purged window as complete and the deletion never shows up as a gap. So:

* ``--channel X`` deletes X's messages **and** clears X's coverage, both while
  holding X's coverage lock (:func:`jlab.coverage.channel_lock`) — a
  concurrent fetch of X can therefore not store-and-widen over the delete.
* ``--older-than DAYS`` works **one channel at a time**: for every channel
  that has old messages or any coverage record, it takes that channel's lock,
  deletes that channel's messages older than the cutoff and trims its coverage
  to the cutoff (:func:`jlab.coverage.trim_before`), then releases it before
  the next. Holding one lock at a time cannot deadlock against a fetch (which
  also holds one), never blocks the whole guild behind one busy channel, and
  gives each channel exactly the atomicity a channel purge gets. The channel
  set is taken at the start of the run — a channel first cached *during* the
  run is caught by the next run (the verb is idempotent and meant for cron).
* ``--author X`` does **not** change coverage: the windows were fetched, and
  the author is suppressed rather than un-fetched. It first records a
  **keyed hash** of X in the suppression list (:func:`jlab.cache.
  suppress_author` — HMAC-SHA256 under an HKDF sub-key of ``JLAB_CACHE_KEY``,
  never the raw id), then deletes. :func:`jlab.cache.store_messages` refuses
  suppressed authors, so no later fetch or reconciliation sweep re-caches
  them. Suppression is recorded *before* the delete, so a missing key fails
  (code 2) with nothing deleted rather than deleting without the guard.

Locks are taken blocking by default (a purge waits for an in-flight fetch of
the same channel rather than failing a cron run); ``blocking=False`` raises
:class:`CliError` (code 2) instead.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Iterator

from jlab import cache as _cache
from jlab import coverage as _coverage
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


def _result(
    kind: str,
    value: str,
    dry_run: bool,
    cache: dict,
    reports: dict,
    coverage: dict,
) -> dict[str, Any]:
    return {
        "target": {"kind": kind, "value": value},
        "dry_run": dry_run,
        "cache": cache,
        "reports": reports,
        "coverage": coverage,
    }


def _with_collection(collection: Any, fn: Any) -> Any:
    if collection is None:
        with _mongo.message_collection() as col:
            return fn(col)
    return fn(collection)


def _coverage_of(col: Any) -> Any:
    return _mongo.sibling_collection(col, _mongo.COVERAGE_COLLECTION)


def purge_author(
    author_id: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Suppress one author, delete their messages, and sweep every report naming them.

    Coverage is deliberately left unchanged: the windows were fetched, and the
    author is now suppressed (see the module docstring), so ``coverage`` in the
    result is always ``{"channels": [], "applied": False}``.
    """
    target = validate_target(author_id, "author id")

    def run(col: Any) -> tuple[dict, dict]:
        if dry_run:
            suppression = {"recorded": False, "already_present": None}
        else:
            # Before the delete: no key means nothing is deleted without the guard.
            sup = _mongo.sibling_collection(col, _mongo.SUPPRESSION_COLLECTION)
            suppression = _cache.suppress_author(target, collection=sup)
        cache = _cache.delete_by_author(target, collection=col, dry_run=dry_run)
        return cache, suppression

    cache, suppression = _with_collection(collection, run)
    reports = sweep_reports(target, report_dirs=report_dirs, dry_run=dry_run)
    result = _result("author", target, dry_run, cache, reports, {"channels": [], "applied": False})
    result["suppression"] = suppression
    return result


def purge_channel(
    channel_id: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
    blocking: bool = True,
) -> dict[str, Any]:
    """Delete one channel's messages and coverage, and every report carrying its id.

    The delete and the coverage clear run under the channel's coverage lock.
    """
    target = validate_target(channel_id, "channel id")

    def run(col: Any) -> tuple[dict, list[str]]:
        cov = _coverage_of(col)
        if dry_run:
            had = bool(_coverage.read_coverage(target, collection=cov))
            return _cache.delete_by_channel(target, collection=col, dry_run=True), (
                [target] if had else []
            )
        with _coverage.channel_lock(target, blocking=blocking):
            cache = _cache.delete_by_channel(target, collection=col)
            had = bool(_coverage.read_coverage(target, collection=cov))
            _coverage.clear_coverage(target, collection=cov)
        return cache, [target] if had else []

    cache, channels = _with_collection(collection, run)
    reports = sweep_reports(target, report_dirs=report_dirs, dry_run=dry_run)
    return _result(
        "channel", target, dry_run, cache, reports, {"channels": channels, "applied": not dry_run}
    )


def _run_older_than(cutoff: dt.datetime) -> Any:
    def _predicate(run_dir: Path) -> bool:
        match = _RUN_STAMP.match(run_dir.name)
        if not match:
            return False  # an unrecognised directory is never guessed at
        stamp = dt.datetime.strptime(match.group(1), _RUN_STAMP_FORMAT)
        return stamp.replace(tzinfo=dt.timezone.utc) < cutoff

    return _predicate


def _older_than_cache(
    col: Any, cutoff: dt.datetime, dry_run: bool, blocking: bool
) -> tuple[dict, list[str]]:
    cov = _coverage_of(col)
    if dry_run:
        cache = _cache.delete_older_than(cutoff, collection=col, dry_run=True)
        affected = [
            channel
            for channel in _coverage.covered_channels(collection=cov)
            if any(i.start < cutoff for i in _coverage.read_coverage(channel, collection=cov))
        ]
        return cache, affected

    channels = sorted(
        set(_cache.old_message_channels(cutoff, collection=col))
        | set(_coverage.covered_channels(collection=cov))
    )
    matched = deleted = 0
    affected: list[str] = []
    for channel in channels:
        if not _coverage.is_valid_channel_id(channel):
            # Cannot carry coverage (widen validates the id), so no lock to take.
            part = _cache.delete_older_than(cutoff, channel_id=channel, collection=col)
        else:
            with _coverage.channel_lock(channel, blocking=blocking):
                part = _cache.delete_older_than(cutoff, channel_id=channel, collection=col)
                before = _coverage.read_coverage(channel, collection=cov)
                if any(i.start < cutoff for i in before):
                    _coverage.trim_before(channel, cutoff, collection=cov)
                    affected.append(channel)
        matched += part["matched"]
        deleted += part["deleted"]
    return {"matched": matched, "deleted": deleted}, affected


def purge_older_than(
    days: Any,
    *,
    collection: Any = None,
    report_dirs: Iterable[Path] | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Enforce the retention bound: drop old cached messages, coverage and report runs.

    Each channel's delete and coverage trim run under that channel's lock, one
    channel at a time (see the module docstring for why).
    """
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=(
                "refusing to purge: --older-than must be a positive number of days, "
                f"got {days!r}"
            ),
            remediation="pass a whole number of days greater than zero, e.g. --older-than 365",
        )
    try:
        cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)
    except OverflowError:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"refusing to purge: --older-than {days} days reaches before year 1",
            remediation="pass a realistic retention window, e.g. --older-than 365",
        )
    cache, channels = _with_collection(
        collection, lambda col: _older_than_cache(col, cutoff, dry_run, blocking)
    )
    reports = _sweep(report_dirs, _run_older_than(cutoff), dry_run)
    result = _result(
        "older_than",
        str(days),
        dry_run,
        cache,
        reports,
        {"channels": channels, "applied": not dry_run},
    )
    result["cutoff"] = cutoff.isoformat()
    return result

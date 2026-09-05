"""Cache the link-extraction payload so a report can be regenerated
without a second Discord scan (t9).

One 90-day sweep over ~120 channels costs a large share of a bot token
shared with other mesh agents (see ``jlab/members/aggregate.py`` for the
same cost on the members side). A maintainer who wants a differently
shaped report today — a narrower time window, a different filename, a
rerun after a rendering bug fix — should not have to pay for a second
scan. This module persists exactly the payload
:func:`jlab.links.extract.extract_links` produces, plus the one piece of
metadata a renderer needs to know the cache is a cache: *when the scan
ran*.

What is (and is not) cached
----------------------------

The cache carries the extraction records verbatim — ``url``, ``channel``,
``timestamp``, ``thread``, ``author_id``, ``jump_url``,
``from_attachment`` — the same all-scalar, id-only shape
:func:`~jlab.links.extract.extract_links` already returns (see its module
docstring). No display names are ever written here: ``author_id`` is a
Discord snowflake string, never resolved to a name. Name resolution is a
render-time concern (see ``jlab/members/resolve.py`` for the sibling
report's identical posture) and stays entirely outside this module.

Also cached, optionally: a *trimmed* copy of the scan's coverage statuses
(d3). ``jlab.links.report``'s ``COVERAGE_COLUMNS`` are built from just six
scalar fields off a ``scan_window()`` result -- ``cutoff``,
``scanned_text_channels``, ``channels_ok``, ``channels_partial``,
``channels_failed``, ``complete`` (see :data:`_COVERAGE_KEYS`). Passing
that same ``scan_window()`` result as ``write_cache(..., coverage=...)``
stores exactly those six fields under a ``coverage`` key -- no channel
list, no per-channel reasons, no message content -- so a later
``--from-cache`` render can state the run's real coverage instead of
``unknown``. ``coverage`` is optional and additive: a cache written
without it (or written before this key existed) has no ``coverage`` key
at all, and :func:`load_cache` returns such a payload unchanged --
callers that read ``payload.get("coverage")`` see ``None`` and fall back
to rendering ``unknown``, exactly as before.

Cached the same way, and for the same reason: the *rest* of the scan's
self-description (:data:`_SCAN_META_KEYS`) -- ``guild_id``, ``since_days``,
``exclude_bots``, ``message_count`` -- stored under a ``scan_meta`` key.
A ``--from-cache`` render has no scan to read those from, and rebuilding
them from the environment and flags *of the re-render* produces a report
whose metadata contradicts its own records: change ``JLAB_GUILD_ID`` or
pass ``--include-bots`` and the page would claim a guild and a bot policy
the cached records were never gathered under. Caching them is what keeps
the report's header a statement about the scan that actually happened.
``scan_meta`` is optional and additive on exactly the ``coverage`` terms
above -- absent field, absent key, ``unknown`` at render time.

Scan time vs. render time
--------------------------

The cache stores ``scanned_at`` — the UTC instant the scan that produced
these records ran — never the instant the cache is loaded or a report is
rendered from it. A renderer reading :func:`load_cache`'s return value
must show *that* timestamp on the report's face, not
``datetime.now()``, so a maintainer looking at a cache-rendered report
can never mistake a week-old scan for a fresh one.

Attachment-URL staleness
--------------------------

Discord attachment URLs are signed and expire roughly 14-22 hours after
they are fetched (measured; see ``jlab/links/extract.py``'s "Ephemeral
attachment URLs" section). :func:`attachments_expired` tells a caller
whether a cache has crossed the *conservative* (low) end of that window,
so a renderer can flag cached ``from_attachment`` records as dead links
rather than presenting them as live. This module does no rendering
itself — it only makes the fact checkable.

Storage
-------

One JSON file per run, written into that run's own directory from
``jlab/links/paths.py`` (``links_run_dir(run_id)``), via the same
all-or-nothing :func:`jlab.atomic_writeset.write_artifact_set` primitive
the rest of the links pipeline uses — so a crash mid-write never leaves a
truncated, half-written cache file behind. The run directory sits under
``data/reports/links/``, which is gitignored (see ``.gitignore``); this
module writes nothing outside that boundary.

Stdlib only: ``json``, ``datetime``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jlab.atomic_writeset import write_artifact_set
from jlab.links.paths import links_report_path, links_run_dir

# Default cache filename, relative to a run's own directory.
CACHE_FILENAME = "links-cache.json"

# Conservative (low) end of the measured 14-22 hour attachment-URL expiry
# window. A cache older than this is treated as carrying dead attachment
# URLs -- better to flag them early than to present a URL that might
# already be dead as live.
ATTACHMENT_URL_EXPIRY_HOURS = 14

# The scan_window() fields jlab.links.report's COVERAGE_COLUMNS are built
# from (see _coverage_cells / _coverage_note there). Trimming to exactly
# these keys -- and nothing else off the scan result -- is what makes the
# cached "coverage" a small, content-free copy rather than a duplicate of
# the whole scan.
_COVERAGE_KEYS: tuple[str, ...] = (
    "cutoff",
    "scanned_text_channels",
    "channels_ok",
    "channels_partial",
    "channels_failed",
    "complete",
)

# The remaining scan_window() fields the report's metadata block states as
# fact -- which guild was read, how long a window, whether bots were kept,
# and how many messages were considered. They are cached for exactly the
# reason the coverage fields are: on a --from-cache render there is no scan
# to read them from, and reconstructing them from the CURRENT environment
# and the CURRENT flags makes the report assert something it never
# measured. Like the coverage keys these are all scalars -- no channel
# list, no message content.
_SCAN_META_KEYS: tuple[str, ...] = (
    "guild_id",
    "since_days",
    "exclude_bots",
    "message_count",
)


def _trim(scan_result: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    """Keep only the fields *keys* names, and only those actually present.

    A field the scan result does not carry contributes no key at all, so a
    consumer sees its absence and renders ``unknown`` rather than reading
    an invented default.
    """
    if not scan_result:
        return {}
    return {key: scan_result[key] for key in keys if key in scan_result}


def write_cache(
    run_id: str,
    records: list[dict[str, Any]],
    *,
    scanned_at: datetime | None = None,
    coverage: dict[str, Any] | None = None,
    scan_meta: dict[str, Any] | None = None,
    filename: str = CACHE_FILENAME,
) -> Path:
    """Persist *records* (an :func:`extract_links` payload) for *run_id*.

    *scanned_at* is the instant the scan that produced *records* ran --
    defaults to ``datetime.now(timezone.utc)`` when omitted, so a caller
    that scans and immediately caches doesn't need to thread a timestamp
    through by hand. It is always stored normalised to UTC, ISO 8601.

    *coverage*, when given, is the ``scan_window()`` result the records
    were extracted from (or any mapping carrying the same keys). Only the
    handful of fields :data:`_COVERAGE_KEYS` names are kept -- see the
    module docstring's "Also cached, optionally" section -- and stored
    under a ``coverage`` key. Omitting *coverage* (the default) writes the
    same two-key ``{scanned_at, records}`` payload this module has always
    written; no ``coverage`` key is added to the file in that case, so an
    old caller/consumer sees no shape change.

    *scan_meta* is the same ``scan_window()`` result again, trimmed instead
    to :data:`_SCAN_META_KEYS` -- the guild that was read, the window
    length, whether bots were excluded, and how many messages were
    considered -- and stored under a ``scan_meta`` key. It follows the
    ``coverage`` precedent exactly: a field the scan result does not carry
    is not written, an omitted *scan_meta* adds no key at all, and a
    consumer reading ``payload.get("scan_meta")`` on an older cache sees
    ``None`` and renders ``unknown``. Caching it is what stops a
    ``--from-cache`` render from restating the *current* guild id or the
    *current* ``--include-bots`` flag as though the original scan had used
    them.

    Written atomically via :func:`jlab.atomic_writeset.write_artifact_set`
    onto *run_id*'s own directory, so a crash mid-write cannot leave a
    truncated cache file in place. Returns the path the cache was written
    to.
    """
    when = scanned_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)

    payload: dict[str, Any] = {
        "scanned_at": when.isoformat(),
        "records": records,
    }
    trimmed_coverage = _trim(coverage, _COVERAGE_KEYS)
    if trimmed_coverage:
        payload["coverage"] = trimmed_coverage
    trimmed_meta = _trim(scan_meta, _SCAN_META_KEYS)
    if trimmed_meta:
        payload["scan_meta"] = trimmed_meta
    text = json.dumps(payload, indent=2, sort_keys=True)

    dest_dir = links_run_dir(run_id)
    write_artifact_set(dest_dir, {filename: text})
    return links_report_path(run_id, filename)


def load_cache(run_id: str, *, filename: str = CACHE_FILENAME) -> dict[str, Any]:
    """Load the cache written by :func:`write_cache` for *run_id*.

    Never opens a Discord connection or imports anything that would --
    this is a plain JSON read. Returns a dict carrying ``scanned_at``
    (ISO 8601 UTC string -- the scan time, never the load time) and
    ``records`` (the exact list :func:`extract_links` produced, round-
    tripped through JSON with no loss: empty ``{}`` channel/thread refs
    and ``from_attachment`` booleans survive unchanged). When the cache
    was written with coverage fields (see :func:`write_cache`), a
    ``coverage`` key is present too; a cache written without them -- or
    written before this key existed -- simply has no ``coverage`` key, and
    this function does not invent one: callers should use
    ``payload.get("coverage")``. The ``scan_meta`` key behaves identically.

    This is a *read*, not a validator: the JSON on disk is returned as it
    parses, whatever shape that is. A caller that must trust the payload's
    shape (``records``, ``scanned_at``) is responsible for checking it and
    reporting a corrupt cache as such -- see
    ``jlab.cli._commands.discord._load_links_cache``.
    """
    path = links_report_path(run_id, filename)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def cache_scanned_at(payload: dict[str, Any]) -> datetime:
    """Return the scan timestamp recorded in a loaded cache *payload*."""
    return datetime.fromisoformat(payload["scanned_at"])


def attachments_expired(payload: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return whether *payload*'s cached attachment URLs are likely dead.

    True once *now* is more than :data:`ATTACHMENT_URL_EXPIRY_HOURS` past
    the cache's ``scanned_at`` -- the conservative (earliest) end of the
    measured 14-22 hour Discord attachment-URL expiry window. A renderer
    should treat any ``from_attachment: true`` record in an expired cache
    as a dead link rather than presenting it as live. This function makes
    that fact checkable; it does not render or filter anything itself.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    scanned = cache_scanned_at(payload)
    return current - scanned > timedelta(hours=ATTACHMENT_URL_EXPIRY_HOURS)

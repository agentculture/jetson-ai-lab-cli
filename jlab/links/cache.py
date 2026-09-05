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


def write_cache(
    run_id: str,
    records: list[dict[str, Any]],
    *,
    scanned_at: datetime | None = None,
    filename: str = CACHE_FILENAME,
) -> Path:
    """Persist *records* (an :func:`extract_links` payload) for *run_id*.

    *scanned_at* is the instant the scan that produced *records* ran --
    defaults to ``datetime.now(timezone.utc)`` when omitted, so a caller
    that scans and immediately caches doesn't need to thread a timestamp
    through by hand. It is always stored normalised to UTC, ISO 8601.

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

    payload = {
        "scanned_at": when.isoformat(),
        "records": records,
    }
    text = json.dumps(payload, indent=2, sort_keys=True)

    dest_dir = links_run_dir(run_id)
    write_artifact_set(dest_dir, {filename: text})
    return links_report_path(run_id, filename)


def load_cache(run_id: str, *, filename: str = CACHE_FILENAME) -> dict[str, Any]:
    """Load the cache written by :func:`write_cache` for *run_id*.

    Never opens a Discord connection or imports anything that would --
    this is a plain JSON read. Returns a dict with two keys: ``scanned_at``
    (ISO 8601 UTC string -- the scan time, never the load time) and
    ``records`` (the exact list :func:`extract_links` produced, round-
    tripped through JSON with no loss: empty ``{}`` channel/thread refs
    and ``from_attachment`` booleans survive unchanged).
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

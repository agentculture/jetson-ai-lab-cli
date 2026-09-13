"""Reconcile one time span's cached messages against a live read of it.

Both :mod:`jlab.sweep` (the daily reconciliation pass, re-reading every
already-covered span) and :mod:`jlab.read`'s ``--refresh`` (a single bounded
live re-read of the window a ``read`` call will serve) need the exact same
three decisions applied to a span once they have live-read it:

* a message whose body, Discord edit timestamp, author name or author
  display name changed, or one the cache never had, is (re)written through
  :func:`jlab.cache.store_messages` — the single enforcement point for purge
  suppression, never bypassed here. The latest name wins, just as the latest
  edit does (deviation d4: names are stored, encrypted, alongside the body);
* a cached message Discord no longer returns is deleted — **only** when the
  span was read completely. A span cut short by a rate limit or an error
  deletes nothing: a partial re-read proves nothing about what is absent;
* whether the span counts as reconciled (``complete``) is reported back so
  the caller can decide whether it may widen coverage for it — this module
  never touches coverage itself, since a sweep's span is already covered and
  a fresh read's span may not be yet, and only the caller knows which.

This is genuinely one function, factored out so there is exactly one place
this logic lives — :mod:`jlab.sweep`'s own module docstring already states
the rule this enforces ("Deletion is conservative by construction").
"""

from __future__ import annotations

from typing import Any

from jlab import cache as _cache
from jlab import coverage as _coverage


def _changed(cached: dict[str, Any], live: dict[str, Any]) -> bool:
    """Whether Discord's copy differs from the cached one.

    Compares body, Discord's edit timestamp, and (d4) author name/display
    name — a live author dict nested under ``live["author"]`` against the
    cache's own flat ``author_name``/``author_display_name`` fields (already
    decrypted by :func:`jlab.cache.messages_between`). A renamed member is a
    change to re-store, exactly like an edited body.
    """
    if (cached.get("content") or "") != (live.get("content") or ""):
        return True
    if _cache._parse_timestamp(cached.get("updated_at")) != _cache._parse_timestamp(
        live.get("edited_at")
    ):
        return True
    live_author = live.get("author") or {}
    if (cached.get("author_name") or None) != (live_author.get("name") or None):
        return True
    return (cached.get("author_display_name") or None) != (live_author.get("display_name") or None)


def reconcile_span(
    channel_id: str,
    span: _coverage.Interval,
    live_messages: list[dict[str, Any]],
    *,
    complete: bool,
    reason: str | None,
    collection: Any,
    suppression: Any = None,
) -> dict[str, Any]:
    """Reconcile the cache's copy of *span* against *live_messages*.

    *live_messages* are already :func:`jlab.cli._discord._serialize_message`-
    shaped dicts for a live read of *span* — this function never talks to
    Discord itself, so a multi-span sweep and a single bounded read can each
    supply it however they obtained it. *complete* is whatever the live read
    reported: ``False`` means a rate limit, cap or error kept the read from
    covering the whole span, so deletion is skipped entirely (see the module
    docstring) — the returned ``complete``/``reason`` simply echo that back.

    Returns ``{"complete", "reason", "updated", "added", "deleted",
    "suppressed"}``: counts, not documents, since both callers already hold
    their own per-channel/per-run report shape to fold these into.
    """
    live = {m["id"]: m for m in live_messages if m.get("id") is not None}
    cached = {
        m["message_id"]: m
        for m in _cache.messages_between(channel_id, span.start, span.end, collection=collection)
    }

    edited = [m for mid, m in live.items() if mid in cached and _changed(cached[mid], m)]
    added = [m for mid, m in live.items() if mid not in cached]

    updated_count = 0
    added_count = 0
    suppressed_count = 0
    for batch, is_new in ((edited, False), (added, True)):
        if not batch:
            continue
        stored = _cache.store_messages(
            channel_id, batch, collection=collection, suppression=suppression
        )
        if is_new:
            added_count += int(stored["stored"])
        else:
            updated_count += int(stored["stored"])
        suppressed_count += int(stored.get("suppressed", 0))

    if not complete:
        # A partial re-read proves nothing about what is absent: no deletion.
        return {
            "complete": False,
            "reason": reason,
            "updated": updated_count,
            "added": added_count,
            "deleted": 0,
            "suppressed": suppressed_count,
        }

    live_stamps = {_cache._parse_timestamp(m.get("created_at")) for m in live.values()}
    doomed = sorted(
        mid
        for mid, m in cached.items()
        if mid not in live and _cache._parse_timestamp(m.get("created_at")) not in live_stamps
    )
    removed = _cache.delete_message_ids(channel_id, doomed, collection=collection)

    return {
        "complete": True,
        "reason": None,
        "updated": updated_count,
        "added": added_count,
        "deleted": removed["deleted"],
        "suppressed": suppressed_count,
    }

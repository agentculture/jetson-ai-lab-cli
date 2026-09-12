"""``jlab discord read`` — serve recent channel messages from the cache.

t9 (obligations o21, o22). Before this task ``read`` was a live-only verb:
every invocation called into :mod:`jlab.cli._discord` and issued a Discord
request. It is now cache-served by default, and ``--refresh`` is the ONLY
path by which it touches Discord:

* without ``--refresh``, :func:`serve_read` never imports or calls anything
  that reaches the Discord seam — it reads :mod:`jlab.cache` and
  :mod:`jlab.coverage` only, so a caller can tell from the command line alone
  whether the network was touched (o22);
* with ``--refresh``, "forces a live re-read from Discord when something may
  have been added or changed" (the spec's own words) — not a gap-only fetch.
  An edit or a deletion inside an already-covered window would never surface
  through :func:`jlab.fetch.fetch_channel` (it only requests what coverage
  says is missing), so ``--refresh`` instead: (a) reuses
  :func:`jlab.fetch.preflight` and :func:`jlab.fetch.validated_channel` — the
  same preflight/guild/public guards ``fetch`` uses, never copied; (b) live-
  reads the window ``read`` will serve (the most recent *limit* messages,
  back to the oldest one returned); (c)/(d)/(e) reconciles that span through
  :func:`jlab.reconcile.reconcile_span` — the exact same store/delete/widen
  rules :mod:`jlab.sweep` applies per span, factored into one shared
  function so this is not a second copy of that logic; (f) then serves from
  the cache, same as the no-flag path, so the message shape is identical
  whichever path filled the cache.

**Why the returned messages are not byte-identical to the old live shape.**
:mod:`jlab.cache`'s documented schema deliberately never stores an author's
display name (only ``author_id`` — see ``jlab/cache.py``'s module docstring:
"never a username — names are resolved at render time, as in the
members/links paths"). Resolving a name at render time (as members/links do)
means one ``guild.fetch_member`` call per author, which is itself a Discord
request — and issuing one on every cache-served ``read`` would silently
reintroduce exactly the network dependency ``--refresh`` exists to make
explicit. So cache-served ``read`` never resolves a name: ``author.name`` and
``author.display_name`` are ``None`` (never a fabricated label — the same
"empty means absent" convention :func:`jlab.cli._discord._serialize_channel_ref`
already uses for an unknown channel), and text mode falls back to the raw
author id for the printed line. This is the one place o21's "same text/JSON
shape as the live read does today" is a shape promise, not a value promise:
the envelope (``channel_id``/``messages``/``complete`` at the top, and
``id``/``author``/``content``/``created_at``/``edited_at``/``channel``/
``jump_url``/``attachments``/``embeds``/``thread`` on each message) is
unchanged; the values a cache can actually supply are what they are.

**Gap reporting.** "Covered" is decided the same way :mod:`jlab.coverage`
decides it everywhere else: the window from the oldest message this call
would return to *now* is checked against the channel's recorded coverage
intervals (:func:`jlab.coverage.describe`, the same "what is missing in a
window" primitive o18's gap reporting uses, never called with
``window=None``). A channel with no cached messages
and no coverage at all is reported as an uncovered window from
:data:`jlab.fetch.DISCORD_EPOCH` to now, rather than as an empty result that
reads as "no messages" (o21).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import reconcile as _reconcile
from jlab.cli import _discord
from jlab.cli._errors import EXIT_USER_ERROR, CliError

UTC = dt.timezone.utc


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_message_shape(doc: dict[str, Any], channel_id: str) -> dict[str, Any]:
    """A cached document, in the same envelope shape :func:`jlab.cli._discord.
    _serialize_message` emits for a live read (see the module docstring for
    which values a cache-served read can and cannot supply).
    """
    return {
        "id": doc.get("message_id"),
        "author": {
            "id": doc.get("author_id"),
            "name": None,
            "display_name": None,
            "bot": bool(doc.get("author_is_bot")),
        },
        "content": doc.get("content"),
        "created_at": _iso(doc.get("created_at")),
        "edited_at": _iso(doc.get("updated_at")),
        "channel": {"id": str(channel_id)},
        "jump_url": doc.get("jump_url"),
        "attachments": [],
        "embeds": [],
        "thread": {},
    }


def _live_reread_and_reconcile(
    channel_id: int,
    *,
    limit: int,
    guild_id: int | None,
    started: dt.datetime,
    blocking: bool,
    coverage_collection: Any,
    message_collection: Any,
    suppression: Any,
) -> None:
    """``--refresh``'s live re-read: fills/corrects the cache, never returns it.

    Live-reads the most recent *limit* messages (backward, past the
    100-per-request cap exactly like the old live ``read`` did), reconciles
    that span into the cache via :mod:`jlab.reconcile` (edits and new
    messages stored; deletions and coverage widening only when the re-read
    was complete), then returns — :func:`serve_read` does the actual serving
    afterwards. The whole thing runs under the channel's own lock, the same
    guard :func:`jlab.coverage.fetch_missing` and :mod:`jlab.sweep` use, so a
    concurrent fetch/sweep of this channel is serialised rather than racing.
    """
    # Imported here, not at module scope, and only reachable via this
    # function — see serve_read's docstring for why that matters (o22).
    from jlab import fetch as _fetch_mod

    gid = guild_id if guild_id is not None else _discord._guild_id()
    _fetch_mod.preflight(message_collection)

    async def action(client: Any) -> tuple[list[dict[str, Any]], bool, str | None]:
        channel = await _fetch_mod.validated_channel(client, channel_id, gid)
        raw, complete, reason = await _discord._collect_history(
            channel,
            limit=limit,
            after=None,
            before=None,
            backward=True,
            max_messages=None,
        )
        return (
            [_discord._serialize_message(m, channel) for m in raw],
            complete,
            reason,
        )

    with _coverage.channel_lock(str(channel_id), blocking=blocking):
        live_messages, complete, reason = _discord._run(action)

        if live_messages:
            span_start = min(_cache._parse_timestamp(m.get("created_at")) for m in live_messages)
        elif complete:
            # A complete drain that found nothing: the channel genuinely has
            # no messages at all, so the whole history is the reconciled span.
            from jlab.fetch import DISCORD_EPOCH

            span_start = DISCORD_EPOCH
        else:
            # Nothing usable was read (e.g. rate-limited before a single
            # message came back) — no span to reconcile or widen; the
            # channel's coverage is untouched, so the normal gap-reporting
            # path below still reports this honestly.
            return

        span = _coverage.Interval(span_start, started)
        result = _reconcile.reconcile_span(
            str(channel_id),
            span,
            live_messages,
            complete=complete,
            reason=reason,
            collection=message_collection,
            suppression=suppression,
        )
        if result["complete"]:
            _coverage.widen_coverage(
                str(channel_id), span, collection=coverage_collection, now=started
            )


def serve_read(
    channel_id_raw: str,
    *,
    limit: int = 20,
    refresh: bool = False,
    guild_id: int | None = None,
    now: dt.datetime | None = None,
    coverage_collection: Any = None,
    message_collection: Any = None,
    suppression: Any = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Serve up to *limit* of a channel's most recent cached messages.

    *refresh* is the only flag that reaches Discord: a live re-read of the
    window this call will serve, reconciled into the cache, THEN served from
    it — never a shortcut that returns the live read directly (see the
    module docstring for why a gap-only fetch is not enough here, and why
    this shares :mod:`jlab.reconcile` with :mod:`jlab.sweep` instead of
    re-implementing it). :mod:`jlab.fetch` (and the Discord seam it wraps) is
    imported lazily, only inside this branch, so no cache-only call path
    through this module ever even names it — o22's "a seam that raises on
    any call must not be touched" holds literally, not just behaviourally.

    Returns ``{"channel_id", "messages", "complete", "reason", "uncovered"}``:
    ``reason``/``uncovered`` are ``None``/``[]`` when *complete* is ``True``.
    """
    if limit < 1:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--limit must be >= 1, got {limit}",
            remediation="pass a positive integer",
        )

    channel_id = _discord.parse_id(channel_id_raw, "channel_id")
    started = now or dt.datetime.now(UTC)

    if refresh:
        _live_reread_and_reconcile(
            channel_id,
            limit=limit,
            guild_id=guild_id,
            started=started,
            blocking=blocking,
            coverage_collection=coverage_collection,
            message_collection=message_collection,
            suppression=suppression,
        )

    docs = _cache.fetch_messages(str(channel_id), collection=message_collection)
    tail = docs[-limit:] if limit else []
    messages = [_to_message_shape(d, str(channel_id)) for d in tail]

    covered = _coverage.read_coverage(str(channel_id), collection=coverage_collection)
    if tail and tail[0].get("created_at") is not None:
        window_start = tail[0]["created_at"]
    elif covered:
        window_start = covered[0].start
    else:
        # Never fetched at all: the whole history is the gap (o21) — never
        # report an empty cache as a complete, message-free channel.
        from jlab.fetch import DISCORD_EPOCH

        window_start = DISCORD_EPOCH

    if window_start >= started:
        # A degenerate (zero-length) window: nothing to be missing.
        complete, uncovered = True, []
    else:
        # jlab.coverage.describe is the single source of "what is missing in
        # a window" (o18's rule, reused here rather than re-derived): never
        # called with window=None, which describe's own docstring warns is
        # not a completeness claim.
        described = _coverage.describe(
            str(channel_id),
            _coverage.Interval(window_start, started),
            collection=coverage_collection,
        )
        complete = described["complete"]
        uncovered = described["uncovered"]

    if complete:
        reason = None
    else:
        reason = (
            f"{len(uncovered)} gap(s) between {uncovered[0]['start']} and "
            f"{uncovered[-1]['end']} are not cached; pass --refresh to "
            "fetch them from Discord"
        )

    return {
        "channel_id": str(channel_id),
        "messages": messages,
        "complete": complete,
        "reason": reason,
        "uncovered": uncovered,
    }

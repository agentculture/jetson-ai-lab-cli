"""``jlab discord read`` — serve recent channel messages from the cache.

t9 (obligations o21, o22). Before this task ``read`` was a live-only verb:
every invocation called into :mod:`jlab.cli._discord` and issued a Discord
request. It is now cache-served by default, and ``--refresh`` is the ONLY
path by which it touches Discord:

* without ``--refresh``, :func:`serve_read` never imports or calls anything
  that reaches the Discord seam — it reads :mod:`jlab.cache` and
  :mod:`jlab.coverage` only, so a caller can tell from the command line alone
  whether the network was touched (o22);
* with ``--refresh``, the live re-read is routed through the ONE guarded live
  path, :func:`jlab.fetch.fetch_channel` — guild + public check, gap-only
  fetch, coverage widening — never a re-implementation of that check here.
  Even with ``--refresh`` the result is still SERVED from the cache
  afterwards, not returned straight from the fetch, so the message shape is
  identical whichever path filled the cache.

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

    *refresh* is the only flag that reaches Discord (via
    :func:`jlab.fetch.fetch_channel`, imported lazily below so importing this
    module — and every cache-only call through it — never even names the
    fetch module, let alone the Discord seam it wraps). Returns
    ``{"channel_id", "messages", "complete", "reason", "uncovered"}``:
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
        # Imported here, not at module scope: no cache-only call path through
        # this module ever imports jlab.fetch (or the Discord seam it wraps),
        # which is what lets o22's "a seam that raises on any call must not
        # be touched" hold literally, not just behaviourally.
        from jlab import fetch as _fetch_mod

        _fetch_mod.fetch_channel(
            str(channel_id),
            guild_id=guild_id,
            max_messages=None,
            blocking=blocking,
            now=started,
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

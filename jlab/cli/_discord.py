"""Discord adapter — isolates ALL coupling to discord-bot-cli.

Every public function here is the *only* place that touches
``discord_bot_cli``. The command modules import this module, not the
upstream package, so an upstream API change touches one file.

Lazy-import: ``discord_bot_cli`` is **never** imported at module scope.
The private ``_seam()`` does the import inside a try/except and raises
a structured :class:`jlab.cli._errors.CliError` (code 2) when the
optional ``[discord]`` extra is absent. Both transport entry points
(:func:`parse_id` and :func:`_run`) translate discord-bot-cli's own
``CliError`` into jlab's so the 0/1/2 exit-code contract is preserved.

Local workaround (upstream ``agentculture/discord-bot-cli#14``): the
``author.bot`` flag, the display name, and ``after=``/``before=``/time-window
paging past the upstream 100-message cap are implemented **here**, against the raw
discord.py objects the action closures already hold. They are deliberately
confined to this one module so that dropping them for the upstream fields is a
single-file change. The pieces to delete when #14 ships are marked
``WORKAROUND(discord-bot-cli#14)``.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any

from jlab import cache as _cache
from jlab import mongo as _mongo
from jlab.cli._errors import CliError

_GUILD_ID_DEFAULT = "1326246312072581160"

# Per-channel read status. A *failed* channel is distinguishable from an
# *empty* one: empty is ``ok`` with ``message_count == 0``.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# Conservative fan-out cap. The live guild has ~104 public text channels and
# full-window paging turns each into many REST round trips, so an unbounded
# gather would put hundreds of requests in flight and hit Discord rate limits.
DEFAULT_CONCURRENCY = 4

# Windowed-scan defaults.
DEFAULT_WINDOW_DAYS = 90
DEFAULT_MAX_MESSAGES_PER_CHANNEL = 5000

# 429 handling: retry a rate-limited read a few times with the server's own
# ``retry_after`` (falling back to a fixed pause), then report *partial*.
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_FALLBACK_DELAY = 5.0
_RATE_LIMIT_MAX_DELAY = 60.0


async def _sleep(seconds: float) -> None:
    """Indirection over :func:`asyncio.sleep` so tests can skip real backoff."""
    await asyncio.sleep(seconds)


def _guild_id() -> int:
    """Return the guild id from ``JLAB_GUILD_ID`` env or the default."""
    raw = os.environ.get("JLAB_GUILD_ID", _GUILD_ID_DEFAULT)
    return parse_id(raw, "JLAB_GUILD_ID")


def _seam() -> Any:
    """Return ``discord_client`` from the optional ``discord_bot_cli`` extra.

    Raises :class:`CliError` (code 2) when the extra is not installed.
    """
    try:
        from discord_bot_cli import discord_client  # noqa: F811
    except ImportError:
        raise CliError(
            code=2,
            message="discord-bot-cli (with its [discord] extra) is not installed",
            remediation="install it: uv pip install 'jetson-ai-lab[discord]'",
        )
    return discord_client


def _as_cli_error(exc: Exception, *, code: int, message: str, remediation: str) -> CliError:
    """Translate an exception into jlab's :class:`CliError`.

    A jlab ``CliError`` passes through unchanged. discord-bot-cli's own
    ``CliError`` is preserved verbatim (its ``code``/``message``/``remediation``)
    so an environment failure stays exit 2 with its remediation, instead of
    being wrapped as a generic exit-1 "unexpected" by ``_dispatch``. Anything
    else gets the supplied fallback.
    """
    if isinstance(exc, CliError):
        return exc
    try:
        from discord_bot_cli.cli._errors import CliError as db_clierror  # noqa: F811
    except ImportError:
        db_clierror = ()  # type: ignore[assignment]
    if db_clierror and isinstance(exc, db_clierror):
        return CliError(code=exc.code, message=exc.message, remediation=exc.remediation)
    return CliError(code=code, message=message, remediation=remediation)


def _run(action: Any) -> Any:
    """Run a ``discord_client`` action, translating failures to :class:`CliError`.

    discord-bot-cli raises its own ``CliError`` for missing token / rejected
    token / unreadable guild (exit 2). Translating here keeps those as exit-2
    environment errors with their remediation rather than generic exit-1.
    """
    dc = _seam()
    try:
        return dc.run(action)
    except Exception as exc:  # noqa: BLE001
        raise _as_cli_error(
            exc,
            code=2,
            message=f"Discord request failed: {exc}",
            remediation="check DISCORD_BOT_TOKEN and that the bot can read the guild",
        ) from exc


def parse_id(value: str, label: str) -> int:
    """Parse a numeric id string, translating errors to :class:`CliError`."""
    dc = _seam()
    try:
        return dc.parse_id(value, label)
    except Exception as exc:  # noqa: BLE001
        raise _as_cli_error(
            exc,
            code=1,
            message=f"invalid {label}: {value!r}",
            remediation="pass a numeric id",
        ) from exc


def _channel_public(channel: Any, everyone: Any) -> bool | None:
    """Whether ``@everyone`` can view *channel* (``None`` if perms are unknown)."""
    try:
        return bool(channel.permissions_for(everyone).view_channel)
    except Exception:  # noqa: BLE001
        return None


def list_channels(guild_id: int, public_only: bool = True) -> list[dict]:
    """List a guild's channels with a ``public`` flag.

    When *public_only* is ``True`` (the default), drop entries whose
    ``public`` field is not ``True``.
    """

    async def action(client: Any) -> list[dict]:
        guild = await client.fetch_guild(guild_id)
        everyone = guild.default_role
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "type": str(getattr(c.type, "name", c.type)),
                "public": _channel_public(c, everyone),
            }
            for c in await guild.fetch_channels()
        ]

    channels = _run(action)
    if public_only:
        channels = [c for c in channels if c.get("public") is True]
    return channels


# ---------------------------------------------------------------------------
# WORKAROUND(discord-bot-cli#14) — author.bot + display name.
#
# Upstream ``channel.py::_message_dict`` emits only ``{id, author{id,name},
# content, created_at}``. jlab's action closures hold the RAW discord.py
# objects, so the two missing fields are read straight off them here. Delete
# this block and consume upstream's serializer once #14 ships.
# ---------------------------------------------------------------------------


def _serialize_author(author: Any) -> dict:
    """Serialize a message author, including ``bot`` and a display name.

    ``bot`` is the authoritative ``author.bot`` flag — bot exclusion must never
    use a name heuristic. The display name is the best *available* label and is
    never invented: the per-guild ``nick`` (only present when the author is a
    ``Member``), else ``global_name`` (``User``), else the username. Authoritative
    per-guild name/membership resolution is a separate concern (``guild.fetch_member``).
    """
    name = getattr(author, "name", None)
    nick = getattr(author, "nick", None)
    global_name = getattr(author, "global_name", None)
    author_id = getattr(author, "id", None)
    return {
        "id": str(author_id) if author_id is not None else None,
        "name": name,
        "display_name": nick or global_name or name,
        "bot": bool(getattr(author, "bot", False)),
    }


def _serialize_attachment(attachment: Any) -> dict:
    """Serialize an ``Attachment`` — the URL is the point; the rest is context."""
    attachment_id = getattr(attachment, "id", None)
    return {
        "id": str(attachment_id) if attachment_id is not None else None,
        "filename": getattr(attachment, "filename", None),
        "url": getattr(attachment, "url", None),
        "content_type": getattr(attachment, "content_type", None),
        "size": getattr(attachment, "size", None),
    }


def _serialize_embed_field(field: Any) -> dict:
    """Serialize one embed field (``name``/``value``); values carry links."""
    return {"name": getattr(field, "name", None), "value": getattr(field, "value", None)}


def _serialize_embed(embed: Any) -> dict:
    """Serialize an ``Embed`` — url **and** body.

    Carrying only ``url`` would lose most links: a live probe found that
    ``type=rich`` embeds have ``url is None`` and keep their links inside the
    description / field values, while ``type=link``/``type=article`` embeds do
    carry a ``url`` — but one that merely duplicates a URL already present in
    ``message.content``. The body is therefore the load-bearing part.
    """
    fields = getattr(embed, "fields", None) or []
    return {
        "type": str(getattr(embed, "type", None)) if getattr(embed, "type", None) else None,
        "url": getattr(embed, "url", None),
        "title": getattr(embed, "title", None),
        "description": getattr(embed, "description", None),
        "fields": [_serialize_embed_field(f) for f in fields],
    }


def _serialize_channel_ref(channel: Any) -> dict:
    """An ``{id, name}`` reference to a channel, or ``{}`` when there is none.

    Empty means *absent*, never a placeholder string: a consumer branches on
    truthiness and is never handed a fabricated identity.
    """
    if channel is None:
        return {}
    channel_id = getattr(channel, "id", None)
    return {
        "id": str(channel_id) if channel_id is not None else None,
        "name": getattr(channel, "name", None),
    }


def _jump_url(message: Any, channel: Any) -> str | None:
    """The message's jump link, or ``None`` — never a fabricated one.

    discord.py exposes ``Message.jump_url`` directly. The fallback builds the
    canonical link from guild/channel/message ids when they are all available,
    and gives up (``None``) when they are not.
    """
    url = getattr(message, "jump_url", None)
    if url:
        return str(url)
    channel = channel if channel is not None else getattr(message, "channel", None)
    guild_id = getattr(getattr(channel, "guild", None), "id", None)
    channel_id = getattr(channel, "id", None)
    message_id = getattr(message, "id", None)
    if guild_id is None or channel_id is None or message_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _serialize_message(message: Any, channel: Any = None) -> dict:
    """Serialize a discord.py ``Message`` to a plain dict (no objects escape).

    *channel* is the enclosing channel, threaded down from the caller so each
    record can name where it was posted (``read_messages`` and
    ``_probe_channel`` both hold it); it falls back to ``message.channel``.

    Keys are only ever **added** here — ``id``/``author``/``content``/
    ``created_at`` keep their names and meaning, so existing ``--json``
    consumers of ``channels``/``read``/``active`` are unaffected.
    """
    message_id = getattr(message, "id", None)
    created = getattr(message, "created_at", None)
    edited = getattr(message, "edited_at", None)
    channel = channel if channel is not None else getattr(message, "channel", None)
    thread = getattr(message, "thread", None)
    return {
        "id": str(message_id) if message_id is not None else None,
        "author": _serialize_author(message.author),
        "content": message.content,
        "created_at": created.isoformat() if created else None,
        # Discord's edit timestamp, None when the message was never edited.
        # The cache stores it as ``updated_at`` so an edit is detectable.
        "edited_at": edited.isoformat() if edited else None,
        "channel": _serialize_channel_ref(channel),
        "jump_url": _jump_url(message, channel),
        "attachments": [
            _serialize_attachment(a) for a in getattr(message, "attachments", None) or []
        ],
        "embeds": [_serialize_embed(e) for e in getattr(message, "embeds", None) or []],
        "thread": _serialize_channel_ref(thread),
    }


# ---------------------------------------------------------------------------
# WORKAROUND(discord-bot-cli#14) — after=/before= paging past the 100 cap.
# ---------------------------------------------------------------------------

# Discord clamps a single history request to 100 messages. The forward
# (``after=``) direction never has to care: discord.py pages internally for an
# unbounded ``history(limit=None, after=...)``. The backward direction walks
# pages explicitly so the cursor is jlab's own and can be asserted on, so it
# needs the per-request page size as a number.
_BACKWARD_PAGE_SIZE = 100


def _history(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    before: Any | None = None,
) -> Any:
    """Call ``channel.history``, passing only the cursors that are set.

    discord.py paginates ``history(limit=None, after=<datetime>)`` for us, so
    that is the whole of the forward "past the 100-message cap" story. A
    ``before`` cursor is passed through the same way; the page walk that uses
    it lives in :func:`_drain_backward`, which — deliberately — never sets
    ``after`` and ``before`` in the same call (see its docstring, qodo
    #3998468666). ``before`` may be a ``datetime`` (an initial window
    boundary) or a message-like object with an ``id`` (a mid-walk cursor);
    either is valid for discord.py's ``before=``.
    """
    kwargs: dict[str, Any] = {"limit": limit}
    if after is not None:
        kwargs["after"] = after
    if before is not None:
        kwargs["before"] = before
    return channel.history(**kwargs)


def _retry_after(exc: Exception) -> float | None:
    """Seconds to back off if *exc* is a Discord rate limit, else ``None``."""
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status", None)
    raw_delay = getattr(exc, "retry_after", None)
    if status != 429 and raw_delay is None:
        return None
    try:
        delay = float(raw_delay)
    except (TypeError, ValueError):
        delay = _RATE_LIMIT_FALLBACK_DELAY
    if delay <= 0:
        delay = _RATE_LIMIT_FALLBACK_DELAY
    return min(delay, _RATE_LIMIT_MAX_DELAY)


def _extreme_id_message(messages: list, *, want_min: bool) -> Any | None:
    """The message with the smallest/largest ``id`` (a snowflake) in *messages*.

    ``None`` if nothing in *messages* carries an ``id``. Comparing by id
    rather than ``created_at`` matters at a page boundary: a ``datetime``
    cursor is exclusive of its *whole* millisecond (qodo #3998468655), so two
    messages sharing one would have the tied one silently skipped or
    re-read. An id (a snowflake) is unique and strictly ordered by creation,
    so it has no such tie.
    """
    candidates = [m for m in messages if getattr(m, "id", None) is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda m: m.id) if want_min else max(candidates, key=lambda m: m.id)


def _min_id_message(messages: list) -> Any | None:
    """The message with the smallest ``id`` in *messages*, or ``None``."""
    return _extreme_id_message(messages, want_min=True)


def _max_id_message(messages: list) -> Any | None:
    """The message with the largest ``id`` in *messages*, or ``None``."""
    return _extreme_id_message(messages, want_min=False)


def _resume_cursor(
    collected: list,
    fallback: Any,
    *,
    backward: bool = False,
) -> Any:
    """Where to resume paging after a retry, given the direction of travel.

    Forward (``after=``) paging resumes from the NEWEST message read so far;
    backward (``before=``) paging must resume from the OLDEST. Both resume by
    the message itself (its id, a snowflake) rather than its ``created_at``
    datetime — see :func:`_extreme_id_message` for why a datetime cursor is
    the wrong tool here (qodo #3998468655). Using the wrong edge does not
    error either way — it silently skips the unread remainder of the window
    (backward) or re-reads it (forward), so the direction is load-bearing.
    *fallback* is the caller's own cursor, used when nothing was read yet.
    """
    message = _min_id_message(collected) if backward else _max_id_message(collected)
    return message if message is not None else fallback


async def _drain_backward_page(
    channel: Any,
    *,
    after: datetime | None,
    cursor: Any,
    page_size: int,
    max_messages: int | None,
    collected: list,
) -> tuple[list, bool, bool]:
    """Read one backward page into *collected*.

    Returns ``(page, capped, floor_hit)``. ``after`` is never handed to
    ``_history`` here — it is enforced client-side instead, message by
    message, and the page stops the moment one is reached (see
    :func:`_drain_backward`'s docstring for why). Since nothing is ever
    filtered server-side by ``after``, this page is exactly the newest
    ``page_size`` messages strictly older than *cursor* (or the newest
    overall, when *cursor* is ``None``) — discord.py's default order for a
    ``before=``-only call — so a message reached once ``after`` is crossed is
    guaranteed to be the oldest remaining in the page: it is safe to stop
    without reading the rest.
    """
    page: list = []
    floor_hit = False
    async for message in _history(channel, limit=page_size, after=None, before=cursor):
        created = getattr(message, "created_at", None)
        if after is not None and created is not None and created <= after:
            floor_hit = True
            break
        page.append(message)
        collected.append(message)
        if max_messages is not None and len(collected) > max_messages:
            # Overshoot by one, then drop it — see _drain.
            collected.pop()
            return page, True, floor_hit
    return page, False, floor_hit


def _next_backward_cursor(page: list, previous_id: Any) -> Any:
    """The next ``before`` cursor: the message with the smallest id in *page*.

    Raises when the page carries no advance over *previous_id* — a page that
    came back full but did not move the cursor backwards would loop forever
    otherwise; the caller turns that into a *partial* result carrying what
    was already read.
    """
    oldest = _min_id_message(page)
    if oldest is None or (previous_id is not None and oldest.id >= previous_id):
        raise RuntimeError(
            "backward cursor did not advance past "
            f"{previous_id!r}; stopping rather than re-reading the same page"
        )
    return oldest


async def _drain_backward(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    before: datetime | None,
    max_messages: int | None,
    collected: list,
) -> bool:
    """Walk ``history`` backwards page by page; ``True`` if the cap stopped it.

    Each page is requested with ``before=<oldest message of the previous
    page>``, so the cursor strictly decreases. ``after`` is deliberately
    **never** passed to the same ``history()`` call as ``before`` (qodo
    #3998468666): discord.py's ``Messageable.history`` defaults
    ``oldest_first`` to ``after is not None``, and that default picks which
    pagination *strategy* runs — an ``after``-anchored walk vs a
    ``before``-anchored one — not merely the order messages come back in. If
    both cursors were sent together, discord.py would silently run the
    ``after``-anchored strategy and keep re-fetching the slice nearest
    ``after`` while ``before`` never gets a turn, so the walk would report a
    short page (and "complete") after caching only the oldest slice of the
    window. The ``after`` floor is instead enforced client-side, page by
    page, in :func:`_drain_backward_page`.

    Three things end the walk: the ``after`` floor being crossed, a short
    page (fewer messages than asked for — the window is exhausted), or the
    *limit* total being reached.
    """
    cursor: Any = before
    previous_id: Any = None
    while True:
        if limit is not None and len(collected) >= limit:
            return False
        page_size = _BACKWARD_PAGE_SIZE
        if limit is not None:
            page_size = min(page_size, limit - len(collected))
        page, capped, floor_hit = await _drain_backward_page(
            channel,
            after=after,
            cursor=cursor,
            page_size=page_size,
            max_messages=max_messages,
            collected=collected,
        )
        if capped:
            return True
        if floor_hit or len(page) < page_size:
            return False  # the after floor was crossed, or the window is exhausted
        cursor = _next_backward_cursor(page, previous_id)
        previous_id = cursor.id


async def _drain(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    before: datetime | None = None,
    backward: bool = False,
    max_messages: int | None,
    collected: list,
) -> bool:
    """Drain ``history`` into *collected*; return ``True`` if the cap stopped it.

    With *backward* set the read walks older, from *before* (or from the
    newest message when *before* is ``None``) towards *after*; otherwise it is
    the original forward drain, whose call shape is unchanged.
    """
    if backward:
        return await _drain_backward(
            channel,
            limit=limit,
            after=after,
            before=before,
            max_messages=max_messages,
            collected=collected,
        )
    async for message in _history(channel, limit=limit, after=after, before=before):
        collected.append(message)
        if max_messages is not None and len(collected) > max_messages:
            # Overshoot by one, then drop it: hitting the cap exactly means the
            # window WAS fully read, and reporting that as 'partial' would
            # understate real coverage. Only a message beyond the cap proves
            # truncation.
            collected.pop()
            return True
    return False


def _retry_is_resumable(*, backward: bool, after: datetime | None, collected: list) -> bool:
    """Whether a failed read can retry mid-stream rather than start over.

    A backward walk always can (its cursor is the oldest message read so
    far). An unwindowed forward one cannot — it would re-read from the top
    and duplicate what is already collected.
    """
    return backward or after is not None or not collected


def _retry_cursors(
    *,
    backward: bool,
    collected: list,
    after: datetime | None,
    before: datetime | None,
) -> tuple[datetime | None, Any]:
    """The ``(after, before)`` cursor pair to resume paging with after a retry."""
    if backward:
        return after, _resume_cursor(collected, before, backward=True)
    return _resume_cursor(collected, after), before


async def _collect_history(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    before: datetime | None = None,
    backward: bool = False,
    max_messages: int | None,
) -> tuple[list, bool, str | None]:
    """Read a channel's history, paging the window in either direction.

    Returns ``(messages, complete, reason)``. *complete* is ``False`` when the
    requested window could not be fully paged — the cap was hit, or a read
    failed part-way — so a truncated window is always reported, never silent.
    Raises when nothing at all could be read (the caller records ``failed``).
    """
    collected: list = []
    after_cursor = after
    before_cursor = before
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            capped = await _drain(
                channel,
                limit=limit,
                after=after_cursor,
                before=before_cursor,
                backward=backward,
                max_messages=max_messages,
                collected=collected,
            )
        except Exception as exc:  # noqa: BLE001
            delay = _retry_after(exc)
            resumable = _retry_is_resumable(backward=backward, after=after, collected=collected)
            if delay is None or not resumable or attempt >= _RATE_LIMIT_RETRIES:
                if collected:
                    return collected, False, f"read failed after {len(collected)} messages: {exc}"
                raise
            await _sleep(delay)
            after_cursor, before_cursor = _retry_cursors(
                backward=backward, collected=collected, after=after, before=before
            )
            continue
        if capped:
            return (
                collected,
                False,
                f"message cap reached ({max_messages}); window not fully paged",
            )
        return collected, True, None
    return collected, False, "rate limited: retries exhausted"  # pragma: no cover


def _ordered(messages: list) -> list:
    """Sort messages oldest-first regardless of the order history yielded them."""
    stamped = [m for m in messages if getattr(m, "created_at", None)]
    unstamped = [m for m in messages if not getattr(m, "created_at", None)]
    stamped.sort(key=lambda m: m.created_at)
    return stamped + unstamped


def read_messages(channel_id: int, limit: int = 20) -> dict:
    """Read a channel's most recent *limit* messages, oldest first.

    *limit* has no upper bound: it is jlab's own bound, not a Discord one
    (Discord clamps a single request to 100), so this pages backward through
    :func:`_collect_history` past that cap exactly like :func:`scan_window`
    already does. For a caller passing the default ``limit=20`` (or any
    value <= 100) this issues the same single ``channel.history(limit=...)``
    call as before, so the messages returned — and the text-mode lines —
    are unchanged. The ``--json`` payload gained a ``complete`` field, an
    additive change: JSON consumers see one extra key.

    Returns ``{"messages": [...], "complete": bool, "reason": str | None}``.
    ``complete`` is ``False`` when a rate limit or a hard failure kept the
    requested window from being fully read — the caller must report that
    rather than silently returning a truncated result.
    """
    if limit < 1:
        raise CliError(
            code=1,
            message=f"--limit must be >= 1, got {limit}",
            remediation="pass a positive integer",
        )

    async def action(client: Any) -> dict:
        channel = await client.fetch_channel(channel_id)
        collected, complete, reason = await _collect_history(
            channel,
            limit=limit,
            after=None,
            before=None,
            backward=True,
            max_messages=None,
        )
        collected.reverse()  # history yields newest-first; emit oldest-first
        return {
            "messages": [_serialize_message(m, channel) for m in collected],
            "complete": complete,
            "reason": reason,
        }

    return _run(action)


def _rank_channel(chan: dict, cutoff: datetime, fetch_limit: int, preview: int) -> dict | None:
    """Build a ranked row for *chan*, or ``None`` if it is inactive in the window."""
    stamped = [
        (m, datetime.fromisoformat(m["created_at"])) for m in chan["messages"] if m["created_at"]
    ]
    if not stamped:
        return None
    newest = max(t for _, t in stamped)
    if newest < cutoff:
        return None
    in_window = [m for m, t in stamped if t >= cutoff]
    return {
        "id": chan["id"],
        "name": chan["name"],
        "last_post": newest.isoformat(),
        "msgs_in_window": len(in_window),
        "saturated": len(in_window) == fetch_limit,
        "preview": [
            {
                "author": m["author"]["display_name"],
                "content": m["content"],
                "created_at": m["created_at"],
            }
            for m in chan["messages"][-preview:]
        ],
    }


def _channel_row(
    channel: Any,
    messages: list[dict],
    status: str,
    reason: str | None,
    complete: bool,
) -> dict:
    """Build a probed-channel row carrying its own read status."""
    return {
        "id": str(channel.id),
        "name": channel.name,
        "messages": messages,
        "message_count": len(messages),
        "status": status,
        "reason": reason,
        "complete": complete,
    }


async def _probe_channel(
    channel: Any,
    fetch_limit: int | None = None,
    *,
    after: datetime | None = None,
    max_messages: int | None = None,
    exclude_bots: bool = False,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Serialize a channel's messages to plain dicts inside the session.

    A per-channel failure no longer disappears into an empty message list: the
    row carries an explicit ``status``:

    - ``ok`` — the requested window was read in full (an *empty* channel is
      ``ok`` with ``message_count == 0``);
    - ``partial`` — some messages were read but the window is truncated
      (message cap hit, or a read failed part-way); ``reason`` says why;
    - ``failed`` — nothing could be read at all; ``reason`` carries the error.

    A failure is therefore always distinguishable from an empty channel, and it
    never aborts the whole scan. *semaphore*, when given, bounds how many
    channels are read concurrently.
    """
    guard: Any = semaphore if semaphore is not None else nullcontext()
    async with guard:
        try:
            raw, complete, reason = await _collect_history(
                channel,
                limit=fetch_limit,
                after=after,
                max_messages=max_messages,
            )
        except Exception as exc:  # noqa: BLE001
            return _channel_row(channel, [], STATUS_FAILED, f"read failed: {exc}", False)

    messages = [_serialize_message(m, channel) for m in _ordered(raw)]
    if exclude_bots:
        messages = [m for m in messages if not m["author"]["bot"]]
    status = STATUS_OK if complete else STATUS_PARTIAL
    return _channel_row(channel, messages, status, reason, complete)


def _bounded_concurrency(value: int) -> int:
    """Validate a fan-out cap, raising a user error (exit 1) when unusable."""
    try:
        bound = int(value)
    except (TypeError, ValueError):
        bound = 0
    if bound < 1:
        raise CliError(
            code=1,
            message=f"--concurrency must be >= 1, got {value}",
            remediation=f"pass a positive integer (default {DEFAULT_CONCURRENCY})",
        )
    return bound


async def _public_text_channels(client: Any, guild_id: int) -> list[Any]:
    """Public text channels only — the filter runs BEFORE any message fetch.

    Private / role-gated channels are dropped here, so no code path ever calls
    ``history()`` on one and their contents cannot leak into a scan result.
    """
    guild = await client.fetch_guild(guild_id)
    everyone = guild.default_role
    return [
        c
        for c in await guild.fetch_channels()
        if getattr(c.type, "name", c.type) == "text" and _channel_public(c, everyone) is True
    ]


def active_scan(
    guild_id: int,
    since_days: int = 30,
    fetch_limit: int = 30,
    top: int = 0,
    preview: int = 5,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict:
    """Shallow scan: rank active public text channels by recent traffic.

    Uses a **single** ``discord_client.run`` session; per-channel history reads
    fan out through ``asyncio.gather`` but are bounded by an
    ``asyncio.Semaphore`` (*concurrency*, default :data:`DEFAULT_CONCURRENCY`),
    so the ~104-channel live guild never puts an unbounded number of requests in
    flight. Discord objects are serialized inside the session and never escape
    the closing client.

    The returned shape is unchanged: this verb stays a shallow, ``fetch_limit``
    probe. Per-channel status now exists on the probed rows (and bots are still
    counted here — see :func:`scan_window` for the windowed, bot-free scan).
    """
    bound = _bounded_concurrency(concurrency)

    async def action(client: Any) -> list[dict]:
        text_channels = await _public_text_channels(client, guild_id)
        semaphore = asyncio.Semaphore(bound)
        return list(
            await asyncio.gather(
                *[_probe_channel(c, fetch_limit, semaphore=semaphore) for c in text_channels]
            )
        )

    probed = _run(action)
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    rows = [
        row
        for chan in probed
        if (row := _rank_channel(chan, cutoff, fetch_limit, preview)) is not None
    ]
    rows.sort(key=lambda r: (r["msgs_in_window"], r["last_post"]), reverse=True)
    if top > 0:
        rows = rows[:top]

    return {
        "guild_id": str(guild_id),
        "since_days": since_days,
        "fetch_limit": fetch_limit,
        "probed_text_channels": len(probed),
        "active_channels": len(rows),
        "channels": rows,
    }


def scan_window(
    guild_id: int,
    since_days: int = DEFAULT_WINDOW_DAYS,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_messages_per_channel: int | None = DEFAULT_MAX_MESSAGES_PER_CHANNEL,
    exclude_bots: bool = True,
) -> dict:
    """Read every public text channel's **full** message window.

    Unlike :func:`active_scan` (a shallow ``fetch_limit`` probe), this pages
    ``history(limit=None, after=<cutoff>)`` past the upstream 100-message cap,
    so the window is covered rather than sampled.

    Coverage is never overstated. Every channel carries a ``status`` of ``ok``
    / ``partial`` / ``failed`` plus a ``reason``; the top-level ``complete``
    is ``True`` only when every channel came back ``ok``. Bots are excluded via
    the authoritative ``author.bot`` flag, never a name heuristic.

    Private / role-gated channels are filtered out *before* any message fetch.
    """
    if since_days < 1:
        raise CliError(
            code=1,
            message=f"--since must be >= 1 day, got {since_days}",
            remediation=f"pass a positive number of days (default {DEFAULT_WINDOW_DAYS})",
        )
    bound = _bounded_concurrency(concurrency)
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    async def action(client: Any) -> list[dict]:
        text_channels = await _public_text_channels(client, guild_id)
        semaphore = asyncio.Semaphore(bound)
        return list(
            await asyncio.gather(
                *[
                    _probe_channel(
                        c,
                        None,
                        after=cutoff,
                        max_messages=max_messages_per_channel,
                        exclude_bots=exclude_bots,
                        semaphore=semaphore,
                    )
                    for c in text_channels
                ]
            )
        )

    channels = _run(action)
    counts = {STATUS_OK: 0, STATUS_PARTIAL: 0, STATUS_FAILED: 0}
    for chan in channels:
        counts[chan["status"]] = counts.get(chan["status"], 0) + 1

    return {
        "guild_id": str(guild_id),
        "since_days": since_days,
        "cutoff": cutoff.isoformat(),
        "concurrency": bound,
        "max_messages_per_channel": max_messages_per_channel,
        "exclude_bots": exclude_bots,
        "scanned_text_channels": len(channels),
        "channels_ok": counts[STATUS_OK],
        "channels_partial": counts[STATUS_PARTIAL],
        "channels_failed": counts[STATUS_FAILED],
        "message_count": sum(c["message_count"] for c in channels),
        "complete": all(c["status"] == STATUS_OK for c in channels),
        "channels": channels,
    }


def doctor(guild_id: int) -> dict:
    """Verify token + importable + guild readable, and the jlab-mongodb cache.

    Raises :class:`CliError` on failure — from the existing token/extra/guild
    checks, or from :func:`jlab.mongo.check_cache` when the paged-read cache
    (jlab-mongodb) is absent, unreachable, or turns out not to be jlab's own
    dedicated instance. Either failure exits code 2 with an actionable
    ``hint:``; this function never returns a partial/silent result on
    failure.
    """
    list_channels(guild_id)
    cache = _mongo.check_cache()
    encryption = _cache.measure_encryption()
    return {
        "ok": True,
        "guild_id": str(guild_id),
        "cache": cache,
        "encryption": encryption,
    }

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
``author.bot`` flag, the display name, and ``after=``/time-window paging past
the upstream 100-message cap are implemented **here**, against the raw
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


def _serialize_message(message: Any) -> dict:
    """Serialize a discord.py ``Message`` to a plain dict (no objects escape)."""
    message_id = getattr(message, "id", None)
    created = getattr(message, "created_at", None)
    return {
        "id": str(message_id) if message_id is not None else None,
        "author": _serialize_author(message.author),
        "content": message.content,
        "created_at": created.isoformat() if created else None,
    }


# ---------------------------------------------------------------------------
# WORKAROUND(discord-bot-cli#14) — after=/time-window paging past the 100 cap.
# ---------------------------------------------------------------------------


def _history(channel: Any, *, limit: int | None, after: datetime | None) -> Any:
    """Call ``channel.history``, passing ``after`` only when a window is set.

    discord.py paginates ``history(limit=None, after=<datetime>)`` for us, so
    this is the whole of the "past the 100-message cap" story.
    """
    if after is None:
        return channel.history(limit=limit)
    return channel.history(limit=limit, after=after)


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


def _resume_cursor(collected: list, after: datetime | None) -> datetime | None:
    """Where to resume paging after a retry: the newest message read so far."""
    stamps = [m.created_at for m in collected if getattr(m, "created_at", None)]
    return max(stamps) if stamps else after


async def _drain(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    max_messages: int | None,
    collected: list,
) -> bool:
    """Drain ``history`` into *collected*; return ``True`` if the cap stopped it."""
    async for message in _history(channel, limit=limit, after=after):
        collected.append(message)
        if max_messages is not None and len(collected) > max_messages:
            # Overshoot by one, then drop it: hitting the cap exactly means the
            # window WAS fully read, and reporting that as 'partial' would
            # understate real coverage. Only a message beyond the cap proves
            # truncation.
            collected.pop()
            return True
    return False


async def _collect_history(
    channel: Any,
    *,
    limit: int | None,
    after: datetime | None,
    max_messages: int | None,
) -> tuple[list, bool, str | None]:
    """Read a channel's history, paging the window.

    Returns ``(messages, complete, reason)``. *complete* is ``False`` when the
    requested window could not be fully paged — the cap was hit, or a read
    failed part-way — so a truncated window is always reported, never silent.
    Raises when nothing at all could be read (the caller records ``failed``).
    """
    collected: list = []
    cursor = after
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            capped = await _drain(
                channel,
                limit=limit,
                after=cursor,
                max_messages=max_messages,
                collected=collected,
            )
        except Exception as exc:  # noqa: BLE001
            delay = _retry_after(exc)
            # Only a windowed read can resume mid-stream; an unwindowed one
            # would re-read from the top and duplicate.
            resumable = after is not None or not collected
            if delay is not None and resumable and attempt < _RATE_LIMIT_RETRIES:
                await _sleep(delay)
                cursor = _resume_cursor(collected, after)
                continue
            if collected:
                return collected, False, f"read failed after {len(collected)} messages: {exc}"
            raise
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


def read_messages(channel_id: int, limit: int = 20) -> list[dict]:
    """Read recent messages from a channel.

    *limit* must be between 1 and 100 inclusive.
    """
    if not 1 <= limit <= 100:
        raise CliError(
            code=1,
            message=f"--limit must be 1-100, got {limit}",
            remediation="pass a value between 1 and 100",
        )

    async def action(client: Any) -> list[dict]:
        channel = await client.fetch_channel(channel_id)
        collected = [m async for m in channel.history(limit=limit)]
        collected.reverse()  # history yields newest-first; emit oldest-first
        return [_serialize_message(m) for m in collected]

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

    messages = [_serialize_message(m) for m in _ordered(raw)]
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
    """Verify token + importable + guild readable.

    Raises :class:`CliError` on failure.
    """
    list_channels(guild_id)
    return {"ok": True, "guild_id": str(guild_id)}

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
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jlab.cli._errors import CliError

_GUILD_ID_DEFAULT = "1326246312072581160"


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
        from discord_bot_cli.cli._errors import CliError as _DBCliError  # noqa: F811
    except ImportError:
        _DBCliError = ()  # type: ignore[assignment]
    if _DBCliError and isinstance(exc, _DBCliError):
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
    except Exception as exc:  # noqa: BLE001 - translate to the jlab error contract
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
    except Exception:  # noqa: BLE001 - unknown perms => report, don't crash
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
        return [
            {
                "id": str(m.id),
                "author": {"id": str(m.author.id), "name": m.author.name},
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in collected
        ]

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
            {"author": m["author"], "content": m["content"], "created_at": m["created_at"]}
            for m in chan["messages"][-preview:]
        ],
    }


async def _probe_channel(channel: Any, fetch_limit: int) -> dict:
    """Serialize a channel's recent messages to plain dicts inside the session.

    A per-channel failure (permissions, transient API error) yields an empty
    message list rather than aborting the whole scan — matching the tolerant
    behaviour of the original ``scan.sh``.
    """
    try:
        msgs = [m async for m in channel.history(limit=fetch_limit)]
        msgs.reverse()
    except Exception:  # noqa: BLE001 - one bad channel must not abort the scan
        msgs = []
    return {
        "id": str(channel.id),
        "name": channel.name,
        "messages": [
            {
                "author": m.author.name,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }


def active_scan(
    guild_id: int,
    since_days: int = 30,
    fetch_limit: int = 30,
    top: int = 0,
    preview: int = 5,
) -> dict:
    """Shallow scan: rank active public text channels by recent traffic.

    Uses a **single** ``discord_client.run`` session with ``asyncio.gather``
    for per-channel history reads (each tolerant of its own failure), then ranks
    in-process. Discord objects are serialized inside the session and never
    escape the closing client.
    """

    async def action(client: Any) -> list[dict]:
        guild = await client.fetch_guild(guild_id)
        everyone = guild.default_role
        text_channels = [
            c
            for c in await guild.fetch_channels()
            if getattr(c.type, "name", c.type) == "text" and _channel_public(c, everyone) is True
        ]
        return list(await asyncio.gather(*[_probe_channel(c, fetch_limit) for c in text_channels]))

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


def doctor(guild_id: int) -> dict:
    """Verify token + importable + guild readable.

    Raises :class:`CliError` on failure.
    """
    list_channels(guild_id)
    return {"ok": True, "guild_id": str(guild_id)}

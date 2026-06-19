"""Discord adapter — isolates ALL coupling to discord-bot-cli.

Every public function here is the *only* place that touches
``discord_bot_cli``. The command modules import this module, not the
upstream package, so an upstream API change touches one file.

Lazy-import: ``discord_bot_cli`` is **never** imported at module scope.
The private ``_seam()`` does the import inside a try/except and raises
a structured :class:`jlab.cli._errors.CliError` (code 2) when the
optional ``[discord]`` extra is absent.
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


def parse_id(value: str, label: str) -> int:
    """Parse a numeric id string, translating errors to :class:`CliError`."""
    dc = _seam()
    try:
        return dc.parse_id(value, label)
    except Exception as e:  # noqa: BLE001
        # Translate discord_bot_cli CliError into jlab's.
        try:
            from discord_bot_cli.cli._errors import CliError as _DBCliError  # noqa: F811
        except ImportError:
            raise CliError(
                code=1,
                message=f"invalid {label}: {value!r}",
                remediation="pass a numeric id",
            ) from e
        if isinstance(e, _DBCliError):
            raise CliError(
                code=e.code,
                message=e.message,
                remediation=e.remediation,
            ) from e
        raise CliError(
            code=1,
            message=f"invalid {label}: {value!r}",
            remediation="pass a numeric id",
        ) from e


def list_channels(guild_id: int, public_only: bool = True) -> list[dict]:
    """List a guild's channels with a ``public`` flag.

    When *public_only* is ``True`` (the default), drop entries whose
    ``public`` field is not ``True``.
    """
    dc = _seam()

    async def action(client: Any) -> list[dict]:
        guild = await client.fetch_guild(guild_id)
        everyone = guild.default_role
        out: list[dict] = []
        for c in await guild.fetch_channels():
            try:
                public = bool(c.permissions_for(everyone).view_channel)
            except Exception:  # noqa: BLE001
                public = None
            out.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "type": str(getattr(c.type, "name", c.type)),
                    "public": public,
                }
            )
        return out

    channels = dc.run(action)
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

    dc = _seam()

    async def action(client: Any) -> list[dict]:
        channel = await client.fetch_channel(channel_id)
        collected = [m async for m in channel.history(limit=limit)]
        collected.reverse()
        return [
            {
                "id": str(m.id),
                "author": {"id": str(m.author.id), "name": m.author.name},
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in collected
        ]

    return dc.run(action)


def active_scan(
    guild_id: int,
    since_days: int = 30,
    fetch_limit: int = 30,
    top: int = 0,
    preview: int = 5,
) -> dict:
    """Shallow scan: rank active public text channels by recent traffic.

    Uses a **single** ``discord_client.run(action)`` session with
    ``asyncio.gather`` for per-channel history reads, then ranks in-process.
    """
    dc = _seam()

    async def action(client: Any) -> list[dict]:
        guild = await client.fetch_guild(guild_id)
        everyone = guild.default_role

        # Collect public text channels.
        text_channels: list[Any] = []
        for c in await guild.fetch_channels():
            ctype = getattr(c.type, "name", c.type)
            if ctype != "text":
                continue
            try:
                public = bool(c.permissions_for(everyone).view_channel)
            except Exception:  # noqa: BLE001
                public = None
            if public is not True:
                continue
            text_channels.append(c)

        # Fetch history for every public text channel in parallel, and
        # serialize to plain dicts *inside* the session — Discord objects must
        # not escape the (about-to-close) client.
        async def _fetch(ch: Any) -> dict:
            msgs = [m async for m in ch.history(limit=fetch_limit)]
            msgs.reverse()
            return {
                "id": str(ch.id),
                "name": ch.name,
                "messages": [
                    {
                        "author": m.author.name,
                        "content": m.content,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in msgs
                ],
            }

        return list(await asyncio.gather(*[_fetch(ch) for ch in text_channels]))

    probed = dc.run(action)

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    rows: list[dict] = []
    for chan in probed:
        # Only messages carrying a timestamp can be ranked by recency.
        stamped = [
            (m, datetime.fromisoformat(m["created_at"]))
            for m in chan["messages"]
            if m["created_at"]
        ]
        if not stamped:
            continue
        newest = max(t for _, t in stamped)
        if newest < cutoff:
            continue
        in_window = [m for m, t in stamped if t >= cutoff]
        rows.append(
            {
                "id": chan["id"],
                "name": chan["name"],
                "last_post": newest.isoformat(),
                "msgs_in_window": len(in_window),
                "saturated": len(in_window) == fetch_limit,
                "preview": [
                    {
                        "author": m["author"],
                        "content": m["content"],
                        "created_at": m["created_at"],
                    }
                    for m in chan["messages"][-preview:]
                ],
            }
        )

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

#!/usr/bin/env python3
"""List a guild's channels *with a `public` flag* — read-only.

The stock ``discord-bot-cli channel list`` emits only ``id``/``name``/``type``,
which can't tell a public channel from a private (role-gated) one. This helper
reuses discord-bot-cli's own transport seam — ``discord_client.run(action)``,
the documented extension point — so it inherits the tool's token handling,
one-shot REST session, and structured-error contract without duplicating any of
it or modifying the tool.

A channel is **public** iff the guild's ``@everyone`` role can view it
(``permissions_for(default_role).view_channel``). ``--public-only`` drops
everything else so a private channel's id/name never leaves the process — the
guard for "share the channel list in the public repo, public channels only".

Output: ``{"guild_id", "channels": [{"id","name","type","public"}, ...]}`` on
stdout as JSON. Errors route to stderr as ``{code,message,remediation}`` with the
tool's exit code (0 ok, 1 user error, 2 environment error). Never writes to
Discord.

Run it inside the checkout whose venv carries the ``[discord]`` extra, e.g.::

    uv run --project ~/git/discord-bot-cli python channels.py --public-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from discord_bot_cli import discord_client
from discord_bot_cli.cli._errors import CliError

_DEFAULT_GUILD = "1326246312072581160"  # Jetson AI Lab Research Group


def _list(guild_id: int) -> list[dict[str, object]]:
    async def action(client: object) -> list[dict[str, object]]:
        guild = await client.fetch_guild(guild_id)
        everyone = guild.default_role
        out: list[dict[str, object]] = []
        for c in await guild.fetch_channels():
            try:
                public: object = bool(c.permissions_for(everyone).view_channel)
            except Exception:  # noqa: BLE001 - unknown perms => report, don't crash
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

    return discord_client.run(action)


def main() -> int:
    ap = argparse.ArgumentParser(description="List a guild's channels with a public flag.")
    ap.add_argument(
        "--guild-id",
        default=os.environ.get("JLAB_GUILD_ID", _DEFAULT_GUILD),
        help="Numeric guild id (default: Jetson AI Lab, or $JLAB_GUILD_ID).",
    )
    ap.add_argument(
        "--public-only",
        action="store_true",
        help="Drop non-public channels entirely (private ids/names never emitted).",
    )
    args = ap.parse_args()

    try:
        channels = _list(int(args.guild_id))
    except CliError as err:
        json.dump(err.to_dict(), sys.stderr, ensure_ascii=False)
        sys.stderr.write("\n")
        return err.code

    if args.public_only:
        channels = [c for c in channels if c["public"] is True]

    json.dump(
        {"guild_id": str(args.guild_id), "channels": channels},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

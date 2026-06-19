"""``jetson-ai-lab-cli discord`` — read-only Discord noun group.

Verbs: channels, read, active, doctor, overview.

Read-only only (no post/react/thread). Public channels only by default
(--all is the sole private opt-in).
"""

from __future__ import annotations

import argparse

from jlab.cli import _discord
from jlab.cli._output import emit_diagnostic, emit_result


def _no_verb(args: argparse.Namespace) -> int:
    """Print the noun's overview when no sub-verb is given."""
    return cmd_discord_overview(args)


# -- channels ----------------------------------------------------------------


def cmd_discord_channels(args: argparse.Namespace) -> int:
    guild_id = _discord._guild_id()
    public_only = not bool(getattr(args, "all", False))
    channels = _discord.list_channels(guild_id, public_only=public_only)
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(
            {"guild_id": str(guild_id), "channels": channels},
            json_mode=True,
        )
    else:
        for ch in channels:
            emit_result(
                f"{ch['id']}  {ch['name']}  ({ch['type']})",
                json_mode=False,
            )
    return 0


# -- read -------------------------------------------------------------------


def cmd_discord_read(args: argparse.Namespace) -> int:
    channel_id = _discord.parse_id(args.channel_id, "channel_id")
    limit = int(getattr(args, "limit", 20))
    messages = _discord.read_messages(channel_id, limit=limit)
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(
            {"channel_id": str(channel_id), "messages": messages},
            json_mode=True,
        )
    else:
        for msg in messages:
            author = msg["author"]["name"]
            ts = msg["created_at"] or "?"
            emit_result(
                f"[{ts}] {author}: {msg['content']}",
                json_mode=False,
            )
    return 0


# -- active -----------------------------------------------------------------


def cmd_discord_active(args: argparse.Namespace) -> int:
    guild_id = _discord._guild_id()
    since_days = int(getattr(args, "since", 30))
    fetch_limit = int(getattr(args, "limit", 30))
    top = int(getattr(args, "top", 0))
    preview = int(getattr(args, "preview", 5))
    json_mode = bool(getattr(args, "json", False))

    emit_diagnostic(f"probing public text channels (limit {fetch_limit}) ...")

    result = _discord.active_scan(
        guild_id,
        since_days=since_days,
        fetch_limit=fetch_limit,
        top=top,
        preview=preview,
    )

    if json_mode:
        emit_result(result, json_mode=True)
    else:
        lines = [
            f"# Active channels (last {since_days} days)",
            f"Guild: {result['guild_id']}",
            f"Probed: {result['probed_text_channels']} text channels",
            f"Active: {result['active_channels']}",
            "",
        ]
        for ch in result["channels"]:
            lines.append(
                f"- {ch['name']} ({ch['id']}): "
                f"{ch['msgs_in_window']} msgs, "
                f"last {ch['last_post']}"
            )
        emit_result("\n".join(lines), json_mode=False)
    return 0


# -- doctor -----------------------------------------------------------------


def cmd_discord_doctor(args: argparse.Namespace) -> int:
    guild_id = _discord._guild_id()
    result = _discord.doctor(guild_id)
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(result, json_mode=True)
    else:
        emit_result(f"ok: guild {result['guild_id']}", json_mode=False)
    return 0


# -- overview ----------------------------------------------------------------


def cmd_discord_overview(args: argparse.Namespace) -> int:
    from jlab.cli._commands.overview import emit_overview

    sections = [
        {
            "title": "Verbs",
            "items": [
                "channels [--all] — list guild channels (public-only by default)",
                "read <channel_id> [--limit N] — read recent messages from a channel",
                "active [--since D] [--limit N] [--top K] [--preview P] — rank active channels",
                "doctor — verify token + guild readable",
                "overview — describe this noun group",
            ],
        },
        {
            "title": "Conventions",
            "items": [
                "read-only only (no post/react/thread)",
                "public channels only by default (--all is the sole private opt-in)",
                "every command supports --json",
                "results to stdout, diagnostics/errors to stderr",
            ],
        },
    ]
    json_mode = bool(getattr(args, "json", False))
    emit_overview("jetson-ai-lab-cli discord", sections, json_mode=json_mode)
    return 0


# -- register ----------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "discord",
        help="Read-only Discord scan (channels, read, active, doctor).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="discord_command", parser_class=type(p))

    # channels
    ch = noun_sub.add_parser(
        "channels",
        help="List guild channels (public-only by default).",
    )
    ch.add_argument(
        "--all",
        action="store_true",
        help="Include private/role-gated channels too.",
    )
    ch.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ch.set_defaults(func=cmd_discord_channels, json=False)

    # read
    rd = noun_sub.add_parser(
        "read",
        help="Read recent messages from a channel.",
    )
    rd.add_argument("channel_id", help="Numeric channel id.")
    rd.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Messages to fetch (1-100, default 20).",
    )
    rd.add_argument("--json", action="store_true", help="Emit structured JSON.")
    rd.set_defaults(func=cmd_discord_read, json=False)

    # active
    ac = noun_sub.add_parser(
        "active",
        help="Rank active public text channels by recent traffic.",
    )
    ac.add_argument(
        "--since",
        type=int,
        default=30,
        help="Days to look back (default 30).",
    )
    ac.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Messages fetched per channel (1-100, default 30).",
    )
    ac.add_argument(
        "--top",
        type=int,
        default=0,
        help="Keep only the K most active channels (0 = all).",
    )
    ac.add_argument(
        "--preview",
        type=int,
        default=5,
        help="Messages echoed per active channel (default 5).",
    )
    ac.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ac.set_defaults(func=cmd_discord_active, json=False)

    # doctor
    dr = noun_sub.add_parser(
        "doctor",
        help="Verify token + guild readable.",
    )
    dr.add_argument("--json", action="store_true", help="Emit structured JSON.")
    dr.set_defaults(func=cmd_discord_doctor, json=False)

    # overview
    ov = noun_sub.add_parser(
        "overview",
        help="Describe the discord noun group.",
    )
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_discord_overview, json=False)

"""``jetson-ai-lab-cli discord coverage`` — inspect cache coverage metadata."""

from __future__ import annotations

import argparse
import datetime as dt

from jlab import coverage as _coverage
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.cli._output import emit_result

_JSON_HELP = "Emit structured JSON."
UTC = dt.timezone.utc


def cmd_discord_coverage(args: argparse.Namespace) -> int:
    """Inspect what windows the cache holds for a channel.

    With no channel_id, lists channels that have coverage records.
    With a channel_id and no --since/--until, shows what intervals are cached.
    With --since/--until, shows covered and uncovered spans within that window,
    and whether the window is complete.
    """
    json_mode = bool(getattr(args, "json", False))
    channel_id = getattr(args, "channel_id", None)

    # Validate channel ID early if provided
    if channel_id is not None and not _coverage.is_valid_channel_id(channel_id):
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid channel id: {channel_id!r}",
            "pass a numeric Discord channel id",
        )

    # If no channel specified, list channels with coverage
    if channel_id is None:
        channels = _coverage.covered_channels()
        if json_mode:
            emit_result({"channels": channels}, json_mode=True)
        else:
            for ch in channels:
                emit_result(ch, json_mode=False)
        return 0

    # Parse optional time bounds
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    window = None
    if since is not None and until is not None:
        try:
            start = dt.datetime.fromisoformat(since)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            end = dt.datetime.fromisoformat(until)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            window = _coverage.Interval(start, end)
        except (ValueError, TypeError) as err:
            raise CliError(
                EXIT_USER_ERROR,
                f"invalid timestamp: {err}",
                "pass ISO-8601 timestamps like 2026-09-01T00:00:00+00:00",
            )

    # Query coverage
    result = _coverage.describe(channel_id, window)

    if json_mode:
        emit_result(result, json_mode=True)
    else:
        _emit_coverage_text(result)
    return 0


def _emit_coverage_text(result: dict) -> None:
    """Format coverage result for text output."""
    channel_id = result["channel_id"]
    window = result.get("window")
    covered = result.get("covered") or []
    uncovered = result.get("uncovered") or []
    complete = result.get("complete")

    lines = [f"Channel: {channel_id}"]

    if window is None:
        lines.append("Coverage (no window specified):")
        if not covered:
            lines.append("  (no coverage recorded)")
        else:
            for interval in covered:
                start = interval["start"]
                end = interval["end"]
                lines.append(f"  {start} .. {end}")
        lines.append("Note: completeness requires a time window (--since/--until)")
    else:
        start = window["start"]
        end = window["end"]
        lines.append(f"Window: {start} .. {end}")
        lines.append("Covered:")
        if not covered:
            lines.append("  (none)")
        else:
            for interval in covered:
                i_start = interval["start"]
                i_end = interval["end"]
                lines.append(f"  {i_start} .. {i_end}")
        lines.append("Uncovered:")
        if not uncovered:
            lines.append("  (none)")
        else:
            for interval in uncovered:
                i_start = interval["start"]
                i_end = interval["end"]
                lines.append(f"  {i_start} .. {i_end}")
        status = "complete" if complete else "incomplete"
        lines.append(f"Status: {status}")

    emit_result("\n".join(lines), json_mode=False)


def register(sub: argparse._SubParsersAction) -> None:
    """Register the coverage verb under discord."""
    # The parser is passed as `sub` and already has access to the parent
    # This is called from discord.py's register() function
    cv = sub.add_parser(
        "coverage",
        help="Inspect cache coverage metadata for a channel.",
    )
    cv.add_argument(
        "channel_id",
        nargs="?",
        default=None,
        help="Numeric channel id (omit to list all channels with coverage).",
    )
    cv.add_argument(
        "--since",
        type=str,
        default=None,
        help="Start of time window (ISO-8601 timestamp).",
    )
    cv.add_argument(
        "--until",
        type=str,
        default=None,
        help="End of time window (ISO-8601 timestamp).",
    )
    cv.add_argument("--json", action="store_true", help=_JSON_HELP)
    cv.set_defaults(func=cmd_discord_coverage, json=False)

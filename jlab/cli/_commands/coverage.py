"""``jetson-ai-lab-cli discord coverage`` — inspect cache coverage metadata."""

from __future__ import annotations

import argparse
import datetime as dt

from jlab import coverage as _coverage
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.cli._output import emit_result

_JSON_HELP = "Emit structured JSON."
UTC = dt.timezone.utc


def _validate_channel_id(channel_id: str | None) -> None:
    if channel_id is not None and not _coverage.is_valid_channel_id(channel_id):
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid channel id: {channel_id!r}",
            "pass a numeric Discord channel id",
        )


def _require_paired_bounds(since: str | None, until: str | None) -> None:
    if (since is None) != (until is None):
        raise CliError(
            EXIT_USER_ERROR,
            "--since and --until must be given together",
            "pass both bounds to inspect a window, or neither to list recorded intervals",
        )


def _list_channels(json_mode: bool) -> None:
    channels = _coverage.covered_channels()
    if json_mode:
        emit_result({"channels": channels}, json_mode=True)
    else:
        for ch in channels:
            emit_result(ch, json_mode=False)


def _parse_window(since: str, until: str) -> "_coverage.Interval":
    try:
        start = dt.datetime.fromisoformat(since)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = dt.datetime.fromisoformat(until)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        return _coverage.Interval(start, end)
    except (ValueError, TypeError) as err:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid timestamp: {err}",
            "pass ISO-8601 timestamps like 2026-09-01T00:00:00+00:00",
        ) from err


def cmd_discord_coverage(args: argparse.Namespace) -> int:
    """Inspect what windows the cache holds for a channel.

    With no channel_id, lists channels that have coverage records.
    With a channel_id and no --since/--until, shows what intervals are cached.
    With --since/--until, shows covered and uncovered spans within that window,
    and whether the window is complete.
    """
    json_mode = bool(getattr(args, "json", False))
    channel_id = getattr(args, "channel_id", None)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)

    _validate_channel_id(channel_id)
    _require_paired_bounds(since, until)

    if channel_id is None:
        if since is not None or until is not None:
            raise CliError(
                EXIT_USER_ERROR,
                "--since/--until require a channel_id",
                "pass a channel id to inspect a window, e.g. "
                "discord coverage 123456789012345678 --since ... --until ...",
            )
        _list_channels(json_mode)
        return 0

    window = _parse_window(since, until) if since is not None and until is not None else None

    result = _coverage.describe(channel_id, window)
    if window is None:
        # describe() reports bool(covered) here; with no window that boolean
        # means nothing, so never let recorded intervals read as "complete".
        result["complete"] = None

    if json_mode:
        emit_result(result, json_mode=True)
    else:
        _emit_coverage_text(result)
    return 0


def _format_intervals(intervals: list[dict], empty_label: str) -> list[str]:
    if not intervals:
        return [f"  {empty_label}"]
    return [f"  {interval['start']} .. {interval['end']}" for interval in intervals]


def _emit_coverage_text_no_window(covered: list[dict]) -> list[str]:
    lines = ["Coverage (no window specified):"]
    lines.extend(_format_intervals(covered, "(no coverage recorded)"))
    lines.append("Note: completeness requires a time window (--since/--until)")
    return lines


def _emit_coverage_text_with_window(result: dict) -> list[str]:
    window = result["window"]
    covered = result.get("covered") or []
    uncovered = result.get("uncovered") or []
    lines = [f"Window: {window['start']} .. {window['end']}"]
    lines.append("Covered:")
    lines.extend(_format_intervals(covered, "(none)"))
    lines.append("Uncovered:")
    lines.extend(_format_intervals(uncovered, "(none)"))
    status = "complete" if result.get("complete") else "incomplete"
    lines.append(f"Status: {status}")
    return lines


def _emit_coverage_text(result: dict) -> None:
    """Format coverage result for text output."""
    lines = [f"Channel: {result['channel_id']}"]
    if result.get("window") is None:
        lines.extend(_emit_coverage_text_no_window(result.get("covered") or []))
    else:
        lines.extend(_emit_coverage_text_with_window(result))
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

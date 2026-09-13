"""``jetson-ai-lab-cli discord search`` — regex search over the cached corpus.

Cache-served only: this verb never opens a Discord session (see
``jlab.search``'s module docstring). It points a caller at ``discord fetch``
whenever the requested window is not fully covered, rather than silently
answering from whatever happens to be cached.
"""

from __future__ import annotations

import argparse

from jlab import search as _search
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from jlab.cli._output import emit_diagnostic, emit_result
from jlab.fetch import parse_until as _parse_iso_bound

_JSON_HELP = "Emit structured JSON."


def _parse_bound(value: str | None, flag: str) -> "object | None":
    if value is None:
        return None
    try:
        return _parse_iso_bound(value)
    except CliError as err:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{flag} must be an ISO-8601 date or timestamp, got {value!r}",
            remediation=err.remediation,
        ) from err


def _search_text(result: dict) -> str:
    window = result["window"]
    lines = [
        f"Channel: {result['channel_id']}",
        f"Pattern: {result['pattern']}",
        f"Window: {window['start']} .. {window['end']}",
        f"Coverage: {'complete' if result['complete'] else 'incomplete'}",
    ]
    for gap in result["uncovered"]:
        lines.append(f"  uncovered: {gap['start']} .. {gap['end']}")
    lines.append(f"Matches: {result['match_count']} (scanned {result['scanned']})")
    if result["bounded"]:
        lines.append(f"bounded: hit the {result['timeout']}s execution bound before finishing")
    if result["truncated"]:
        lines.append("truncated: --max-matches reached")
    for m in result["matches"]:
        author = m.get("author_name") or m["author_id"]
        lines.append(f"[{m['created_at']}] {author} ({m['jump_url']}): {m['content']}")
    return "\n".join(lines)


def cmd_discord_search(args: argparse.Namespace) -> int:
    channel_id = args.channel_id
    pattern = args.grep
    since = _parse_bound(getattr(args, "since", None), "--since")
    until = _parse_bound(getattr(args, "until", None), "--until")
    max_matches = getattr(args, "max_matches", None)
    timeout = float(getattr(args, "timeout", _search.DEFAULT_TIMEOUT_SECONDS))
    json_mode = bool(getattr(args, "json", False))

    result = _search.search_channel(
        channel_id,
        pattern,
        since=since,
        until=until,
        max_matches=max_matches,
        timeout=timeout,
    )

    if result["bounded"]:
        emit_diagnostic(
            f"regex matching hit its {result['timeout']}s bound before finishing "
            f"({result['match_count']} match(es) found before the cutoff); "
            "narrow --grep or raise --timeout"
        )
    if result["truncated"]:
        emit_diagnostic(f"stopped early: --max-matches {max_matches} reached")
    if not result["complete"]:
        emit_diagnostic(
            f"search window not fully covered: {len(result['uncovered'])} gap(s); "
            "run `jetson-ai-lab-cli discord fetch` to fill them before trusting a "
            "negative result"
        )

    if json_mode:
        emit_result(result, json_mode=True)
    else:
        emit_result(_search_text(result), json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    """Register the search verb under discord."""
    sr = sub.add_parser(
        "search",
        help="Regex search over the cached corpus (cache-served only; see `discord fetch`).",
    )
    sr.add_argument("channel_id", help="Numeric channel id.")
    sr.add_argument(
        "--grep",
        required=True,
        help="Python `re` pattern to match against cached message content.",
    )
    sr.add_argument(
        "--since",
        default=None,
        help="Start of the search window (ISO-8601; requires --until).",
    )
    sr.add_argument(
        "--until",
        default=None,
        help="End of the search window (ISO-8601; requires --since).",
    )
    sr.add_argument(
        "--max-matches",
        dest="max_matches",
        type=int,
        default=None,
        help="Stop after this many matches (default: unbounded).",
    )
    sr.add_argument(
        "--timeout",
        type=float,
        default=_search.DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Wall-clock bound in seconds for regex matching, enforced in a "
            "child process so a pathological pattern is killed rather than "
            f"hanging (default {_search.DEFAULT_TIMEOUT_SECONDS})."
        ),
    )
    sr.add_argument("--json", action="store_true", help=_JSON_HELP)
    sr.set_defaults(
        func=cmd_discord_search,
        json=False,
        since=None,
        until=None,
        max_matches=None,
        timeout=_search.DEFAULT_TIMEOUT_SECONDS,
    )

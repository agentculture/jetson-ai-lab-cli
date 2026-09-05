"""``jetson-ai-lab-cli discord`` — read-only Discord noun group.

Verbs: channels, read, active, members, links, doctor, overview.

Read-only only (no post/react/thread). Public channels only by default
(--all is the sole private opt-in for channel visibility).
"""

from __future__ import annotations

import argparse

from jlab.cli import _discord
from jlab.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from jlab.cli._output import emit_diagnostic, emit_result
from jlab.links import cache as _links_cache_mod
from jlab.links import extract as _links_extract_mod
from jlab.links import paths as _links_paths_mod
from jlab.links import report as _links_report_mod
from jlab.members import aggregate as _aggregate_mod
from jlab.members import report as _report_mod
from jlab.members import resolve as _resolve_mod

_JSON_HELP = "Emit structured JSON."

# The links pipeline writes TWO artifact sets per run, and
# ``jlab.atomic_writeset.write_artifact_set`` replaces its whole destination
# directory with exactly the files it is handed — so the report set and the
# extraction cache cannot share one run directory without the second write
# deleting the first. The cache therefore lives in a sibling run directory
# named after the report's run id plus this suffix. ``--from-cache`` accepts
# either spelling.
_CACHE_RUN_SUFFIX = "-cache"


def _cache_run_id(run_id: str) -> str:
    """Return the run-directory name holding *run_id*'s extraction cache."""
    if run_id.endswith(_CACHE_RUN_SUFFIX):
        return run_id
    return f"{run_id}{_CACHE_RUN_SUFFIX}"


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
    concurrency = int(getattr(args, "concurrency", _discord.DEFAULT_CONCURRENCY))
    json_mode = bool(getattr(args, "json", False))

    emit_diagnostic(
        f"probing public text channels (limit {fetch_limit}, concurrency {concurrency}) ..."
    )

    result = _discord.active_scan(
        guild_id,
        since_days=since_days,
        fetch_limit=fetch_limit,
        top=top,
        preview=preview,
        concurrency=concurrency,
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


# -- members ------------------------------------------------------------


def cmd_discord_members(args: argparse.Namespace) -> int | None:
    """Scan, aggregate, and (for humans) render an id-only members report.

    ``--json`` emits the **aggregate** stage only — id-only statistics, no
    name resolution — and returns before ``resolve_authors`` is ever called.
    That is the whole privacy design: the rendered HTML report is contained
    by a repo-anchored, gitignored path (see ``jlab/members/paths.py``), but
    stdout redirection is not containable, so resolved display names must
    never be reachable via ``--json``. Do not "helpfully" attach a resolved
    mapping to the JSON payload.

    The text-mode path is the one-invocation pipeline: scan window ->
    aggregate -> resolve names -> render + write the HTML report, printing
    only the path written. ``--include-departed`` includes every author
    regardless of current guild membership; by default authors who have left
    the guild are excluded from the rendered report (still counted in the
    scan/aggregate stage).
    """
    guild_id = _discord._guild_id()
    since_days = int(getattr(args, "since", _discord.DEFAULT_WINDOW_DAYS))
    concurrency = int(getattr(args, "concurrency", _discord.DEFAULT_CONCURRENCY))
    include_departed = bool(getattr(args, "include_departed", False))
    json_mode = bool(getattr(args, "json", False))

    emit_diagnostic(
        f"scanning public text channels for the last {since_days} days "
        f"(concurrency {concurrency}) ..."
    )
    scan_result = _discord.scan_window(
        guild_id,
        since_days=since_days,
        concurrency=concurrency,
    )

    agg = _aggregate_mod.aggregate(scan_result)

    if json_mode:
        # id-only: stop here, never touch resolve_authors.
        emit_result(agg, json_mode=True)
        return None

    emit_diagnostic(f"resolving {len(agg['members'])} author id(s) to names ...")
    stats_by_author_id = {member["author_id"]: member for member in agg["members"]}
    resolve_result = _resolve_mod.resolve_authors(
        guild_id,
        stats_by_author_id,
        include_departed=include_departed,
        concurrency=concurrency,
    )
    resolved = {aid: r.to_dict() for aid, r in resolve_result.resolved.items()}

    included_ids = set(resolve_result.included_author_ids)
    rendered_members = [m for m in agg["members"] if m["author_id"] in included_ids]
    render_agg = dict(agg, members=rendered_members)
    excluded_count = None if include_departed else resolve_result.departed_count

    path = _report_mod.write_report(
        render_agg,
        resolved=resolved,
        excluded_count=excluded_count,
    )
    emit_result(str(path), json_mode=False)
    return None


# -- links ------------------------------------------------------------------


_REBUILD_HINT = (
    "delete that run's cache directory under data/reports/links/ and re-run "
    "without --from-cache to rebuild it"
)


def _corrupt_cache(run_id: str, detail: str) -> CliError:
    """A cache file that parses but is not a cache: the user's data, not a bug.

    Deliberately :data:`EXIT_USER_ERROR`, not the environment code: nothing
    about the machine is broken, the file handed to ``--from-cache`` just
    isn't a usable cache. And deliberately *not* the generic dispatcher
    fallback -- letting a ``KeyError`` bubble out of here would tell the
    user to file a bug against a corrupt file of their own.
    """
    return CliError(
        EXIT_USER_ERROR,
        f"cached links run {run_id!r} is not a usable cache: {detail}",
        _REBUILD_HINT,
    )


def _validate_links_cache(payload: object, run_id: str) -> dict:
    """Check a loaded cache's shape *inside* the loading boundary.

    :func:`jlab.links.cache.load_cache` returns whatever the JSON parses
    to. Every downstream read -- ``payload["records"]``, parsing
    ``scanned_at`` -- would otherwise raise ``KeyError`` / ``TypeError`` /
    ``ValueError`` / ``AttributeError`` well outside any handler, and the
    dispatcher's catch-all would render it as an unexpected internal
    failure. Checking here keeps a corrupt cache a clean, named user error.
    """
    if not isinstance(payload, dict):
        raise _corrupt_cache(run_id, "its top-level value is not a JSON object")

    records = payload.get("records")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise _corrupt_cache(run_id, "its 'records' key is missing or is not a list of objects")

    if not isinstance(payload.get("scanned_at"), str):
        raise _corrupt_cache(run_id, "its 'scanned_at' key is missing or is not a string")
    try:
        _links_cache_mod.cache_scanned_at(payload)
    except (TypeError, ValueError):
        raise _corrupt_cache(run_id, "its 'scanned_at' value is not an ISO 8601 timestamp")

    for key in ("coverage", "scan_meta"):
        value = payload.get(key)
        if value is not None and not isinstance(value, dict):
            raise _corrupt_cache(run_id, f"its {key!r} key is not a JSON object")

    return payload


def _load_links_cache(run_id: str) -> dict:
    """Load and shape-check a cached extraction payload, or fail cleanly.

    The three failure modes are kept apart because their exit codes differ
    (see :mod:`jlab.cli._errors`): a missing cache, a malformed run id and
    a corrupt cache are all bad *input* (1); an unreadable file -- wrong
    permissions, a bad mount, an I/O error -- is the *environment* (2).
    """
    try:
        payload = _links_cache_mod.load_cache(_cache_run_id(run_id))
    except FileNotFoundError:
        raise CliError(
            EXIT_USER_ERROR,
            f"no cached links run {run_id!r} was found",
            "list data/reports/links/ for the run ids that have a "
            f"'{_CACHE_RUN_SUFFIX}' directory, or re-run without --from-cache",
        )
    except OSError:
        raise CliError(
            EXIT_ENV_ERROR,
            f"the cache file for links run {run_id!r} could not be read",
            "check that the file and data/reports/links/ are readable by this "
            "user, then retry — or re-run without --from-cache to rebuild it",
        )
    except ValueError:
        # json.JSONDecodeError is a ValueError; catching the subclass too
        # would be redundant (python:S5713).
        raise _corrupt_cache(run_id, "the file is not valid JSON")
    except CliError:
        # jlab.links.paths refuses a run id that is not a bare path segment
        # and calls that an environment error. Reached from --from-cache it
        # is bad *input* -- the user typed the run id.
        raise CliError(
            EXIT_USER_ERROR,
            f"--from-cache run id {run_id!r} is not a valid run id",
            "pass a plain run id like '20260905T101112Z-1a2b3c4d', "
            "as printed on stderr by a previous run",
        )

    return _validate_links_cache(payload, run_id)


def cmd_discord_links(args: argparse.Namespace) -> int | None:
    """Scan, extract shared addresses, and render one run's whole artifact set.

    One invocation is the whole pipeline: scan window -> extract links ->
    cache the extraction -> resolve author names in a single batch -> write
    the HTML report and both CSVs into one per-run directory, printing only
    that HTML path. Progress goes to stderr.

    ``--json`` emits the **extraction** stage only — id-only records, no
    name resolution, no files written — and returns before
    ``resolve_authors``, ``write_cache`` or the report writer is reachable.
    That containment is unconditional: no flag combination turns it off,
    because stdout redirection is not containable the way the gitignored
    report directory is. Do not "helpfully" attach a resolved mapping here.

    Unlike :func:`cmd_discord_members`, the render path applies **no**
    ``included_author_ids`` row filter. There, dropping a departed author
    drops a person from a people-report; here it would delete a real link
    share from the record of what was posted. A departed author keeps their
    row, with their id and no display name.

    ``--from-cache <run-id>`` re-renders a previous run's extraction without
    opening a Discord scan at all. The cache carries the extraction payload,
    the instant the scan ran, a trimmed copy of that scan's coverage
    statuses (d3), and the rest of that scan's self-description — guild id,
    window length, bot policy, message count. **Every** metadata figure a
    cached render states is read back from there, never from the current
    environment or the current flags: re-rendering with a different
    ``JLAB_GUILD_ID`` or the opposite ``--include-bots`` must not change
    what the report says about the scan that produced its rows. A cache
    written before those keys existed has nothing to show, and the render
    falls back to ``unknown`` cells rather than guessing. When the cache is
    older than the attachment-URL expiry window, that is said on stderr.
    """
    guild_id = _discord._guild_id()
    since_days = int(getattr(args, "since", _discord.DEFAULT_WINDOW_DAYS))
    concurrency = int(getattr(args, "concurrency", _discord.DEFAULT_CONCURRENCY))
    include_bots = bool(getattr(args, "include_bots", False))
    from_cache = getattr(args, "from_cache", None)
    json_mode = bool(getattr(args, "json", False))

    generated_at: str | None = None
    run_id: str | None = None

    if from_cache:
        payload = _load_links_cache(from_cache)
        records = list(payload.get("records") or [])
        generated_at = _links_cache_mod.cache_scanned_at(payload).isoformat(timespec="seconds")
        emit_diagnostic(
            f"rendering cached links run {from_cache} "
            f"(scanned {generated_at}) — no Discord scan ..."
        )
        if _links_cache_mod.attachments_expired(payload):
            emit_diagnostic(
                "this cache is older than the "
                f"{_links_cache_mod.ATTACHMENT_URL_EXPIRY_HOURS}-hour Discord "
                "attachment-URL expiry window: addresses badged 'expiring' in "
                "it have almost certainly stopped resolving"
            )
        # Every metadata figure the report will state comes from the cache
        # and ONLY from the cache: the original scan's guild id, window
        # length, bot policy and message count ("scan_meta") plus its own
        # coverage statuses ("coverage"). Nothing here may be rebuilt from
        # `guild_id` or `include_bots` above -- those describe *this*
        # invocation, and a report that restated them would assert a guild
        # and a bot policy its records were never gathered under. A cache
        # written before these keys existed contributes nothing, and the
        # report renders those cells "unknown" rather than inventing them.
        scan_result = {
            **(payload.get("scan_meta") or {}),
            **(payload.get("coverage") or {}),
        }
        report_guild_id = scan_result.get("guild_id")
        report_since_days = scan_result.get("since_days")
        cached_exclude_bots = scan_result.get("exclude_bots")
        report_include_bots = None if cached_exclude_bots is None else not cached_exclude_bots
    else:
        emit_diagnostic(
            f"scanning public text channels for the last {since_days} days "
            f"(concurrency {concurrency}) ..."
        )
        scan_result = _discord.scan_window(
            guild_id,
            since_days=since_days,
            concurrency=concurrency,
            exclude_bots=not include_bots,
        )
        records = _links_extract_mod.extract_links(scan_result, include_bots=include_bots)
        run_id = _links_paths_mod.new_run_id()
        report_guild_id = str(guild_id)
        report_since_days = since_days
        report_include_bots = include_bots

    if json_mode:
        # id-only: stop here, never touch resolve_authors, the cache or the
        # report writer. The three metadata fields describe the scan these
        # records came from -- restored from the cache on a --from-cache
        # run, null when that cache predates them.
        emit_result(
            {
                "guild_id": report_guild_id,
                "since_days": report_since_days,
                "include_bots": report_include_bots,
                "records": records,
            },
            json_mode=True,
        )
        return None

    if run_id is not None:
        try:
            _links_cache_mod.write_cache(
                _cache_run_id(run_id),
                records,
                coverage=scan_result,
                scan_meta=scan_result,
            )
        except OSError:
            # Sanitized deliberately: the raw OSError carries a filesystem
            # path and an errno string that are noise to the caller and
            # would leak an absolute path into stderr.
            raise CliError(
                EXIT_ENV_ERROR,
                "this run's links extraction cache could not be written to disk",
                "check that data/reports/links/ exists, is writable by this "
                "user, and that the filesystem is not full or read-only, then "
                "re-run",
            )
        emit_diagnostic(
            f"cached this run's extraction as {run_id} "
            f"(re-render it with --from-cache {run_id})"
        )

    author_ids = sorted({r["author_id"] for r in records if r.get("author_id")})
    emit_diagnostic(f"resolving {len(author_ids)} author id(s) to names ...")
    # include_departed=True: this stage only supplies names. Every link row
    # is kept regardless of current membership (see the docstring).
    resolve_result = _resolve_mod.resolve_authors(
        guild_id,
        {aid: {} for aid in author_ids},
        include_departed=True,
        concurrency=concurrency,
    )
    resolved = {aid: r.to_dict() for aid, r in resolve_result.resolved.items()}

    try:
        path = _links_report_mod.write_report(
            scan_result,
            records,
            resolved=resolved,
            generated_at=generated_at,
            run_id=run_id,
        )
    except OSError:
        raise CliError(
            EXIT_ENV_ERROR,
            "this run's links report could not be written to disk",
            "check that data/reports/links/ exists, is writable by this user, "
            "and that the filesystem is not full or read-only, then re-run",
        )
    emit_result(str(path), json_mode=False)
    return None


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
                "active [--since D] [--limit N] [--top K] [--preview P] "
                "[--concurrency C] — rank active channels",
                "members [--since D] [--concurrency C] [--include-departed] "
                "[--json] — scan + write an id-only members HTML report and CSV",
                "links [--since D] [--concurrency C] [--include-bots] "
                "[--from-cache RUN] [--json] — scan + write a shared-address "
                "HTML report and CSVs",
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
        help="Read-only Discord scan (channels, read, active, members, links, doctor).",
    )
    p.add_argument("--json", action="store_true", help=_JSON_HELP)
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
    ch.add_argument("--json", action="store_true", help=_JSON_HELP)
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
    rd.add_argument("--json", action="store_true", help=_JSON_HELP)
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
    ac.add_argument(
        "--concurrency",
        type=int,
        default=_discord.DEFAULT_CONCURRENCY,
        help=(
            "Channels read concurrently "
            f"(default {_discord.DEFAULT_CONCURRENCY}; keeps Discord rate limits happy)."
        ),
    )
    ac.add_argument("--json", action="store_true", help=_JSON_HELP)
    ac.set_defaults(func=cmd_discord_active, json=False)

    # members
    mm = noun_sub.add_parser(
        "members",
        help=(
            "Scan participation and write an id-only members HTML report plus "
            "a CSV into a per-run subdirectory."
        ),
    )
    mm.add_argument(
        "--since",
        type=int,
        default=_discord.DEFAULT_WINDOW_DAYS,
        help=f"Days to look back (default {_discord.DEFAULT_WINDOW_DAYS}).",
    )
    mm.add_argument(
        "--concurrency",
        type=int,
        default=_discord.DEFAULT_CONCURRENCY,
        help=(
            "Channels read concurrently "
            f"(default {_discord.DEFAULT_CONCURRENCY}; keeps Discord rate limits happy)."
        ),
    )
    mm.add_argument(
        "--include-departed",
        action="store_true",
        help=(
            "Include every author regardless of current guild membership "
            "(default excludes authors who have left the guild)."
        ),
    )
    mm.add_argument(
        "--json",
        action="store_true",
        help=(
            f"{_JSON_HELP} Emits the id-only aggregate (no name resolution, "
            "no HTML report written)."
        ),
    )
    mm.set_defaults(func=cmd_discord_members, json=False, include_departed=False)

    # links
    lk = noun_sub.add_parser(
        "links",
        help=(
            "Scan public text channels for shared addresses and write one run's "
            "HTML report plus its flat and per-address CSVs."
        ),
    )
    lk.add_argument(
        "--since",
        type=int,
        default=_discord.DEFAULT_WINDOW_DAYS,
        help=f"Days to look back (default {_discord.DEFAULT_WINDOW_DAYS}).",
    )
    lk.add_argument(
        "--concurrency",
        type=int,
        default=_discord.DEFAULT_CONCURRENCY,
        help=(
            "Channels read concurrently "
            f"(default {_discord.DEFAULT_CONCURRENCY}; keeps Discord rate limits happy)."
        ),
    )
    lk.add_argument(
        "--include-bots",
        action="store_true",
        help="Include bot- and webhook-authored shares (default excludes them).",
    )
    lk.add_argument(
        "--from-cache",
        metavar="RUN_ID",
        default=None,
        help=(
            "Re-render a previous run's cached extraction instead of scanning "
            "Discord again; pass the run id from that run's report directory."
        ),
    )
    lk.add_argument(
        "--json",
        action="store_true",
        help=(
            f"{_JSON_HELP} Emits the id-only extraction (no name resolution, "
            "no report and no cache written)."
        ),
    )
    lk.set_defaults(
        func=cmd_discord_links,
        json=False,
        include_bots=False,
        from_cache=None,
    )

    # doctor
    dr = noun_sub.add_parser(
        "doctor",
        help="Verify token + guild readable.",
    )
    dr.add_argument("--json", action="store_true", help=_JSON_HELP)
    dr.set_defaults(func=cmd_discord_doctor, json=False)

    # overview
    ov = noun_sub.add_parser(
        "overview",
        help="Describe the discord noun group.",
    )
    ov.add_argument("--json", action="store_true", help=_JSON_HELP)
    ov.set_defaults(func=cmd_discord_overview, json=False)

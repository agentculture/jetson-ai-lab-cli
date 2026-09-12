"""``jlab discord fetch`` — backward paged fetch of a public channel into the cache.

This is the write path onto :mod:`jlab.coverage` and :mod:`jlab.cache`: it
turns "fetch channel X" into "fetch only what :mod:`jlab.coverage` says is
missing", storing every span through :func:`jlab.cache.store_messages` (which
encrypts and enforces purge suppression) and widening coverage only after a
span is durably written. The interval arithmetic, the write-before-widen
ordering and the per-channel lock all live in :mod:`jlab.coverage`
(:func:`jlab.coverage.fetch_missing`) — this module supplies the *fetcher*
Discord side of that contract and the CLI-facing shape, and does not
re-implement any of it.

**Public check (o3).** :func:`jlab.cli._discord._channel_public` is re-applied
to the caller-supplied channel id, inside the same action coroutine, *before*
any ``history()`` call — a non-public id never reaches ``fetch_missing`` at
all, so its name and contents can never reach the cache or the CLI's output.

**One session per invocation.** ``discord_client.run`` is a one-shot
``asyncio.run``: the client (and everything bound to its event loop —
channels, messages) is unusable once it closes. So every ``history()`` call
this run issues, across every gap :func:`jlab.coverage.fetch_missing`
discovers, has to happen inside the ONE coroutine that session opens — not
one session per gap. But ``fetch_missing`` is a synchronous function that
calls its ``fetch(gap)`` argument synchronously, and :func:`jlab.cli._discord.
_collect_history` is async. Bridging those without nesting an ``asyncio.run``
inside an already-running loop (which raises) is what :func:`_bounded_fetcher`
and :func:`fetch_channel` do together:

* ``fetch_missing`` (synchronous, and the Mongo I/O in ``store``/``widen`` it
  drives is blocking too) runs on a worker thread via
  ``loop.run_in_executor``;
* each time it calls ``fetch(gap)`` from that worker thread, the closure
  schedules ``_collect_history(...)`` back onto the ORIGINAL event loop with
  ``asyncio.run_coroutine_threadsafe`` and blocks the worker thread on the
  result. The coroutine body still only ever executes on the one loop the
  Discord client belongs to; only the scheduling crosses threads.

This is the standard bridge for "synchronous code that itself must call back
into one specific running loop" — it keeps the whole drain, however many gaps
it takes, inside the one action coroutine the spec requires, without
reimplementing ``fetch_missing``'s gap/lock/widen logic here.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import crypto as _crypto
from jlab import mongo as _mongo
from jlab.cli import _discord
from jlab.cli._errors import EXIT_USER_ERROR, CliError

UTC = dt.timezone.utc

#: Discord's own epoch (the platform launched 2015-01-01). "Drain to the
#: channel's beginning" means to here, not to ``datetime.min`` — an
#: arbitrarily distant bound invites overflow/off-by-one surprises in
#: astimezone()/isoformat() that nothing here needs to risk.
DISCORD_EPOCH = dt.datetime(2015, 1, 1, tzinfo=UTC)


def parse_until(value: str) -> dt.datetime:
    """Parse ``--until``'s value as a timezone-aware datetime, or raise code 1.

    Accepts a bare date (``2026-09-01``, midnight UTC) or a full ISO-8601
    timestamp; a naive timestamp is treated as UTC, matching
    :func:`jlab.cache._parse_timestamp`'s convention elsewhere in the cache.
    """
    text = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--until must be an ISO-8601 date or timestamp, got {value!r}",
            remediation="pass e.g. --until 2026-09-01 or --until 2026-09-01T00:00:00+00:00",
        )
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _bounded_fetcher(
    loop: asyncio.AbstractEventLoop,
    channel: Any,
    budget: int | None,
) -> _coverage.Fetcher:
    """Build a :data:`jlab.coverage.Fetcher` bounded by a total-message budget.

    Runs on a worker thread (see the module docstring); every gap's
    ``_collect_history`` call is scheduled back onto *loop* and awaited there.
    *budget*, when given, is decremented across every gap this run fetches —
    it is a total for the whole invocation, not a per-gap limit — and once it
    reaches zero, every remaining gap is refused without issuing a Discord
    request at all.
    """
    remaining: dict[str, int | None] = {"n": budget}

    def fetch(span: _coverage.Interval) -> tuple[list[dict], bool, str | None]:
        if remaining["n"] is not None and remaining["n"] <= 0:
            return [], False, "max-messages budget exhausted before this span"

        async def _run_gap() -> tuple[list, bool, str | None]:
            return await _discord._collect_history(
                channel,
                limit=None,
                after=span.start,
                before=span.end,
                backward=True,
                max_messages=remaining["n"],
            )

        future = asyncio.run_coroutine_threadsafe(_run_gap(), loop)
        raw, complete, reason = future.result()
        messages = [_discord._serialize_message(m, channel) for m in raw]
        if remaining["n"] is not None:
            remaining["n"] -= len(messages)
        return messages, complete, reason

    return fetch


def fetch_channel(
    channel_id_raw: str,
    *,
    guild_id: int | None = None,
    until: dt.datetime | None = None,
    max_messages: int | None = None,
    blocking: bool = True,
    now: dt.datetime | None = None,
    coverage_collection: Any = None,
    message_collection: Any = None,
    suppression: Any = None,
) -> dict[str, Any]:
    """Backward-page a public channel's missing history into the cache.

    *channel_id_raw* is parsed and re-validated as public (via
    :func:`jlab.cli._discord._channel_public`) inside the same action
    coroutine, before any history request. *until* bounds how far back the
    drain goes (default: :data:`DISCORD_EPOCH`, i.e. the channel's
    beginning); *max_messages* bounds the TOTAL messages fetched this
    invocation (default: unbounded). Coverage decides what is actually
    requested — these two flags only bound the window and budget that
    :func:`jlab.coverage.fetch_missing` computes gaps against.

    *coverage_collection*, *message_collection* and *suppression* are test
    seams; production callers leave them ``None`` so :mod:`jlab.coverage` and
    :mod:`jlab.cache` open their own jlab-mongodb handles.

    Returns :func:`jlab.coverage.fetch_missing`'s result dict, plus a
    ``suppressed`` key (messages written but immediately excluded because
    their author is on the purge suppression list — never bypassing
    :func:`jlab.cache.store_messages`, which is the sole enforcement point).
    """
    channel_id = _discord.parse_id(channel_id_raw, "channel_id")
    gid = guild_id if guild_id is not None else _discord._guild_id()

    if max_messages is not None and max_messages < 1:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"--max-messages must be >= 1, got {max_messages}",
            remediation="pass a positive integer, or omit it for no budget",
        )

    window_start = until if until is not None else DISCORD_EPOCH
    started = now or dt.datetime.now(UTC)
    if window_start >= started:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=(
                f"--until must name a moment before now "
                f"({window_start.isoformat()} is not before {started.isoformat()})"
            ),
            remediation="pass a date in the past",
        )
    window = _coverage.Interval(window_start, started)

    # Preflight: a missing key or jlab-mongodb URI is an exit-2 setup error, so
    # surface it before connecting to Discord — never after a span of message
    # bodies has already been downloaded only to be thrown away.
    _crypto._key_material()
    if message_collection is None:
        with _mongo.message_collection():
            pass

    suppressed_total: dict[str, int] = {"n": 0}

    def store(_span: _coverage.Interval, messages: list[dict]) -> None:
        result = _cache.store_messages(
            str(channel_id),
            messages,
            collection=message_collection,
            suppression=suppression,
        )
        suppressed_total["n"] += result.get("suppressed", 0)

    async def action(client: Any) -> dict[str, Any]:
        guild = await client.fetch_guild(gid)
        channel = await client.fetch_channel(channel_id)
        everyone = guild.default_role
        # The bot may belong to other guilds; a channel id from one of them
        # would otherwise pass the public test against OUR @everyone role.
        owner = getattr(getattr(channel, "guild", None), "id", None)
        if owner is None or int(owner) != int(gid):
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"refusing to fetch channel {channel_id}: not a channel of guild {gid}",
                remediation=(
                    "fetch only reads the configured guild's public channels; "
                    "run `jetson-ai-lab-cli discord channels` to list them"
                ),
            )
        # o3: the public check is re-applied to THIS id, not inherited from a
        # listing, and runs before any history() call — _channel_public is
        # the single source of the public test (never re-derived here).
        if _discord._channel_public(channel, everyone) is not True:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"refusing to fetch channel {channel_id}: not a public channel",
                remediation=(
                    "fetch only reads channels @everyone can view; "
                    "run `jetson-ai-lab-cli discord channels` to confirm visibility"
                ),
            )
        loop = asyncio.get_running_loop()
        fetcher = _bounded_fetcher(loop, channel, max_messages)
        return await loop.run_in_executor(
            None,
            lambda: _coverage.fetch_missing(
                str(channel_id),
                window,
                fetch=fetcher,
                store=store,
                collection=coverage_collection,
                blocking=blocking,
                now=started,
            ),
        )

    result = _discord._run(action)
    result["suppressed"] = suppressed_total["n"]
    return result

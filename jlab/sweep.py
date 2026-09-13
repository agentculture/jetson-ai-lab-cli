"""``jlab discord sweep`` — the daily reconciliation pass over the message cache.

**What it does.** One idempotent pass, run on demand or from cron / a systemd
timer (jlab builds no scheduler). For every channel that has a coverage record
(:func:`jlab.coverage.covered_channels`), one channel at a time and under that
channel's own lock (:func:`jlab.coverage.channel_lock`, never a guild-wide
lock):

1. **Re-verify visibility live.** The channel must still exist, belong to the
   configured guild, and pass :func:`jlab.cli._discord._channel_public` — the
   same single public test ``fetch`` applies, so the definition of public
   cannot drift between the two paths. A channel that is gone (404), forbidden
   (403), now in another guild, or no longer public is purged through
   :func:`jlab.purge.purge_channel` (cache content, coverage, derived report
   runs). It is reported by id only, never by name. Any *other* failure to
   re-verify (5xx, network, a rate limit that never cleared) purges nothing and
   reports the channel incomplete: an outage is not evidence a channel went
   private.
2. **Re-read every covered interval** backward with
   :func:`jlab.cli._discord._collect_history` (its 429 back-off-and-resume is
   what makes a rate limit *delay* the sweep rather than narrow it), then:

   * a message whose body or Discord edit timestamp changed is rewritten
     through :func:`jlab.cache.store_messages`, so the cache holds the latest
     version and ``updated_at`` stays Discord's; a message Discord has but the
     cache lacks is stored the same way. That function stays the single
     enforcement point for purge suppression — a suppressed author is never
     re-cached here;
   * a cached message Discord no longer returns is deleted — **only** inside a
     span that was re-read completely. A span cut short by a rate limit or an
     error deletes nothing, is reported under ``incomplete``, and leaves
     coverage exactly as it was, so the next sweep reconciles the same span.

3. **Reap leftover encryption probes** (risk r9) older than
   :data:`PROBE_REAP_AGE`.

**Deletion is conservative by construction.** Candidates come from
:func:`jlab.cache.messages_between`, whose bounds are exclusive like Discord's
``after=``/``before=`` cursors, so nothing the re-read could not have returned
is ever deleted. And because the backward page cursor is a *timestamp*, a
message sharing its exact ``created_at`` with a message the re-read did return
may have been skipped at a page boundary; such a message is kept rather than
deleted on ambiguous evidence (see the residual below).

**Sessions and threads.** Like :mod:`jlab.fetch`, all Discord calls happen in
the one REST session ``_discord._run`` opens (``client.login`` only — no
gateway). The synchronous per-channel work (flock, Mongo I/O, the purge) runs
on one worker thread; each Discord coroutine is scheduled back onto the
session's loop with ``asyncio.run_coroutine_threadsafe``. Channels are handled
strictly one after another on that single thread, which is what the
per-process re-entrant lock requires.

Honest limits: a genuinely deleted message whose ``created_at`` equals, to
the microsecond, a surviving message's is not removed; each span's messages
are held in memory while it is compared.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
from pathlib import Path
from typing import Any, Callable, Iterable

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import crypto as _crypto
from jlab import mongo as _mongo
from jlab import purge as _purge
from jlab import reconcile as _reconcile
from jlab.cli import _discord

UTC = dt.timezone.utc

#: A probe younger than this may belong to a ``doctor`` still reading it back.
PROBE_REAP_AGE = dt.timedelta(hours=1)

#: HTTP statuses that mean the bot can no longer see the channel at all.
_PURGE_STATUSES = {404: "not_found", 403: "forbidden"}


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _new_row(channel_id: str) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "purged": False,
        "purge_reason": None,
        "spans": 0,
        "updated": 0,
        "added": 0,
        "deleted": 0,
        "suppressed": 0,
        "complete": True,
        "incomplete": [],
        "error": None,
    }


class _Sweeper:
    """Per-run state: the Mongo handles, the guild, and the loop bridge."""

    def __init__(
        self,
        *,
        col: Any,
        suppression: Any,
        report_dirs: Iterable[Path] | None,
        blocking: bool,
        gid: int,
    ) -> None:
        self.col = col
        self.cov = _mongo.sibling_collection(col, _mongo.COVERAGE_COLLECTION)
        self.suppression = suppression
        self.report_dirs = report_dirs
        self.blocking = blocking
        self.gid = gid

    # -- one channel ----------------------------------------------------------

    def channel(
        self,
        channel_id: str,
        client: Any,
        guild: Any,
        everyone: Any,
        on_loop: Callable[[Any], Any],
    ) -> dict[str, Any]:
        row = _new_row(channel_id)
        if not channel_id.isdigit():
            row["complete"] = False
            row["error"] = "coverage record does not name a Discord channel id; not swept"
            return row
        with _coverage.channel_lock(channel_id, blocking=self.blocking):
            reason, channel = self._verify(channel_id, client, guild, everyone, on_loop, row)
            if row["error"] is not None:
                return row
            if reason is not None:
                return self._purge(channel_id, reason, row)
            for span in _coverage.read_coverage(channel_id, collection=self.cov):
                self._reconcile(channel_id, channel, span, on_loop, row)
        return row

    def _verify(
        self,
        channel_id: str,
        client: Any,
        guild: Any,
        everyone: Any,
        on_loop: Callable[[Any], Any],
        row: dict[str, Any],
    ) -> tuple[str | None, Any]:
        try:
            channel = on_loop(client.fetch_channel(int(channel_id)))
        except Exception as exc:  # noqa: BLE001
            reason = _PURGE_STATUSES.get(_status(exc) or 0)
            if reason is None:
                row["complete"] = False
                row["error"] = f"could not re-verify visibility; nothing purged: {exc}"
            return reason, None
        # See jlab.fetch.validated_channel's WORKAROUND(discord-bot-cli#20)
        # comment: the raw fetch_channel() result's own
        # ``.guild`` (checked here, for the owner test) is an unavailable,
        # roleless stub under this gateway-less client, so it is read ONLY
        # for its id — never used for the permission check below.
        owner = getattr(getattr(channel, "guild", None), "id", None)
        if owner is None or int(owner) != int(self.gid):
            return "other_guild", None
        # Resolve permissions against a shallow copy carrying the
        # fully-fetched (role-populated) guild now that the owner check
        # above has confirmed *channel* really belongs to it — never by
        # mutating *channel* itself, which is reused below (and, in tests,
        # across calls). Without this, permissions_for would resolve
        # against the stub's empty roles (which would misreport every
        # public channel as private and cause this sweep to purge it).
        permission_probe = copy.copy(channel)
        permission_probe.guild = guild
        if _discord._channel_public(permission_probe, everyone) is not True:
            return "not_public", None
        return None, channel

    def _purge(self, channel_id: str, reason: str, row: dict[str, Any]) -> dict[str, Any]:
        result = _purge.purge_channel(
            channel_id,
            collection=self.col,
            report_dirs=self.report_dirs,
            blocking=self.blocking,
        )
        row["purged"] = True
        row["purge_reason"] = reason
        row["deleted"] = int(result["cache"]["deleted"])
        row["report_runs_removed"] = len(result["reports"]["runs_removed"])
        return row

    def _reconcile(
        self,
        channel_id: str,
        channel: Any,
        span: _coverage.Interval,
        on_loop: Callable[[Any], Any],
        row: dict[str, Any],
    ) -> None:
        row["spans"] += 1
        try:
            raw, complete, reason = on_loop(
                _discord._collect_history(
                    channel,
                    limit=None,
                    after=span.start,
                    before=span.end,
                    backward=True,
                    max_messages=None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raw, complete, reason = [], False, f"read failed: {exc}"

        live_messages = [_discord._serialize_message(m, channel) for m in raw]
        result = _reconcile.reconcile_span(
            channel_id,
            span,
            live_messages,
            complete=complete,
            reason=reason,
            collection=self.col,
            suppression=self.suppression,
        )
        row["updated"] += result["updated"]
        row["added"] += result["added"]
        row["deleted"] += result["deleted"]
        row["suppressed"] += result["suppressed"]
        if not result["complete"]:
            row["complete"] = False
            row["incomplete"].append({**span.to_dict(), "reason": result["reason"]})


def _summarise(
    rows: list[dict[str, Any]], gid: int, started: dt.datetime, reaped: int
) -> dict[str, Any]:
    totals = {
        "updated": sum(r["updated"] for r in rows),
        "added": sum(r["added"] for r in rows),
        "deleted": sum(r["deleted"] for r in rows),
        "suppressed": sum(r["suppressed"] for r in rows),
        "purged": sum(1 for r in rows if r["purged"]),
    }
    incomplete = [r["channel_id"] for r in rows if not r["complete"]]
    return {
        "guild_id": str(gid),
        "started_at": started.isoformat(),
        "channels_swept": len(rows),
        "complete": not incomplete,
        "incomplete_channels": incomplete,
        "totals": totals,
        "probes_reaped": reaped,
        "channels": rows,
    }


def sweep(
    *,
    guild_id: int | None = None,
    now: dt.datetime | None = None,
    blocking: bool = True,
    message_collection: Any = None,
    suppression: Any = None,
    report_dirs: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Run one reconciliation pass; return the per-channel report.

    Preflights the cache key and jlab-mongodb **before** any Discord request,
    so a missing ``JLAB_CACHE_KEY`` / ``JLAB_MONGO_URI`` exits 2 having read
    nothing. *message_collection*, *suppression* and *report_dirs* are test
    seams; coverage is always the sibling of the message collection, the same
    handle :func:`jlab.purge.purge_channel` clears.
    """
    started = now or dt.datetime.now(UTC)
    _crypto._key_material()
    if message_collection is None:
        with _mongo.message_collection() as col:
            return _sweep(col, guild_id, started, blocking, suppression, report_dirs)
    return _sweep(message_collection, guild_id, started, blocking, suppression, report_dirs)


def _sweep(
    col: Any,
    guild_id: int | None,
    started: dt.datetime,
    blocking: bool,
    suppression: Any,
    report_dirs: Iterable[Path] | None,
) -> dict[str, Any]:
    gid = guild_id if guild_id is not None else _discord._guild_id()
    sweeper = _Sweeper(
        col=col, suppression=suppression, report_dirs=report_dirs, blocking=blocking, gid=gid
    )
    reaped = _cache.reap_probes(older_than=started - PROBE_REAP_AGE, collection=col)
    channels = _coverage.covered_channels(collection=sweeper.cov)
    if not channels:
        return _summarise([], gid, started, reaped)

    async def action(client: Any) -> list[dict[str, Any]]:
        guild = await client.fetch_guild(gid)
        everyone = guild.default_role
        loop = asyncio.get_running_loop()

        def on_loop(coro: Any) -> Any:
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        def body() -> list[dict[str, Any]]:
            return [sweeper.channel(c, client, guild, everyone, on_loop) for c in channels]

        return await loop.run_in_executor(None, body)

    rows = _discord._run(action)
    return _summarise(rows, gid, started, reaped)

"""Per-channel cache coverage — merged intervals, gap arithmetic, locking.

**What coverage is.** For each channel, the set of time spans whose messages
jlab has fetched from Discord *and durably written* to jlab-mongodb. It is what
lets a repeat fetch ask only for what is missing, and what lets a search or a
read say "this part of your window was never downloaded" instead of answering
from a subset as if it were the whole.

**Why intervals, not an (oldest, newest) pair.** A single pair cannot
represent two disjoint fetches. Fetch September, later fetch January, and one
pair reports ``(September, January)`` as continuous while October to December
was never downloaded. That bug hides itself twice over: a search answers from
a cache it believes complete, and the gap-only fetch computes the uncovered
span as *nothing* — so the gap is never fetched either. Coverage is therefore
a **sorted list of disjoint intervals**; spans that overlap or touch merge,
spans that do not stay apart, and "what is missing" is real interval
subtraction (:func:`subtract`).

Intervals are closed ``[start, end]`` over timezone-aware datetimes. Touching
means ``a.end == b.start`` exactly — no tolerance is applied, so a one-
microsecond gap between two fetched spans is reported as a gap. Under-claiming
is the safe direction; see below.

**Write before widen (o12).** :func:`fetch_missing` stores a span's messages
and only *then* widens coverage by that span. A crash, an exception or a kill
between the two leaves coverage **narrower** than what is actually cached,
never wider — the next run re-fetches a span it did not need to, which costs
requests but never silently omits messages. "Durably written" means the store
call returned: :func:`jlab.mongo.message_collection` and
:func:`jlab.mongo.coverage_collection` hand out journal-acknowledged
(``j=True``) handles, so an acknowledged write has reached the journal.

**Serialisation (o12).** Two fetches against one channel would otherwise
interleave read-merge-write of the coverage document and could lose a span or
record one neither run completed. Every coverage mutation takes a per-channel
``fcntl.flock`` advisory lock, following the sibling precedent in
``headspace-cli``'s ``headspace/core/store.py``:

* the lock file lives in its own tree, ``<state home>/locks/<channel>.lock``
  (``$JLAB_STATE_HOME``, else ``~/.jlab``), never inside what it guards. The
  guarded data — the channel's messages and its coverage document — lives in
  jlab-mongodb, entirely outside that tree, and the tree holds lock files and
  nothing else. Purging a channel's cache (deletion, reconciliation) can
  therefore never unlink a lock file another process is already blocked on,
  which would hand the next acquirer a different inode and silently break
  mutual exclusion;
* lock files are never read, never parsed, and deliberately outlive the
  channel they name;
* nested acquisition in one process is re-entrant, so :func:`fetch_missing`
  can hold the lock for the whole run while :func:`widen_coverage` takes it
  again per span Re-entrancy is per *process*: two threads in one
  process share the lock rather than queueing behind each other, exactly as
  in the headspace precedent. The CLI is single-threaded per invocation.

The honest scope limit: ``flock`` serialises processes **on one host**. Two
machines writing to one jlab-mongodb are not serialised by it. jlab-mongodb is
a dedicated, locally bound instance (see README), so that is the deployment
this lock is correct for.

This module prints nothing. Failures are :class:`~jlab.cli._errors.CliError`;
rendering belongs to the CLI layer (:mod:`jlab.cli._output`).
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from jlab import mongo as _mongo
from jlab.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

#: Schema version stamped on every coverage document. A document carrying a
#: version this code does not know is refused, never reinterpreted.
SCHEMA_VERSION = 1

#: The environment variable naming jlab's local state home (lock files).
STATE_HOME_ENV = "JLAB_STATE_HOME"
_DEFAULT_STATE_DIRNAME = ".jlab"
_LOCKS_DIRNAME = "locks"
_LOCK_SUFFIX = ".lock"

# Channel ids become file names under locks/, so they are constrained rather
# than escaped: no separators, no leading dot, no traversal.
_CHANNEL_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Intervals and their arithmetic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    """A closed time span ``[start, end]`` over timezone-aware datetimes."""

    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        for label, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, dt.datetime) or value.tzinfo is None:
                raise CliError(
                    code=EXIT_USER_ERROR,
                    message=f"coverage interval {label} must be a timezone-aware datetime",
                    remediation="pass an ISO-8601 timestamp with an explicit offset, e.g. "
                    "2026-09-01T00:00:00+00:00",
                )
        if self.start > self.end:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=(
                    f"coverage interval starts after it ends "
                    f"({self.start.isoformat()} > {self.end.isoformat()})"
                ),
                remediation="swap the bounds so the older timestamp comes first",
            )

    def to_dict(self) -> dict[str, str]:
        """JSON-safe form: ISO-8601 strings, UTC-normalised."""
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
        }


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sort *intervals* and merge any that overlap or touch.

    Disjoint intervals stay disjoint — that is the whole point of this module.
    """
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    merged: list[Interval] = []
    for interval in ordered:
        if merged and interval.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = Interval(last.start, max(last.end, interval.end))
        else:
            merged.append(interval)
    return merged


def subtract(window: Interval, covered: Sequence[Interval]) -> list[Interval]:
    """Return the parts of *window* that no interval in *covered* reaches.

    The result is sorted and disjoint, and empty exactly when *window* is
    fully covered. Coverage outside *window* is ignored.
    """
    gaps: list[Interval] = []
    cursor = window.start
    for interval in merge(covered):
        if interval.end < cursor:
            continue
        if interval.start > window.end:
            break
        if interval.start > cursor:
            gaps.append(Interval(cursor, interval.start))
        cursor = max(cursor, interval.end)
        if cursor >= window.end:
            return gaps
    if cursor < window.end:
        gaps.append(Interval(cursor, window.end))
    return gaps


def intersect(window: Interval, covered: Sequence[Interval]) -> list[Interval]:
    """Return the parts of *covered* that fall inside *window*, clipped to it."""
    out: list[Interval] = []
    for interval in merge(covered):
        start = max(interval.start, window.start)
        end = min(interval.end, window.end)
        if start <= end and interval.end >= window.start and interval.start <= window.end:
            out.append(Interval(start, end))
    return out


# ---------------------------------------------------------------------------
# The advisory lock — held in a sibling tree, never inside what it guards
# ---------------------------------------------------------------------------


def state_home() -> Path:
    """``$JLAB_STATE_HOME`` when set, else ``~/.jlab``. Pure — creates nothing."""
    override = os.environ.get(STATE_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_STATE_DIRNAME


def lock_root() -> Path:
    """The tree holding lock files and nothing else — no cached data, ever."""
    return state_home() / _LOCKS_DIRNAME


def _validate_channel_id(channel_id: Any) -> str:
    value = str(channel_id)
    if not _CHANNEL_ID_RE.match(value):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"invalid channel id for coverage: {value!r}",
            remediation="pass a numeric Discord channel id",
        )
    return value


def lock_path(channel_id: Any) -> Path:
    """Where *channel_id*'s lock file lives. Validates the id; creates nothing."""
    return lock_root() / f"{_validate_channel_id(channel_id)}{_LOCK_SUFFIX}"


_held: dict[str, int] = {}
_bookkeeping = threading.RLock()


def release_all_locks() -> None:
    """Drop every lock this process holds. For tests and emergency cleanup."""
    with _bookkeeping:
        fds = list(_held.values())
        _held.clear()
    for fd in fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _acquire_flock(channel: str, *, blocking: bool) -> int:
    path = lock_path(channel)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as err:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot open the coverage lock for channel {channel}: {err}",
            remediation=f"check permissions on {path.parent}, or set {STATE_HOME_ENV}",
        ) from err
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as err:
        os.close(fd)
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"channel {channel} is being fetched by another jlab process",
            remediation="wait for the other invocation to finish, then retry",
        ) from err
    except OSError as err:  # pragma: no cover - platform/filesystem specific
        os.close(fd)
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot lock channel {channel}: {err}",
            remediation=f"check that {path} lives on a filesystem supporting flock",
        ) from err
    return fd


@contextmanager
def channel_lock(channel_id: Any, *, blocking: bool = True) -> Iterator[None]:
    """Hold *channel_id*'s coverage lock for the duration of the block.

    Re-entrant within one process. With ``blocking=False`` a channel another
    process holds raises :class:`CliError` (code 2) instead of waiting.
    """
    channel = _validate_channel_id(channel_id)
    with _bookkeeping:
        reentrant = channel in _held
    if reentrant:
        yield
        return

    # Acquired outside the bookkeeping mutex: a thread waiting on flock must
    # never block the thread that has to release it.
    fd = _acquire_flock(channel, blocking=blocking)
    with _bookkeeping:
        _held[channel] = fd
    try:
        yield
    finally:
        with _bookkeeping:
            held_fd = _held.pop(channel, fd)
        try:
            fcntl.flock(held_fd, fcntl.LOCK_UN)
        finally:
            os.close(held_fd)


# ---------------------------------------------------------------------------
# Persistence — one document per channel, beside the messages in jlab-mongodb
# ---------------------------------------------------------------------------


def _aware(value: Any, channel: str) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"the coverage record for channel {channel} holds a non-datetime bound",
            remediation=(
                "the record is corrupt; delete that channel's coverage document and "
                "re-fetch — jlab will not guess at what it covered"
            ),
        )
    # BSON datetimes carry no zone; pymongo returns them naive, in UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _run_with(collection: Any, action: Callable[[Any], Any]) -> Any:
    if collection is None:
        with _mongo.coverage_collection() as col:
            return action(col)
    return action(collection)


def _read(col: Any, channel: str) -> list[Interval]:
    doc = col.find_one({"_id": channel})
    if doc is None:
        return []
    version = doc.get("schema")
    if version != SCHEMA_VERSION:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                f"the coverage record for channel {channel} has schema {version!r}; "
                f"this jlab understands schema {SCHEMA_VERSION}"
            ),
            remediation="upgrade jlab; it never reinterprets coverage it cannot read",
        )
    return merge(
        Interval(_aware(raw.get("start"), channel), _aware(raw.get("end"), channel))
        for raw in doc.get("intervals") or []
    )


def read_coverage(channel_id: Any, *, collection: Any = None) -> list[Interval]:
    """The merged coverage intervals recorded for *channel_id*, oldest first."""
    channel = _validate_channel_id(channel_id)
    return _run_with(collection, lambda col: _read(col, channel))


def widen_coverage(
    channel_id: Any,
    span: Interval,
    *,
    collection: Any = None,
    now: dt.datetime | None = None,
) -> list[Interval]:
    """Record *span* as covered for *channel_id*; return the merged result.

    **Call this only after the span's messages are durably written.** The
    read-merge-write runs under the channel lock so two widenings cannot lose
    each other's spans.
    """
    channel = _validate_channel_id(channel_id)
    stamp = now or dt.datetime.now(UTC)

    def action(col: Any) -> list[Interval]:
        with channel_lock(channel):
            merged = merge([*_read(col, channel), span])
            col.update_one(
                {"_id": channel},
                {
                    "$set": {
                        "schema": SCHEMA_VERSION,
                        "channel_id": channel,
                        "intervals": [{"start": i.start, "end": i.end} for i in merged],
                        "updated_at": stamp,
                    }
                },
                upsert=True,
            )
            return merged

    return _run_with(collection, action)


def describe(
    channel_id: Any,
    window: Interval | None,
    *,
    collection: Any = None,
) -> dict[str, Any]:
    """What the cache holds for *channel_id*, and what it is missing in *window*.

    ``uncovered`` names every gap inside the window, so a caller answering from
    the cache can report a partial window as partial (o18). With *window*
    ``None`` the whole recorded coverage is returned and nothing is "missing".
    """
    covered = read_coverage(channel_id, collection=collection)
    if window is None:
        return {
            "channel_id": str(channel_id),
            "window": None,
            "covered": [i.to_dict() for i in covered],
            "uncovered": [],
            "complete": bool(covered),
        }
    gaps = subtract(window, covered)
    return {
        "channel_id": str(channel_id),
        "window": window.to_dict(),
        "covered": [i.to_dict() for i in intersect(window, covered)],
        "uncovered": [g.to_dict() for g in gaps],
        "complete": not gaps,
    }


# ---------------------------------------------------------------------------
# Incremental fetch — request only the gaps, write before widening
# ---------------------------------------------------------------------------

#: ``fetch(span) -> (messages, complete, reason)`` — issues the Discord
#: requests for one span. The same shape
#: :func:`jlab.cli._discord._collect_history` returns: *complete* is ``False``
#: when the span could not be fully drained (rate limit, cap, part-way failure).
Fetcher = Callable[[Interval], tuple[Sequence[dict], bool, "str | None"]]
#: ``store(span, messages)`` — durably writes one span's messages.
Storer = Callable[[Interval, Sequence[dict]], Any]


def _default_store(channel: str) -> Storer:
    def store(span: Interval, messages: Sequence[dict]) -> Any:
        from jlab import cache as _cache  # local: keep coverage importable on its own

        return _cache.store_messages(channel, messages)

    return store


def _unpack(result: Any, span: Interval) -> tuple[list[dict], bool, str | None]:
    """Insist the fetcher says whether it finished — a bare list proves nothing."""
    if not (isinstance(result, tuple) and len(result) == 3 and isinstance(result[1], bool)):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                f"the fetch for {span.start.isoformat()}..{span.end.isoformat()} did not "
                "report whether the span was fully drained; refusing to record coverage"
            ),
            remediation=(
                "return (messages, complete, reason) from the fetcher, as " "_collect_history does"
            ),
        )
    messages, complete, reason = result
    return list(messages), complete, reason


def clear_coverage(channel_id: Any, *, collection: Any = None) -> None:
    """Forget every interval recorded for *channel_id*, under its lock.

    A purge that removes a channel's messages must call this too; coverage that
    outlives the messages it describes is exactly the over-claim this module
    exists to prevent.
    """
    channel = _validate_channel_id(channel_id)

    def action(col: Any) -> None:
        with channel_lock(channel):
            col.delete_one({"_id": channel})

    _run_with(collection, action)


def fetch_missing(
    channel_id: Any,
    window: Interval,
    *,
    fetch: Fetcher,
    store: Storer | None = None,
    collection: Any = None,
    now: dt.datetime | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Fetch only the uncovered parts of *window*, storing each before widening.

    Holds the channel lock for the whole run, so a concurrent fetch of the same
    channel waits (or, with ``blocking=False``, raises) rather than
    interleaving. For each gap, in order: *fetch* it, *store* what came back,
    and only if the fetcher reports the span **complete**, widen coverage by
    it. Three things keep coverage from ever being wider than reality:

    * an exception from *fetch* or *store* leaves completed spans recorded and
      the failing span unrecorded, and is re-raised;
    * an incomplete span is stored but not widened — it is listed under
      ``incomplete`` with the fetcher's reason, and the next run retries it;
    * no span is claimed past *now* (the moment this run started): a window
      reaching into the future is fetched and recorded only up to *now*, so a
      message posted later is a gap, not a silent omission.

    ``fetch_calls`` in the result counts the *fetch* calls issued — one per
    uncovered gap, none for an already-covered window. It is not a Discord
    request count: how many pages a span costs is the fetcher's business.
    """
    channel = _validate_channel_id(channel_id)
    writer = store if store is not None else _default_store(channel)
    started = now or dt.datetime.now(UTC)

    def action(col: Any) -> dict[str, Any]:
        with channel_lock(channel, blocking=blocking):
            before = _read(col, channel)
            gaps = [
                Interval(g.start, min(g.end, started))
                for g in subtract(window, before)
                if g.start < started
            ]
            already = intersect(window, before)
            stored = 0
            incomplete: list[dict[str, Any]] = []
            for gap in gaps:
                messages, complete, reason = _unpack(fetch(gap), gap)
                writer(gap, messages)
                stored += len(messages)
                if complete:
                    widen_coverage(channel, gap, collection=col)
                else:
                    incomplete.append({**gap.to_dict(), "reason": reason})
            after = _read(col, channel)
            return {
                "channel_id": channel,
                "window": window.to_dict(),
                "fetched": [g.to_dict() for g in gaps],
                "already_covered": [i.to_dict() for i in already],
                "incomplete": incomplete,
                "fetch_calls": len(gaps),
                "stored": stored,
                "coverage": [i.to_dict() for i in after],
                "uncovered": [g.to_dict() for g in subtract(window, after)],
                "complete": not subtract(window, after),
            }

    return _run_with(collection, action)

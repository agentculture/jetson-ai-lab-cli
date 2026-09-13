"""Tests for ``jlab.coverage`` — interval coverage, gap arithmetic, locking.

These tests exist because of a specific, self-hiding bug the spec's original
instruction would have shipped (docs/specs/2026-09-12-...:58): tracking
per-channel coverage as a single ``(oldest, newest)`` pair cannot represent
two disjoint fetches. Fetch September, later fetch January, and one pair
claims the whole autumn — a search then answers from a cache it believes
complete while months in the middle were never downloaded, *and* the
gap-only-fetch obligation is defeated because the uncovered span computes to
nothing. Nothing errors. Nothing looks wrong.

So the interval arithmetic is asserted directly, not through a happy path:

* o18 — two disjoint fetches leave **two** intervals and the gap between them
  is *named* by ``describe``, not swallowed;
* o6  — a repeat fetch over a covered window issues **strictly fewer**
  requests, asserted by counting them;
* o12 — coverage widens only **after** the span's messages are durably
  written, and two fetches against one channel are serialised by an advisory
  lock held outside the tree it guards.

No network and no MongoDB: the coverage collection is injected, mirroring the
fake-collection pattern in tests/test_cache.py and tests/test_mongo.py.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import os
import pathlib
import threading

import pytest

from jlab import coverage as _coverage
from jlab.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

UTC = dt.timezone.utc


def _at(year: int, month: int, day: int = 1) -> dt.datetime:
    return dt.datetime(year, month, day, tzinfo=UTC)


def _iv(a: dt.datetime, b: dt.datetime) -> _coverage.Interval:
    return _coverage.Interval(a, b)


# ---------------------------------------------------------------------------
# Fakes — an in-memory stand-in for the coverage collection, and a lock root
# that is never the operator's real home.
# ---------------------------------------------------------------------------


class _FakeCollection:
    """Just enough of a pymongo collection for the coverage document."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.writes: list[str] = []

    def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        _id = flt["_id"]
        doc = self.docs.get(_id)
        if doc is None:
            if not upsert:
                return
            doc = {"_id": _id}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[_id] = doc
        doc.update(update.get("$set", {}))
        self.writes.append(_id)

    def find_one(self, flt: dict) -> dict | None:
        doc = self.docs.get(flt["_id"])
        return dict(doc) if doc is not None else None

    def delete_one(self, flt: dict) -> None:
        self.docs.pop(flt["_id"], None)


@pytest.fixture()
def col() -> _FakeCollection:
    return _FakeCollection()


@pytest.fixture()
def lock_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Point the lock tree at a temp dir — never the operator's real home."""
    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path))
    _coverage.release_all_locks()
    return tmp_path


class _RecordingFetcher:
    """A fake Discord fetch that counts the *requests* a span would cost.

    One request per day of span, so a narrower gap genuinely costs fewer
    requests — otherwise "strictly fewer" could be satisfied trivially by
    always making exactly one call.
    """

    def __init__(self, per_day: int = 1) -> None:
        self.spans: list[tuple[dt.datetime, dt.datetime]] = []
        self.requests = 0
        self._per_day = per_day

    def __call__(self, span: _coverage.Interval) -> tuple[list[dict], bool, str | None]:
        self.spans.append((span.start, span.end))
        days = max(1, round((span.end - span.start).total_seconds() / 86400))
        self.requests += days * self._per_day
        # Same (messages, complete, reason) shape _collect_history returns.
        return [{"id": f"m-{len(self.spans)}-{i}"} for i in range(days)], True, None


# ---------------------------------------------------------------------------
# Interval construction
# ---------------------------------------------------------------------------


def test_interval_rejects_naive_datetimes() -> None:
    with pytest.raises(CliError) as excinfo:
        _coverage.Interval(dt.datetime(2026, 1, 1), _at(2026, 2))
    assert excinfo.value.code == EXIT_USER_ERROR
    assert excinfo.value.remediation


def test_interval_rejects_reversed_bounds() -> None:
    with pytest.raises(CliError) as excinfo:
        _coverage.Interval(_at(2026, 2), _at(2026, 1))
    assert excinfo.value.code == EXIT_USER_ERROR


def test_interval_allows_an_instant() -> None:
    moment = _at(2026, 1)
    assert _iv(moment, moment).start == moment


# ---------------------------------------------------------------------------
# merge() — the arithmetic the single-pair design could not express
# ---------------------------------------------------------------------------


def test_merge_combines_overlapping_intervals() -> None:
    merged = _coverage.merge([_iv(_at(2026, 1), _at(2026, 3)), _iv(_at(2026, 2), _at(2026, 4))])
    assert [(i.start, i.end) for i in merged] == [(_at(2026, 1), _at(2026, 4))]


def test_merge_combines_touching_intervals() -> None:
    """End-to-start adjacency merges: there is no uncovered instant between."""
    merged = _coverage.merge([_iv(_at(2026, 1), _at(2026, 2)), _iv(_at(2026, 2), _at(2026, 3))])
    assert [(i.start, i.end) for i in merged] == [(_at(2026, 1), _at(2026, 3))]


def test_merge_keeps_disjoint_intervals_separate() -> None:
    """THE bug. Two disjoint spans must stay two, never collapse into one."""
    september = _iv(_at(2025, 9), _at(2025, 10))
    january = _iv(_at(2026, 1), _at(2026, 2))
    merged = _coverage.merge([september, january])
    assert [(i.start, i.end) for i in merged] == [
        (_at(2025, 9), _at(2025, 10)),
        (_at(2026, 1), _at(2026, 2)),
    ]


def test_merge_absorbs_a_contained_interval() -> None:
    merged = _coverage.merge([_iv(_at(2026, 1), _at(2026, 6)), _iv(_at(2026, 2), _at(2026, 3))])
    assert [(i.start, i.end) for i in merged] == [(_at(2026, 1), _at(2026, 6))]


def test_merge_sorts_unordered_input() -> None:
    merged = _coverage.merge([_iv(_at(2026, 5), _at(2026, 6)), _iv(_at(2026, 1), _at(2026, 2))])
    assert [i.start for i in merged] == [_at(2026, 1), _at(2026, 5)]


def test_merge_of_nothing_is_nothing() -> None:
    assert _coverage.merge([]) == []


# ---------------------------------------------------------------------------
# subtract() — what is still missing from a requested window
# ---------------------------------------------------------------------------


def test_subtract_names_the_interior_gap() -> None:
    window = _iv(_at(2025, 9), _at(2026, 2))
    covered = [_iv(_at(2025, 9), _at(2025, 10)), _iv(_at(2026, 1), _at(2026, 2))]
    assert [(i.start, i.end) for i in _coverage.subtract(window, covered)] == [
        (_at(2025, 10), _at(2026, 1))
    ]


def test_subtract_returns_nothing_when_fully_covered() -> None:
    window = _iv(_at(2026, 1), _at(2026, 2))
    assert _coverage.subtract(window, [_iv(_at(2025, 12), _at(2026, 3))]) == []


def test_subtract_clips_coverage_to_the_requested_window() -> None:
    window = _iv(_at(2026, 2), _at(2026, 5))
    covered = [_iv(_at(2025, 1), _at(2026, 3))]
    assert [(i.start, i.end) for i in _coverage.subtract(window, covered)] == [
        (_at(2026, 3), _at(2026, 5))
    ]


def test_subtract_reports_leading_and_trailing_gaps() -> None:
    window = _iv(_at(2026, 1), _at(2026, 6))
    covered = [_iv(_at(2026, 2), _at(2026, 3))]
    assert [(i.start, i.end) for i in _coverage.subtract(window, covered)] == [
        (_at(2026, 1), _at(2026, 2)),
        (_at(2026, 3), _at(2026, 6)),
    ]


def test_subtract_with_no_coverage_is_the_whole_window() -> None:
    window = _iv(_at(2026, 1), _at(2026, 6))
    assert [(i.start, i.end) for i in _coverage.subtract(window, [])] == [
        (_at(2026, 1), _at(2026, 6))
    ]


def test_subtract_ignores_coverage_outside_the_window() -> None:
    window = _iv(_at(2026, 1), _at(2026, 2))
    covered = [_iv(_at(2020, 1), _at(2020, 2)), _iv(_at(2030, 1), _at(2030, 2))]
    assert [(i.start, i.end) for i in _coverage.subtract(window, covered)] == [
        (_at(2026, 1), _at(2026, 2))
    ]


# ---------------------------------------------------------------------------
# Persistence — coverage lives beside the messages, in jlab-mongodb
# ---------------------------------------------------------------------------


def test_coverage_starts_empty(col: _FakeCollection) -> None:
    assert _coverage.read_coverage("chan-1", collection=col) == []


def test_widen_then_read_round_trips(col: _FakeCollection, lock_home) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    got = _coverage.read_coverage("chan-1", collection=col)
    assert [(i.start, i.end) for i in got] == [(_at(2026, 1), _at(2026, 2))]


def test_widen_merges_a_touching_span(col: _FakeCollection, lock_home) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 2), _at(2026, 3)), collection=col)
    got = _coverage.read_coverage("chan-1", collection=col)
    assert [(i.start, i.end) for i in got] == [(_at(2026, 1), _at(2026, 3))]


def test_widen_keeps_disjoint_spans_apart_on_disk(col: _FakeCollection, lock_home) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2025, 9), _at(2025, 10)), collection=col)
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    stored = col.docs["chan-1"]["intervals"]
    assert len(stored) == 2


def test_coverage_is_per_channel(col: _FakeCollection, lock_home) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    assert _coverage.read_coverage("chan-2", collection=col) == []


def test_naive_timestamps_from_bson_are_read_back_as_utc(col: _FakeCollection) -> None:
    """pymongo hands back tz-naive UTC datetimes; coverage must re-attach UTC.

    Without this, a round-tripped interval compares against an aware window
    and raises TypeError deep in the arithmetic.
    """
    col.docs["chan-1"] = {
        "_id": "chan-1",
        "schema": _coverage.SCHEMA_VERSION,
        "intervals": [
            {"start": dt.datetime(2026, 1, 1), "end": dt.datetime(2026, 2, 1)},
        ],
    }
    got = _coverage.read_coverage("chan-1", collection=col)
    assert [(i.start, i.end) for i in got] == [(_at(2026, 1), _at(2026, 2))]


def test_unknown_schema_version_is_refused_not_reinterpreted(col: _FakeCollection) -> None:
    col.docs["chan-1"] = {
        "_id": "chan-1",
        "schema": _coverage.SCHEMA_VERSION + 99,
        "intervals": [],
    }
    with pytest.raises(CliError) as excinfo:
        _coverage.read_coverage("chan-1", collection=col)
    assert excinfo.value.code == EXIT_ENV_ERROR


# ---------------------------------------------------------------------------
# describe() — o18: a partially covered window names its gaps
# ---------------------------------------------------------------------------


def test_disjoint_fetches_name_the_gap_between_them(col: _FakeCollection, lock_home) -> None:
    """o18, end to end: fetch September, fetch January, ask about the span.

    The answer must not be "covered". The October-to-January gap is named.
    """
    fetch = _RecordingFetcher()
    stored: list[dict] = []

    def store(span, messages):
        stored.extend(messages)

    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2025, 9), _at(2025, 10)),
        fetch=fetch,
        store=store,
        collection=col,
    )
    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2026, 1), _at(2026, 2)),
        fetch=fetch,
        store=store,
        collection=col,
    )

    report = _coverage.describe("chan-1", _iv(_at(2025, 9), _at(2026, 2)), collection=col)
    assert report["complete"] is False
    assert [(g["start"], g["end"]) for g in report["uncovered"]] == [
        (_at(2025, 10).isoformat(), _at(2026, 1).isoformat())
    ]
    assert len(report["covered"]) == 2


def test_describe_reports_a_fully_covered_window_as_complete(
    col: _FakeCollection, lock_home
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2025, 1), _at(2027, 1)), collection=col)
    report = _coverage.describe("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    assert report["complete"] is True
    assert report["uncovered"] == []


def test_describe_of_an_unknown_channel_is_wholly_uncovered(col: _FakeCollection) -> None:
    report = _coverage.describe("nobody", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    assert report["complete"] is False
    assert len(report["uncovered"]) == 1


def test_describe_without_a_window_reports_the_whole_coverage(
    col: _FakeCollection, lock_home
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    report = _coverage.describe("chan-1", None, collection=col)
    assert len(report["covered"]) == 1
    assert report["window"] is None


# ---------------------------------------------------------------------------
# fetch_missing() — o6: the second run costs strictly fewer requests
# ---------------------------------------------------------------------------


def test_repeat_fetch_of_a_covered_window_issues_strictly_fewer_requests(
    col: _FakeCollection, lock_home
) -> None:
    window = _iv(_at(2026, 1, 1), _at(2026, 1, 8))
    fetch = _RecordingFetcher()

    first = _coverage.fetch_missing("chan-1", window, fetch=fetch, store=_noop, collection=col)
    after_first = fetch.requests

    second = _coverage.fetch_missing("chan-1", window, fetch=fetch, store=_noop, collection=col)
    second_run_requests = fetch.requests - after_first

    assert after_first > 0
    assert second_run_requests < after_first
    assert second_run_requests == 0
    assert first["fetch_calls"] == 1
    assert second["fetch_calls"] == 0
    assert second["already_covered"]


def test_partial_overlap_fetches_only_the_uncovered_span(col: _FakeCollection, lock_home) -> None:
    fetch = _RecordingFetcher()
    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2026, 1, 1), _at(2026, 1, 8)),
        fetch=fetch,
        store=_noop,
        collection=col,
    )
    first_requests = fetch.requests
    fetch.spans.clear()

    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2026, 1, 1), _at(2026, 1, 10)),
        fetch=fetch,
        store=_noop,
        collection=col,
    )
    second_requests = fetch.requests - first_requests

    assert fetch.spans == [(_at(2026, 1, 8), _at(2026, 1, 10))]
    assert 0 < second_requests < first_requests


def test_interior_gap_is_the_only_thing_refetched(col: _FakeCollection, lock_home) -> None:
    fetch = _RecordingFetcher()
    for span in (_iv(_at(2025, 9), _at(2025, 10)), _iv(_at(2026, 1), _at(2026, 2))):
        _coverage.fetch_missing("chan-1", span, fetch=fetch, store=_noop, collection=col)
    fetch.spans.clear()

    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2025, 9), _at(2026, 2)),
        fetch=fetch,
        store=_noop,
        collection=col,
    )
    assert fetch.spans == [(_at(2025, 10), _at(2026, 1))]

    report = _coverage.describe("chan-1", _iv(_at(2025, 9), _at(2026, 2)), collection=col)
    assert report["complete"] is True


def _noop(span, messages) -> None:  # pragma: no cover - trivial
    return None


# ---------------------------------------------------------------------------
# o12: write-before-widen ordering
# ---------------------------------------------------------------------------


def test_coverage_widens_only_after_the_messages_are_written(
    col: _FakeCollection, lock_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    def fetch(span):
        order.append("fetch")
        return [{"id": "m1"}], True, None

    def store(span, messages):
        order.append("store")
        # At this instant the span must NOT yet be claimed as covered.
        assert _coverage.read_coverage("chan-1", collection=col) == []

    original = _coverage.widen_coverage

    def widen(*args, **kwargs):
        order.append("widen")
        return original(*args, **kwargs)

    monkeypatch.setattr(_coverage, "widen_coverage", widen)
    _coverage.fetch_missing(
        "chan-1", _iv(_at(2026, 1), _at(2026, 2)), fetch=fetch, store=store, collection=col
    )

    assert order == ["fetch", "store", "widen"]


def test_a_failed_store_leaves_coverage_unwidened(col: _FakeCollection, lock_home) -> None:
    """Under-claiming is safe; over-claiming is the failure this prevents."""

    def store(span, messages):
        raise CliError(EXIT_ENV_ERROR, "jlab-mongodb went away", "restart it")

    with pytest.raises(CliError):
        _coverage.fetch_missing(
            "chan-1",
            _iv(_at(2026, 1), _at(2026, 2)),
            fetch=_RecordingFetcher(),
            store=store,
            collection=col,
        )
    assert _coverage.read_coverage("chan-1", collection=col) == []


def test_an_interrupted_multi_span_run_keeps_the_spans_it_finished(
    col: _FakeCollection, lock_home
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 2), _at(2026, 3)), collection=col)
    calls: list[int] = []

    def store(span, messages):
        calls.append(1)
        if len(calls) == 2:
            raise CliError(EXIT_ENV_ERROR, "interrupted", "retry")

    with pytest.raises(CliError):
        _coverage.fetch_missing(
            "chan-1",
            _iv(_at(2026, 1), _at(2026, 5)),
            fetch=_RecordingFetcher(),
            store=store,
            collection=col,
        )

    got = _coverage.read_coverage("chan-1", collection=col)
    # The first gap (Jan-Feb) completed and merged with the pre-existing span;
    # the second (Mar-May) failed and must not appear.
    assert [(i.start, i.end) for i in got] == [(_at(2026, 1), _at(2026, 3))]


# ---------------------------------------------------------------------------
# o12: the advisory lock
# ---------------------------------------------------------------------------


def test_lock_file_lives_outside_the_tree_it_guards(
    col: _FakeCollection, lock_home: pathlib.Path
) -> None:
    """The lock tree holds lock files and nothing else; the data is elsewhere.

    Purging a channel's cached data therefore can never delete a lock another
    process is already blocked on (the headspace store.py precedent).
    """
    stored: list[dict] = []
    _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2026, 1), _at(2026, 2)),
        fetch=_RecordingFetcher(),
        store=lambda span, messages: stored.extend(messages),
        collection=col,
    )
    assert stored, "the fake store must actually have received the span's messages"
    path = _coverage.lock_path("chan-1")
    assert path.parent == _coverage.lock_root() == lock_home / "locks"
    assert sorted(p.name for p in lock_home.rglob("*") if p.is_file()) == ["chan-1.lock"]
    assert path.read_bytes() == b""  # never written, never parsed
    assert "chan-1" in col.docs  # coverage went to the collection, not the lock tree


def test_lock_excludes_a_second_holder(lock_home: pathlib.Path) -> None:
    with _coverage.channel_lock("chan-1"):
        fd = os.open(_coverage.lock_path("chan-1"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_lock_is_released_after_the_block(lock_home: pathlib.Path) -> None:
    with _coverage.channel_lock("chan-1"):
        pass
    fd = os.open(_coverage.lock_path("chan-1"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_non_blocking_lock_on_a_busy_channel_raises_rather_than_hanging(
    lock_home: pathlib.Path,
) -> None:
    held = threading.Event()
    release = threading.Event()

    _coverage.lock_path("chan-1").parent.mkdir(parents=True, exist_ok=True)

    def hold() -> None:
        fd = os.open(_coverage.lock_path("chan-1"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.set()
        release.wait(5)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert held.wait(5)
        with pytest.raises(CliError) as excinfo:
            with _coverage.channel_lock("chan-1", blocking=False):
                pass
        assert excinfo.value.code == EXIT_ENV_ERROR
        assert "chan-1" in excinfo.value.message
    finally:
        release.set()
        thread.join(5)


def test_lock_is_reentrant_within_one_process(lock_home: pathlib.Path) -> None:
    with _coverage.channel_lock("chan-1"):
        with _coverage.channel_lock("chan-1"):
            pass


def test_fetch_holds_the_lock_for_the_whole_run(col: _FakeCollection, lock_home) -> None:
    observed: list[bool] = []

    def fetch(span):
        fd = os.open(_coverage.lock_path("chan-1"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                observed.append(False)
            except BlockingIOError:
                observed.append(True)
        finally:
            os.close(fd)
        return [], True, None

    _coverage.fetch_missing(
        "chan-1", _iv(_at(2026, 1), _at(2026, 2)), fetch=fetch, store=_noop, collection=col
    )
    assert observed == [True]


def test_widen_takes_the_lock(
    col: _FakeCollection, lock_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken: list[str] = []
    original = _coverage.channel_lock

    @contextlib.contextmanager
    def spy(channel_id, **kwargs):
        taken.append(channel_id)
        with original(channel_id, **kwargs):
            yield

    monkeypatch.setattr(_coverage, "channel_lock", spy)
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    assert taken == ["chan-1"]


def test_a_channel_id_cannot_escape_the_lock_directory(lock_home: pathlib.Path) -> None:
    for bad in ("../escape", "a/b", "", ".hidden"):
        with pytest.raises(CliError) as excinfo:
            _coverage.lock_path(bad)
        assert excinfo.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Module invariants
# ---------------------------------------------------------------------------


def test_coverage_module_opens_no_mongo_client_of_its_own() -> None:
    source = pathlib.Path(_coverage.__file__).read_text(encoding="utf-8")
    assert "MongoClient" not in source
    assert "JLAB_MONGO_URI" not in source


# ---------------------------------------------------------------------------
# Over-claim paths beyond the obvious ones
# ---------------------------------------------------------------------------


def test_an_incomplete_span_is_stored_but_not_claimed_as_covered(
    col: _FakeCollection, lock_home
) -> None:
    """A rate-limited, part-read drain must not widen coverage over the gap.

    What was read is still stored (it is real data), but the span is reported
    as incomplete and stays uncovered, so the next run fetches it again.
    """
    stored: list[dict] = []

    def fetch(span):
        return [{"id": "m1"}], False, "rate limited: retries exhausted"

    result = _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2026, 1), _at(2026, 2)),
        fetch=fetch,
        store=lambda span, messages: stored.extend(messages),
        collection=col,
    )
    assert stored == [{"id": "m1"}]
    assert _coverage.read_coverage("chan-1", collection=col) == []
    assert result["complete"] is False
    assert result["incomplete"] == [
        {
            "start": _at(2026, 1).isoformat(),
            "end": _at(2026, 2).isoformat(),
            "reason": "rate limited: retries exhausted",
        }
    ]


def test_a_fetch_that_does_not_say_whether_it_finished_is_refused(
    col: _FakeCollection, lock_home
) -> None:
    """A bare message list cannot prove the span was drained; refuse it."""
    stored: list[dict] = []
    with pytest.raises(CliError) as excinfo:
        _coverage.fetch_missing(
            "chan-1",
            _iv(_at(2026, 1), _at(2026, 2)),
            fetch=lambda span: [{"id": "m1"}],
            store=lambda span, messages: stored.extend(messages),
            collection=col,
        )
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert _coverage.read_coverage("chan-1", collection=col) == []


def test_coverage_never_extends_past_when_the_fetch_started(
    col: _FakeCollection, lock_home
) -> None:
    """A window reaching into the future must not claim the future as read.

    Otherwise a message posted after the fetch, inside the window, would be
    silently absent from every later search.
    """
    started = _at(2026, 9, 12)
    fetch = _RecordingFetcher()
    window = _iv(_at(2026, 9, 1), _at(2026, 10, 1))

    _coverage.fetch_missing("chan-1", window, fetch=fetch, store=_noop, collection=col, now=started)

    assert fetch.spans == [(_at(2026, 9, 1), started)]
    got = _coverage.read_coverage("chan-1", collection=col)
    assert [(i.start, i.end) for i in got] == [(_at(2026, 9, 1), started)]
    report = _coverage.describe("chan-1", window, collection=col)
    assert [(g["start"], g["end"]) for g in report["uncovered"]] == [
        (started.isoformat(), _at(2026, 10, 1).isoformat())
    ]


def test_a_window_wholly_in_the_future_fetches_nothing_and_claims_nothing(
    col: _FakeCollection, lock_home
) -> None:
    fetch = _RecordingFetcher()
    result = _coverage.fetch_missing(
        "chan-1",
        _iv(_at(2027, 1), _at(2027, 2)),
        fetch=fetch,
        store=_noop,
        collection=col,
        now=_at(2026, 9, 12),
    )
    assert fetch.spans == []
    assert result["complete"] is False
    assert _coverage.read_coverage("chan-1", collection=col) == []


def test_clear_coverage_drops_every_interval_under_the_lock(
    col: _FakeCollection, lock_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A channel purge must take its coverage with it, or coverage over-claims."""
    _coverage.widen_coverage("chan-1", _iv(_at(2025, 9), _at(2025, 10)), collection=col)
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 1), _at(2026, 2)), collection=col)
    taken: list[str] = []
    original = _coverage.channel_lock

    @contextlib.contextmanager
    def spy(channel_id, **kwargs):
        taken.append(channel_id)
        with original(channel_id, **kwargs):
            yield

    monkeypatch.setattr(_coverage, "channel_lock", spy)
    _coverage.clear_coverage("chan-1", collection=col)
    assert _coverage.read_coverage("chan-1", collection=col) == []
    assert taken == ["chan-1"]


def test_non_blocking_fetch_on_a_busy_channel_raises_before_fetching(
    col: _FakeCollection, lock_home
) -> None:
    fetch = _RecordingFetcher()
    _coverage.lock_path("chan-1").parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_coverage.lock_path("chan-1"), os.O_RDWR | os.O_CREAT, 0o600)
    result: dict = {}

    def other_process_holds() -> None:
        # A different open file description, as a second process would have.
        try:
            with pytest.raises(CliError) as excinfo:
                _coverage.fetch_missing(
                    "chan-1",
                    _iv(_at(2026, 1), _at(2026, 2)),
                    fetch=fetch,
                    store=_noop,
                    collection=col,
                    blocking=False,
                )
            result["code"] = excinfo.value.code
        except BaseException as exc:  # surfaced below
            result["error"] = exc

    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        thread = threading.Thread(target=other_process_holds)
        thread.start()
        thread.join(5)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert "error" not in result, result.get("error")
    assert result["code"] == EXIT_ENV_ERROR
    assert fetch.spans == []


# ---------------------------------------------------------------------------
# trim_before — the retention purge narrows coverage, never widens it
# ---------------------------------------------------------------------------


def test_trim_before_drops_and_clips_spans_older_than_the_cutoff(
    col: _FakeCollection, lock_home
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2024, 1), _at(2024, 6)), collection=col)
    _coverage.widen_coverage("chan-1", _iv(_at(2025, 1), _at(2026, 3)), collection=col)
    _coverage.widen_coverage("chan-1", _iv(_at(2026, 5), _at(2026, 6)), collection=col)
    cutoff = _at(2025, 7)
    after = _coverage.trim_before("chan-1", cutoff, collection=col)
    assert after == [_iv(cutoff, _at(2026, 3)), _iv(_at(2026, 5), _at(2026, 6))]
    assert _coverage.read_coverage("chan-1", collection=col) == after
    # merge invariants hold on what was written back: sorted, disjoint
    assert _coverage.merge(after) == after
    before = _coverage.describe("chan-1", _iv(_at(2024, 1), cutoff), collection=col)
    assert before["complete"] is False
    assert before["covered"] == [_iv(cutoff, cutoff).to_dict()]  # only the boundary instant
    later = _coverage.describe("chan-1", _iv(cutoff, _at(2026, 3)), collection=col)
    assert later["complete"] is True


def test_trim_before_leaves_spans_after_the_cutoff_untouched(
    col: _FakeCollection, lock_home
) -> None:
    span = _iv(_at(2026, 5), _at(2026, 6))
    _coverage.widen_coverage("chan-1", span, collection=col)
    assert _coverage.trim_before("chan-1", _at(2026, 1), collection=col) == [span]


def test_trim_before_removes_the_document_when_nothing_remains(
    col: _FakeCollection, lock_home
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2024, 1), _at(2024, 6)), collection=col)
    assert _coverage.trim_before("chan-1", _at(2025, 1), collection=col) == []
    assert "chan-1" not in col.docs


def test_trim_before_takes_the_channel_lock(
    col: _FakeCollection, lock_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coverage.widen_coverage("chan-1", _iv(_at(2024, 1), _at(2026, 6)), collection=col)
    taken: list[str] = []
    original = _coverage.channel_lock

    @contextlib.contextmanager
    def spy(channel_id, **kwargs):
        taken.append(channel_id)
        with original(channel_id, **kwargs):
            yield

    monkeypatch.setattr(_coverage, "channel_lock", spy)
    _coverage.trim_before("chan-1", _at(2025, 1), collection=col)
    assert taken == ["chan-1"]


def test_trim_before_rejects_a_naive_cutoff(col: _FakeCollection, lock_home) -> None:
    with pytest.raises(CliError):
        _coverage.trim_before("chan-1", dt.datetime(2025, 1, 1), collection=col)


# ---------------------------------------------------------------------------
# BSON precision — a real jlab-mongodb stores datetimes to the millisecond
# (lapse l4: the plain fake kept microseconds, so this never showed)
# ---------------------------------------------------------------------------


class _BsonPrecisionCollection(_FakeCollection):
    """Truncates every stored datetime to whole milliseconds, as BSON does."""

    @staticmethod
    def _truncate(value):
        if isinstance(value, dt.datetime):
            return value.replace(microsecond=value.microsecond // 1000 * 1000)
        if isinstance(value, list):
            return [_BsonPrecisionCollection._truncate(v) for v in value]
        if isinstance(value, dict):
            return {k: _BsonPrecisionCollection._truncate(v) for k, v in value.items()}
        return value

    def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        super().update_one(flt, self._truncate(update), upsert=upsert)


_MICRO_NOW = dt.datetime(2026, 9, 13, 4, 45, 12, 123456, tzinfo=UTC)


def test_a_window_widened_at_a_microsecond_now_reads_back_complete_under_bson_precision(
    lock_home,
) -> None:
    col = _BsonPrecisionCollection()
    window = _iv(_at(2026, 9, 1), _MICRO_NOW)
    _coverage.widen_coverage("chan-ms", window, collection=col)
    described = _coverage.describe("chan-ms", window, collection=col)
    assert described["uncovered"] == []
    assert described["complete"] is True


def test_a_repeat_fetch_at_a_microsecond_now_issues_no_fetch_under_bson_precision(
    lock_home,
) -> None:
    col = _BsonPrecisionCollection()
    window = _iv(_at(2026, 9, 1), _MICRO_NOW)
    first = _coverage.fetch_missing(
        "chan-ms2",
        window,
        fetch=lambda span: ([], True, None),
        store=_noop,
        collection=col,
        now=_MICRO_NOW,
    )
    assert first["complete"] is True
    assert first["uncovered"] == []
    calls: list = []
    second = _coverage.fetch_missing(
        "chan-ms2",
        window,
        fetch=lambda span: (calls.append(span), ([], True, None))[1],
        store=_noop,
        collection=col,
        now=_MICRO_NOW,
    )
    assert second["fetch_calls"] == 0
    assert calls == []


def test_stored_coverage_is_never_wider_than_the_span_at_millisecond_precision(lock_home) -> None:
    col = _BsonPrecisionCollection()
    start = dt.datetime(2026, 9, 1, 0, 0, 0, 999999, tzinfo=UTC)
    end = dt.datetime(2026, 9, 2, 0, 0, 0, 500, tzinfo=UTC)
    _coverage.widen_coverage("chan-ms3", _iv(start, end), collection=col)
    [stored] = _coverage.read_coverage("chan-ms3", collection=col)
    assert stored.start >= start
    assert stored.end <= end

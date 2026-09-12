"""Tests for ``jlab.fetch`` and the ``discord fetch`` verb (t7).

No network and no real MongoDB: the Discord seam is a fake (reusing the
``_BackwardChannel`` / ``_FakeGuild`` / ``_FakeSeam`` tree from
tests/test_discord.py) and the Mongo collections are the fake-pymongo
in-memory stand-ins from tests/test_cache.py and tests/test_coverage.py.

Acceptance criteria under test (docs/plans/2026-09-12-...:64-72, t7):

* o3  — a non-public channel id is refused (code 1) before any history() call;
        its name/content never reach output or cache.
* c8  — ``_channel_public`` is the single source of the public test, reused
        rather than re-derived.
* o7  — a query against an already-fetched window returns results with the
        Discord seam monkeypatched to raise on any call.
* c26 — stop conditions: ``--until``, ``--max-messages``, and draining to the
        channel's beginning when neither is given; coverage decides what is
        actually requested.

Plus the two residuals the brief calls out explicitly: the backward-cursor
stall guard degrades to a partial result rather than hanging, and a second
concurrent fetch of one channel waits rather than interleaving.
"""

from __future__ import annotations

import base64
import datetime as dt
import fcntl
import json
import os
import threading

import pytest

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import fetch as _fetch_mod
from jlab import mongo as _mongo
from jlab.cli import _discord, main
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from tests.test_cache import _FakeCollection
from tests.test_discord import (
    _BackwardChannel,
    _FakeGuild,
    _FakeMsg,
    _FakePerms,
    _FakeSeam,
    _window_msgs,
)

UTC = dt.timezone.utc
_KEY_ENV = "JLAB_CACHE_KEY"
_TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(_KEY_ENV, _TEST_KEY)
    return _TEST_KEY


@pytest.fixture()
def lock_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path / "state"))
    _coverage.release_all_locks()
    yield tmp_path
    _coverage.release_all_locks()


def _cov(col: _FakeCollection):
    return col.database[_mongo.COVERAGE_COLLECTION]


def _seam(monkeypatch: pytest.MonkeyPatch, chan, *, guild: _FakeGuild | None = None) -> None:
    g = guild if guild is not None else _FakeGuild([chan])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild=g, channel=chan))


class _RaisingSeam:
    """Stand-in that raises on *any* call — proves the cache path never uses it."""

    def run(self, _action):
        raise AssertionError("the Discord seam must not be called for a cached window")

    def parse_id(self, value, _label):
        return int(value)


# ---------------------------------------------------------------------------
# o3 / c8 — the public check
# ---------------------------------------------------------------------------


def test_fetch_refuses_a_non_public_channel_before_any_history_call(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42001", "secret-ops", _window_msgs(5), public=False)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    with pytest.raises(CliError) as excinfo:
        _fetch_mod.fetch_channel(
            "42001", coverage_collection=_cov(col), message_collection=col
        )

    assert excinfo.value.code == EXIT_USER_ERROR
    assert chan.history_calls == []  # no history() call was ever issued
    assert col.docs == {}  # nothing reached the cache
    assert _coverage.read_coverage("42001", collection=_cov(col)) == []


def test_fetch_reuses_channel_public_as_the_single_source_of_truth(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """``_channel_public`` is called, not re-derived from ``permissions_for``."""
    chan = _BackwardChannel("42002", "general", _window_msgs(3), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    calls: list[tuple] = []
    original = _discord._channel_public

    def spy(channel, everyone):
        calls.append((channel, everyone))
        return original(channel, everyone)

    monkeypatch.setattr(_discord, "_channel_public", spy)
    _fetch_mod.fetch_channel("42002", coverage_collection=_cov(col), message_collection=col)
    assert calls and calls[0][0] is chan


def test_cli_fetch_of_a_private_channel_exits_1(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, capsys: pytest.CaptureFixture[str]
) -> None:
    chan = _BackwardChannel("42003", "secret", _window_msgs(2), public=False)
    _seam(monkeypatch, chan)
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(_FakeCollection()))
    rc = main(["discord", "fetch", "42003"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "Traceback" not in err


def _ctx(col):
    class _Ctx:
        def __enter__(self):
            return col

        def __exit__(self, *exc):
            return False

    return _Ctx()


# ---------------------------------------------------------------------------
# Basic fetch: stores messages, widens coverage, reports stored/suppressed
# ---------------------------------------------------------------------------


def test_fetch_stores_messages_and_widens_coverage(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42004", "general", _window_msgs(10), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    result = _fetch_mod.fetch_channel(
        "42004", coverage_collection=_cov(col), message_collection=col
    )

    assert result["complete"] is True
    assert result["stored"] == 10
    assert result["suppressed"] == 0
    assert len(_cache.fetch_messages("42004", collection=col)) == 10
    covered = _coverage.read_coverage("42004", collection=_cov(col))
    assert len(covered) == 1


# ---------------------------------------------------------------------------
# o7 — a query against an already-fetched window never touches the seam again
# ---------------------------------------------------------------------------


def test_a_query_after_fetch_never_touches_the_discord_seam_again(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42005", "general", _window_msgs(6), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    _fetch_mod.fetch_channel("42005", coverage_collection=_cov(col), message_collection=col)

    # Now swap the seam for one that raises on ANY call, and query the cache.
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    messages = _cache.fetch_messages("42005", collection=col)
    assert len(messages) == 6
    assert all(m["content"] for m in messages)


# ---------------------------------------------------------------------------
# c26 — stop conditions
# ---------------------------------------------------------------------------


def test_fetch_defaults_to_draining_to_the_channels_beginning(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42006", "general", _window_msgs(4), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    started = dt.datetime(2026, 9, 12, tzinfo=UTC)

    result = _fetch_mod.fetch_channel(
        "42006", coverage_collection=_cov(col), message_collection=col, now=started
    )

    assert result["fetched"] == [
        {"start": _fetch_mod.DISCORD_EPOCH.isoformat(), "end": started.isoformat()}
    ]


def test_fetch_until_bounds_how_far_back_the_drain_goes(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [
        _FakeMsg("o1", "ann", "old", now - dt.timedelta(minutes=30)),
        _FakeMsg("o2", "ann", "older-still", now - dt.timedelta(minutes=45)),
        _FakeMsg("n1", "ann", "recent", now - dt.timedelta(minutes=5)),
        _FakeMsg("n2", "ann", "recenter", now - dt.timedelta(minutes=2)),
    ]
    chan = _BackwardChannel("42007", "general", msgs, public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    until = now - dt.timedelta(minutes=10)

    result = _fetch_mod.fetch_channel(
        "42007",
        until=until,
        coverage_collection=_cov(col),
        message_collection=col,
        now=now,
    )

    assert result["complete"] is True
    stored_ids = {m["message_id"] for m in _cache.fetch_messages("42007", collection=col)}
    assert stored_ids == {"n1", "n2"}  # strictly newer than `until`


def test_fetch_refuses_an_until_at_or_after_now(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42008", "general", _window_msgs(2), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    now = dt.datetime(2026, 9, 12, tzinfo=UTC)

    with pytest.raises(CliError) as excinfo:
        _fetch_mod.fetch_channel(
            "42008",
            until=now,
            now=now,
            coverage_collection=_cov(col),
            message_collection=col,
        )
    assert excinfo.value.code == EXIT_USER_ERROR


def test_fetch_max_messages_bounds_the_total_across_gaps(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42009", "general", _window_msgs(20), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    result = _fetch_mod.fetch_channel(
        "42009", max_messages=7, coverage_collection=_cov(col), message_collection=col
    )

    assert result["complete"] is False
    assert result["stored"] == 7
    assert len(_cache.fetch_messages("42009", collection=col)) == 7
    assert result["incomplete"]


def test_fetch_rejects_a_non_positive_max_messages(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42010", "general", _window_msgs(2), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _fetch_mod.fetch_channel(
            "42010", max_messages=0, coverage_collection=_cov(col), message_collection=col
        )
    assert excinfo.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Gap-only repeat fetch — the whole point of building on fetch_missing
# ---------------------------------------------------------------------------


def test_repeat_fetch_over_a_covered_window_issues_no_further_history_calls(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42011", "general", _window_msgs(15), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    now = dt.datetime.now(UTC)

    first = _fetch_mod.fetch_channel(
        "42011", coverage_collection=_cov(col), message_collection=col, now=now
    )
    assert first["complete"] is True
    calls_after_first = len(chan.history_calls)
    assert calls_after_first > 0

    second = _fetch_mod.fetch_channel(
        "42011", coverage_collection=_cov(col), message_collection=col, now=now
    )
    assert second["fetched"] == []
    assert len(second["already_covered"]) == 1
    assert len(chan.history_calls) == calls_after_first  # no new requests at all


def test_an_incomplete_drain_is_not_claimed_as_covered(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """Mutation guard: a fetcher that lied about completeness must not widen coverage."""
    chan = _BackwardChannel("42012", "general", _window_msgs(20), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    now = dt.datetime.now(UTC)

    _fetch_mod.fetch_channel(
        "42012", max_messages=5, coverage_collection=_cov(col), message_collection=col, now=now
    )
    window = _coverage.Interval(_fetch_mod.DISCORD_EPOCH, now)
    described = _coverage.describe("42012", window, collection=_cov(col))
    assert described["complete"] is False
    assert described["uncovered"]


# ---------------------------------------------------------------------------
# Cursor-type residual: a full page sharing one timestamp stalls the walk;
# the guarantee under test is "reports partial", never "hangs".
# ---------------------------------------------------------------------------


class _StuckChannel(_BackwardChannel):
    """A server that ignores ``before`` — every page is identical."""

    def _page(self, limit, after, before):  # noqa: ARG002
        newest_first = list(reversed(self._messages))
        cap = self.page_cap if limit is None else min(limit, self.page_cap)
        return newest_first[:cap]


def test_a_non_advancing_channel_ends_in_a_partial_result_not_a_hang(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    msgs = _window_msgs(250)
    chan = _StuckChannel("42013", "stuck", msgs, public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    result = _fetch_mod.fetch_channel(
        "42013", coverage_collection=_cov(col), message_collection=col
    )

    assert result["complete"] is False
    assert result["incomplete"]
    assert "cursor" in result["incomplete"][0]["reason"]
    assert len(chan.history_calls) == 2  # page 1, then the non-advancing page 2 — no spin
    # What was read is still stored (a stall is not a reason to discard data).
    assert len(_cache.fetch_messages("42013", collection=col)) == 200


# ---------------------------------------------------------------------------
# Concurrency residual: a second fetch of the SAME channel waits, never
# interleaves. The lock is process-wide re-entrant, so a genuine "other
# worker" is simulated with a raw fd, exactly as tests/test_coverage.py does.
# ---------------------------------------------------------------------------


def test_a_concurrent_fetch_of_the_same_channel_waits_rather_than_interleaving(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan = _BackwardChannel("42014", "general", _window_msgs(3), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()

    _coverage.lock_path("42014").parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_coverage.lock_path("42014"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)

    outcome: dict = {}

    def run_fetch() -> None:
        outcome["result"] = _fetch_mod.fetch_channel(
            "42014", coverage_collection=_cov(col), message_collection=col
        )

    thread = threading.Thread(target=run_fetch)
    thread.start()
    try:
        thread.join(timeout=0.3)
        # Still blocked on the foreign lock: nothing fetched, nothing returned.
        assert thread.is_alive()
        assert chan.history_calls == []
        assert "result" not in outcome
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    thread.join(5)
    assert not thread.is_alive()
    assert outcome["result"]["complete"] is True


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_env(monkeypatch: pytest.MonkeyPatch, key: str, lock_home):
    chan = _BackwardChannel("42015", "general", _window_msgs(4), public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    monkeypatch.setattr(_mongo, "coverage_collection", lambda *a, **k: _ctx(_cov(col)))
    return chan, col


def test_cli_fetch_reports_json(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    chan, col = cli_env
    rc = main(["discord", "fetch", "42015", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel_id"] == "42015"
    assert payload["complete"] is True
    assert payload["stored"] == 4
    assert payload["suppressed"] == 0


def test_cli_fetch_reports_text(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "fetch", "42015"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "complete" in out
    assert "42015" in out


def test_cli_fetch_accepts_until_and_max_messages(
    cli_env, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["discord", "fetch", "42015", "--until", "2020-01-01", "--max-messages", "2"])
    assert rc == 0
    capsys.readouterr()


def test_cli_fetch_rejects_a_malformed_until(
    cli_env, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["discord", "fetch", "42015", "--until", "not-a-date"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


def test_explain_discord_fetch_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "discord", "fetch"])
    assert rc == 0
    assert "discord fetch" in capsys.readouterr().out

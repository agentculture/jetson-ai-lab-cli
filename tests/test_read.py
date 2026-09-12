"""Tests for ``jlab.read`` and the cache-served ``discord read`` verb (t9).

No network and no real Mongo: the Discord seam is a fake reused from
tests/test_discord.py, and the Mongo collections are the fake-pymongo
stand-ins from tests/test_cache.py, exactly as tests/test_fetch.py does.

Acceptance criteria under test (docs/plans/2026-09-12-...:84-91, t9):

* o21 — a covered window is served from the cache in the same text/JSON
        shape as a live read; an uncovered/partly covered window reports the
        gap (stderr + ``complete: false`` + ``uncovered`` in --json) rather
        than returning an empty result.
* o22 — ``--refresh`` is the ONLY path by which ``read`` contacts Discord: a
        seam that raises on any call must not be touched without it, and with
        it the live re-read is routed through ``jlab.fetch.fetch_channel``
        (guild + public check, gap-only fetch); a private/other-guild channel
        exits 1 and leaks no name or content.
"""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import fetch as _fetch_mod
from jlab import mongo as _mongo
from jlab import read as _read_mod
from jlab.cli import _discord, main
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from tests.test_cache import _FakeCollection
from tests.test_discord import (
    _BackwardChannel,
    _FakeGuild,
    _FakeMsg,
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
        raise AssertionError("the Discord seam must not be called without --refresh")

    def parse_id(self, value, _label):
        return int(value)


def _fetch_into_cache(monkeypatch, channel_id: str, n: int, col: _FakeCollection, **kw):
    chan = _BackwardChannel(channel_id, "general", _window_msgs(n), public=True)
    _seam(monkeypatch, chan)
    return _fetch_mod.fetch_channel(
        channel_id, coverage_collection=_cov(col), message_collection=col, **kw
    )


# ---------------------------------------------------------------------------
# o21 — covered window served from the cache
# ---------------------------------------------------------------------------


def test_covered_window_serves_the_same_messages_from_the_cache(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    # A shared `now`: coverage only ever reaches as far as the moment a fetch
    # ran, so a read has to be pinned to that same instant to be genuinely
    # "covered" — a later wall-clock `now` always opens a fresh (honest) gap.
    now = dt.datetime.now(UTC)
    _fetch_into_cache(monkeypatch, "50001", 6, col, now=now)

    # Prove the cache path never touches the seam again.
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())

    result = _read_mod.serve_read(
        "50001", limit=6, now=now, coverage_collection=_cov(col), message_collection=col
    )

    assert result["complete"] is True
    assert result["reason"] is None
    assert result["uncovered"] == []
    assert len(result["messages"]) == 6
    assert [m["content"] for m in result["messages"]] == [f"m{i}" for i in range(6)]
    for msg in result["messages"]:
        assert msg["author"]["name"] is None  # never fabricated
        assert msg["author"]["id"]
        assert set(msg) == {
            "id",
            "author",
            "content",
            "created_at",
            "edited_at",
            "channel",
            "jump_url",
            "attachments",
            "embeds",
            "thread",
        }


def test_covered_window_respects_limit_and_returns_the_most_recent(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    _fetch_into_cache(monkeypatch, "50002", 10, col)
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())

    result = _read_mod.serve_read(
        "50002", limit=3, coverage_collection=_cov(col), message_collection=col
    )

    assert [m["content"] for m in result["messages"]] == ["m7", "m8", "m9"]


# ---------------------------------------------------------------------------
# o21 — uncovered / partly covered window reports the gap, never empty-as-ok
# ---------------------------------------------------------------------------


def test_never_fetched_channel_reports_an_uncovered_gap_not_an_empty_result(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())

    result = _read_mod.serve_read(
        "50003", limit=20, coverage_collection=_cov(col), message_collection=col
    )

    assert result["messages"] == []
    assert result["complete"] is False
    assert "--refresh" in result["reason"]
    assert result["uncovered"] != []


def test_partly_covered_window_reports_the_gap(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    now = dt.datetime.now(UTC)
    # Cover only a narrow recent slice, well after DISCORD_EPOCH, then store one
    # message even older than the covered slice so the read window (oldest
    # returned message .. now) reaches back into an uncovered stretch.
    _coverage.widen_coverage(
        "50004",
        _coverage.Interval(now - dt.timedelta(minutes=5), now),
        collection=_cov(col),
    )
    _cache.store_messages(
        "50004",
        [
            {
                "id": "m1",
                "author": {"id": "a1", "bot": False},
                "content": "old one",
                "created_at": (now - dt.timedelta(days=1)).isoformat(),
            }
        ],
        collection=col,
    )

    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    result = _read_mod.serve_read(
        "50004", limit=20, now=now, coverage_collection=_cov(col), message_collection=col
    )

    assert result["complete"] is False
    assert len(result["messages"]) == 1
    assert result["uncovered"] != []


def test_cli_read_reports_gap_on_stderr_and_json_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    monkeypatch.setattr(
        _read_mod,
        "serve_read",
        lambda channel_id_raw, *, limit=20, refresh=False: {
            "channel_id": "999",
            "messages": [],
            "complete": False,
            "reason": "2 gap(s) ...; pass --refresh to fetch them from Discord",
            "uncovered": [{"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-02T00:00:00+00:00"}],
        },
    )
    rc = main(["discord", "read", "999", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "not fully cached" in captured.err
    payload = json.loads(captured.out)
    assert payload["complete"] is False
    assert payload["uncovered"]
    assert set(payload) == {"channel_id", "messages", "complete", "uncovered"}


# ---------------------------------------------------------------------------
# o22 — --refresh is the only path that reaches Discord
# ---------------------------------------------------------------------------


def test_default_read_never_touches_the_seam_even_when_uncovered(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())

    # No pytest.raises here: the point is that nothing raises, because the
    # seam is never called at all for an uncovered window without --refresh.
    result = _read_mod.serve_read(
        "50005",
        limit=20,
        refresh=False,
        coverage_collection=_cov(col),
        message_collection=col,
    )
    assert result["complete"] is False


def test_refresh_routes_through_fetch_channel_then_serves_from_cache(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    chan = _BackwardChannel("50006", "general", _window_msgs(4), public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "50006",
        limit=4,
        refresh=True,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is True
    assert len(result["messages"]) == 4
    assert chan.history_calls  # the guarded live path actually ran
    assert len(_cache.fetch_messages("50006", collection=col)) == 4


def test_refresh_refuses_a_non_public_channel_before_any_history_call(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    chan = _BackwardChannel("50007", "secret-ops", _window_msgs(3), public=False)
    _seam(monkeypatch, chan)

    with pytest.raises(CliError) as excinfo:
        _read_mod.serve_read(
            "50007",
            refresh=True,
            coverage_collection=_cov(col),
            message_collection=col,
        )

    assert excinfo.value.code == EXIT_USER_ERROR
    assert "secret-ops" not in excinfo.value.message
    assert chan.history_calls == []
    assert col.docs == {}


def test_refresh_refuses_a_channel_from_another_guild(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    col = _FakeCollection()
    chan = _BackwardChannel("50008", "elsewhere", _window_msgs(3), public=True, guild_id=999)
    _seam(monkeypatch, chan)

    with pytest.raises(CliError) as excinfo:
        _read_mod.serve_read(
            "50008",
            refresh=True,
            coverage_collection=_cov(col),
            message_collection=col,
        )

    assert excinfo.value.code == EXIT_USER_ERROR
    assert "elsewhere" not in excinfo.value.message
    assert chan.history_calls == []


def test_cli_read_with_refresh_flag_reaches_fetch_channel(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, capsys: pytest.CaptureFixture[str]
) -> None:
    col = _FakeCollection()
    chan = _BackwardChannel("50009", "general", _window_msgs(2), public=True)
    _seam(monkeypatch, chan)
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    monkeypatch.setattr(_mongo, "coverage_collection", lambda *a, **k: _ctx(_cov(col)))

    rc = main(["discord", "read", "50009", "--refresh", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert len(payload["messages"]) == 2


class _Ctx:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self._value

    def __exit__(self, *exc):
        return False


def _ctx(value):
    return _Ctx(value)


def test_explain_discord_read_mentions_refresh(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "discord", "read"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--refresh" in out
    assert "cache" in out.lower()

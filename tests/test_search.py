"""Tests for ``jlab.search`` and the ``discord search`` verb (t8).

No network and no real MongoDB: the Discord seam only ever appears as a fake
used to populate the cache via ``jlab.fetch.fetch_channel`` (reusing the
``_BackwardChannel``/``_FakeGuild``/``_FakeSeam`` tree from
tests/test_discord.py); search itself is proven never to touch it.

Acceptance criteria under test (docs/plans/2026-09-12-...:74-82, t8):

1. a malformed ``--grep`` pattern is compiled up front and exits 1
   (error:/hint:, no traceback) before any Mongo or Discord access.
2. o13 — a pathological backtracking pattern terminates within a declared
   bound and reports ``bounded: true``, never hanging, never reading as an
   empty "no matches".
3. o18 — a partially (or never) covered window reports the uncovered spans
   rather than answering as if complete; a channel with NO coverage is
   uncovered, never "zero matches".
4. search is cache-served only: it never contacts Discord (proven with a
   seam that raises on any call).
5. output shape (id, created_at, author id, jump url, content) in text and
   --json, plus complete/uncovered/bounded; --max-matches stops early and
   says so.
"""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from jlab import coverage as _coverage
from jlab import fetch as _fetch_mod
from jlab import mongo as _mongo
from jlab import search as _search
from jlab.cli import _discord, main
from jlab.cli._errors import EXIT_USER_ERROR, CliError
from tests.test_cache import _FakeCollection
from tests.test_discord import _BackwardChannel, _FakeGuild, _FakeMsg, _FakeSeam, _window_msgs

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


def _ctx(col):
    class _Ctx:
        def __enter__(self):
            return col

        def __exit__(self, *exc):
            return False

    return _Ctx()


def _seam(monkeypatch: pytest.MonkeyPatch, chan, *, guild: _FakeGuild | None = None) -> None:
    g = guild if guild is not None else _FakeGuild([chan])
    monkeypatch.setattr(_discord, "_seam", lambda: _FakeSeam(guild=g, channel=chan))


class _RaisingSeam:
    """Stand-in that raises on *any* call — proves search never uses it."""

    def run(self, _action):
        raise AssertionError("discord search must never touch the Discord seam")

    def parse_id(self, value, _label):
        return int(value)


def _fetch_into(monkeypatch, channel_id, messages, *, public=True, now=None):
    chan = _BackwardChannel(channel_id, "general", messages, public=public)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    _fetch_mod.fetch_channel(
        channel_id, coverage_collection=_cov(col), message_collection=col, now=now
    )
    return chan, col


# ---------------------------------------------------------------------------
# 1 — malformed pattern: compiled up front, before any Mongo access
# ---------------------------------------------------------------------------


def test_malformed_grep_exits_before_any_mongo_or_coverage_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*a, **k):
        raise AssertionError("Mongo must not be touched for a malformed pattern")

    monkeypatch.setattr(_mongo, "message_collection", _boom)
    monkeypatch.setattr(_mongo, "coverage_collection", _boom)

    with pytest.raises(CliError) as excinfo:
        _search.search_channel("123", "(unclosed[")
    assert excinfo.value.code == EXIT_USER_ERROR


def test_cli_search_rejects_a_malformed_grep(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "search", "123", "--grep", "(unclosed["])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "Traceback" not in err


def test_compile_pattern_accepts_a_well_formed_regex() -> None:
    compiled = _search.compile_pattern("hello.*world")
    assert compiled.pattern == "hello.*world"


# ---------------------------------------------------------------------------
# 2 (o13) — pathological backtracking pattern is bounded, never hangs
# ---------------------------------------------------------------------------


def test_pathological_pattern_is_bounded_not_hung(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    evil = "a" * 32 + "b"  # (a+)+$ against this is catastrophic backtracking
    msgs = [
        _FakeMsg("m1", "ann", "hello world", now - dt.timedelta(minutes=5)),
        _FakeMsg("m2", "ann", evil, now - dt.timedelta(minutes=4)),
    ]
    chan, col = _fetch_into(monkeypatch, "50002", msgs)

    result = _search.search_channel(
        "50002",
        r"(a+)+$",
        timeout=0.2,
        collection=col,
        coverage_collection=_cov(col),
        now=now + dt.timedelta(minutes=10),
    )

    assert result["bounded"] is True
    # Never an empty result silently read as "no matches" — the bound is the
    # explicit, distinguishing reason, always present regardless of hits.
    assert "bounded" in result
    assert isinstance(result["match_count"], int)


def test_a_well_formed_pattern_within_the_bound_is_not_reported_bounded(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = _window_msgs(5)
    chan, col = _fetch_into(monkeypatch, "50003", msgs)

    result = _search.search_channel(
        "50003",
        "m[0-9]",
        timeout=_search.DEFAULT_TIMEOUT_SECONDS,
        collection=col,
        coverage_collection=_cov(col),
        now=now + dt.timedelta(minutes=10),
    )
    assert result["bounded"] is False
    assert result["match_count"] == 5


def test_negative_or_zero_timeout_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    chan, col = _fetch_into(monkeypatch, "50004", _window_msgs(2))
    with pytest.raises(CliError) as excinfo:
        _search.search_channel(
            "50004", "x", timeout=0, collection=col, coverage_collection=_cov(col)
        )
    assert excinfo.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# 3 (o18) — coverage gaps reported honestly
# ---------------------------------------------------------------------------


def test_a_channel_with_no_coverage_is_reported_as_uncovered_never_zero_matches(
    key: str, lock_home
) -> None:
    col = _FakeCollection()
    result = _search.search_channel(
        "60001", "anything", collection=col, coverage_collection=_cov(col)
    )
    assert result["complete"] is False
    assert result["uncovered"]
    assert result["match_count"] == 0


def test_a_partially_covered_window_reports_the_uncovered_span(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "hello", now - dt.timedelta(days=40))]
    chan, col = _fetch_into(monkeypatch, "60002", msgs)

    # The cache only holds a window ending at `now` (fetch's default upper
    # bound); asking about a window reaching into the future must report the
    # unreachable-future span as uncovered, not silently answer as complete.
    future_until = now + dt.timedelta(days=5)
    result = _search.search_channel(
        "60002",
        "hello",
        since=now - dt.timedelta(days=100),
        until=future_until,
        collection=col,
        coverage_collection=_cov(col),
    )
    assert result["complete"] is False
    assert result["uncovered"]


def test_a_fully_covered_window_reports_complete(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "hello", now - dt.timedelta(minutes=5))]
    chan, col = _fetch_into(monkeypatch, "60003", msgs, now=now)

    result = _search.search_channel(
        "60003",
        "hello",
        collection=col,
        coverage_collection=_cov(col),
        now=now,
    )
    assert result["complete"] is True
    assert result["uncovered"] == []
    assert result["match_count"] == 1


def test_since_without_until_is_a_code_1_error(key: str, lock_home) -> None:
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _search.search_channel(
            "60004",
            "x",
            since=dt.datetime.now(UTC),
            collection=col,
            coverage_collection=_cov(col),
        )
    assert excinfo.value.code == EXIT_USER_ERROR


def test_until_without_since_is_a_code_1_error(key: str, lock_home) -> None:
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _search.search_channel(
            "60005",
            "x",
            until=dt.datetime.now(UTC),
            collection=col,
            coverage_collection=_cov(col),
        )
    assert excinfo.value.code == EXIT_USER_ERROR


def test_cli_search_rejects_a_lone_since_bound(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "search", "123", "--grep", "x", "--since", "2026-09-01"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# ---------------------------------------------------------------------------
# 4 — cache-served only: never touches Discord
# ---------------------------------------------------------------------------


def test_search_never_touches_the_discord_seam(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "September was busy", now - dt.timedelta(minutes=5))]
    chan, col = _fetch_into(monkeypatch, "70001", msgs)

    # Swap the seam for one that raises on ANY call, then search.
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    result = _search.search_channel(
        "70001",
        "September",
        collection=col,
        coverage_collection=_cov(col),
        now=now + dt.timedelta(minutes=1),
    )
    assert result["match_count"] == 1


def test_search_module_never_imports_the_discord_seam() -> None:
    """Static guard: jlab/search.py has no import of jlab.cli._discord at all."""
    import ast

    import jlab.search as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            imported.update(alias.name for alias in node.names)
    assert not any("_discord" in name or "discord_client" in name for name in imported)


# ---------------------------------------------------------------------------
# 5 — output shape + --max-matches truncation
# ---------------------------------------------------------------------------


def test_match_output_shape(monkeypatch: pytest.MonkeyPatch, key: str, lock_home) -> None:
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "hello world", now - dt.timedelta(minutes=5))]
    chan, col = _fetch_into(monkeypatch, "80001", msgs)

    result = _search.search_channel(
        "80001",
        "hello",
        collection=col,
        coverage_collection=_cov(col),
        now=now + dt.timedelta(minutes=1),
    )
    assert result["match_count"] == 1
    match = result["matches"][0]
    assert set(match.keys()) == {
        "message_id",
        "channel_id",
        "author_id",
        "created_at",
        "jump_url",
        "content",
    }
    assert match["content"] == "hello world"
    assert match["author_id"] is not None


def test_max_matches_stops_early_and_reports_truncated(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [
        _FakeMsg(f"m{i}", "ann", "needle here", now - dt.timedelta(minutes=10 - i))
        for i in range(8)
    ]
    chan, col = _fetch_into(monkeypatch, "80002", msgs)

    result = _search.search_channel(
        "80002",
        "needle",
        max_matches=3,
        collection=col,
        coverage_collection=_cov(col),
        now=now + dt.timedelta(minutes=1),
    )
    assert result["match_count"] == 3
    assert result["truncated"] is True
    assert result["bounded"] is False


def test_max_matches_rejects_non_positive(key: str, lock_home) -> None:
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _search.search_channel(
            "80003", "x", max_matches=0, collection=col, coverage_collection=_cov(col)
        )
    assert excinfo.value.code == EXIT_USER_ERROR


def test_no_matches_in_a_fully_covered_window_is_distinct_from_uncovered(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "hello", now - dt.timedelta(minutes=5))]
    chan, col = _fetch_into(monkeypatch, "80004", msgs, now=now)

    result = _search.search_channel(
        "80004",
        "zzz-not-present",
        collection=col,
        coverage_collection=_cov(col),
        now=now,
    )
    assert result["match_count"] == 0
    assert result["complete"] is True  # genuinely zero matches, not a gap
    assert result["bounded"] is False


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_env(monkeypatch: pytest.MonkeyPatch, key: str, lock_home):
    now = dt.datetime.now(UTC)
    msgs = [_FakeMsg("m1", "ann", "September was busy", now - dt.timedelta(minutes=5))]
    chan = _BackwardChannel("90001", "general", msgs, public=True)
    _seam(monkeypatch, chan)
    col = _FakeCollection()
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    monkeypatch.setattr(_mongo, "coverage_collection", lambda *a, **k: _ctx(_cov(col)))
    _fetch_mod.fetch_channel("90001", coverage_collection=_cov(col), message_collection=col)
    return chan, col


def test_cli_search_reports_json(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "search", "90001", "--grep", "September", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["content"] == "September was busy"
    assert payload["complete"] in (True, False)
    assert "bounded" in payload
    assert "uncovered" in payload


def test_cli_search_reports_text(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "search", "90001", "--grep", "September"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "September was busy" in out
    assert "Matches: 1" in out


def test_cli_search_requires_grep(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["discord", "search", "90001"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


def test_cli_search_reports_uncovered_diagnostic(
    monkeypatch: pytest.MonkeyPatch, key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    col = _FakeCollection()
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    monkeypatch.setattr(_mongo, "coverage_collection", lambda *a, **k: _ctx(_cov(col)))
    rc = main(["discord", "search", "90099", "--grep", "x"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "not fully covered" in err


def test_explain_discord_search_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "discord", "search"])
    assert rc == 0
    assert "discord search" in capsys.readouterr().out

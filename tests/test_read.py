"""Tests for ``jlab.read`` and the cache-served ``discord read`` verb (t9).

No network and no real Mongo: the Discord seam is a fake reused from
tests/test_discord.py, and the Mongo collection is tests/test_purge.py's
fake-pymongo stand-in (it understands the ``$gt``/``$lt`` operators
``jlab.cache.messages_between`` — used by the ``--refresh`` reconcile path —
needs; the plainer tests/test_cache.py fake only matches on equality), the
same fake tests/test_sweep.py uses for exactly that reason.

Acceptance criteria under test (docs/plans/2026-09-12-...:84-91, t9):

* o21 — a covered window is served from the cache in the same text/JSON
        shape as a live read; an uncovered/partly covered window reports the
        gap (stderr + ``complete: false`` + ``uncovered`` in --json) rather
        than returning an empty result.
* o22 — ``--refresh`` is the ONLY path by which ``read`` contacts Discord: a
        seam that raises on any call must not be touched without it. With it,
        the window ``read`` will serve is live RE-read (not a gap-only
        fetch — an edit or deletion inside an already-covered window must
        surface), reconciled into the cache via ``jlab.reconcile`` (the same
        span-reconcile logic ``jlab.sweep`` uses), and only THEN served from
        the cache; a private/other-guild channel exits 1 and leaks no name or
        content, via the same guards ``jlab.fetch.fetch_channel`` uses.
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
from tests.test_discord import (
    _BackwardChannel,
    _FakeGuild,
    _FakeSeam,
    _window_msgs,
)
from tests.test_purge import _FakeCollection

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
        assert msg["author"]["name"] == "ann"  # d4: the cache's own stored name
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
            "uncovered": [
                {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-02T00:00:00+00:00"}
            ],
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


def test_refresh_applies_an_edit_inside_an_already_covered_window(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """--refresh is a live RE-read, not a gap-only fetch: an edit Discord
    already has, inside a span the cache already holds, must surface."""
    col = _FakeCollection()
    msgs = _window_msgs(3)
    now = dt.datetime.now(UTC)

    # Seed the cache with the ORIGINAL content, as if a prior fetch cached it.
    _cache.store_messages(
        "60001",
        [
            {
                "id": m.id,
                "author": {"id": f"{m.id}a", "bot": False},
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        collection=col,
    )
    assert [d["content"] for d in _cache.fetch_messages("60001", collection=col)] == [
        "m0",
        "m1",
        "m2",
    ]

    # Discord's live copy has since been edited.
    msgs[1].content = "EDITED CONTENT"
    chan = _BackwardChannel("60001", "general", msgs, public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "60001",
        limit=3,
        refresh=True,
        now=now,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is True
    assert "EDITED CONTENT" in [m["content"] for m in result["messages"]]
    assert "EDITED CONTENT" in [
        d["content"] for d in _cache.fetch_messages("60001", collection=col)
    ]


def test_refresh_applies_a_renamed_authors_new_name(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """d4: a changed display name is a change to re-store, like an edited body."""
    col = _FakeCollection()
    msgs = _window_msgs(3)
    now = dt.datetime.now(UTC)

    _cache.store_messages(
        "60005",
        [
            {
                "id": m.id,
                "author": {"id": f"{m.id}a", "name": "ann", "bot": False},
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        collection=col,
    )
    assert _cache.fetch_messages("60005", collection=col)[1]["author_name"] == "ann"

    msgs[1].author.name = "annette"
    chan = _BackwardChannel("60005", "general", msgs, public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "60005",
        limit=3,
        refresh=True,
        now=now,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is True
    renamed = next(m for m in result["messages"] if m["id"] == msgs[1].id)
    assert renamed["author"]["name"] == "annette"  # the rename was applied
    others = [m for m in result["messages"] if m["id"] != msgs[1].id]
    assert all(m["author"]["name"] == "ann" for m in others)  # unaffected


def test_refresh_removes_a_message_deleted_on_discord(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """A message the cache holds but Discord no longer returns is deleted,
    once the span containing it was re-read completely."""
    col = _FakeCollection()
    msgs = _window_msgs(3)
    now = dt.datetime.now(UTC)

    _cache.store_messages(
        "60002",
        [
            {
                "id": m.id,
                "author": {"id": f"{m.id}a", "bot": False},
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        collection=col,
    )

    deleted = msgs.pop(1)  # "deleted on Discord": no longer in the live channel
    chan = _BackwardChannel("60002", "general", msgs, public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "60002",
        limit=3,
        refresh=True,
        now=now,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is True
    remaining_ids = {d["message_id"] for d in _cache.fetch_messages("60002", collection=col)}
    assert deleted.id not in remaining_ids
    assert deleted.id not in [m["id"] for m in result["messages"]]


def test_refresh_incomplete_reread_deletes_nothing_and_reports_incomplete(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """A rate-limited/failed re-read deletes nothing and is reported incomplete."""

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(_discord, "_sleep", _fake_sleep)

    col = _FakeCollection()
    msgs = _window_msgs(250)
    now = dt.datetime.now(UTC)

    # Seed the cache with all 250, as if fully fetched previously — coverage
    # is deliberately left empty so this refresh attempt is the only thing
    # that could ever mark the window complete.
    _cache.store_messages(
        "60003",
        [
            {
                "id": m.id,
                "author": {"id": f"{m.id}a", "bot": False},
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        collection=col,
    )
    assert len(_cache.fetch_messages("60003", collection=col)) == 250

    # This message is cached, but "gone from Discord" by the time the live
    # channel below is built (near the newest end, so it would have fallen
    # inside the FIRST, successfully-read 100-message page) — proving
    # deletion really was skipped, not merely never attempted.
    vanished = msgs.pop(240)
    assert len(msgs) == 249

    class _BoomOnPageTwo(_BackwardChannel):
        def history(self, limit=None, after=None, before=None):
            self.history_calls.append({"limit": limit, "after": after, "before": before})
            if len(self.history_calls) == 2:

                async def _boom():
                    raise RuntimeError("connection reset")
                    yield  # pragma: no cover

                return _boom()
            page = self._page(limit, after, before)

            async def _gen():
                for m in page:
                    yield m

            return _gen()

    chan = _BoomOnPageTwo("60003", "deep", msgs, public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "60003",
        limit=250,
        refresh=True,
        now=now,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is False
    # nothing was deleted, including `vanished` — cached, absent from the
    # live channel, and inside the one page that WAS fully read — because
    # the re-read as a whole did not complete
    cached_ids = {d["message_id"] for d in _cache.fetch_messages("60003", collection=col)}
    assert len(cached_ids) == 250
    assert vanished.id in cached_ids


def test_refresh_does_not_widen_coverage_for_an_incomplete_span(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home
) -> None:
    """An incomplete re-read must not make the window look covered afterwards.

    Coverage starts empty; the cache is seeded with EXACTLY the 100 messages
    a boomed-on-page-two re-read will re-confirm, and ``read``'s own limit
    matches that count, so the final gap-check window is exactly the
    reconciled span. If that span were wrongly widened despite the read
    being incomplete, this call's own ``complete`` would flip to ``True``.
    """

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(_discord, "_sleep", _fake_sleep)

    col = _FakeCollection()
    full_history = _window_msgs(150)  # more than one page (page_cap=100)
    now = dt.datetime.now(UTC)
    newest_100 = full_history[-100:]  # what a boomed-on-page-2 drain returns

    _cache.store_messages(
        "60004",
        [
            {
                "id": m.id,
                "author": {"id": f"{m.id}a", "bot": False},
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in newest_100
        ],
        collection=col,
    )

    class _BoomOnPageTwo(_BackwardChannel):
        def history(self, limit=None, after=None, before=None):
            self.history_calls.append({"limit": limit, "after": after, "before": before})
            if len(self.history_calls) == 2:

                async def _boom():
                    raise RuntimeError("connection reset")
                    yield  # pragma: no cover

                return _boom()
            page = self._page(limit, after, before)

            async def _gen():
                for m in page:
                    yield m

            return _gen()

    chan = _BoomOnPageTwo("60004", "deep", full_history, public=True)
    _seam(monkeypatch, chan)

    result = _read_mod.serve_read(
        "60004",
        limit=200,
        refresh=True,
        now=now,
        coverage_collection=_cov(col),
        message_collection=col,
    )

    assert result["complete"] is False
    assert _coverage.read_coverage("60004", collection=_cov(col)) == []


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

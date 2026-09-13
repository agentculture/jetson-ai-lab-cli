"""Tests for ``jlab.sweep`` and the ``discord sweep`` verb (t11).

The daily reconciliation sweep: re-verify each covered channel's visibility,
purge the ones that are gone / foreign / no longer public, and re-read every
covered interval so edits are applied and deletions removed — deleting ONLY
inside spans that were re-read completely.

No network and no real MongoDB: the Discord seam is a multi-channel fake
built from tests/test_discord.py's ``_BackwardChannel`` / ``_FakeGuild``, and
the collections are tests/test_purge.py's in-memory fake (which understands
the query operators the cache helpers issue).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import datetime as dt
import json

import pytest

from jlab import cache as _cache
from jlab import coverage as _coverage
from jlab import fetch as _fetch_mod
from jlab import mongo as _mongo
from jlab import sweep as _sweep
from jlab.cli import _discord, main
from tests.test_discord import (
    _BackwardChannel,
    _FakeGuild,
    _FakeMsg,
    _RateLimited,
    _RealisticChannel,
)
from tests.test_purge import _FakeCollection

UTC = dt.timezone.utc
_TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("JLAB_" + "CACHE_KEY", _TEST_KEY)
    return _TEST_KEY


@pytest.fixture()
def lock_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(tmp_path / "state"))
    _coverage.release_all_locks()
    yield tmp_path
    _coverage.release_all_locks()


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_discord, "_sleep", _fake_sleep)
    return slept


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _HttpError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"{status} {text}")
        self.status = status


class _MultiClient:
    """A REST-only client: fetch_guild + fetch_channel, nothing else.

    Any other attribute access (``connect``, ``start``, ``wait_for``, a gateway
    event hook ...) is recorded and fails, so a test can assert the sweep
    converges with no gateway connection anywhere.
    """

    def __init__(self, seam: "_MultiSeam") -> None:
        self._seam = seam

    async def fetch_guild(self, _gid: int):
        return self._seam.guild

    async def fetch_channel(self, cid: int):
        self._seam.channel_fetches.append(str(cid))
        found = self._seam.channels.get(str(cid))
        if found is None:
            raise _HttpError(404, "Not Found (error code: 10003): Unknown Channel")
        if isinstance(found, Exception):
            raise found
        return found

    def __getattr__(self, name: str):
        self._seam.other_access.append(name)
        raise AssertionError(f"the sweep touched client.{name}; only REST fetches are allowed")


class _MultiSeam:
    def __init__(self, channels: dict) -> None:
        self.channels = channels
        self.guild = _FakeGuild([c for c in channels.values() if not isinstance(c, Exception)])
        self.channel_fetches: list[str] = []
        self.other_access: list[str] = []
        self.runs = 0

    def run(self, action):
        self.runs += 1
        return asyncio.run(action(_MultiClient(self)))

    def parse_id(self, value, _label):
        return int(value)


class _RaisingSeam:
    def run(self, _action):
        raise AssertionError("the Discord seam must not be called")

    def parse_id(self, value, _label):
        return int(value)


class _FlakyChannel(_BackwardChannel):
    """A ``_BackwardChannel`` that raises 429 on chosen history-call numbers."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.fail_calls: set[int] = set()
        self.fail_from: int | None = None

    def history(self, limit=None, after=None, before=None):
        self.history_calls.append({"limit": limit, "after": after, "before": before})
        n = len(self.history_calls)
        if n in self.fail_calls or (self.fail_from is not None and n >= self.fail_from):

            async def _boom():
                raise _RateLimited()
                yield  # pragma: no cover

            return _boom()
        page = self._page(limit, after, before)

        async def _gen():
            for m in page:
                yield m

        return _gen()


def _msgs(prefix: str, n: int, *, base: dt.datetime | None = None) -> list:
    base = base or dt.datetime.now(UTC) - dt.timedelta(days=2)
    return [
        _FakeMsg(f"{prefix}{i:04d}", "ann", f"{prefix} body {i}", base + dt.timedelta(minutes=i))
        for i in range(n)
    ]


def _install(monkeypatch: pytest.MonkeyPatch, channels: dict) -> _MultiSeam:
    seam = _MultiSeam(channels)
    monkeypatch.setattr(_discord, "_seam", lambda: seam)
    return seam


def _cov(col: _FakeCollection):
    return col.database[_mongo.COVERAGE_COLLECTION]


def _seed(col: _FakeCollection, *channel_ids: str) -> None:
    for cid in channel_ids:
        result = _fetch_mod.fetch_channel(
            cid, coverage_collection=_cov(col), message_collection=col
        )
        assert result["complete"] is True


def _run_sweep(col: _FakeCollection, tmp_path, **kw) -> dict:
    return _sweep.sweep(message_collection=col, report_dirs=[tmp_path / "reports"], **kw)


def _cached(col: _FakeCollection, cid: str) -> dict[str, dict]:
    return {m["message_id"]: m for m in _cache.fetch_messages(cid, collection=col)}


def _row(result: dict, cid: str) -> dict:
    (row,) = [r for r in result["channels"] if r["channel_id"] == cid]
    return row


# ---------------------------------------------------------------------------
# Acceptance 1 — a full reconciliation cycle
# ---------------------------------------------------------------------------


def test_full_cycle_applies_an_edit_removes_a_deletion_and_purges_a_private_channel(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    a_msgs = _msgs("a", 12)
    b_msgs = _msgs("b", 5)
    chan_a = _BackwardChannel("51001", "general", a_msgs)
    chan_b = _BackwardChannel("51002", "soon-secret-room", b_msgs)
    _install(monkeypatch, {"51001": chan_a, "51002": chan_b})
    col = _FakeCollection()
    _seed(col, "51001", "51002")
    assert len(_cached(col, "51002")) == 5

    # Discord changes: an edit, a deletion, and a channel made private.
    edited_at = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    a_msgs[3].content = "edited body"
    a_msgs[3].edited_at = edited_at
    deleted = a_msgs.pop(7)
    chan_b._public = False

    result = _run_sweep(col, tmp_path)

    cached_a = _cached(col, "51001")
    assert cached_a["a0003"]["content"] == "edited body"
    assert cached_a["a0003"]["updated_at"] == edited_at
    assert deleted.id not in cached_a
    assert len(cached_a) == 11
    # The private channel: content AND coverage gone.
    assert _cached(col, "51002") == {}
    assert _coverage.read_coverage("51002", collection=_cov(col)) == []

    row_a, row_b = _row(result, "51001"), _row(result, "51002")
    assert (row_a["updated"], row_a["deleted"], row_a["purged"]) == (1, 1, False)
    assert row_b["purged"] is True and row_b["purge_reason"] == "not_public"
    assert row_b["deleted"] == 5
    assert result["complete"] is True
    # A purged channel is reported by id only — never by name.
    assert "soon-secret-room" not in json.dumps(result, default=str)


def test_sweep_never_purges_a_public_channel_over_the_stub_guild_bug(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    """discord-bot-cli#20, the critical case: without the fix, ``_verify``
    would have checked permissions against the roleless stub guild a raw
    ``fetch_channel()`` attaches (see tests/test_discord.py's
    ``_RealisticChannel``/``_UnavailableGuildStub``), reading every
    genuinely public channel as "not public" and purging it — wiping the
    whole cache on every sweep. This is the regression test for that.
    """
    msgs = _msgs("g", 4)
    chan = _BackwardChannel("51555", "general", msgs)
    _install(monkeypatch, {"51555": chan})
    col = _FakeCollection()
    _seed(col, "51555")

    # Swap in the realistic (stub-guild-carrying) channel for the sweep's
    # re-verify step only — the initial seed fetch above already proved the
    # cache holds 4 messages under a plain, flag-based fake.
    seam = _discord._seam()
    seam.channels["51555"] = _RealisticChannel("51555", "general", msgs)

    result = _run_sweep(col, tmp_path)

    row = _row(result, "51555")
    assert row["purged"] is False
    assert len(_cached(col, "51555")) == 4
    assert _coverage.read_coverage("51555", collection=_cov(col)) != []


def test_sweep_applies_a_renamed_authors_new_name(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    """d4: a changed display name is a change to re-store, like an edited body."""
    a_msgs = _msgs("r", 4)
    chan_a = _BackwardChannel("51099", "general", a_msgs)
    _install(monkeypatch, {"51099": chan_a})
    col = _FakeCollection()
    _seed(col, "51099")
    assert _cached(col, "51099")["r0002"]["author_name"] == "ann"

    a_msgs[2].author.name = "annette"
    a_msgs[2].author.nick = "Annette (JAL)"

    result = _run_sweep(col, tmp_path)

    cached = _cached(col, "51099")
    assert cached["r0002"]["author_name"] == "annette"
    assert cached["r0002"]["author_display_name"] == "Annette (JAL)"
    row = _row(result, "51099")
    assert row["updated"] == 1


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        (None, "not_found"),
        (_HttpError(403, "Forbidden (error code: 50001): Missing Access"), "forbidden"),
        ("other_guild", "other_guild"),
    ],
)
def test_a_vanished_forbidden_or_moved_channel_is_purged(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path, replacement, reason
) -> None:
    chan = _BackwardChannel("51003", "moving-room", _msgs("m", 4))
    seam = _install(monkeypatch, {"51003": chan})
    col = _FakeCollection()
    _seed(col, "51003")

    if replacement is None:
        del seam.channels["51003"]
    elif replacement == "other_guild":
        seam.channels["51003"] = _BackwardChannel(
            "51003", "moving-room", _msgs("m", 4), guild_id=999
        )
    else:
        seam.channels["51003"] = replacement

    result = _run_sweep(col, tmp_path)
    row = _row(result, "51003")
    assert row["purged"] is True and row["purge_reason"] == reason
    assert _cached(col, "51003") == {}
    assert _coverage.read_coverage("51003", collection=_cov(col)) == []
    assert "moving-room" not in json.dumps(result, default=str)


def test_a_transient_error_re_verifying_a_channel_purges_nothing(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    chan = _BackwardChannel("51004", "general", _msgs("t", 4))
    seam = _install(monkeypatch, {"51004": chan})
    col = _FakeCollection()
    _seed(col, "51004")
    coverage_before = _coverage.read_coverage("51004", collection=_cov(col))

    seam.channels["51004"] = _HttpError(503, "Service Unavailable")
    result = _run_sweep(col, tmp_path)

    row = _row(result, "51004")
    assert row["purged"] is False
    assert row["complete"] is False
    assert row["error"]
    assert result["complete"] is False
    assert "51004" in result["incomplete_channels"]
    assert len(_cached(col, "51004")) == 4
    assert _coverage.read_coverage("51004", collection=_cov(col)) == coverage_before


def test_purging_a_channel_sweeps_its_derived_reports(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    chan = _BackwardChannel("51005", "general", _msgs("r", 2))
    seam = _install(monkeypatch, {"51005": chan})
    col = _FakeCollection()
    _seed(col, "51005")
    run = tmp_path / "reports" / "20260901T000000Z"
    run.mkdir(parents=True)
    (run / "links.csv").write_text("https://discord.com/channels/1/51005/9\n")
    del seam.channels["51005"]

    result = _run_sweep(col, tmp_path)
    assert _row(result, "51005")["purged"] is True
    assert not run.exists()


# ---------------------------------------------------------------------------
# Acceptance 2 — o19: converge using only the sweep, no gateway anywhere
# ---------------------------------------------------------------------------


def test_o19_the_cache_converges_to_discord_using_only_the_sweep(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    msgs = _msgs("c", 260)  # three backward pages
    chan = _BackwardChannel("51010", "general", msgs)
    seam = _install(monkeypatch, {"51010": chan})
    col = _FakeCollection()
    _seed(col, "51010")
    seam.runs = 0  # count only the sweep's own sessions

    # A burst of changes a gateway would have delivered while jlab was down.
    stamp = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
    for i in (0, 99, 100, 101, 199, 259):
        msgs[i].content = f"edited {i}"
        msgs[i].edited_at = stamp
    for i in sorted((5, 100, 150, 200, 258), reverse=True):
        msgs.pop(i)

    _run_sweep(col, tmp_path)

    cached = _cached(col, "51010")
    live = {m.id: m for m in msgs}
    assert set(cached) == set(live)
    for mid, m in live.items():
        assert cached[mid]["content"] == m.content
        assert cached[mid]["updated_at"] == getattr(m, "edited_at", None)
    # No gateway: the client was used only through its REST fetches, in one
    # one-shot session.
    assert seam.other_access == []
    assert seam.runs == 1


# ---------------------------------------------------------------------------
# Acceptance 3 — o20: rate limits delay, never narrow; incomplete deletes nothing
# ---------------------------------------------------------------------------


def test_o20_a_rate_limit_delays_the_sweep_rather_than_narrowing_it(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path, no_sleep
) -> None:
    msgs = _msgs("r", 250)
    chan = _FlakyChannel("51020", "general", msgs)
    _install(monkeypatch, {"51020": chan})
    col = _FakeCollection()
    _seed(col, "51020")

    # 429 on the sweep's second page, mid-drain.
    chan.fail_calls = {len(chan.history_calls) + 2}
    gone = msgs.pop(10)  # on the LAST page: only reachable past the 429

    result = _run_sweep(col, tmp_path)

    assert no_sleep == [0.25]  # it waited out the server's retry_after
    row = _row(result, "51020")
    assert row["complete"] is True and row["incomplete"] == []
    assert row["deleted"] == 1
    assert gone.id not in _cached(col, "51020")
    assert result["complete"] is True


def test_o20_an_incomplete_span_reports_itself_and_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path, no_sleep
) -> None:
    msgs = _msgs("i", 150)
    chan = _FlakyChannel("51021", "general", msgs)
    _install(monkeypatch, {"51021": chan})
    col = _FakeCollection()
    _seed(col, "51021")
    coverage_before = _coverage.read_coverage("51021", collection=_cov(col))

    # The rate limit never clears after the first page of the sweep.
    chan.fail_from = len(chan.history_calls) + 2
    msgs[140].content = "edited in the part that WAS read"
    msgs[140].edited_at = dt.datetime.now(UTC) - dt.timedelta(minutes=5)
    old_gone = msgs.pop(5)  # never re-read
    new_gone = msgs.pop(-1)  # inside the page that was read — still not deleted

    result = _run_sweep(col, tmp_path)

    row = _row(result, "51021")
    assert row["complete"] is False
    assert row["deleted"] == 0
    (span,) = row["incomplete"]
    assert span["start"] == coverage_before[0].to_dict()["start"]
    assert span["end"] == coverage_before[0].to_dict()["end"]
    assert span["reason"]
    assert result["complete"] is False
    assert result["incomplete_channels"] == ["51021"]
    cached = _cached(col, "51021")
    assert old_gone.id in cached and new_gone.id in cached
    # Edits are safe to apply from a partial read; only deletion is gated.
    assert cached["i0140"]["content"] == "edited in the part that WAS read"
    # Coverage is never narrowed by an incomplete sweep.
    assert _coverage.read_coverage("51021", collection=_cov(col)) == coverage_before


def test_a_span_whose_read_fails_outright_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path, no_sleep
) -> None:
    msgs = _msgs("f", 20)
    chan = _FlakyChannel("51022", "general", msgs)
    _install(monkeypatch, {"51022": chan})
    col = _FakeCollection()
    _seed(col, "51022")
    chan.fail_from = len(chan.history_calls) + 1
    msgs.pop(3)

    result = _run_sweep(col, tmp_path)
    row = _row(result, "51022")
    assert row["complete"] is False and row["incomplete"]
    assert len(_cached(col, "51022")) == 20


def test_a_cached_message_outside_every_covered_span_is_never_deleted(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    msgs = _msgs("o", 6)
    chan = _BackwardChannel("51023", "general", msgs)
    _install(monkeypatch, {"51023": chan})
    col = _FakeCollection()
    _seed(col, "51023")
    # A message cached by a drain that never completed (stored, not covered).
    stray_created = msgs[0].created_at - dt.timedelta(days=400)
    stray = {
        "id": "stray1",
        "author": {"id": "9", "bot": False},
        "content": "outside coverage",
        "created_at": stray_created.isoformat(),
        "edited_at": None,
        "jump_url": None,
    }
    _cache.store_messages("51023", [stray], collection=col)
    _coverage.clear_coverage("51023", collection=_cov(col))
    _coverage.widen_coverage(
        "51023",
        _coverage.Interval(stray_created + dt.timedelta(days=1), dt.datetime.now(UTC)),
        collection=_cov(col),
    )

    _run_sweep(col, tmp_path)
    assert "stray1" in _cached(col, "51023")


def test_a_cached_message_sharing_a_live_timestamp_is_kept(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    """A backward cursor is a timestamp: a same-millisecond sibling of a page
    boundary may be skipped by the re-read, so its absence proves nothing."""
    msgs = _msgs("s", 4)
    chan = _BackwardChannel("51024", "general", msgs)
    _install(monkeypatch, {"51024": chan})
    col = _FakeCollection()
    twin = _FakeMsg("s9999", "ann", "twin", msgs[1].created_at)
    msgs.insert(2, twin)
    _seed(col, "51024")
    msgs.remove(twin)

    result = _run_sweep(col, tmp_path)
    assert "s9999" in _cached(col, "51024")
    assert _row(result, "51024")["deleted"] == 0


# ---------------------------------------------------------------------------
# Acceptance 4 — idempotence
# ---------------------------------------------------------------------------


def test_running_the_sweep_twice_changes_nothing_the_second_time(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    msgs = _msgs("d", 30)
    chan = _BackwardChannel("51030", "general", msgs)
    gone = _BackwardChannel("51031", "gone", _msgs("g", 3))
    seam = _install(monkeypatch, {"51030": chan, "51031": gone})
    col = _FakeCollection()
    _seed(col, "51030", "51031")
    msgs[2].content = "changed"
    msgs[2].edited_at = dt.datetime.now(UTC) - dt.timedelta(minutes=1)
    msgs.pop(9)
    del seam.channels["51031"]

    first = _run_sweep(col, tmp_path)
    assert first["totals"]["updated"] == 1
    assert first["totals"]["deleted"] == 1 + 3
    assert first["totals"]["purged"] == 1
    docs_after_first = copy.deepcopy(col.docs)
    coverage_after_first = copy.deepcopy(_cov(col).docs)

    second = _run_sweep(col, tmp_path)
    assert second["totals"] == {
        "updated": 0,
        "added": 0,
        "deleted": 0,
        "suppressed": 0,
        "purged": 0,
    }
    assert col.docs == docs_after_first  # not even stored_at moved
    assert _cov(col).docs == coverage_after_first


def test_a_suppressed_authors_message_is_never_re_cached_by_the_sweep(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    msgs = _msgs("p", 5)
    chan = _BackwardChannel("51032", "general", msgs)
    _install(monkeypatch, {"51032": chan})
    col = _FakeCollection()
    _seed(col, "51032")
    author = msgs[0].author.id
    sup = col.database[_mongo.SUPPRESSION_COLLECTION]
    _cache.suppress_author(author, collection=sup)
    _cache.delete_by_author(author, collection=col)
    remaining = copy.deepcopy(col.docs)

    for _ in range(2):
        result = _run_sweep(col, tmp_path)
        assert col.docs == remaining
    # _FakeMsg gives each message its own author; only msgs[0]'s is suppressed.
    assert _row(result, "51032")["suppressed"] == 1


# ---------------------------------------------------------------------------
# One channel lock at a time; probe reaping
# ---------------------------------------------------------------------------


def test_the_sweep_holds_one_channel_lock_at_a_time(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    chans = {cid: _BackwardChannel(cid, "c", _msgs(cid, 3)) for cid in ("51040", "51041")}
    seam = _install(monkeypatch, chans)
    col = _FakeCollection()
    _seed(col, *chans)
    del seam.channels["51041"]  # exercise the purge path under the lock too

    original = _coverage.channel_lock
    held: list[str] = []
    widest: list[int] = [0]
    fetched_under: dict[str, list[str]] = {}

    from contextlib import contextmanager

    @contextmanager
    def spy(channel_id, *, blocking=True):
        with original(channel_id, blocking=blocking):
            new = str(channel_id) not in held
            if new:
                held.append(str(channel_id))
            widest[0] = max(widest[0], len(set(held)))
            try:
                yield
            finally:
                if new:
                    held.remove(str(channel_id))

    monkeypatch.setattr(_coverage, "channel_lock", spy)
    original_fetch = _MultiClient.fetch_channel

    async def fetch_spy(self, cid):
        fetched_under[str(cid)] = list(held)
        return await original_fetch(self, cid)

    monkeypatch.setattr(_MultiClient, "fetch_channel", fetch_spy)

    _run_sweep(col, tmp_path)
    assert widest[0] == 1
    # Visibility is re-verified while holding that channel's lock.
    assert fetched_under == {"51040": ["51040"], "51041": ["51041"]}


def test_stale_encryption_probe_documents_are_reaped(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    _install(monkeypatch, {})
    col = _FakeCollection()
    now = dt.datetime.now(UTC)
    probe = {
        "id": "__jlab_encryption_probe__deadbeef",
        "author": {"id": None, "bot": False},
        "content": "probe",
        "created_at": now.isoformat(),
        "edited_at": None,
        "jump_url": None,
    }
    fresh = dict(probe, id="__jlab_encryption_probe__fresh")
    _cache.store_messages(
        "__jlab_encryption_probe__", [probe], collection=col, now=now - dt.timedelta(days=1)
    )
    _cache.store_messages("__jlab_encryption_probe__", [fresh], collection=col, now=now)

    result = _run_sweep(col, tmp_path, now=now)

    assert result["probes_reaped"] == 1
    assert "__jlab_encryption_probe__deadbeef" not in col.docs
    # A probe a concurrent doctor may be reading back right now is left alone.
    assert "__jlab_encryption_probe__fresh" in col.docs


def test_a_sweep_with_nothing_covered_opens_no_discord_session(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path
) -> None:
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    result = _run_sweep(_FakeCollection(), tmp_path)
    assert result["channels"] == [] and result["complete"] is True


# ---------------------------------------------------------------------------
# jlab.cache helpers
# ---------------------------------------------------------------------------


def _doc_msg(mid: str, created: dt.datetime) -> dict:
    return {
        "id": mid,
        "author": {"id": "7", "bot": False},
        "content": f"body {mid}",
        "created_at": created.isoformat(),
        "edited_at": None,
        "jump_url": None,
    }


def test_messages_between_is_exclusive_and_per_channel(key: str) -> None:
    col = _FakeCollection()
    t0 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    stamps = [t0 + dt.timedelta(hours=h) for h in range(4)]
    _cache.store_messages("1", [_doc_msg(f"x{i}", s) for i, s in enumerate(stamps)], collection=col)
    _cache.store_messages("2", [_doc_msg("y", stamps[1])], collection=col)

    got = _cache.messages_between("1", stamps[0], stamps[3], collection=col)
    assert [m["message_id"] for m in got] == ["x1", "x2"]
    assert got[0]["content"] == "body x1"  # decrypted


def test_delete_message_ids_is_scoped_to_the_channel(key: str) -> None:
    col = _FakeCollection()
    t0 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    _cache.store_messages("1", [_doc_msg("a", t0), _doc_msg("b", t0)], collection=col)
    _cache.store_messages("2", [_doc_msg("c", t0)], collection=col)

    assert _cache.delete_message_ids("1", ["a", "c"], collection=col) == {"deleted": 1}
    assert set(col.docs) == {"b", "c"}
    assert _cache.delete_message_ids("1", [], collection=col) == {"deleted": 0}
    assert set(col.docs) == {"b", "c"}


@pytest.mark.parametrize("bad", [None, "a", [None], [""], [{"$ne": ""}], ["  "]])
def test_delete_message_ids_refuses_anything_but_plain_ids(key: str, bad) -> None:
    from jlab.cli._errors import CliError

    col = _FakeCollection()
    _cache.store_messages("1", [_doc_msg("a", dt.datetime(2026, 9, 1, tzinfo=UTC))], collection=col)
    with pytest.raises(CliError):
        _cache.delete_message_ids("1", bad, collection=col)
    with pytest.raises(CliError):
        _cache.delete_message_ids("", ["a"], collection=col)
    assert set(col.docs) == {"a"}


# ---------------------------------------------------------------------------
# Acceptance 5 + 6 — CLI: preflight exits 2, --json per-channel counts
# ---------------------------------------------------------------------------


def _ctx(col):
    class _Ctx:
        def __enter__(self):
            return col

        def __exit__(self, *exc):
            return False

    return _Ctx()


def test_cli_sweep_without_a_cache_key_exits_2_before_any_discord_request(
    monkeypatch: pytest.MonkeyPatch, lock_home, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JLAB_" + "CACHE_KEY", raising=False)
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(_FakeCollection()))
    assert main(["discord", "sweep"]) == 2
    err = capsys.readouterr().err
    assert "CACHE_KEY" in err and "hint:" in err and "Traceback" not in err


def test_cli_sweep_without_a_mongo_uri_exits_2_before_any_discord_request(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("JLAB_MONGO_URI", raising=False)
    monkeypatch.setattr(_discord, "_seam", lambda: _RaisingSeam())
    assert main(["discord", "sweep", "--json"]) == 2
    err = capsys.readouterr().err
    assert "JLAB_MONGO_URI" in err


@pytest.fixture()
def cli_col(monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path):
    from jlab import purge as _purge

    msgs = _msgs("j", 8)
    chan = _BackwardChannel("51050", "general", msgs)
    private = _BackwardChannel("51051", "hidden-name", _msgs("h", 2))
    _install(monkeypatch, {"51050": chan, "51051": private})
    col = _FakeCollection()
    _seed(col, "51050", "51051")
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    monkeypatch.setattr(_purge, "_report_roots", lambda: [tmp_path / "reports"])
    msgs[1].content = "edit"
    msgs[1].edited_at = dt.datetime.now(UTC) - dt.timedelta(minutes=3)
    msgs.pop(4)
    private._public = False
    return col


def test_cli_sweep_json_reports_per_channel_counts(
    cli_col, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["discord", "sweep", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["complete"] is True
    rows = {r["channel_id"]: r for r in payload["channels"]}
    for row in rows.values():
        for field in ("updated", "deleted", "purged", "complete", "incomplete"):
            assert field in row
    assert (rows["51050"]["updated"], rows["51050"]["deleted"]) == (1, 1)
    assert rows["51050"]["purged"] is False and rows["51050"]["incomplete"] == []
    assert rows["51051"]["purged"] is True
    assert "hidden-name" not in captured.out + captured.err


def test_cli_sweep_text_mode(cli_col, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["discord", "sweep"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("complete:")
    assert "51050" in out and "51051" in out and "purged" in out
    assert "hidden-name" not in out


def test_cli_sweep_incomplete_is_reported_on_both_streams(
    monkeypatch: pytest.MonkeyPatch, key: str, lock_home, tmp_path, no_sleep, capsys
) -> None:
    msgs = _msgs("k", 150)
    chan = _FlakyChannel("51060", "general", msgs)
    _install(monkeypatch, {"51060": chan})
    col = _FakeCollection()
    _seed(col, "51060")
    monkeypatch.setattr(_mongo, "message_collection", lambda *a, **k: _ctx(col))
    chan.fail_from = len(chan.history_calls) + 2

    assert main(["discord", "sweep"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("incomplete:")
    assert "51060" in captured.out
    assert "incomplete" in captured.err


def test_cli_sweep_rejects_a_positional_target(capsys: pytest.CaptureFixture[str]) -> None:
    """The sweep takes no target: a stray argument is an error, never ignored."""
    with pytest.raises(SystemExit) as info:
        main(["discord", "sweep", "51050"])
    assert info.value.code == 1
    assert capsys.readouterr().err.startswith("error:")


def test_explain_and_overview_list_the_sweep(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "discord", "sweep"]) == 0
    assert "discord sweep" in capsys.readouterr().out
    assert main(["explain", "discord"]) == 0
    assert "discord sweep" in capsys.readouterr().out
    assert main(["discord", "overview"]) == 0
    assert "sweep" in capsys.readouterr().out

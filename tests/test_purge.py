"""Tests for ``jlab.purge`` and the ``discord purge`` verb (t12, obligation o15).

Compliance-critical. The published jetson-bot privacy policy already promises
that a deletion request removes the person's data **and** the "backups and
derived indexes associated with deleted data"; Discord's Developer Terms
require the same on user request, on Discord's request, and once retention is
no longer necessary. These tests are what make those promises true rather than
paperwork.

The central test is the o15 end-to-end one:
:func:`test_fetch_then_purge_then_search_finds_nothing_for_that_author` —
fetch into the cache, render a derived report carrying that author, purge by
author id, then search the cache and sweep the report tree and find nothing
for them anywhere.

No network and no real MongoDB: the collection handle is injected, mirroring
tests/test_cache.py's fake-pymongo pattern.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re

import pytest

from jlab import cache as _cache
from jlab import purge as _purge
from jlab.cli import main
from jlab.cli._errors import EXIT_USER_ERROR, CliError

_KEY_ENV = "JLAB_CACHE_KEY"
_TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(_KEY_ENV, _TEST_KEY)
    return _TEST_KEY


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int = 1):
        self._docs = sorted(
            self._docs,
            key=lambda d: (d.get(field) is None, d.get(field)),
            reverse=direction < 0,
        )
        return self

    def limit(self, n: int):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


def _matches(doc: dict, flt: dict) -> bool:
    for field, want in flt.items():
        got = doc.get(field)
        if isinstance(want, dict):
            for op, operand in want.items():
                if op == "$lt":
                    if got is None or not got < operand:
                        return False
                elif op == "$ne":
                    if got == operand:
                        return False
                else:  # pragma: no cover - unsupported operator in a test fake
                    raise AssertionError(f"fake collection got operator {op!r}")
        elif got != want:
            return False
    return True


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, "_FakeCollection"] = {}

    def __getitem__(self, name: str) -> "_FakeCollection":
        if name not in self.collections:
            self.collections[name] = _FakeCollection(database=self)
        return self.collections[name]


class _FakeCollection:
    """Enough of a pymongo collection for the cache + purge + coverage layers."""

    def __init__(self, database: "_FakeDatabase | None" = None) -> None:
        self.docs: dict[str, dict] = {}
        self.deleted_filters: list[dict] = []
        self.before_delete: list = []  # hooks: called with the filter before a delete
        self.before_write: list = []  # hooks: called with the _id before an update
        # Sibling collections (coverage, suppression) share one database, as a
        # real pymongo Collection's ``.database`` does.
        self.database = database if database is not None else _FakeDatabase()

    # -- writes
    def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        _id = flt["_id"]
        for hook in list(self.before_write):
            hook(_id)
        doc = self.docs.get(_id)
        if doc is None:
            if not upsert:
                return
            doc = {"_id": _id}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[_id] = doc
        doc.update(update.get("$set", {}))

    def delete_one(self, flt: dict) -> None:
        self.docs.pop(flt["_id"], None)

    def delete_many(self, flt: dict):
        for hook in list(self.before_delete):
            hook(flt)
        self.deleted_filters.append(dict(flt))
        doomed = [k for k, d in self.docs.items() if _matches(d, flt)]
        for k in doomed:
            del self.docs[k]
        return type("_Res", (), {"deleted_count": len(doomed)})()

    # -- reads
    def find(self, flt: dict | None = None) -> _FakeCursor:
        flt = flt or {}
        out = [d for d in self.docs.values() if _matches(d, flt)]
        return _FakeCursor([dict(d) for d in out])

    def find_one(self, flt: dict) -> dict | None:
        for doc in self.find(flt):
            return doc
        return None

    def count_documents(self, flt: dict) -> int:
        return len(list(self.find(flt)))

    def distinct(self, field: str, flt: dict | None = None) -> list:
        return sorted({d.get(field) for d in self.find(flt or {}) if d.get(field) is not None})


def _message(
    mid: str = "1",
    *,
    content: str = "hello world",
    created: str = "2026-09-01T12:00:00+00:00",
    author: str = "42",
) -> dict:
    return {
        "id": mid,
        "author": {"id": author, "name": "someone", "bot": False},
        "content": content,
        "created_at": created,
        "edited_at": None,
        "channel": {"id": "chan-1", "name": "general"},
        "jump_url": f"https://discord.com/channels/1/chan-1/{mid}",
        "attachments": [],
        "embeds": [],
        "thread": None,
    }


def _reports_tree(tmp_path, *, author: str = "42", channel: str = "777") -> list:
    """Build a members + links report tree in the shape the real ones take.

    One run per report family mentions *author* / *channel*; one run mentions
    neither, and must survive any purge (a purge that wipes the whole report
    tree is not a targeted deletion).
    """
    members = tmp_path / "reports" / "members"
    links = tmp_path / "reports" / "links"
    hit_m = members / "20260901T101112Z-aaaaaaaa"
    hit_m.mkdir(parents=True)
    (hit_m / "members-report.html").write_text(
        f"<html><body><tr><td>{author}</td><td>someone</td></tr></body></html>",
        encoding="utf-8",
    )
    (hit_m / "members.csv").write_text(f"author_id,messages\n{author},12\n", encoding="utf-8")

    miss_m = members / "20260902T101112Z-bbbbbbbb"
    miss_m.mkdir(parents=True)
    (miss_m / "members-report.html").write_text("<html>99 only</html>", encoding="utf-8")

    hit_l = links / "20260901T101112Z-cccccccc"
    hit_l.mkdir(parents=True)
    (hit_l / "links.csv").write_text(
        f"url,channel,author_id,jump_url\nhttps://x/,general,{author},"
        f"https://discord.com/channels/1/{channel}/9\n",
        encoding="utf-8",
    )
    return [members, links], hit_m, miss_m, hit_l


# ---------------------------------------------------------------------------
# Target validation — an accidental invocation must not wipe the cache
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", "*", "all", ".*", "%", "42*", "42 43", "-1"])
def test_wildcard_or_empty_target_is_refused(bad: str) -> None:
    with pytest.raises(CliError) as excinfo:
        _purge.validate_target(bad, "author id")
    assert excinfo.value.code == EXIT_USER_ERROR
    assert excinfo.value.remediation


def test_a_plain_snowflake_is_accepted() -> None:
    assert _purge.validate_target(" 42 ", "author id") == "42"


def test_purge_author_refuses_a_wildcard_before_touching_the_collection(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message("1")], collection=col)
    with pytest.raises(CliError):
        _purge.purge_author("*", collection=col, report_dirs=[])
    assert len(col.docs) == 1
    assert col.deleted_filters == []


# ---------------------------------------------------------------------------
# Cache-side deletion
# ---------------------------------------------------------------------------


def test_delete_by_author_removes_only_that_author(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [_message("1", author="42"), _message("2", author="99"), _message("3", author="42")],
        collection=col,
    )
    result = _cache.delete_by_author("42", collection=col)
    assert result["deleted"] == 2
    left = _cache.fetch_messages(collection=col)
    assert [m["author_id"] for m in left] == ["99"]


def test_delete_by_channel_removes_only_that_channel(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message("1")], collection=col)
    _cache.store_messages("chan-2", [_message("2")], collection=col)
    result = _cache.delete_by_channel("chan-2", collection=col)
    assert result["deleted"] == 1
    assert [m["channel_id"] for m in _cache.fetch_messages(collection=col)] == ["chan-1"]


def test_delete_dry_run_counts_but_removes_nothing(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message("1"), _message("2")], collection=col)
    result = _cache.delete_by_author("42", collection=col, dry_run=True)
    assert result["matched"] == 2
    assert result["deleted"] == 0
    assert len(col.docs) == 2
    assert col.deleted_filters == []


def test_delete_older_than_bounds_retention(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [
            _message("old", created="2020-01-01T00:00:00+00:00"),
            _message("new", created="2026-09-01T00:00:00+00:00"),
        ],
        collection=col,
    )
    cutoff = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    result = _cache.delete_older_than(cutoff, collection=col)
    assert result["deleted"] == 1
    assert [m["message_id"] for m in _cache.fetch_messages(collection=col)] == ["new"]


# ---------------------------------------------------------------------------
# Derived reports
# ---------------------------------------------------------------------------


def test_report_sweep_removes_only_runs_carrying_the_target(tmp_path) -> None:
    roots, hit_m, miss_m, hit_l = _reports_tree(tmp_path)
    result = _purge.sweep_reports("42", report_dirs=roots)
    assert not hit_m.exists()
    assert not hit_l.exists()
    assert miss_m.exists()
    assert sorted(result["runs_removed"]) == sorted([str(hit_l), str(hit_m)])
    assert result["runs_scanned"] == 3


def test_report_sweep_dry_run_removes_nothing(tmp_path) -> None:
    roots, hit_m, _miss, hit_l = _reports_tree(tmp_path)
    result = _purge.sweep_reports("42", report_dirs=roots, dry_run=True)
    assert hit_m.exists() and hit_l.exists()
    assert sorted(result["runs_matched"]) == sorted([str(hit_l), str(hit_m)])
    assert result["runs_removed"] == []


def test_report_sweep_matches_a_channel_id_in_a_jump_url(tmp_path) -> None:
    roots, _hit_m, _miss, hit_l = _reports_tree(tmp_path, channel="777")
    _purge.sweep_reports("777", report_dirs=roots)
    assert not hit_l.exists()


def test_report_sweep_tolerates_binary_and_missing_roots(tmp_path) -> None:
    roots, hit_m, _miss, _hit_l = _reports_tree(tmp_path)
    (hit_m / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
    result = _purge.sweep_reports("42", report_dirs=roots + [tmp_path / "nope"])
    assert not hit_m.exists()
    assert result["runs_removed"]


# ---------------------------------------------------------------------------
# o15 — fetch, purge, then search finds nothing for that author
# ---------------------------------------------------------------------------


def _search(collection, pattern: str) -> list[dict]:
    """The search t8 will ship, in miniature: decrypt, then match client-side."""
    rx = re.compile(pattern)
    return [m for m in _cache.fetch_messages(collection=collection) if rx.search(m["content"])]


def test_fetch_then_purge_then_search_finds_nothing_for_that_author(key: str, tmp_path) -> None:
    col = _FakeCollection()
    roots, hit_m, miss_m, hit_l = _reports_tree(tmp_path, author="42")

    # fetch
    _cache.store_messages(
        "chan-1",
        [
            _message("1", author="42", content="please delete my secret confession"),
            _message("2", author="99", content="unrelated secret"),
        ],
        collection=col,
    )
    assert len(_search(col, "secret")) == 2
    assert [m["author_id"] for m in _search(col, "confession")] == ["42"]

    # purge
    result = _purge.purge_author("42", collection=col, report_dirs=roots)
    assert result["target"] == {"kind": "author", "value": "42"}
    assert result["cache"]["deleted"] == 1
    assert result["reports"]["runs_removed"]

    # search — nothing for that author, anywhere
    assert _search(col, "confession") == []
    assert [m["author_id"] for m in _search(col, "secret")] == ["99"]
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                assert "42" not in path.read_text(encoding="utf-8", errors="ignore")
    assert not hit_m.exists() and not hit_l.exists()
    assert miss_m.exists()


def test_purge_channel_clears_cache_and_reports(key: str, tmp_path) -> None:
    col = _FakeCollection()
    roots, _hit_m, _miss, hit_l = _reports_tree(tmp_path, channel="777")
    _cache.store_messages("777", [_message("1")], collection=col)
    _cache.store_messages("888", [_message("2")], collection=col)
    result = _purge.purge_channel("777", collection=col, report_dirs=roots)
    assert result["cache"]["deleted"] == 1
    assert not hit_l.exists()
    assert [m["channel_id"] for m in _cache.fetch_messages(collection=col)] == ["888"]


def test_purge_older_than_prunes_cache_and_stale_report_runs(key: str, tmp_path) -> None:
    col = _FakeCollection()
    roots, hit_m, miss_m, _hit_l = _reports_tree(tmp_path)
    _cache.store_messages(
        "chan-1",
        [
            _message("old", created="2020-01-01T00:00:00+00:00"),
            _message("new", created="2026-09-10T00:00:00+00:00"),
        ],
        collection=col,
    )
    now = dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc)
    result = _purge.purge_older_than(5, collection=col, report_dirs=roots, now=now)
    assert result["cache"]["deleted"] == 1
    # every seeded run id predates the cutoff, so every run directory goes
    assert not hit_m.exists() and not miss_m.exists()
    assert [m["message_id"] for m in _cache.fetch_messages(collection=col)] == ["new"]


def test_purge_older_than_refuses_an_overflowing_window(key: str) -> None:
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _purge.purge_older_than(10**12, collection=col, report_dirs=[])
    assert excinfo.value.code == EXIT_USER_ERROR


def test_purge_older_than_refuses_a_non_positive_window(key: str) -> None:
    col = _FakeCollection()
    with pytest.raises(CliError) as excinfo:
        _purge.purge_older_than(0, collection=col, report_dirs=[])
    assert excinfo.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# The CLI verb — a real runnable command, not a documented Mongo query
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_env(monkeypatch: pytest.MonkeyPatch, key: str, tmp_path):
    """Point the purge verb at a fake collection and a temp report tree."""
    col = _FakeCollection()
    roots, hit_m, miss_m, hit_l = _reports_tree(tmp_path)
    _cache.store_messages(
        "123",
        [_message("1", author="42"), _message("2", author="99")],
        collection=col,
    )

    class _Ctx:
        def __enter__(self):
            return col

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(_purge._mongo, "message_collection", lambda *a, **k: _Ctx())
    monkeypatch.setattr(_purge, "_report_roots", lambda: list(roots))
    return col, roots, hit_m, miss_m, hit_l


def test_cli_purge_requires_a_target(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "purge"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "Traceback" not in err


def test_cli_purge_without_yes_is_a_preview(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    col, _roots, hit_m, _miss, _hit_l = cli_env
    rc = main(["discord", "purge", "--author", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out.lower()
    assert len(col.docs) == 2
    assert hit_m.exists()


def test_cli_purge_with_yes_deletes_and_reports_json(
    cli_env, capsys: pytest.CaptureFixture[str]
) -> None:
    col, _roots, hit_m, miss_m, hit_l = cli_env
    rc = main(["discord", "purge", "--author", "42", "--yes", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == {"kind": "author", "value": "42"}
    assert payload["dry_run"] is False
    assert payload["cache"]["deleted"] == 1
    assert sorted(payload["reports"]["runs_removed"]) == sorted([str(hit_l), str(hit_m)])
    assert [d["author_id"] for d in col.docs.values()] == ["99"]
    assert miss_m.exists()


def test_cli_purge_rejects_two_targets(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["discord", "purge", "--author", "42", "--channel", "777", "--yes"])
    assert rc == 1
    assert capsys.readouterr().err.startswith("error:")


def test_cli_purge_rejects_a_wildcard_author(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    col, *_ = cli_env
    rc = main(["discord", "purge", "--author", "*", "--yes"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "hint:" in err
    assert len(col.docs) == 2


def test_cli_purge_channel_runs(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    col, *_ = cli_env
    rc = main(["discord", "purge", "--channel", "555", "--yes", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == {"kind": "channel", "value": "555"}
    assert payload["cache"]["deleted"] == 0
    rc = main(["discord", "purge", "--channel", "123", "--yes", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cache"]["deleted"] == 2
    assert col.docs == {}


def test_cli_purge_older_than_runs(cli_env, capsys: pytest.CaptureFixture[str]) -> None:
    col, *_ = cli_env
    rc = main(["discord", "purge", "--older-than", "36500", "--yes", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["kind"] == "older_than"
    assert payload["cache"]["deleted"] == 0
    assert len(col.docs) == 2


def test_explain_discord_purge_resolves(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["explain", "discord", "purge"])
    assert rc == 0
    assert "discord purge" in capsys.readouterr().out


# ===========================================================================
# Wave-3 integration: purge x coverage x suppression (t6 + t12 + deviation d3)
# ===========================================================================

import fcntl  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402

from jlab import coverage as _coverage  # noqa: E402
from jlab import crypto as _crypto  # noqa: E402
from jlab import mongo as _mongo  # noqa: E402
from jlab.cli._errors import EXIT_ENV_ERROR  # noqa: E402

UTC = dt.timezone.utc
_RAW_AUTHOR = "123456789012345678"  # long enough that a hex digest can't contain it by chance


@pytest.fixture(autouse=True)
def lock_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Every purge test locks under a temp state home — never ~/.jlab."""
    home = tmp_path / "state"
    monkeypatch.setenv(_coverage.STATE_HOME_ENV, str(home))
    _coverage.release_all_locks()
    yield home
    _coverage.release_all_locks()


def _cov(col: _FakeCollection) -> _FakeCollection:
    return col.database[_mongo.COVERAGE_COLLECTION]


def _sup(col: _FakeCollection) -> _FakeCollection:
    return col.database[_mongo.SUPPRESSION_COLLECTION]


def _at(year: int, month: int, day: int = 1) -> dt.datetime:
    return dt.datetime(year, month, day, tzinfo=UTC)


def _iv(a: dt.datetime, b: dt.datetime) -> _coverage.Interval:
    return _coverage.Interval(a, b)


def _lock_held_elsewhere(channel: str) -> bool:
    """True when some *other* open file description holds *channel*'s flock."""
    path = _coverage.lock_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


# -- 1. channel purge clears coverage ----------------------------------------


def test_purge_channel_leaves_its_window_uncovered(key: str) -> None:
    col = _FakeCollection()
    window = _iv(_at(2026, 8), _at(2026, 9))
    _cache.store_messages(
        "777", [_message("1", created="2026-08-15T00:00:00+00:00")], collection=col
    )
    _coverage.widen_coverage("777", window, collection=_cov(col))
    _coverage.widen_coverage("888", window, collection=_cov(col))

    result = _purge.purge_channel("777", collection=col, report_dirs=[])

    described = _coverage.describe("777", window, collection=_cov(col))
    assert described["complete"] is False
    assert described["uncovered"] == [window.to_dict()]
    # another channel's coverage is untouched
    assert _coverage.describe("888", window, collection=_cov(col))["complete"] is True
    assert result["coverage"] == {"channels": ["777"], "applied": True}


def test_purge_channel_takes_the_channel_lock_around_the_delete(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("777", [_message("1")], collection=col)
    _coverage.widen_coverage("777", _iv(_at(2026, 8), _at(2026, 9)), collection=_cov(col))
    seen: list[bool] = []
    col.before_delete.append(lambda flt: seen.append(_lock_held_elsewhere("777")))
    # _lock_held_elsewhere opens a fresh fd, so it sees our own process's flock
    _purge.purge_channel("777", collection=col, report_dirs=[])
    assert seen == [True]


# -- 2. retention purge trims coverage before the cutoff ---------------------


def test_purge_older_than_uncovers_spans_before_the_cutoff_for_every_channel(key: str) -> None:
    col = _FakeCollection()
    now = _at(2026, 9, 12)
    cutoff = now - dt.timedelta(days=5)
    _cache.store_messages(
        "777",
        [
            _message("old7", created="2020-06-01T00:00:00+00:00"),
            _message("new7", created="2026-09-10T00:00:00+00:00"),
        ],
        collection=col,
    )
    _cache.store_messages(
        "888", [_message("old8", created="2019-03-01T00:00:00+00:00")], collection=col
    )
    _coverage.widen_coverage("777", _iv(_at(2020, 1), _at(2026, 9, 10)), collection=_cov(col))
    _coverage.widen_coverage("888", _iv(_at(2019, 1), _at(2020, 6)), collection=_cov(col))
    _coverage.widen_coverage("888", _iv(_at(2026, 9, 1), _at(2026, 9, 11)), collection=_cov(col))
    # a coverage-only channel (its old messages already gone) is trimmed too
    _coverage.widen_coverage("999", _iv(_at(2018, 1), _at(2018, 2)), collection=_cov(col))

    result = _purge.purge_older_than(5, collection=col, report_dirs=[], now=now)

    assert result["cache"]["deleted"] == 2
    assert sorted(m["message_id"] for m in _cache.fetch_messages(collection=col)) == ["new7"]
    for channel in ("777", "888", "999"):
        before = _coverage.describe(channel, _iv(_at(2017, 1), cutoff), collection=_cov(col))
        assert before["complete"] is False, channel
        assert all(c["start"] == cutoff.isoformat() for c in before["covered"]), channel
    # spans after the cutoff are untouched
    assert _coverage.read_coverage("777", collection=_cov(col)) == [_iv(cutoff, _at(2026, 9, 10))]
    assert _coverage.read_coverage("888", collection=_cov(col)) == [_iv(cutoff, _at(2026, 9, 11))]
    assert _coverage.read_coverage("999", collection=_cov(col)) == []
    assert result["coverage"] == {"channels": ["777", "888", "999"], "applied": True}


def test_purge_older_than_deletes_each_channel_under_its_own_lock(key: str) -> None:
    col = _FakeCollection()
    now = _at(2026, 9, 12)
    _cache.store_messages(
        "777", [_message("a", created="2020-01-01T00:00:00+00:00")], collection=col
    )
    _cache.store_messages(
        "888", [_message("b", created="2020-01-01T00:00:00+00:00")], collection=col
    )
    seen: list[tuple[str, bool]] = []

    def probe(flt: dict) -> None:
        channel = flt.get("channel_id")
        seen.append((channel, channel is not None and _lock_held_elsewhere(channel)))

    col.before_delete.append(probe)
    _purge.purge_older_than(5, collection=col, report_dirs=[], now=now)
    assert seen == [("777", True), ("888", True)]


# -- 3. purge cannot interleave with a fetch holding the lock ----------------


def _hold_lock(channel: str):
    path = _coverage.lock_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)  # a different open file description: a "fetch"
    return fd


def _release(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_non_blocking_purge_channel_refuses_while_a_fetch_holds_the_lock(key: str) -> None:
    col = _FakeCollection()
    window = _iv(_at(2026, 8), _at(2026, 9))
    _cache.store_messages("777", [_message("1")], collection=col)
    _coverage.widen_coverage("777", window, collection=_cov(col))
    fd = _hold_lock("777")
    try:
        with pytest.raises(CliError) as excinfo:
            _purge.purge_channel("777", collection=col, report_dirs=[], blocking=False)
    finally:
        _release(fd)
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert len(col.docs) == 1
    assert _coverage.describe("777", window, collection=_cov(col))["complete"] is True


def test_purge_channel_waits_for_a_fetch_holding_the_lock(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("777", [_message("1")], collection=col)
    fd = _hold_lock("777")
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["result"] = _purge.purge_channel("777", collection=col, report_dirs=[])
        except BaseException as exc:  # surfaced below
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    try:
        thread.start()
        thread.join(0.5)
        assert thread.is_alive(), "purge ran while a fetch held the channel lock"
        assert len(col.docs) == 1
    finally:
        _release(fd)
        thread.join(5)
    assert "error" not in outcome, outcome.get("error")
    assert col.docs == {}


def test_non_blocking_purge_older_than_refuses_while_a_fetch_holds_a_channel(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "777", [_message("1", created="2020-01-01T00:00:00+00:00")], collection=col
    )
    fd = _hold_lock("777")
    try:
        with pytest.raises(CliError) as excinfo:
            _purge.purge_older_than(
                5, collection=col, report_dirs=[], now=_at(2026, 9, 12), blocking=False
            )
    finally:
        _release(fd)
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert len(col.docs) == 1


# -- 4. author purge records a keyed-hash suppression ------------------------


def test_purge_author_suppresses_later_stores_and_keeps_no_raw_id(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("777", [_message("1", author=_RAW_AUTHOR)], collection=col)

    result = _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    assert result["suppression"] == {"recorded": True, "already_present": False}
    assert result["coverage"] == {"channels": [], "applied": False}

    summary = _cache.store_messages(
        "777",
        [
            _message("1", author=_RAW_AUTHOR),
            _message("2", author=_RAW_AUTHOR),
            _message("3", author="99"),
        ],
        collection=col,
    )
    assert summary == {"stored": 1, "suppressed": 2}
    assert [d["author_id"] for d in col.docs.values()] == ["99"]

    records = list(_sup(col).docs.values())
    assert len(records) == 1
    for record in records:
        assert _RAW_AUTHOR not in repr(record)


def test_suppression_digest_is_hmac_under_an_hkdf_subkey_not_the_content_key(key: str) -> None:
    digest = _crypto.author_digest(_RAW_AUTHOR)
    passphrase = key.encode()
    msg = _RAW_AUTHOR.encode()
    assert digest == _crypto.author_digest(_RAW_AUTHOR)  # deterministic
    assert digest != hashlib.sha256(msg).hexdigest()
    assert digest != hmac.new(passphrase, msg, hashlib.sha256).hexdigest()
    assert digest != hmac.new(_crypto._content_key(), msg, hashlib.sha256).hexdigest()
    assert _crypto._suppression_key() != _crypto._content_key()
    assert _crypto._suppression_key() == _crypto._hkdf(passphrase, _crypto._INFO_SUPPRESSION, 32)
    assert digest == hmac.new(_crypto._suppression_key(), msg, hashlib.sha256).hexdigest()


def test_suppressing_the_same_author_twice_leaves_one_record(key: str) -> None:
    col = _FakeCollection()
    _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    first = dict(next(iter(_sup(col).docs.values())))
    again = _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    assert again["suppression"] == {"recorded": True, "already_present": True}
    assert len(_sup(col).docs) == 1
    assert next(iter(_sup(col).docs.values())) == first  # suppressed_at not rewritten


def test_purge_author_without_a_key_fails_before_deleting(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    col = _FakeCollection()
    _cache.store_messages("777", [_message("1", author=_RAW_AUTHOR)], collection=col)
    monkeypatch.delenv(_KEY_ENV)
    with pytest.raises(CliError) as excinfo:
        _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert _sup(col).docs == {}
    assert len(col.docs) == 1


def test_author_purge_does_not_change_coverage(key: str) -> None:
    col = _FakeCollection()
    window = _iv(_at(2026, 8), _at(2026, 9))
    _coverage.widen_coverage("777", window, collection=_cov(col))
    _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    assert _coverage.describe("777", window, collection=_cov(col))["complete"] is True


def test_a_store_racing_the_purge_does_not_resurrect_the_author(key: str) -> None:
    """Purge lands between store's suppression check and its write."""
    col = _FakeCollection()
    fired: list[str] = []

    def purge_mid_write(_id: str) -> None:
        if not fired:
            fired.append(_id)
            _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])

    col.before_write.append(purge_mid_write)
    summary = _cache.store_messages(
        "777",
        [_message("1", author=_RAW_AUTHOR), _message("2", author="99")],
        collection=col,
    )
    assert fired
    assert [d["author_id"] for d in col.docs.values()] == ["99"]
    assert summary == {"stored": 1, "suppressed": 1}


def test_suppression_recorded_under_another_key_blocks_every_store(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rotated key would silently stop matching old digests — fail closed instead."""
    col = _FakeCollection()
    _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[])
    monkeypatch.setenv(_KEY_ENV, base64.urlsafe_b64encode(b"z" * 32).decode())
    with pytest.raises(CliError) as excinfo:
        _cache.store_messages("777", [_message("1", author=_RAW_AUTHOR)], collection=col)
    assert excinfo.value.code == EXIT_ENV_ERROR
    assert col.docs == {}


# -- 5. o15 end to end, now with a second fetch ------------------------------


def test_fetch_purge_search_then_fetch_again_still_finds_nothing(key: str, tmp_path) -> None:
    col = _FakeCollection()
    roots, hit_m, _miss, hit_l = _reports_tree(tmp_path, author=_RAW_AUTHOR)
    window = _iv(_at(2026, 8), _at(2026, 9))
    discord = [
        _message(
            "1",
            author=_RAW_AUTHOR,
            content="my secret confession",
            created="2026-08-10T00:00:00+00:00",
        ),
        _message("2", author="99", content="unrelated secret", created="2026-08-11T00:00:00+00:00"),
    ]

    def fetch(span):
        return list(discord), True, None

    def store(span, messages):
        return _cache.store_messages("777", messages, collection=col)

    now = _at(2026, 9, 12)
    _coverage.fetch_missing("777", window, fetch=fetch, store=store, collection=_cov(col), now=now)
    assert len(_search(col, "secret")) == 2

    _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=roots)
    assert _search(col, "confession") == []
    assert not hit_m.exists() and not hit_l.exists()

    # fetch again: a gap-only fetch over a now-uncovered window, and a plain re-store
    _coverage.clear_coverage("777", collection=_cov(col))
    _coverage.fetch_missing("777", window, fetch=fetch, store=store, collection=_cov(col), now=now)
    _cache.store_messages("777", discord, collection=col)
    assert _search(col, "confession") == []
    assert [m["author_id"] for m in _search(col, "secret")] == ["99"]


# -- 6. dry run changes nothing ----------------------------------------------


@pytest.mark.parametrize("kind", ["author", "channel", "older_than"])
def test_dry_run_changes_nothing_including_coverage_and_suppression(
    key: str, kind: str, lock_home
) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "777",
        [_message("1", author=_RAW_AUTHOR, created="2020-01-01T00:00:00+00:00")],
        collection=col,
    )
    _coverage.widen_coverage("777", _iv(_at(2019, 1), _at(2026, 9)), collection=_cov(col))
    _coverage.release_all_locks()
    lock_files_before = sorted(p.name for p in lock_home.rglob("*"))
    docs_before = {k: dict(v) for k, v in col.docs.items()}
    cov_before = {k: dict(v) for k, v in _cov(col).docs.items()}

    if kind == "author":
        result = _purge.purge_author(_RAW_AUTHOR, collection=col, report_dirs=[], dry_run=True)
        assert result["suppression"] == {"recorded": False, "already_present": None}
    elif kind == "channel":
        result = _purge.purge_channel("777", collection=col, report_dirs=[], dry_run=True)
        assert result["coverage"] == {"channels": ["777"], "applied": False}
    else:
        result = _purge.purge_older_than(
            5, collection=col, report_dirs=[], dry_run=True, now=_at(2026, 9, 12)
        )
        assert result["coverage"] == {"channels": ["777"], "applied": False}

    assert col.docs == docs_before
    assert col.deleted_filters == []
    assert _cov(col).docs == cov_before
    assert _sup(col).docs == {}
    assert sorted(p.name for p in lock_home.rglob("*")) == lock_files_before


# -- CLI surface: additive fields only ---------------------------------------


def test_cli_purge_json_reports_coverage_and_suppression(
    cli_env, capsys: pytest.CaptureFixture[str]
) -> None:
    col, *_ = cli_env
    _coverage.widen_coverage("123", _iv(_at(2026, 8), _at(2026, 9)), collection=_cov(col))
    assert main(["discord", "purge", "--author", "42", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["suppression"]["recorded"] is True
    assert main(["discord", "purge", "--channel", "123", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["coverage"] == {"channels": ["123"], "applied": True}
    assert _cov(col).docs == {}


def test_cli_purge_text_mentions_coverage_and_suppression(
    cli_env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["discord", "purge", "--author", "42", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "suppression:" in out
    assert "keyed hash" in out
    assert "coverage:" in out

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
                else:  # pragma: no cover - unsupported operator in a test fake
                    raise AssertionError(f"fake collection got operator {op!r}")
        elif got != want:
            return False
    return True


class _FakeCollection:
    """Enough of a pymongo collection for the cache + purge layers."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.deleted_filters: list[dict] = []

    # -- writes
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

    def delete_one(self, flt: dict) -> None:
        self.docs.pop(flt["_id"], None)

    def delete_many(self, flt: dict):
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

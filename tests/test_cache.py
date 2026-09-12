"""Tests for ``jlab.crypto`` and ``jlab.cache`` — the encrypted message cache.

Compliance-critical (o16): message content is encrypted **at the application
layer** on store and decrypted on fetch, so encryption at rest does not depend
on the MongoDB edition's storage engine (community edition ships no encrypted
storage engine). The failure mode these tests exist to prevent is a *silent*
one: content written to jlab-mongodb in plaintext because the key was missing.
That is asserted directly — a missing key must raise, and must leave the
collection untouched.

No network calls: pymongo is lazy-imported behind ``jlab.mongo._seam`` and the
collection handle is injected, mirroring tests/test_mongo.py's fake-pymongo
pattern.
"""

from __future__ import annotations

import base64
import datetime as dt
import pathlib

import pytest

from jlab import cache as _cache
from jlab import crypto as _crypto
from jlab.cli._errors import CliError

_KEY_ENV = "JLAB_CACHE_KEY"
_TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
_OTHER_KEY = base64.urlsafe_b64encode(b"j" * 32).decode()


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(_KEY_ENV, _TEST_KEY)
    return _TEST_KEY


# ---------------------------------------------------------------------------
# Fakes — an in-memory stand-in for a pymongo collection.
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


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, "_FakeCollection"] = {}

    def __getitem__(self, name: str) -> "_FakeCollection":
        if name not in self.collections:
            self.collections[name] = _FakeCollection(database=self)
        return self.collections[name]


class _FakeCollection:
    """Enough of a pymongo collection for the cache layer, and nothing more."""

    def __init__(self, database: "_FakeDatabase | None" = None) -> None:
        self.docs: dict[str, dict] = {}
        self.index_calls: list[tuple] = []
        # Sibling collections (suppression list) live in the same database, as
        # with a real pymongo Collection's ``.database``.
        self.database = database if database is not None else _FakeDatabase()

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

    def create_index(self, *a, **kw) -> None:
        self.index_calls.append((a, kw))

    # -- reads
    def find(self, flt: dict | None = None) -> _FakeCursor:
        flt = flt or {}
        out = [d for d in self.docs.values() if all(d.get(k) == v for k, v in flt.items())]
        return _FakeCursor([dict(d) for d in out])

    def find_one(self, flt: dict) -> dict | None:
        for doc in self.find(flt):
            return doc
        return None


def _message(
    mid: str = "1",
    *,
    content: str = "hello world",
    created: str = "2026-09-01T12:00:00+00:00",
    edited: str | None = None,
    author: str = "42",
) -> dict:
    """A message dict in the shape ``jlab.cli._discord._serialize_message`` emits."""
    return {
        "id": mid,
        "author": {"id": author, "name": "someone", "bot": False},
        "content": content,
        "created_at": created,
        "edited_at": edited,
        "channel": {"id": "chan-1", "name": "general"},
        "jump_url": f"https://discord.com/channels/1/chan-1/{mid}",
        "attachments": [],
        "embeds": [],
        "thread": None,
    }


# ---------------------------------------------------------------------------
# jlab.crypto — key handling (a missing key must never fall back to plaintext)
# ---------------------------------------------------------------------------


def test_missing_key_raises_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_KEY_ENV, raising=False)
    with pytest.raises(CliError) as exc:
        _crypto.encrypt("secret")
    assert exc.value.code == 2
    assert _KEY_ENV in exc.value.message
    assert exc.value.remediation


def test_blank_key_raises_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_ENV, "   ")
    with pytest.raises(CliError) as exc:
        _crypto.encrypt("secret")
    assert exc.value.code == 2


def test_short_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A too-short key is refused outright rather than silently weakening storage."""
    monkeypatch.setenv(_KEY_ENV, "abc")
    with pytest.raises(CliError) as exc:
        _crypto.encrypt("secret")
    assert exc.value.code == 2
    assert exc.value.remediation


def test_key_env_var_is_read_only_in_the_crypto_module() -> None:
    """``JLAB_CACHE_KEY`` has one choke point, mirroring ``JLAB_MONGO_URI``."""
    import ast

    root = pathlib.Path(_cache.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "crypto.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # A prose mention in a docstring is a different node value; only an
            # exact string literal (what an os.environ lookup would use) counts.
            if isinstance(node, ast.Constant) and node.value == _KEY_ENV:
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"{_KEY_ENV} read outside jlab/crypto.py: {offenders}"


# ---------------------------------------------------------------------------
# jlab.crypto — the envelope
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip(key: str) -> None:
    env = _crypto.encrypt("hello world")
    assert _crypto.decrypt(env) == "hello world"


def test_encrypt_round_trips_unicode_and_empty(key: str) -> None:
    for text in ["", "héllo — ünicode ✅", "x" * 5000]:
        assert _crypto.decrypt(_crypto.encrypt(text)) == text


def test_ciphertext_does_not_contain_plaintext(key: str) -> None:
    env = _crypto.encrypt("jetson orin nano supersecret")
    blob = repr(env)
    assert "jetson" not in blob
    assert "supersecret" not in blob
    assert base64.b64decode(env["c"]) != b"jetson orin nano supersecret"


def test_encryption_is_randomised_per_call(key: str) -> None:
    a = _crypto.encrypt("same text")
    b = _crypto.encrypt("same text")
    assert a["c"] != b["c"]
    assert a["n"] != b["n"]


def test_tampered_ciphertext_is_rejected(key: str) -> None:
    env = _crypto.encrypt("hello world")
    raw = bytearray(base64.b64decode(env["c"]))
    raw[0] ^= 0xFF
    env["c"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(CliError) as exc:
        _crypto.decrypt(env)
    assert exc.value.code == 2


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_ENV, _TEST_KEY)
    env = _crypto.encrypt("hello world")
    monkeypatch.setenv(_KEY_ENV, _OTHER_KEY)
    with pytest.raises(CliError) as exc:
        _crypto.decrypt(env)
    assert exc.value.code == 2
    assert exc.value.remediation


def test_unknown_envelope_version_is_rejected(key: str) -> None:
    env = _crypto.encrypt("hello world")
    env["v"] = 99
    with pytest.raises(CliError) as exc:
        _crypto.decrypt(env)
    assert exc.value.code == 2


def test_decrypt_rejects_a_plaintext_string(key: str) -> None:
    """A raw ``str`` where an envelope belongs is an error, not a pass-through."""
    with pytest.raises(CliError):
        _crypto.decrypt("hello world")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# jlab.cache — store (the plaintext-on-missing-key failure mode)
# ---------------------------------------------------------------------------


def test_store_without_key_writes_nothing_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of this task: no key means no write, never a plaintext write."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    col = _FakeCollection()
    with pytest.raises(CliError) as exc:
        _cache.store_messages("chan-1", [_message()], collection=col)
    assert exc.value.code == 2
    assert col.docs == {}


def test_stored_document_holds_no_plaintext(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message(content="orin nano devkit")], collection=col)
    raw = col.docs["1"]
    assert "orin nano devkit" not in repr(raw)
    assert not isinstance(raw["content"], str)
    # AES-GCM folds the authentication tag into the ciphertext, so the
    # envelope carries version, nonce and sealed bytes only.
    assert set(raw["content"]) >= {"v", "n", "c"}


def test_store_and_fetch_round_trip(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message(content="orin nano devkit")], collection=col)
    got = _cache.fetch_messages("chan-1", collection=col)
    assert [m["content"] for m in got] == ["orin nano devkit"]
    assert got[0]["message_id"] == "1"
    assert got[0]["author_id"] == "42"


def test_fetch_without_key_raises_rather_than_returning_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_KEY_ENV, _TEST_KEY)
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message()], collection=col)
    monkeypatch.delenv(_KEY_ENV, raising=False)
    with pytest.raises(CliError) as exc:
        _cache.fetch_messages("chan-1", collection=col)
    assert exc.value.code == 2


def test_fetch_is_scoped_to_the_channel_and_ordered_by_created(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [
            _message("2", content="second", created="2026-09-02T00:00:00+00:00"),
            _message("1", content="first", created="2026-09-01T00:00:00+00:00"),
        ],
        collection=col,
    )
    _cache.store_messages("chan-2", [_message("3", content="elsewhere")], collection=col)
    got = _cache.fetch_messages("chan-1", collection=col)
    assert [m["content"] for m in got] == ["first", "second"]


def test_fetch_limit_applies(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [_message(str(i), created=f"2026-09-0{i}T00:00:00+00:00") for i in range(1, 5)],
        collection=col,
    )
    assert len(_cache.fetch_messages("chan-1", limit=2, collection=col)) == 2


# ---------------------------------------------------------------------------
# jlab.cache — the three timestamps (created / updated / stored)
# ---------------------------------------------------------------------------


def test_document_carries_created_updated_and_stored(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [_message(created="2026-09-01T12:00:00+00:00", edited="2026-09-03T08:30:00+00:00")],
        collection=col,
    )
    doc = col.docs["1"]
    assert doc["created_at"] == dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert doc["updated_at"] == dt.datetime(2026, 9, 3, 8, 30, tzinfo=dt.timezone.utc)
    assert isinstance(doc["stored_at"], dt.datetime)
    assert doc["stored_at"].tzinfo is not None


def test_updated_is_null_when_unedited(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message(edited=None)], collection=col)
    assert col.docs["1"]["updated_at"] is None
    assert "updated_at" in col.docs["1"]


def test_restoring_refreshes_stored_at_and_tracks_the_edit(key: str) -> None:
    """An edit is detectable: updated_at changes and stored_at moves forward."""
    col = _FakeCollection()
    _cache.store_messages(
        "chan-1",
        [_message(content="before")],
        collection=col,
        now=dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc),
    )
    first_stored = col.docs["1"]["stored_at"]
    _cache.store_messages(
        "chan-1",
        [_message(content="after", edited="2026-09-06T09:00:00+00:00")],
        collection=col,
        now=dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc),
    )
    doc = col.docs["1"]
    assert doc["stored_at"] > first_stored
    assert doc["updated_at"] == dt.datetime(2026, 9, 6, 9, 0, tzinfo=dt.timezone.utc)
    assert doc["created_at"] == dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert _cache.fetch_messages("chan-1", collection=col)[0]["content"] == "after"


def test_fetch_returns_all_three_timestamps(key: str) -> None:
    col = _FakeCollection()
    _cache.store_messages("chan-1", [_message(edited="2026-09-03T08:30:00+00:00")], collection=col)
    got = _cache.fetch_messages("chan-1", collection=col)[0]
    assert {"created_at", "updated_at", "stored_at"} <= set(got)


# ---------------------------------------------------------------------------
# measure_encryption — doctor measures, never assumes (o16)
# ---------------------------------------------------------------------------


def test_measure_encryption_reports_a_measured_round_trip(key: str) -> None:
    col = _FakeCollection()
    result = _cache.measure_encryption(collection=col)
    assert result["encrypted"] is True
    assert result["measured"] is True
    assert result["method"]
    assert result["algorithm"]


def test_measure_encryption_leaves_no_probe_behind(key: str) -> None:
    col = _FakeCollection()
    _cache.measure_encryption(collection=col)
    assert col.docs == {}


def test_measure_encryption_fails_when_content_is_stored_in_clear(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the store path ever regressed to plaintext, doctor must say so."""

    def _identity(text: str) -> dict:
        return {"v": 1, "n": "", "c": text, "t": ""}

    monkeypatch.setattr(_cache._crypto, "encrypt", _identity)
    col = _FakeCollection()
    with pytest.raises(CliError) as exc:
        _cache.measure_encryption(collection=col)
    assert exc.value.code == 2
    assert "plaintext" in exc.value.message.lower()
    assert col.docs == {}


def test_measure_encryption_without_key_is_an_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_KEY_ENV, raising=False)
    col = _FakeCollection()
    with pytest.raises(CliError) as exc:
        _cache.measure_encryption(collection=col)
    assert exc.value.code == 2
    assert col.docs == {}


# ---------------------------------------------------------------------------
# o8 — the retention position is stated in CLAUDE.md, not left to inference
# ---------------------------------------------------------------------------


def test_claude_md_states_the_full_body_retention_position() -> None:
    text = (pathlib.Path(_cache.__file__).parent.parent / "CLAUDE.md").read_text(encoding="utf-8")
    lower = text.lower()
    assert "full message bodies are retained" in lower
    # stated *beside* the two existing positions it deliberately departs from
    assert "no-content rule" in lower
    assert "url-only" in lower

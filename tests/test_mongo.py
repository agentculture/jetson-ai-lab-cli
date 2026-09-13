"""Tests for ``jlab.mongo`` — the jlab-mongodb URI, reachability, and

instance-identity checks that back ``jlab discord doctor``'s cache check.

No network calls: pymongo itself is lazy-imported inside ``jlab.mongo`` and
every test here monkeypatches the seam (``jlab.mongo._seam``), mirroring the
established pattern in tests/test_discord.py
(``monkeypatch.setattr(_discord, "_seam", ...)``).

pymongo is added to the runtime dependency set by a parallel task (t1) and
may not be installed in this environment — these tests must pass either way,
which is exactly what the lazy-import + monkeypatched-seam pattern buys us.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from jlab import mongo as _mongo
from jlab.cli._errors import CliError

_ENV_VAR = "JLAB_MONGO_URI"

# ---------------------------------------------------------------------------
# Fakes — a minimal pymongo double, never the real package.
# ---------------------------------------------------------------------------


class _FakePyMongoError(Exception):
    """Stand-in for ``pymongo.errors.PyMongoError``."""


class _FakeErrorsModule:
    PyMongoError = _FakePyMongoError


class _FakeClient:
    def __init__(self, uri: str, *, address=("localhost", 27019), raise_on_ping=None, **_kw):
        self._uri = uri
        self._address = address
        self._raise_on_ping = raise_on_ping
        self.closed = False

    @property
    def admin(self):
        return self

    def command(self, name):
        if self._raise_on_ping is not None:
            raise self._raise_on_ping
        return {"ok": 1}

    @property
    def address(self):
        return self._address

    def close(self):
        self.closed = True


class _FakePyMongoModule:
    """A fake top-level ``pymongo`` module, as ``_seam()`` would return."""

    def __init__(self, *, address=("localhost", 27019), raise_on_ping=None):
        self.errors = _FakeErrorsModule
        self._address = address
        self._raise_on_ping = raise_on_ping
        self.last_client: _FakeClient | None = None

    def MongoClient(self, uri, **kw):
        client = _FakeClient(uri, address=self._address, raise_on_ping=self._raise_on_ping)
        self.last_client = client
        return client


# ---------------------------------------------------------------------------
# _mongo_uri() — env-only, no baked-in default, legacy ports rejected (o4)
# ---------------------------------------------------------------------------


def test_mongo_uri_env_absent_exits_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_VAR, raising=False)
    with pytest.raises(CliError) as exc:
        _mongo._mongo_uri()
    assert exc.value.code == 2
    assert _ENV_VAR in exc.value.message
    assert exc.value.remediation


@pytest.mark.parametrize("port", [27017, 27018])
def test_mongo_uri_rejects_legacy_ports_explicit(
    monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv(_ENV_VAR, f"mongodb://localhost:{port}/jlab")
    with pytest.raises(CliError) as exc:
        _mongo._mongo_uri()
    assert exc.value.code == 2
    assert str(port) in exc.value.message
    assert exc.value.remediation


def test_mongo_uri_rejects_omitted_port_defaulting_to_27017(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mongodb:// URI with no explicit port defaults to 27017 — reject that too."""
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost/jlab")
    with pytest.raises(CliError) as exc:
        _mongo._mongo_uri()
    assert exc.value.code == 2
    assert "27017" in exc.value.message


def test_mongo_uri_accepts_a_dedicated_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    assert _mongo._mongo_uri() == "mongodb://localhost:27019/jlab"


def test_mongo_uri_rejects_legacy_port_among_multiple_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        _ENV_VAR, "mongodb://host-a:27019,host-b:27018,host-c:27020/jlab?replicaSet=rs0"
    )
    with pytest.raises(CliError) as exc:
        _mongo._mongo_uri()
    assert exc.value.code == 2
    assert "27018" in exc.value.message


def test_forbidden_ports_are_exactly_the_two_legacy_instances() -> None:
    assert _mongo._FORBIDDEN_PORTS == {27017, 27018}


def test_no_stray_legacy_port_int_literals_in_module_source() -> None:
    """No default/fallback anywhere in the module resolves to 27017/27018.

    Walks the module's AST for every literal *integer* 27017 or 27018 (a
    string in a docstring or an error message naming the port for a human
    is a different AST node — ``str`` != ``int`` — and is not flagged). Each
    integer literal found must sit on a line that names one of the two
    constants that are allowed to know these numbers (``_FORBIDDEN_PORTS``,
    ``_MONGO_DEFAULT_PORT``) or compare against them (``==``/``in``) —
    never a bare default assigned to something a caller could silently fall
    back to.
    """
    import ast

    path = pathlib.Path(inspect.getfile(_mongo))
    src = path.read_text()
    lines = src.splitlines()
    tree = ast.parse(src)

    found_any = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, int) or isinstance(node.value, bool):
            continue
        if node.value not in (27017, 27018):
            continue
        found_any = True
        line = lines[node.lineno - 1]
        assert (
            "_FORBIDDEN_PORTS" in line or "_MONGO_DEFAULT_PORT" in line or "==" in line
        ), f"unexpected bare legacy-port integer literal at line {node.lineno}: {line!r}"

    # Guard the guard: if the module stopped mentioning these ports at all
    # (e.g. a refactor), this test would trivially pass without checking
    # anything — fail loudly instead so the omission is noticed.
    assert found_any


# ---------------------------------------------------------------------------
# check_cache() — reachability + instance-identity (o4, o10)
# ---------------------------------------------------------------------------


def test_check_cache_missing_pymongo_exits_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")

    def _boom() -> None:
        raise CliError(code=2, message="pymongo is not installed", remediation="install it")

    monkeypatch.setattr(_mongo, "_seam", _boom)
    with pytest.raises(CliError) as exc:
        _mongo.check_cache()
    assert exc.value.code == 2


def test_check_cache_reachable_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _FakePyMongoModule(address=("localhost", 27019))
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    result = _mongo.check_cache()
    assert result["reachable"] is True
    assert result["port"] == 27019
    assert fake.last_client is not None
    assert fake.last_client.closed is True


def test_check_cache_unreachable_exits_env_error_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _FakePyMongoModule(raise_on_ping=_FakePyMongoError("connection refused"))
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    with pytest.raises(CliError) as exc:
        _mongo.check_cache()
    assert exc.value.code == 2
    assert exc.value.remediation
    assert fake.last_client is not None
    assert fake.last_client.closed is True


def test_check_cache_rejects_connected_server_on_legacy_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if the URI string looks fine, reject a server that answers on 27017/18.

    Covers DNS aliasing / SRV records / port omission resolving somewhere
    unexpected: the instance-identity check is on the server actually
    reached, not just on the URI text.
    """
    monkeypatch.setenv(_ENV_VAR, "mongodb://jlab-alias:27019/jlab")
    fake = _FakePyMongoModule(address=("127.0.0.1", 27018))
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    with pytest.raises(CliError) as exc:
        _mongo.check_cache()
    assert exc.value.code == 2
    assert "27018" in exc.value.message
    assert fake.last_client is not None
    assert fake.last_client.closed is True


def test_check_cache_never_returns_silently_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable cache must raise, never return an empty/falsy result."""
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _FakePyMongoModule(raise_on_ping=_FakePyMongoError("timeout"))
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    try:
        result = _mongo.check_cache()
    except CliError:
        result = None
    assert result is None


# ---------------------------------------------------------------------------
# _seam() — real body, ImportError -> CliError(2)
# ---------------------------------------------------------------------------


def test_seam_missing_pymongo_raises_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "pymongo", None)
    with pytest.raises(CliError) as exc:
        _mongo._seam()
    assert exc.value.code == 2
    assert "pymongo" in exc.value.message
    assert exc.value.remediation


# ---------------------------------------------------------------------------
# message_collection() — the one handle other modules may use (jlab.cache)
# ---------------------------------------------------------------------------


class _FakeDatabase:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def __getitem__(self, name: str) -> str:
        self.asked.append(name)
        return f"collection:{name}"


class _FakeClientWithDb(_FakeClient):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.db = _FakeDatabase()
        self.default_db_arg: object = None

    def get_default_database(self, default=None):
        self.default_db_arg = default
        return self.db


class _FakePyMongoModuleWithDb(_FakePyMongoModule):
    def MongoClient(self, uri, **kw):
        client = _FakeClientWithDb(uri, address=self._address)
        self.last_client = client
        return client


def test_message_collection_yields_the_messages_collection_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _FakePyMongoModuleWithDb()
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    with _mongo.message_collection() as col:
        assert col == f"collection:{_mongo.MESSAGES_COLLECTION}"
        assert fake.last_client.closed is False
    assert fake.last_client.closed is True


def test_message_collection_requires_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """No baked-in default host: the cache handle goes through the same guard."""
    monkeypatch.delenv(_ENV_VAR, raising=False)
    monkeypatch.setattr(_mongo, "_seam", lambda: _FakePyMongoModuleWithDb())
    with pytest.raises(CliError) as exc:
        with _mongo.message_collection():
            pass
    assert exc.value.code == 2


@pytest.mark.parametrize("port", [27017, 27018])
def test_message_collection_rejects_legacy_ports(
    monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv(_ENV_VAR, f"mongodb://localhost:{port}/jlab")
    monkeypatch.setattr(_mongo, "_seam", lambda: _FakePyMongoModuleWithDb())
    with pytest.raises(CliError) as exc:
        with _mongo.message_collection():
            pass
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# coverage_collection() — same guards, and journal-acknowledged writes (o12)
# ---------------------------------------------------------------------------


class _RecordingPyMongoModule(_FakePyMongoModuleWithDb):
    def MongoClient(self, uri, **kw):
        self.client_kwargs = kw
        return super().MongoClient(uri, **kw)


def test_coverage_collection_yields_the_coverage_collection_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _RecordingPyMongoModule()
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)

    with _mongo.coverage_collection() as col:
        assert col == f"collection:{_mongo.COVERAGE_COLLECTION}"
    assert fake.last_client.closed is True
    assert _mongo.COVERAGE_COLLECTION != _mongo.MESSAGES_COLLECTION


@pytest.mark.parametrize("opener", ["message_collection", "coverage_collection"])
def test_cache_handles_request_journaled_writes(
    monkeypatch: pytest.MonkeyPatch, opener: str
) -> None:
    """Coverage widens on "the write returned"; that must mean the journal has it."""
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _RecordingPyMongoModule()
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)
    with getattr(_mongo, opener)():
        pass
    assert fake.client_kwargs.get("journal") is True


@pytest.mark.parametrize("opener", ["message_collection", "coverage_collection"])
def test_cache_handles_decode_datetimes_as_timezone_aware(
    monkeypatch: pytest.MonkeyPatch, opener: str
) -> None:
    """discord-bot-cli#20's sibling live-environment defect: pymongo decodes
    BSON datetimes as naive by default, dropping the UTC tzinfo every stored
    ``created_at``/``updated_at``/``stored_at`` carries going in — which
    raised ``TypeError: can't compare offset-naive and offset-aware
    datetimes`` the first time ``discord read --refresh`` ran against real
    jlab-mongodb (every unit test's fake collection round-trips plain Python
    objects, never BSON, so this never surfaced there). ``tz_aware=True`` at
    the single client-construction choke point fixes it.
    """
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _RecordingPyMongoModule()
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)
    with getattr(_mongo, opener)():
        pass
    assert fake.client_kwargs.get("tz_aware") is True


@pytest.mark.parametrize("port", [27017, 27018])
def test_coverage_collection_rejects_legacy_ports(
    monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv(_ENV_VAR, f"mongodb://localhost:{port}/jlab")
    monkeypatch.setattr(_mongo, "_seam", lambda: _FakePyMongoModuleWithDb())
    with pytest.raises(CliError) as exc:
        with _mongo.coverage_collection():
            pass
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# suppression_collection() / sibling_collection() — the purge suppression list
# (deviation d3) is reached through this module, never a client of its own.
# ---------------------------------------------------------------------------


def test_suppression_collection_yields_its_own_collection_journaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_VAR, "mongodb://localhost:27019/jlab")
    fake = _RecordingPyMongoModule()
    monkeypatch.setattr(_mongo, "_seam", lambda: fake)
    with _mongo.suppression_collection() as col:
        assert col == f"collection:{_mongo.SUPPRESSION_COLLECTION}"
    assert fake.last_client.closed is True
    assert fake.client_kwargs.get("journal") is True
    assert _mongo.SUPPRESSION_COLLECTION not in (
        _mongo.MESSAGES_COLLECTION,
        _mongo.COVERAGE_COLLECTION,
    )


@pytest.mark.parametrize("port", [27017, 27018])
def test_suppression_collection_rejects_legacy_ports(
    monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv(_ENV_VAR, f"mongodb://localhost:{port}/jlab")
    monkeypatch.setattr(_mongo, "_seam", lambda: _FakePyMongoModuleWithDb())
    with pytest.raises(CliError) as exc:
        with _mongo.suppression_collection():
            pass
    assert exc.value.code == 2


def test_sibling_collection_uses_the_same_database() -> None:
    db = _FakeDatabase()

    class _Col:
        database = db

    assert _mongo.sibling_collection(_Col(), "suppression") == "collection:suppression"
    assert db.asked == ["suppression"]


def test_sibling_collection_without_a_database_fails_closed() -> None:
    """No way to find the suppression list means no write, never a silent skip."""
    with pytest.raises(CliError) as exc:
        _mongo.sibling_collection(object(), "suppression")
    assert exc.value.code == 2
    assert exc.value.remediation

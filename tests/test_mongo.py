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

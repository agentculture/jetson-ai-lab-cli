"""jlab-mongodb — connection, reachability, and instance-identity checks.

jlab owns a **dedicated** MongoDB instance (``jlab-mongodb``) and must never
write to a borrowed one. Two other mongod containers already run on this
machine and are explicitly off-limits:

* port **27017** — ``qq-mongodb``, a legacy instance;
* port **27018** — ``eidetic-mongo``, the shared eidetic memory store.

The connection URI is read **only** from the ``JLAB_MONGO_URI`` environment
variable (mirroring how ``DISCORD_BOT_TOKEN`` is read from the env, never a
flag, in :mod:`jlab.cli._discord`) — there is **no baked-in host/port
default**. :func:`_mongo_uri` is the single choke point that both requires
the env var and rejects any URI that resolves to a legacy port, whether the
port is explicit, implied by a multi-host list, or simply omitted (a bare
``mongodb://host/db`` defaults to port 27017 per the MongoDB URI spec, so an
omitted port is rejected too — see ``_MONGO_DEFAULT_PORT``). No other module
should read ``JLAB_MONGO_URI`` directly; go through this one.

:func:`check_cache` additionally verifies the *actual* server reached (its
post-connection ``client.address``) is not on a legacy port, covering DNS
aliasing / SRV records / anything the URI string alone can't catch — the
instance-identity check is on the server actually reached, not just on the
URI text.

Lazy-import: ``pymongo`` is **never** imported at module scope, mirroring
:func:`jlab.cli._discord._seam`. This keeps this task's tests (and this
module's own import) working before pymongo lands in ``pyproject.toml`` via
a parallel task, and turns a missing/incompatible install into a clean
:class:`jlab.cli._errors.CliError` (code 2) rather than a traceback.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from jlab.cli._errors import EXIT_ENV_ERROR, CliError

_MONGO_URI_ENV = "JLAB_MONGO_URI"

# Database used when the URI names no default database, and the collection the
# encrypted message cache lives in (see :mod:`jlab.cache`).
_DEFAULT_DB_NAME = "jlab"
MESSAGES_COLLECTION = "messages"

# The two mongod containers this machine already runs, neither of which is
# jlab's: qq-mongodb (legacy) on 27017, eidetic-mongo (memory store) on
# 27018. jlab-mongodb must run on neither.
_FORBIDDEN_PORTS = {27017, 27018}

# MongoDB's own default port when a URI omits one explicitly (mongodb://spec).
# An omitted port must be treated as resolving to this, not as "unspecified".
_MONGO_DEFAULT_PORT = 27017

# Short server-selection timeout: doctor/reachability checks must fail fast
# with an actionable hint rather than hang.
_SERVER_SELECTION_TIMEOUT_MS = 2000


def _hosts_and_ports(uri: str) -> list[tuple[str, int]]:
    """Extract (host, port) pairs from a ``mongodb://`` or ``mongodb+srv://`` URI.

    ``mongodb+srv://`` URIs resolve their real hosts/ports via a DNS SRV
    lookup the URI text does not carry, so no port pairs are returned for
    that scheme (there is nothing to check syntactically — the connected
    server's actual address is still checked in :func:`check_cache`).
    """
    if uri.startswith("mongodb+srv://"):
        return []
    if uri.startswith("mongodb://"):
        rest = uri[len("mongodb://") :]
    else:
        rest = uri

    # Drop path/query, then userinfo.
    rest = rest.split("/", 1)[0].split("?", 1)[0]
    if "@" in rest:
        rest = rest.rsplit("@", 1)[1]

    pairs: list[tuple[str, int]] = []
    for host_port in rest.split(","):
        host_port = host_port.strip()
        if not host_port:
            continue
        if ":" in host_port:
            host, _, port_s = host_port.rpartition(":")
            try:
                port = int(port_s)
            except ValueError:
                port = _MONGO_DEFAULT_PORT
        else:
            host, port = host_port, _MONGO_DEFAULT_PORT
        pairs.append((host, port))
    return pairs


def _reject_legacy_ports(uri: str) -> None:
    for host, port in _hosts_and_ports(uri):
        if port in _FORBIDDEN_PORTS:
            legacy = "qq-mongodb (legacy)" if port == 27017 else "eidetic-mongo (memory store)"
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=(
                    f"{_MONGO_URI_ENV} resolves to host {host!r} on port {port}, "
                    f"which is {legacy}, not jlab's own instance"
                ),
                remediation=(
                    "point "
                    f"{_MONGO_URI_ENV} at jlab's own dedicated jlab-mongodb "
                    "instance, on a port that is neither 27017 nor 27018 "
                    "(see README for the container setup)"
                ),
            )


def _mongo_uri() -> str:
    """Return the jlab-mongodb URI from ``JLAB_MONGO_URI``, or raise.

    Raises :class:`CliError` (code 2) when the env var is absent, and when
    the URI resolves — explicitly, via an omitted port, or across a
    multi-host list — to a legacy port (27017/27018).
    """
    uri = os.environ.get(_MONGO_URI_ENV)
    if not uri:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{_MONGO_URI_ENV} is not set",
            remediation=(
                f"set {_MONGO_URI_ENV} to jlab's own dedicated jlab-mongodb "
                "instance, e.g. mongodb://127.0.0.1:27019/jlab (not 27017 or "
                "27018 — those belong to qq-mongodb and eidetic-mongo); see "
                "README for the container setup"
            ),
        )
    _reject_legacy_ports(uri)
    return uri


def _seam() -> Any:
    """Return the ``pymongo`` module, lazily imported.

    Raises :class:`CliError` (code 2) when pymongo is not installed.
    """
    try:
        import pymongo  # noqa: F811
    except ImportError:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="pymongo is not installed",
            remediation="install jlab's runtime dependencies: uv sync",
        )
    return pymongo


@contextmanager
def message_collection(uri: str | None = None) -> Iterator[Any]:
    """Yield the cached-messages collection, closing the client afterwards.

    The single way for other modules to reach jlab-mongodb: the URI still comes
    from :func:`_mongo_uri` (env only) and still passes
    :func:`_reject_legacy_ports`, so no caller can route around the
    dedicated-instance guard by opening its own client.
    """
    pymongo = _seam()
    resolved_uri = _mongo_uri() if uri is None else uri
    _reject_legacy_ports(resolved_uri)
    client = pymongo.MongoClient(
        resolved_uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS
    )
    try:
        database = client.get_default_database(default=_DEFAULT_DB_NAME)
        yield database[MESSAGES_COLLECTION]
    finally:
        client.close()


def check_cache(uri: str | None = None) -> dict[str, object]:
    """Verify jlab-mongodb is reachable and is genuinely jlab's own instance.

    Raises :class:`CliError` (code 2, with an actionable ``hint:``) on any
    failure — an absent env var, a URI naming a legacy port, an unreachable
    server, or a reachable server that turns out to be listening on a
    legacy port. This function never returns a falsy/empty result to signal
    failure: every failure path raises.
    """
    resolved_uri = _mongo_uri() if uri is None else uri
    _reject_legacy_ports(resolved_uri)

    pymongo = _seam()
    client = pymongo.MongoClient(
        resolved_uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS
    )
    try:
        try:
            client.admin.command("ping")
        except pymongo.errors.PyMongoError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"jlab-mongodb is unreachable at the configured URI: {exc}",
                remediation=(
                    "start the jlab-mongodb container and verify "
                    f"{_MONGO_URI_ENV}, then retry (see README for the "
                    "container setup)"
                ),
            ) from exc

        address = client.address
        host = address[0] if address else None
        port = address[1] if address else None
        if port in _FORBIDDEN_PORTS:
            legacy = "qq-mongodb (legacy)" if port == 27017 else "eidetic-mongo (memory store)"
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=(
                    f"connected to {host}:{port}, which is {legacy}, not jlab's " "own instance"
                ),
                remediation=(
                    f"point {_MONGO_URI_ENV} at jlab's own dedicated "
                    "jlab-mongodb instance, on a port that is neither 27017 "
                    "nor 27018"
                ),
            )
    finally:
        client.close()

    return {"reachable": True, "host": host, "port": port}

"""The jlab-mongodb message cache — documents, timestamps, encrypt/decrypt.

This is the layer between the Discord paging primitive and jlab-mongodb. Every
database handle comes from :mod:`jlab.mongo` (the single choke point for the
URI, the legacy-port guard and the lazy pymongo import) and every message body
passes through :mod:`jlab.crypto` on the way in and on the way out.

**Retention position (deliberate, stated — see CLAUDE.md).** This path retains
**full message bodies**. That is a third position, not an oversight: the
members path keeps counts and lengths but never content, the links path keeps
URLs but never the surrounding text, and this cache keeps the whole message —
because a paged read and a regex search over history cannot exist without it.
What the cache holds is therefore encrypted at the application layer and
deletable per author and per channel.

**Document shape** (one per Discord message, ``_id`` = the message id):

===============  ========================================================
``channel_id``   the public channel the message was read from
``author_id``    Discord's author id (never a username — names are
                 resolved at render time, as in the members/links paths)
``created_at``   Discord's creation timestamp
``updated_at``   Discord's edit timestamp, ``None`` when never edited
``stored_at``    when *jlab* wrote this copy
``content``      the encrypted envelope (never a plaintext string)
``jump_url``     durable pointer back to the original message
===============  ========================================================

The three timestamps are the point of the schema: ``created_at`` orders the
corpus, ``created_at`` vs ``updated_at`` makes an edit detectable, and
``stored_at`` means the age of the local copy is always known. A later task
builds the reconciliation sweep on exactly these three.

Metadata is stored in the clear on purpose so the cache stays queryable by
channel, author and time; only the body is encrypted.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Any, Iterable, Iterator, Sequence

from jlab import crypto as _crypto
from jlab import mongo as _mongo
from jlab.cli._errors import EXIT_ENV_ERROR, CliError

#: Schema version stamped on every document, so a later migration can tell
#: old documents from new ones without guessing.
SCHEMA_VERSION = 1

#: Prefix for the throwaway document ``measure_encryption`` writes and deletes.
_PROBE_PREFIX = "__jlab_encryption_probe__"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_timestamp(value: Any) -> dt.datetime | None:
    """Accept an ISO-8601 string or a datetime; return an aware datetime or None."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _author_id(message: dict) -> str | None:
    author = message.get("author") or {}
    if isinstance(author, dict):
        value = author.get("id")
    else:
        value = author
    return str(value) if value is not None else None


def _document(channel_id: str, message: dict, now: dt.datetime) -> dict[str, Any]:
    """Build the storable document for *message*, encrypting its body.

    Raises :class:`CliError` (code 2) — via :func:`jlab.crypto.encrypt` — when
    no key is configured, **before** anything is handed to pymongo.
    """
    message_id = message.get("id")
    if message_id is None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="a message with no id cannot be cached",
            remediation="re-fetch the channel; every Discord message carries an id",
        )
    return {
        "_id": str(message_id),
        "schema": SCHEMA_VERSION,
        "message_id": str(message_id),
        "channel_id": str(channel_id),
        "author_id": _author_id(message),
        "author_is_bot": bool((message.get("author") or {}).get("bot")),
        "created_at": _parse_timestamp(message.get("created_at")),
        "updated_at": _parse_timestamp(message.get("edited_at")),
        "stored_at": now,
        "jump_url": message.get("jump_url"),
        "content": _crypto.encrypt(message.get("content") or ""),
    }


def store_messages(
    channel_id: str,
    messages: Iterable[dict],
    *,
    collection: Any = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Encrypt and upsert *messages* into the cache; return a small summary.

    Every body is encrypted **before** the first write is issued, so a missing
    ``JLAB_CACHE_KEY`` raises :class:`CliError` (code 2) with the collection
    left untouched. There is no path through this function that writes message
    content in the clear.
    """
    now = now or _utcnow()
    batch: Sequence[dict] = list(messages)
    documents = [_document(channel_id, m, now) for m in batch]

    if collection is None:
        with _mongo.message_collection() as col:
            return _upsert(col, documents)
    return _upsert(collection, documents)


def _upsert(collection: Any, documents: Sequence[dict]) -> dict[str, Any]:
    for doc in documents:
        _id = doc.pop("_id")
        created = doc.pop("created_at")
        collection.update_one(
            {"_id": _id},
            {"$set": doc, "$setOnInsert": {"created_at": created}},
            upsert=True,
        )
        # created_at is Discord's and immutable; write it only on insert so a
        # re-store can never rewrite history, while updated_at/stored_at do move.
        doc["_id"] = _id
        doc["created_at"] = created
    return {"stored": len(documents)}


def _decrypt_document(doc: dict) -> dict[str, Any]:
    return {
        "message_id": doc.get("message_id") or doc.get("_id"),
        "channel_id": doc.get("channel_id"),
        "author_id": doc.get("author_id"),
        "author_is_bot": doc.get("author_is_bot", False),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "stored_at": doc.get("stored_at"),
        "jump_url": doc.get("jump_url"),
        "content": _crypto.decrypt(doc.get("content")),
    }


def iter_messages(
    channel_id: str | None = None,
    *,
    collection: Any = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield cached messages, oldest first, with content decrypted.

    Decryption is not optional and not lazy: a missing or wrong key raises
    :class:`CliError` (code 2) rather than yielding an envelope or skipping the
    document. A regex search (a later task) consumes this iterator and matches
    the decrypted text client-side — the server never sees plaintext, so no
    server-side query can filter on content.
    """
    query: dict[str, Any] = {}
    if channel_id is not None:
        query["channel_id"] = str(channel_id)

    def _run(col: Any) -> Iterator[dict[str, Any]]:
        cursor = col.find(query).sort("created_at", 1)
        if limit:
            cursor = cursor.limit(limit)
        for doc in cursor:
            yield _decrypt_document(doc)

    if collection is None:
        with _mongo.message_collection() as col:
            yield from _run(col)
    else:
        yield from _run(collection)


def fetch_messages(
    channel_id: str | None = None,
    *,
    collection: Any = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Eager :func:`iter_messages` — cached messages, oldest first, decrypted."""
    return list(iter_messages(channel_id, collection=collection, limit=limit))


def measure_encryption(*, collection: Any = None) -> dict[str, Any]:
    """Measure — not assume — that stored content is ciphertext.

    Writes a throwaway probe document through the **real** store path, reads
    the raw document straight back out of the collection *without* decrypting,
    and asserts that the probe's unique marker appears nowhere in it. Then
    fetches it through the normal decrypting path and asserts the marker comes
    back. The probe is deleted either way.

    This is what lets ``doctor`` report encryption as measured: it is a
    round-trip against the live collection, not a reading of configuration.
    Raises :class:`CliError` (code 2) if the key is absent, if the marker is
    found in the stored document (a plaintext regression), or if the
    round-trip does not return the marker.
    """
    if collection is None:
        with _mongo.message_collection() as col:
            return _measure(col)
    return _measure(collection)


def _measure(collection: Any) -> dict[str, Any]:
    marker = f"{_PROBE_PREFIX}{secrets.token_hex(16)}"
    probe_id = f"{_PROBE_PREFIX}{secrets.token_hex(8)}"
    fingerprint = _crypto.key_fingerprint()  # raises when no key is configured
    probe = {
        "id": probe_id,
        "author": {"id": None, "bot": False},
        "content": marker,
        "created_at": _utcnow().isoformat(),
        "edited_at": None,
        "jump_url": None,
    }
    try:
        store_messages(_PROBE_PREFIX, [probe], collection=collection)
        raw = collection.find_one({"_id": probe_id})
        if raw is None:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="the encryption probe could not be read back from jlab-mongodb",
                remediation=(
                    "verify JLAB_MONGO_URI points at a writable jlab-mongodb "
                    "instance, then re-run `jetson-ai-lab-cli discord doctor`"
                ),
            )
        if marker in repr(raw):
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=(
                    "jlab-mongodb stored message content as plaintext — "
                    "application-level encryption is NOT in effect"
                ),
                remediation=(
                    "do not cache Discord content until this is fixed: the "
                    "store path must encrypt via jlab.crypto.encrypt before "
                    "writing; purge the affected collection and re-fetch"
                ),
            )
        back = _decrypt_document(raw)
        if back["content"] != marker:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message="the encryption probe did not survive a store/fetch round-trip",
                remediation=(
                    "check that JLAB_CACHE_KEY is unchanged since the cache "
                    "was written, then re-run doctor"
                ),
            )
    finally:
        collection.delete_one({"_id": probe_id})

    return {
        "encrypted": True,
        "measured": True,
        "method": "store/fetch probe: stored document scanned for the probe marker",
        "algorithm": _crypto.ALGORITHM,
        "envelope_version": _crypto.ENVELOPE_VERSION,
        "key_fingerprint": fingerprint,
    }

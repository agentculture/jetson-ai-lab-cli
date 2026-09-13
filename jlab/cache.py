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

=====================  ==================================================
``channel_id``         the public channel the message was read from
``author_id``          Discord's author id, cleartext (queried by
                       purge/suppression; never a username)
``author_name``        encrypted (deviation d4; ``None`` when Discord gave
                       none, or on a document cached before d4)
``author_display_name``  encrypted (deviation d4; same absence rule)
``created_at``         Discord's creation timestamp
``updated_at``         Discord's edit timestamp, ``None`` when never edited
``stored_at``          when *jlab* wrote this copy
``content``            the encrypted envelope (never a plaintext string)
``jump_url``           durable pointer back to the original message
=====================  ==================================================

The three timestamps are the point of the schema: ``created_at`` orders the
corpus, ``created_at`` vs ``updated_at`` makes an edit detectable, and
``stored_at`` means the age of the local copy is always known. A later task
builds the reconciliation sweep on exactly these three.

**Deviation d4 — author names are stored, encrypted.** Unlike the
members/links paths (which resolve a name only at render time, from a live
lookup, and never store one), this cache stores ``author_name`` and
``author_display_name`` themselves — each its own ``{v,n,c}`` envelope via
:mod:`jlab.crypto`, exactly like ``content``, never folded into it and never
a cleartext field. That is a deliberate, narrower choice than the
members/links no-name rule: a paged read or search needs to show *who* said
something without an extra Discord round trip, and the id alone does not
read as a name. ``author_id`` stays cleartext (queried by purge and
suppression); every other id/timestamp field stays cleartext too — only
``content`` and the two name fields are ever encrypted.

Metadata is stored in the clear on purpose so the cache stays queryable by
channel, author and time; the body and both name fields are encrypted.
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


def _author_field(message: dict, field: str) -> str | None:
    """*field* (``"name"`` or ``"display_name"``) off ``message["author"]``.

    ``None`` when absent — never a fabricated placeholder, so nothing but a
    genuine value from Discord is ever handed to :func:`jlab.crypto.encrypt`.
    """
    author = message.get("author") or {}
    if not isinstance(author, dict):
        return None
    value = author.get(field)
    return str(value) if value else None


def _encrypt_optional(value: str | None) -> dict[str, object] | None:
    """:func:`jlab.crypto.encrypt` *value*, or ``None`` straight through.

    ``encrypt`` only accepts a string; a ``None`` author name/display name
    (a bare bot account, or an author field Discord didn't send) must stay
    ``None`` in storage — never an encrypted empty string standing in for
    "absent", which would be indistinguishable from a genuinely empty name.
    """
    return _crypto.encrypt(value) if value is not None else None


def _decrypt_optional(envelope: object) -> str | None:
    """The inverse of :func:`_encrypt_optional`: ``None`` stays ``None``.

    Also covers a document cached before deviation d4 (author names), whose
    ``author_name``/``author_display_name`` fields are simply absent —
    ``doc.get(...)`` already yields ``None`` for those, so an older document
    still reads.
    """
    return _crypto.decrypt(envelope) if envelope is not None else None


def _document(channel_id: str, message: dict, now: dt.datetime) -> dict[str, Any]:
    """Build the storable document for *message*, encrypting its body.

    Raises :class:`CliError` (code 2) — via :func:`jlab.crypto.encrypt` — when
    no key is configured, **before** anything is handed to pymongo.

    **Deviation d4.** ``author_name``/``author_display_name`` are encrypted
    the same way ``content`` is — their own ``{v,n,c}`` envelope apiece, never
    folded into ``content``'s — so a name never appears in any cleartext,
    queryable field. ``author_id`` (needed for purge/suppression queries) and
    every timestamp stay cleartext, unchanged.
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
        "author_name": _encrypt_optional(_author_field(message, "name")),
        "author_display_name": _encrypt_optional(_author_field(message, "display_name")),
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
    suppression: Any = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Encrypt and upsert *messages* into the cache; return a small summary.

    Every body is encrypted **before** the first write is issued, so a missing
    ``JLAB_CACHE_KEY`` raises :class:`CliError` (code 2) with the collection
    left untouched. There is no path through this function that writes message
    content in the clear.

    **Purge suppression (deviation d3) is enforced here, centrally.** A message
    whose author matches a record in the suppression list (see
    :func:`suppress_author`) is never written, so every caller — the fetch, the
    reconciliation sweep — honours a purge without knowing about it. The
    summary is ``{"stored": n, "suppressed": k}``. Two guards keep that true:

    * the author digests are re-checked **after** the writes, and any message
      whose author was suppressed in between is deleted again — a purge landing
      between this function's check and its write cannot resurrect the author;
    * a suppression record made under a different ``JLAB_CACHE_KEY`` would no
      longer match its author's digest, silently re-admitting them; such a
      record makes every store with authors refuse (code 2) instead.

    *suppression* defaults to the suppression collection in the same database
    as *collection* (:func:`jlab.mongo.sibling_collection`).
    """
    now = now or _utcnow()
    batch: Sequence[dict] = list(messages)
    documents = [_document(channel_id, m, now) for m in batch]

    if collection is None:
        with _mongo.message_collection() as col:
            return _store(col, suppression, documents)
    return _store(collection, suppression, documents)


def _suppressed_authors(suppression: Any, digests: dict[str, str]) -> set[str]:
    return {a for a, d in digests.items() if suppression.find_one({"_id": d}) is not None}


def _refuse_foreign_key_records(suppression: Any, fingerprint: str) -> None:
    stale = suppression.find_one({"key_fingerprint": {"$ne": fingerprint}})
    if stale is not None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                "the purge suppression list holds records made under a different "
                f"{_crypto.KEY_ENV}; they cannot be matched, so caching is refused "
                "rather than re-admitting purged authors"
            ),
            remediation=(
                f"restore the {_crypto.KEY_ENV} the suppressions were recorded with, or "
                "re-run `jetson-ai-lab-cli discord purge --author ID --yes` under the new "
                "key for every recorded deletion request and then remove the old records"
            ),
        )


def _store(collection: Any, suppression: Any, documents: Sequence[dict]) -> dict[str, Any]:
    digests = {
        a: _crypto.author_digest(a)
        for a in sorted({d["author_id"] for d in documents if d.get("author_id")})
    }
    if not digests:
        return {**_upsert(collection, documents), "suppressed": 0}
    sup = (
        suppression
        if suppression is not None
        else _mongo.sibling_collection(collection, _mongo.SUPPRESSION_COLLECTION)
    )
    _refuse_foreign_key_records(sup, _crypto.key_fingerprint())
    blocked = _suppressed_authors(sup, digests)
    allowed = [d for d in documents if d.get("author_id") not in blocked]
    _upsert(collection, allowed)

    # Re-check after writing: a purge that recorded its suppression and ran its
    # delete between the check above and the writes must not be undone.
    late = _suppressed_authors(sup, {a: digests[a] for a in digests if a not in blocked})
    for doc in allowed:
        if doc.get("author_id") in late:
            collection.delete_one({"_id": doc["_id"]})
    stored = sum(1 for d in allowed if d.get("author_id") not in late)
    return {"stored": stored, "suppressed": len(documents) - stored}


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


def suppress_author(
    author_id: str | int, *, collection: Any, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Record *author_id* in the suppression list *collection*; idempotent.

    The record's ``_id`` is :func:`jlab.crypto.author_digest` — an HMAC under
    an HKDF sub-key of ``JLAB_CACHE_KEY`` — and **no field holds the raw author
    id**. Re-suppressing an author leaves the one existing record unchanged
    (``$setOnInsert`` only). Raises :class:`CliError` (code 2) with nothing
    written when no key is configured. Returns
    ``{"recorded": True, "already_present": bool}``.
    """
    target = _require_delete_value(author_id, "author_id")
    digest = _crypto.author_digest(target)
    fingerprint = _crypto.key_fingerprint()
    existed = collection.find_one({"_id": digest}) is not None
    collection.update_one(
        {"_id": digest},
        {
            "$setOnInsert": {
                "schema": SCHEMA_VERSION,
                "kind": "author",
                "digest": "HMAC-SHA256 under an HKDF-SHA256 sub-key of " + _crypto.KEY_ENV,
                "key_fingerprint": fingerprint,
                "suppressed_at": now or _utcnow(),
            }
        },
        upsert=True,
    )
    return {"recorded": True, "already_present": existed}


def _decrypt_document(doc: dict) -> dict[str, Any]:
    return {
        "message_id": doc.get("message_id") or doc.get("_id"),
        "channel_id": doc.get("channel_id"),
        "author_id": doc.get("author_id"),
        "author_is_bot": doc.get("author_is_bot", False),
        # d4: absent on a document cached before author names were stored —
        # doc.get(...) already yields None there, so an older document reads.
        "author_name": _decrypt_optional(doc.get("author_name")),
        "author_display_name": _decrypt_optional(doc.get("author_display_name")),
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


# ---------------------------------------------------------------------------
# Deletion (t12) — per author, per channel, and by age.
#
# ``author_id`` and ``channel_id`` are stored in the clear precisely so these
# deletions can run as one server-side ``delete_many`` without decrypting the
# corpus. Callers wanting operator-facing validation (snowflake-only targets,
# a dry-run default) go through :mod:`jlab.purge`; the guard below is defence
# in depth so that no caller of this layer can express "delete everything".
# ---------------------------------------------------------------------------


def _require_delete_value(value: Any, field: str) -> str:
    """Refuse a missing, empty or non-scalar delete target.

    A ``None``/empty value, or a dict (which pymongo would read as a query
    operator such as ``{"$ne": ""}``), would widen a targeted delete into a
    collection-wide one. Only a non-empty plain string/int is accepted.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to delete by {field}: target must be a plain id, got {value!r}",
            remediation="pass one explicit Discord id (see `jetson-ai-lab-cli discord purge`)",
        )
    text = str(value).strip()
    if not text:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to delete by {field}: target is empty",
            remediation="pass one explicit Discord id (see `jetson-ai-lab-cli discord purge`)",
        )
    return text


def _delete_where(query: dict[str, Any], collection: Any, dry_run: bool) -> dict[str, Any]:
    def _run(col: Any) -> dict[str, Any]:
        matched = int(col.count_documents(query))
        if dry_run:
            return {"matched": matched, "deleted": 0}
        result = col.delete_many(query)
        return {"matched": matched, "deleted": int(result.deleted_count)}

    if collection is None:
        with _mongo.message_collection() as col:
            return _run(col)
    return _run(collection)


def delete_by_author(
    author_id: str | int, *, collection: Any = None, dry_run: bool = False
) -> dict[str, Any]:
    """Delete every cached message by *author_id*; return ``{matched, deleted}``."""
    query = {"author_id": _require_delete_value(author_id, "author_id")}
    return _delete_where(query, collection, dry_run)


def delete_by_channel(
    channel_id: str | int, *, collection: Any = None, dry_run: bool = False
) -> dict[str, Any]:
    """Delete every cached message from *channel_id*; return ``{matched, deleted}``."""
    query = {"channel_id": _require_delete_value(channel_id, "channel_id")}
    return _delete_where(query, collection, dry_run)


def delete_older_than(
    cutoff: dt.datetime,
    *,
    channel_id: str | int | None = None,
    collection: Any = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete every cached message created before *cutoff* (the retention bound).

    With *channel_id*, only that channel's messages — which is how
    :func:`jlab.purge.purge_older_than` deletes one channel at a time under
    that channel's coverage lock.
    """
    if not isinstance(cutoff, dt.datetime):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"refusing to delete by age: cutoff must be a datetime, got {cutoff!r}",
            remediation="pass --older-than DAYS to `jetson-ai-lab-cli discord purge`",
        )
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=dt.timezone.utc)
    query: dict[str, Any] = {"created_at": {"$lt": cutoff}}
    if channel_id is not None:
        query = {"channel_id": _require_delete_value(channel_id, "channel_id"), **query}
    return _delete_where(query, collection, dry_run)


def old_message_channels(cutoff: dt.datetime, *, collection: Any) -> list[str]:
    """Channel ids holding at least one cached message created before *cutoff*."""
    return sorted(
        str(c) for c in collection.distinct("channel_id", {"created_at": {"$lt": cutoff}})
    )


# ---------------------------------------------------------------------------
# Reconciliation (t11) — what the daily sweep needs to compare a covered span
# against Discord and remove what Discord no longer has.
# ---------------------------------------------------------------------------


def messages_between(
    channel_id: str | int,
    start: dt.datetime,
    end: dt.datetime,
    *,
    collection: Any = None,
) -> list[dict[str, Any]]:
    """Cached messages of *channel_id* created strictly between *start* and *end*.

    Both bounds are **exclusive**, matching Discord's ``after=``/``before=``
    history cursors: a message the re-read could not have returned is never
    a candidate for deletion. Content is decrypted (a missing or wrong key
    raises code 2), because detecting an edit means comparing bodies.
    """
    query = {
        "channel_id": _require_delete_value(channel_id, "channel_id"),
        "created_at": {"$gt": start, "$lt": end},
    }

    def _run(col: Any) -> list[dict[str, Any]]:
        return [_decrypt_document(doc) for doc in col.find(query).sort("created_at", 1)]

    if collection is None:
        with _mongo.message_collection() as col:
            return _run(col)
    return _run(collection)


def delete_message_ids(
    channel_id: str | int,
    message_ids: Any,
    *,
    collection: Any = None,
) -> dict[str, int]:
    """Delete the named messages of *channel_id* only; return ``{"deleted": n}``.

    Scoped by channel as well as id, so a wrong id list can never reach another
    channel's messages. *message_ids* must be a list/tuple of non-empty plain
    strings — ``None``, a bare string, or an entry that is empty or a dict (a
    query operator) is refused with code 2 before anything is deleted. An
    empty list deletes nothing and issues no query.
    """
    channel = _require_delete_value(channel_id, "channel_id")
    if not isinstance(message_ids, (list, tuple)):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                "refusing to delete by message id: expected a list of ids, " f"got {message_ids!r}"
            ),
            remediation="pass an explicit list of Discord message ids",
        )
    ids = []
    for value in message_ids:
        if not isinstance(value, str) or not value.strip():
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"refusing to delete by message id: {value!r} is not a plain id",
                remediation="pass an explicit list of Discord message ids",
            )
        ids.append(value)
    if not ids:
        return {"deleted": 0}

    def _run(col: Any) -> dict[str, int]:
        result = col.delete_many({"channel_id": channel, "_id": {"$in": ids}})
        return {"deleted": int(result.deleted_count)}

    if collection is None:
        with _mongo.message_collection() as col:
            return _run(col)
    return _run(collection)


def reap_probes(*, older_than: dt.datetime, collection: Any = None) -> int:
    """Delete leftover :func:`measure_encryption` probes stored before *older_than*.

    ``_measure`` deletes its probe in a ``finally``, but a kill between the
    store and that delete leaves one behind (risk r9). Only probes older than
    the cutoff go, so a probe a concurrent ``doctor`` is reading back right now
    survives. Returns how many were removed.
    """
    query = {"channel_id": _PROBE_PREFIX, "stored_at": {"$lt": older_than}}

    def _run(col: Any) -> int:
        return int(col.delete_many(query).deleted_count)

    if collection is None:
        with _mongo.message_collection() as col:
            return _run(col)
    return _run(collection)

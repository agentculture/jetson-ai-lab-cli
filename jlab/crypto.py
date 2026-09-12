"""Application-level encryption for cached Discord message content.

**Why application level.** Discord's Developer Terms require API Data to be
encrypted at rest. MongoDB *community* edition ships no encrypted storage
engine, so "encryption at rest" cannot be delegated to the database the way it
could on an enterprise deployment. jlab therefore encrypts message content
**before** it is handed to pymongo and decrypts it **after** it comes back —
what jlab-mongodb stores is ciphertext regardless of which storage engine,
image or volume is underneath.

**The key is configuration, never a default.** It is read from the
``JLAB_CACHE_KEY`` environment variable and from nowhere else — this module is
the single choke point, mirroring how :mod:`jlab.mongo` is the only reader of
``JLAB_MONGO_URI``. There is no generated-and-forgotten fallback and no
plaintext mode: a missing, blank or too-short key raises
:class:`~jlab.cli._errors.CliError` (code 2) with an actionable hint, and the
caller writes nothing. Storing plaintext because the key was absent is the
failure this module exists to prevent.

**Construction.** The envelope is **AES-256-GCM** from the ``cryptography``
package (an approved runtime dependency — see CLAUDE.md), a standardised,
independently reviewed AEAD. A 256-bit content key is derived from the
configured passphrase with HKDF-SHA256 (RFC 5869), so the key material stored
in the environment is never used as a raw cipher key. Each message gets a fresh
96-bit nonce from :mod:`secrets`; GCM's tag authenticates the ciphertext and is
verified before any plaintext is returned, so tampering raises rather than
decrypting to garbage.

This replaces an earlier hand-rolled HMAC-CTR composition. That construction
was a sound assembly of standard primitives but had had no cryptographic
review, which is a poor foundation for the encryption-at-rest commitment the
published privacy policy makes; a reviewed AEAD was approved instead.

**Limitations — still worth stating.** Encryption protects content **at rest in
jlab-mongodb** — against someone reading the database files, a stolen volume, a
mongodump. It does **not** protect against an attacker who also holds the key,
the process memory or the environment. Ciphertext length still leaks plaintext
length. There is no key rotation: re-keying means re-fetching the affected
channels, because jlab never keeps a plaintext copy to re-encrypt from.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jlab.cli._errors import EXIT_ENV_ERROR, CliError

#: The only environment variable this project reads for the cache key.
KEY_ENV = "JLAB_CACHE_KEY"

#: Envelope format version. Bumped if the construction ever changes; an
#: unknown version is refused rather than guessed at.
ENVELOPE_VERSION = 2

#: Short human name of the construction, surfaced by ``doctor``.
ALGORITHM = "HKDF-SHA256 + HMAC-SHA256 keystream (CTR), encrypt-then-MAC HMAC-SHA256"

_HASH = hashlib.sha256
_DIGEST_SIZE = _HASH().digest_size
_NONCE_BYTES = 12  # AES-GCM standard nonce size (NIST SP 800-38D)

# A key shorter than this is refused: the construction's strength is bounded by
# the key, and a four-character passphrase in an env var is not encryption.
_MIN_KEY_CHARS = 32

_HKDF_SALT = b"jlab-cache-v1"
_INFO_ENC = b"jlab-cache-v1/content-encryption"
_INFO_MAC = b"jlab-cache-v1/content-authentication"
# Distinct HKDF label for the purge-suppression HMAC key: never the AES content
# key, never the raw passphrase.
_INFO_SUPPRESSION = b"jlab-cache-v2/author-suppression-hmac"

_MISSING_KEY_HINT = (
    f"set {KEY_ENV} to a high-entropy secret of at least {_MIN_KEY_CHARS} "
    "characters, e.g. "
    "`export JLAB_CACHE_KEY=\"$(python3 -c 'import secrets;"
    "print(secrets.token_urlsafe(32))')\"`, and keep it with the deployment "
    "(losing it makes the cache unreadable; jlab will never fall back to "
    "storing message content unencrypted)"
)


def _key_material() -> bytes:
    """Return the configured key, or raise ``CliError`` (code 2).

    Never returns a default, a generated key, or ``None``. Callers may rely on
    this raising rather than on checking a falsy result.
    """
    raw = os.environ.get(KEY_ENV)
    if raw is None or not raw.strip():
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                f"{KEY_ENV} is not set, so cached Discord message content "
                "cannot be encrypted or decrypted"
            ),
            remediation=_MISSING_KEY_HINT,
        )
    key = raw.strip()
    if len(key) < _MIN_KEY_CHARS:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=(
                f"{KEY_ENV} is only {len(key)} characters; at least "
                f"{_MIN_KEY_CHARS} are required"
            ),
            remediation=_MISSING_KEY_HINT,
        )
    return key.encode("utf-8")


def _hkdf(key_material: bytes, info: bytes, length: int = _DIGEST_SIZE) -> bytes:
    """HKDF-SHA256 (RFC 5869) extract-then-expand over ``hmac``."""
    prk = hmac.new(_HKDF_SALT, key_material, _HASH).digest()
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), _HASH).digest()
        out += block
        counter += 1
    return out[:length]


def _content_key() -> bytes:
    """Derive the 256-bit AES-GCM content key from the configured passphrase.

    The environment value is a passphrase, never a raw cipher key, so it is run
    through HKDF before it reaches AES.
    """
    return _hkdf(_key_material(), b"jlab-cache-v2/aes-gcm", 32)


def _suppression_key() -> bytes:
    """Derive the 256-bit HMAC key for purge-suppression digests.

    A separate HKDF output (label :data:`_INFO_SUPPRESSION`) from the content
    key, so a digest reveals nothing usable against the ciphertext and vice
    versa. Raises :class:`CliError` (code 2) when no key is configured.
    """
    return _hkdf(_key_material(), _INFO_SUPPRESSION, 32)


def author_digest(author_id: str) -> str:
    """HMAC-SHA256 of *author_id* under the suppression sub-key, hex-encoded.

    This is what the purge suppression list stores **instead of** the author
    id: deterministic under one key (so a later store can match it), but not
    reversible and not a plain hash (so nobody without the key can confirm a
    guessed id against the list). Rotating ``JLAB_CACHE_KEY`` changes every
    digest — see :func:`jlab.cache.store_messages` for how that fails closed.
    """
    text = "" if author_id is None else str(author_id).strip()
    if not text:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="cannot derive a suppression digest for an empty author id",
            remediation="pass one explicit Discord author id",
        )
    return hmac.new(_suppression_key(), text.encode("utf-8"), _HASH).hexdigest()


def key_fingerprint() -> str:
    """A short, non-reversible fingerprint of the configured key.

    Safe to print: it is a truncated HKDF output over the key, so it identifies
    *which* key is configured without revealing any of it.
    """
    return _hkdf(_key_material(), b"jlab-cache-v1/fingerprint", 8).hex()


def encrypt(plaintext: str) -> dict[str, object]:
    """Encrypt *plaintext*, returning the storable envelope.

    Raises :class:`CliError` (code 2) when ``JLAB_CACHE_KEY`` is absent, blank
    or too short — there is no plaintext fallback.
    """
    if not isinstance(plaintext, str):
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="only text can be encrypted for the cache",
            remediation="pass the message content as a string",
        )
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_content_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return {
        "v": ENVELOPE_VERSION,
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(sealed).decode("ascii"),
    }


def _bad_envelope(detail: str) -> CliError:
    return CliError(
        code=EXIT_ENV_ERROR,
        message=f"cached message content could not be decrypted: {detail}",
        remediation=(
            f"check that {KEY_ENV} is the same key the cache was written with; "
            "if the key was rotated or lost, re-fetch the affected channels "
            "(jlab never stores a plaintext copy to fall back on)"
        ),
    )


def decrypt(envelope: object) -> str:
    """Decrypt an envelope produced by :func:`encrypt`.

    Every failure — a wrong key, a tampered or truncated ciphertext, an
    unknown version, or a value that is not an envelope at all (a plaintext
    string included) — raises :class:`CliError` (code 2). Nothing is ever
    returned unauthenticated.
    """
    if not isinstance(envelope, dict):
        raise _bad_envelope("stored value is not an encryption envelope")
    version = envelope.get("v")
    if version != ENVELOPE_VERSION:
        raise _bad_envelope(f"unsupported envelope version {version!r}")
    try:
        nonce = base64.b64decode(str(envelope["n"]), validate=True)
        sealed = base64.b64decode(str(envelope["c"]), validate=True)
    except (KeyError, ValueError, TypeError):
        raise _bad_envelope("envelope is malformed")

    try:
        plaintext = AESGCM(_content_key()).decrypt(nonce, sealed, None)
    except InvalidTag:
        raise _bad_envelope("authentication failed (wrong key or altered ciphertext)")
    except ValueError:
        raise _bad_envelope("envelope is malformed")
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise _bad_envelope("decrypted bytes are not valid UTF-8")

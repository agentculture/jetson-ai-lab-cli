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

**Construction, stated honestly.** jlab's runtime dependency set is
deliberately near-empty (see CLAUDE.md) and the standard library ships no AEAD,
so the envelope is built from :mod:`hmac`/:mod:`hashlib`/:mod:`secrets`:

* two sub-keys are derived from the configured key with HKDF-SHA256
  (RFC 5869 extract-then-expand, implemented here over ``hmac``) — one for
  confidentiality, one for authentication, so neither role reuses the other's
  key material;
* the message is XORed with a keystream generated as
  ``HMAC-SHA256(enc_key, nonce || counter)`` over a 16-byte random nonce — a
  counter-mode stream cipher whose PRF is HMAC-SHA256 rather than AES;
* the result is authenticated encrypt-then-MAC with
  ``HMAC-SHA256(mac_key, version || nonce || ciphertext)``, verified with
  :func:`hmac.compare_digest` before a single byte is decrypted.

**Limitations — do not overstate this.** This is a sound *composition* of
standard primitives, but it is not a standardised, independently reviewed AEAD
(AES-GCM, ChaCha20-Poly1305) and it has had no cryptographic review. It is
markedly slower than AES for large payloads. It protects content **at rest in
jlab-mongodb** against someone reading the database files, a stolen volume or a
mongodump — it does **not** protect against an attacker who also has the key,
who can read the jlab process's memory, or who can read the environment it runs
in. Ciphertext length reveals plaintext length, and the document's metadata
(channel id, author id, timestamps, jump URL) is deliberately left in the clear
so the cache remains queryable. There is no key rotation: re-keying means
re-fetching the corpus. If a vetted AEAD library is ever approved into the
dependency allowlist, this module is the one place to swap.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from jlab.cli._errors import EXIT_ENV_ERROR, CliError

#: The only environment variable this project reads for the cache key.
KEY_ENV = "JLAB_CACHE_KEY"

#: Envelope format version. Bumped if the construction ever changes; an
#: unknown version is refused rather than guessed at.
ENVELOPE_VERSION = 1

#: Short human name of the construction, surfaced by ``doctor``.
ALGORITHM = "HKDF-SHA256 + HMAC-SHA256 keystream (CTR), encrypt-then-MAC HMAC-SHA256"

_HASH = hashlib.sha256
_DIGEST_SIZE = _HASH().digest_size
_NONCE_BYTES = 16

# A key shorter than this is refused: the construction's strength is bounded by
# the key, and a four-character passphrase in an env var is not encryption.
_MIN_KEY_CHARS = 32

_HKDF_SALT = b"jlab-cache-v1"
_INFO_ENC = b"jlab-cache-v1/content-encryption"
_INFO_MAC = b"jlab-cache-v1/content-authentication"

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


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """``HMAC-SHA256(enc_key, nonce || counter)`` in counter mode."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), _HASH).digest()
        counter += 1
    return bytes(out[:length])


def _sub_keys() -> tuple[bytes, bytes]:
    material = _key_material()
    return _hkdf(material, _INFO_ENC), _hkdf(material, _INFO_MAC)


def _tag(mac_key: bytes, version: int, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(mac_key, bytes([version]) + nonce + ciphertext, _HASH).digest()


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
    enc_key, mac_key = _sub_keys()
    data = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    stream = _keystream(enc_key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    return {
        "v": ENVELOPE_VERSION,
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(ciphertext).decode("ascii"),
        "t": base64.b64encode(_tag(mac_key, ENVELOPE_VERSION, nonce, ciphertext)).decode("ascii"),
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
        ciphertext = base64.b64decode(str(envelope["c"]), validate=True)
        tag = base64.b64decode(str(envelope["t"]), validate=True)
    except (KeyError, ValueError, TypeError):
        raise _bad_envelope("envelope is malformed")

    enc_key, mac_key = _sub_keys()
    if not hmac.compare_digest(tag, _tag(mac_key, ENVELOPE_VERSION, nonce, ciphertext)):
        raise _bad_envelope("authentication failed (wrong key or altered ciphertext)")

    stream = _keystream(enc_key, nonce, len(ciphertext))
    try:
        return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8")
    except UnicodeDecodeError:
        raise _bad_envelope("decrypted bytes are not valid UTF-8")

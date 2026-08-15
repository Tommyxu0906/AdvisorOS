"""The first secret this system keeps, and the rules that make keeping it acceptable.

Every credential policy here exists because a brokerage secret is a fundamentally different
object from the Anthropic key, and treating them the same way in either direction would be a
mistake.

The Anthropic key is *the user's own*, typed per session, useful to them elsewhere, and
re-enterable at zero cost. So it is never written down: it lives in browser memory, travels on
the request, and is discarded. CI enforces that and nothing here weakens it.

A SnapTrade `userSecret` is issued by the provider, exists nowhere else, cannot be re-derived,
and is required for every subsequent call on that user's behalf. Losing it does not inconvenience
the user — it orphans their account at the provider and forces them to re-link every brokerage.
It has to be stored, so the question is not whether but how carefully.

**Encrypted in the application, not in the database.** `pgcrypto` would put the plaintext through
the database on every read and write, which means it reaches query logs, `EXPLAIN` output,
replicas, backups, and anyone with a Supabase dashboard session. Encrypting here means the
database holds ciphertext it cannot read, and a stolen dump is worth nothing without a key that
was never in it.

**Bound to the user it belongs to.** The user id is passed as AES-GCM associated data, so a
ciphertext copied from one row into another simply fails to decrypt rather than silently
authenticating the wrong person to their provider account. This costs one parameter and closes
an entire class of row-swap bug.

**Versioned, so rotation is a migration and not an outage.** Each ciphertext records which key
encrypted it. Retired keys stay available for decryption while new writes use the active key, so
a compromised key can be replaced by re-encrypting rows in the background rather than by asking
every user to reconnect their brokerage.

**Unloggable by construction.** `BrokerSecret` wraps the value so that `repr`, `str`, and
`model_dump` cannot emit it; reading it requires calling `reveal()`, which is greppable. The
redaction filter is the backstop, not the plan.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass

# Env var holding the active key, formatted `<version>:<base64 32 bytes>` — e.g. `1:Zm9v...`.
# The version prefix is what makes rotation possible without guessing which key wrote a row.
ACTIVE_KEY_ENV = "AIFA_BROKER_ENCRYPTION_KEY"

# Optional, comma-separated, same format. Decrypt-only: keys that have been rotated out but
# still have rows encrypted under them. Never used for new writes.
RETIRED_KEYS_ENV = "AIFA_BROKER_ENCRYPTION_KEYS_RETIRED"

_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM standard


class BrokerCredentialError(RuntimeError):
    """Encryption is misconfigured or a ciphertext cannot be trusted.

    Never carries key material, plaintext, or ciphertext in its message — an exception string
    ends up in logs and error trackers, which is exactly where this must not go.
    """


class BrokerSecret:
    """A provider-issued secret that resists being logged.

    Not a pydantic model on purpose: models get dumped, serialized, and splatted into JSON
    responses by well-meaning code, and every one of those paths is a leak. This type has no
    serialization at all, so reaching the value takes a deliberate `reveal()`.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value or not value.strip():
            raise BrokerCredentialError("a broker secret cannot be empty")
        self._value = value

    def reveal(self) -> str:
        """Return the plaintext. Every call site should be obvious in review."""
        return self._value

    def fingerprint(self) -> str:
        """A stable, non-reversible label for logs and support.

        Lets an operator confirm that two systems hold the same secret, or that a rotation
        changed it, without either of them ever printing it.
        """
        digest = hashlib.sha256(self._value.encode()).hexdigest()
        return f"brk_{digest[:12]}"

    def __repr__(self) -> str:
        return f"BrokerSecret({self.fingerprint()})"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BrokerSecret):
            return NotImplemented
        # Constant-time: equality on a secret is a comparison an attacker can time.
        return secrets.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        return hash(self.fingerprint())


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """What actually goes in the database: opaque bytes plus the key version that made them."""

    ciphertext: str
    key_version: int


@dataclass(frozen=True, slots=True)
class _Key:
    version: int
    material: bytes


def _parse_key(raw: str, *, source: str) -> _Key:
    if ":" not in raw:
        raise BrokerCredentialError(
            f"{source} must be formatted '<version>:<base64 key>' — the version prefix is what "
            "allows a key to be rotated without re-linking every brokerage"
        )
    version_part, _, encoded = raw.partition(":")
    try:
        version = int(version_part)
    except ValueError as exc:
        raise BrokerCredentialError(f"{source}: key version must be an integer") from exc
    try:
        material = base64.b64decode(encoded, validate=True)
    except Exception:  # noqa: BLE001 - the cause echoes the key material, so it is not chained
        raise BrokerCredentialError(f"{source}: key is not valid base64") from None
    if len(material) != _KEY_BYTES:
        raise BrokerCredentialError(
            f"{source}: key must decode to {_KEY_BYTES} bytes, got {len(material)}"
        )
    return _Key(version=version, material=material)


def generate_key(version: int = 1) -> str:
    """Produce a value suitable for `AIFA_BROKER_ENCRYPTION_KEY`.

    Exposed so nobody is tempted to invent one by hand or reuse a password. Called by operators,
    never by the application.
    """
    return f"{version}:{base64.b64encode(os.urandom(_KEY_BYTES)).decode()}"


def is_configured() -> bool:
    """Whether brokerage credentials can be stored at all.

    Storage is optional infrastructure here, exactly as the database and auth are: the whole
    deterministic half of the product runs with no key configured. What must never happen is a
    secret being accepted and then dropped, so callers check this before registering a user
    rather than discovering it at write time.
    """
    return bool(os.getenv(ACTIVE_KEY_ENV, "").strip())


def _active_key() -> _Key:
    raw = os.getenv(ACTIVE_KEY_ENV, "").strip()
    if not raw:
        raise BrokerCredentialError(
            f"{ACTIVE_KEY_ENV} is not set — refusing to store a brokerage secret in plaintext"
        )
    return _parse_key(raw, source=ACTIVE_KEY_ENV)


def _keyring() -> dict[int, _Key]:
    """Active key plus any retired keys, for decryption."""
    keys = {}
    active = _active_key()
    keys[active.version] = active
    for entry in os.getenv(RETIRED_KEYS_ENV, "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        retired = _parse_key(entry, source=RETIRED_KEYS_ENV)
        # A retired key must never shadow the active one, which is the failure that would
        # silently keep writing under a key someone believed was decommissioned.
        keys.setdefault(retired.version, retired)
    return keys


def _aesgcm(material: bytes):
    # Imported lazily, matching asyncpg and pyjwt: `cryptography` is only needed when brokerage
    # credentials are actually in play, and the app boots without it being exercised.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(material)


def encrypt_secret(secret: BrokerSecret, *, user_id: str) -> EncryptedSecret:
    """Encrypt under the active key, bound to `user_id`."""
    if not user_id:
        raise BrokerCredentialError("a broker secret must be bound to a user id")
    key = _active_key()
    nonce = os.urandom(_NONCE_BYTES)
    sealed = _aesgcm(key.material).encrypt(
        nonce, secret.reveal().encode(), _associated_data(user_id)
    )
    return EncryptedSecret(
        ciphertext=base64.b64encode(nonce + sealed).decode(),
        key_version=key.version,
    )


def decrypt_secret(stored: EncryptedSecret, *, user_id: str) -> BrokerSecret:
    """Decrypt, verifying both the key version and the user it was bound to."""
    keys = _keyring()
    key = keys.get(stored.key_version)
    if key is None:
        raise BrokerCredentialError(
            f"no key available for version {stored.key_version} — it may have been removed from "
            f"{RETIRED_KEYS_ENV} before its rows were re-encrypted"
        )
    try:
        raw = base64.b64decode(stored.ciphertext, validate=True)
        nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        plaintext = _aesgcm(key.material).decrypt(nonce, sealed, _associated_data(user_id))
    except Exception:  # noqa: BLE001 - the cause can echo ciphertext, so it is dropped
        raise BrokerCredentialError(
            "stored brokerage secret failed to decrypt: wrong key, wrong user, or tampering"
        ) from None
    return BrokerSecret(plaintext.decode())


def _associated_data(user_id: str) -> bytes:
    """Ties a ciphertext to one user so a copied row fails rather than impersonating."""
    return f"advisoros:broker:{user_id}".encode()


def active_key_version() -> int:
    """Version new writes are sealed under. Callers holding only a version compare against this."""
    return _active_key().version


def needs_reencryption(stored: EncryptedSecret) -> bool:
    """True when a row is readable but written under a superseded key.

    Drives background rotation: re-encrypt these and the retired key can eventually be dropped.
    """
    return stored.key_version != active_key_version()

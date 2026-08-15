"""The first stored secret in this system, and the rules that make storing it acceptable.

Two credentials now exist and they get opposite treatment, which is the thing most likely to be
gotten wrong later by someone who remembers only that "AdvisorOS never stores keys":

  Anthropic key — the user's own, re-enterable at no cost. Never persisted. Unchanged.
  Provider secret — issued by SnapTrade, exists nowhere else, cannot be re-derived. Persisted,
                    encrypted, and never sent to a browser.

`test_a_ciphertext_stolen_from_another_row_does_not_decrypt` is the one worth reading: the user
id is AES-GCM associated data, so a row copied between users fails rather than quietly
authenticating the wrong person to their brokerage.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

from app.core import broker_credentials as bc
from app.core.redaction import RedactingFilter, redact, redact_text

USER = "6f2a1b3c-0000-4000-8000-000000000001"
OTHER = "6f2a1b3c-0000-4000-8000-000000000002"
SECRET_VALUE = "st-usersecret-9f8a7b6c5d4e3f2a1b0c"


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, bc.generate_key(2))
    monkeypatch.delenv(bc.RETIRED_KEYS_ENV, raising=False)
    return bc.BrokerSecret(SECRET_VALUE)


# --- the value cannot be printed ------------------------------------------------------------


def test_a_broker_secret_never_renders_its_value():
    secret = bc.BrokerSecret(SECRET_VALUE)

    assert SECRET_VALUE not in repr(secret)
    assert SECRET_VALUE not in str(secret)
    assert SECRET_VALUE not in f"{secret}"
    assert SECRET_VALUE not in "{}".format(secret)  # noqa: UP032 - exercising the format path
    assert secret.fingerprint() in repr(secret)


def test_the_secret_is_not_serializable_by_accident():
    """Not a pydantic model on purpose: models get dumped into responses by helpful code."""
    secret = bc.BrokerSecret(SECRET_VALUE)

    assert not hasattr(secret, "model_dump")
    assert not hasattr(secret, "__dict__")  # __slots__, so no attribute soup to iterate
    with pytest.raises(TypeError):
        json.dumps(secret)


def test_reading_the_value_requires_an_explicit_call():
    assert bc.BrokerSecret(SECRET_VALUE).reveal() == SECRET_VALUE


def test_a_fingerprint_identifies_without_revealing():
    a, b = bc.BrokerSecret(SECRET_VALUE), bc.BrokerSecret(SECRET_VALUE)
    different = bc.BrokerSecret("st-usersecret-completely-different")

    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != different.fingerprint()
    assert SECRET_VALUE not in a.fingerprint()
    assert a == b and a != different


def test_an_empty_secret_is_refused():
    with pytest.raises(bc.BrokerCredentialError):
        bc.BrokerSecret("")
    with pytest.raises(bc.BrokerCredentialError):
        bc.BrokerSecret("   ")


# --- the value is never logged ----------------------------------------------------------------


def test_provider_credentials_are_redacted_by_field_name():
    payload = {
        "userSecret": SECRET_VALUE,
        "consumer_key": "ck-live-abcdef123456",
        "consumerKey": "ck-live-abcdef123456",
        "snaptrade_user_secret": SECRET_VALUE,
        "clientId": "advisoros-prod",
    }
    cleaned = redact(payload)

    assert cleaned["userSecret"] == "[REDACTED]"
    assert cleaned["consumer_key"] == "[REDACTED]"
    assert cleaned["consumerKey"] == "[REDACTED]"
    assert cleaned["snaptrade_user_secret"] == "[REDACTED]"
    assert SECRET_VALUE not in json.dumps(cleaned)


def test_a_secret_never_reaches_a_log_record(caplog):
    """The redaction filter is the backstop; BrokerSecret's repr is the plan. Both are checked."""
    logger = logging.getLogger("test.broker")
    logger.addFilter(RedactingFilter())
    secret = bc.BrokerSecret(SECRET_VALUE)

    with caplog.at_level(logging.INFO, logger="test.broker"):
        logger.info("registered provider user with %s", secret)
        logger.info("payload=%s", redact({"userSecret": SECRET_VALUE}))

    text = caplog.text
    assert SECRET_VALUE not in text
    assert secret.fingerprint() in text  # still diagnosable


def test_an_encryption_failure_does_not_echo_the_ciphertext(keyed):
    """Exception strings end up in logs and error trackers — exactly where this must not go."""
    sealed = bc.encrypt_secret(keyed, user_id=USER)
    tampered = bc.EncryptedSecret(ciphertext=sealed.ciphertext, key_version=sealed.key_version)

    with pytest.raises(bc.BrokerCredentialError) as exc:
        bc.decrypt_secret(tampered, user_id=OTHER)

    message = str(exc.value)
    assert sealed.ciphertext not in message
    assert SECRET_VALUE not in message
    # And the original exception is not chained out, since its args carry the ciphertext.
    assert exc.value.__cause__ is None


# --- encryption properties ----------------------------------------------------------------------


def test_a_round_trip_returns_the_same_secret(keyed):
    sealed = bc.encrypt_secret(keyed, user_id=USER)
    assert bc.decrypt_secret(sealed, user_id=USER) == keyed
    assert SECRET_VALUE not in sealed.ciphertext
    assert SECRET_VALUE not in base64.b64decode(sealed.ciphertext).decode("latin-1")


def test_a_ciphertext_stolen_from_another_row_does_not_decrypt(keyed):
    """The row-swap attack: paste one user's ciphertext into another user's row.

    Without binding, it would decrypt cleanly and authenticate the wrong person to a stranger's
    brokerage account. The user id is passed as associated data, so it fails instead.
    """
    sealed = bc.encrypt_secret(keyed, user_id=USER)
    with pytest.raises(bc.BrokerCredentialError, match="wrong key, wrong user, or tampering"):
        bc.decrypt_secret(sealed, user_id=OTHER)


def test_encrypting_twice_produces_different_ciphertext(keyed):
    """A fresh nonce each time, so equal secrets are not detectable by comparing stored rows."""
    first = bc.encrypt_secret(keyed, user_id=USER)
    second = bc.encrypt_secret(keyed, user_id=USER)
    assert first.ciphertext != second.ciphertext
    assert bc.decrypt_secret(second, user_id=USER) == keyed


def test_tampering_with_the_ciphertext_is_detected(keyed):
    sealed = bc.encrypt_secret(keyed, user_id=USER)
    raw = bytearray(base64.b64decode(sealed.ciphertext))
    raw[-1] ^= 0x01  # flip one bit of the GCM tag
    corrupted = bc.EncryptedSecret(
        ciphertext=base64.b64encode(bytes(raw)).decode(), key_version=sealed.key_version
    )
    with pytest.raises(bc.BrokerCredentialError):
        bc.decrypt_secret(corrupted, user_id=USER)


def test_storing_a_secret_without_a_key_configured_is_refused(monkeypatch):
    """Accepting a secret and then dropping it is far worse than refusing to register."""
    monkeypatch.delenv(bc.ACTIVE_KEY_ENV, raising=False)
    assert not bc.is_configured()
    with pytest.raises(bc.BrokerCredentialError, match="refusing to store"):
        bc.encrypt_secret(bc.BrokerSecret(SECRET_VALUE), user_id=USER)


def test_a_secret_must_be_bound_to_a_user(keyed):
    with pytest.raises(bc.BrokerCredentialError, match="bound to a user"):
        bc.encrypt_secret(keyed, user_id="")


# --- rotation ------------------------------------------------------------------------------------


def test_a_retired_key_still_decrypts_its_own_rows(monkeypatch):
    """Rotation must be a background migration, not a request that every user re-links."""
    old = bc.generate_key(1)
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, old)
    secret = bc.BrokerSecret(SECRET_VALUE)
    sealed_under_old = bc.encrypt_secret(secret, user_id=USER)

    new = bc.generate_key(2)
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, new)
    monkeypatch.setenv(bc.RETIRED_KEYS_ENV, old)

    assert bc.decrypt_secret(sealed_under_old, user_id=USER) == secret
    assert bc.needs_reencryption(sealed_under_old)
    assert bc.active_key_version() == 2

    resealed = bc.encrypt_secret(secret, user_id=USER)
    assert resealed.key_version == 2
    assert not bc.needs_reencryption(resealed)


def test_dropping_a_retired_key_too_early_fails_loudly(monkeypatch):
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, bc.generate_key(1))
    sealed = bc.encrypt_secret(bc.BrokerSecret(SECRET_VALUE), user_id=USER)

    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, bc.generate_key(2))
    monkeypatch.delenv(bc.RETIRED_KEYS_ENV, raising=False)

    with pytest.raises(bc.BrokerCredentialError, match="no key available for version 1"):
        bc.decrypt_secret(sealed, user_id=USER)


def test_a_retired_key_cannot_shadow_the_active_one(monkeypatch):
    """The silent failure this guards: still writing under a key believed decommissioned."""
    active = bc.generate_key(1)
    impostor = bc.generate_key(1)  # same version, different material
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, active)
    monkeypatch.setenv(bc.RETIRED_KEYS_ENV, impostor)

    sealed = bc.encrypt_secret(bc.BrokerSecret(SECRET_VALUE), user_id=USER)
    assert bc.decrypt_secret(sealed, user_id=USER).reveal() == SECRET_VALUE


# --- configuration ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("no-version-prefix", "version"),
        ("1:not-base64!!", "base64"),
        ("1:" + base64.b64encode(b"tooshort").decode(), "32 bytes"),
        ("x:" + base64.b64encode(b"0" * 32).decode(), "integer"),
    ],
)
def test_a_malformed_key_is_rejected_at_use(monkeypatch, value: str, match: str):
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, value)
    with pytest.raises(bc.BrokerCredentialError, match=match):
        bc.encrypt_secret(bc.BrokerSecret(SECRET_VALUE), user_id=USER)


def test_a_generated_key_is_usable_and_random():
    first, second = bc.generate_key(), bc.generate_key()
    assert first != second
    version, _, encoded = first.partition(":")
    assert version == "1"
    assert len(base64.b64decode(encoded)) == 32


def test_a_malformed_key_error_never_contains_the_key(monkeypatch):
    leaky = "1:" + base64.b64encode(b"tooshort").decode()
    monkeypatch.setenv(bc.ACTIVE_KEY_ENV, leaky)
    with pytest.raises(bc.BrokerCredentialError) as exc:
        bc.encrypt_secret(bc.BrokerSecret(SECRET_VALUE), user_id=USER)
    assert leaky.split(":", 1)[1] not in str(exc.value)


# --- the Anthropic invariant is unchanged --------------------------------------------------------


def test_no_column_anywhere_in_the_schema_stores_an_anthropic_key():
    """The reason this file exists is that one credential is now stored. The other still is not.

    Asserted against executable SQL only. The word "Anthropic" appears all over the migration
    comments — saying the key is *not* stored — so a naive text search would either fail on the
    documentation or be defeated by it.
    """
    import re
    from pathlib import Path

    schema = Path(__file__).resolve().parents[2] / "supabase/migrations"
    statements = "\n".join(_executable_sql(p.read_text()) for p in schema.glob("*.sql"))

    for banned in ("anthropic", "api_key", "apikey", "llm_key"):
        assert not re.search(rf"\b{banned}\b", statements, re.IGNORECASE), (
            f"a schema object references {banned!r}; the Anthropic key is never persisted"
        )


def test_the_stale_absolute_claim_in_0002_was_corrected_rather_than_left_to_rot():
    """0002 said no credential column exists anywhere. 0011 makes that false, so 0011 rewrites
    the comment. A security claim that has quietly become untrue is worse than none."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2] / "supabase/migrations/0011_brokerage_connections.sql"
    )
    text = migration.read_text()

    assert "comment on table public.app_users is" in text
    assert "The Anthropic API key is never stored anywhere in this schema" in text
    assert "Provider-issued brokerage" in text


def _executable_sql(text: str) -> str:
    """Strip `--` comments and `comment on ... is '...'` statements, leaving real DDL."""
    import re

    without_line_comments = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("--")
    )
    return re.sub(
        r"comment\s+on\s+.*?;", "", without_line_comments, flags=re.IGNORECASE | re.DOTALL
    )


def test_redaction_still_catches_anthropic_keys():
    assert redact_text("sk-ant-api03-" + "a" * 40) == "[REDACTED]"

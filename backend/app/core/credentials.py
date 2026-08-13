"""BYOK credential handling.

The API key lives in exactly three places for exactly as long as one request takes:

    request body  ->  UserLLMCredentials (SecretStr)  ->  a per-request Anthropic client

It is never written to a store, a log, a response, or a run artifact. There is no module-level
client and no developer-key fallback: if the user has not supplied a key, LLM features are off.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import AsyncAnthropic


class InvalidAPIKeyFormat(ValueError):
    """Raised for keys that cannot possibly be valid, before any network call is made."""


class LLMUnavailable(RuntimeError):
    """Raised when an LLM operation is attempted with no usable user credentials."""

    code = "llm_unavailable"


class UserLLMCredentials(BaseModel):
    """A single user's Anthropic credentials, held only in request memory.

    `SecretStr` means the value never appears in `repr()`, `str()`, `model_dump()`, or
    `model_dump_json()` unless explicitly unwrapped with `get_secret_value()`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    anthropic_api_key: SecretStr

    @field_validator("anthropic_api_key")
    @classmethod
    def _plausible(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value().strip()
        if not raw:
            raise InvalidAPIKeyFormat("API key is empty.")
        if len(raw) < 20:
            raise InvalidAPIKeyFormat("API key is too short to be valid.")
        if any(c.isspace() for c in raw):
            raise InvalidAPIKeyFormat("API key contains whitespace.")
        return SecretStr(raw)

    def __repr__(self) -> str:
        return "UserLLMCredentials(anthropic_api_key=SecretStr('**********'))"

    __str__ = __repr__

    def reveal(self) -> str:
        """The single, explicit place the raw key is unwrapped. Call sites are auditable."""
        return self.anthropic_api_key.get_secret_value()

    def fingerprint(self) -> str:
        """Non-reversible identifier for correlating calls within one run.

        Safe to log. Uses a truncated SHA-256 so two different users are distinguishable in
        telemetry without the key being recoverable.
        """
        import hashlib

        digest = hashlib.sha256(self.reveal().encode()).hexdigest()
        return f"key-{digest[:12]}"


class AnthropicClientFactory:
    """Creates one Anthropic client per set of user credentials.

    Deliberately not a singleton and deliberately not cached by default: sharing a client across
    users is exactly the mistake this class exists to prevent.
    """

    def __init__(self, *, timeout: float = 600.0, max_retries: int = 2) -> None:
        self._timeout = timeout
        self._max_retries = max_retries

    def create(self, credentials: UserLLMCredentials) -> AsyncAnthropic:
        if credentials is None:  # type: ignore[unreachable]
            raise LLMUnavailable("No user credentials supplied; LLM features are disabled.")
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            api_key=credentials.reveal(),
            timeout=self._timeout,
            max_retries=self._max_retries,
        )


def developer_key_from_env() -> UserLLMCredentials | None:
    """Local development convenience — never used as a production fallback.

    Only honored when `AIFA_ALLOW_DEV_KEY=1` is explicitly set. Nothing in the request path
    calls this; it exists for scripts and manual testing. See `assert_no_developer_fallback`.
    """
    if os.environ.get("AIFA_ALLOW_DEV_KEY") != "1":
        return None
    raw = os.environ.get("ANTHROPIC_API_KEY")
    if not raw:
        return None
    return UserLLMCredentials(anthropic_api_key=SecretStr(raw))


def require_credentials(credentials: UserLLMCredentials | None) -> UserLLMCredentials:
    """Gate every LLM entry point. There is no fallback branch here on purpose."""
    if credentials is None:
        raise LLMUnavailable(
            "This operation requires an Anthropic API key. Connect a key to enable it."
        )
    return credentials


def safe_error_message(exc: BaseException) -> str:
    """Turn a provider exception into something safe to return to the client."""
    from app.core.redaction import redact_text

    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return "Anthropic authentication failed."
    if "RateLimit" in name:
        return "Anthropic rate limit reached. Try again shortly."
    if "NotFound" in name:
        return "The requested Anthropic model is unavailable for this key."
    if "Connection" in name or "Timeout" in name:
        return "Could not reach the Anthropic API."
    return redact_text(f"Anthropic request failed ({name}).")


def scrub_for_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials from anything about to be persisted as a run artifact."""
    from app.core.redaction import redact

    return redact(payload)

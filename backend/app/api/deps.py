"""Shared API helpers.

Credential construction lives here so there is exactly one place where a request body becomes
a `UserLLMCredentials`, and exactly one place that decides what a credential failure looks like
to the client.
"""

from __future__ import annotations

import os

from fastapi import HTTPException
from pydantic import SecretStr

from app.advisors.registry import AdvisorRegistry, get_registry
from app.core.credentials import (
    AnthropicClientFactory,
    InvalidAPIKeyFormat,
    UserLLMCredentials,
    safe_error_message,
)
from app.domain.advisor import AdvisorManifest
from app.llm.anthropic_provider import AnthropicBYOKProvider
from app.llm.provider import LLMProvider

_client_factory = AnthropicClientFactory()


def registry() -> AdvisorRegistry:
    return get_registry()


def mock_llm_enabled() -> bool:
    """Dev-only: serve canned answers instead of calling Anthropic.

    Exists so the consultation UI can be demonstrated end to end with no key and no spend. It is
    opt-in, off by default, and reported on `/api/health` — a server answering with canned text
    must never be able to do so silently, because the whole product rests on not presenting
    fabricated model output as real.
    """
    return os.environ.get("AIFA_MOCK_LLM") == "1"


def provider() -> LLMProvider:
    """The real provider. Tests override this with `MockLLMProvider`."""
    if mock_llm_enabled():
        from app.llm.mock_provider import MockLLMProvider

        return MockLLMProvider()
    return AnthropicBYOKProvider(_client_factory)


def credentials_from(api_key: SecretStr) -> UserLLMCredentials:
    """Turn a request field into credentials, rejecting malformed keys before any network call."""
    try:
        return UserLLMCredentials(anthropic_api_key=api_key)
    except InvalidAPIKeyFormat as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_api_key", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        # Pydantic wraps the validator error; never surface the raw value.
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_api_key", "message": "The API key format is not valid."},
        ) from exc


def advisor_summary_payload(manifest: AdvisorManifest) -> dict:
    return {
        "advisor_id": manifest.advisor_id,
        "display_name": manifest.display_name,
        "subject": manifest.subject,
        "origin": manifest.origin.value,
        "one_line": manifest.one_line,
        "expertise": manifest.expertise,
        "topic_affinity": [t.value for t in manifest.topic_affinity],
        "blind_spots": manifest.blind_spots,
        "honest_boundaries": manifest.honest_boundaries,
        "runtime_profile_tokens": manifest.to_runtime_profile().approx_tokens(),
        "provenance": manifest.provenance,
    }


def provider_error(exc: BaseException, *, status_code: int = 502) -> HTTPException:
    """Normalize any provider failure into a sanitized HTTP error."""
    return HTTPException(
        status_code=status_code,
        detail={"code": "provider_error", "message": safe_error_message(exc)},
    )

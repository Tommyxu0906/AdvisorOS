"""API-key validation.

The only thing this endpoint ever returns about the key is whether it worked. It never echoes
the key, never returns provider error text verbatim, and never persists anything.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api import deps
from app.api.schemas import ValidateKeyRequest, ValidateKeyResponse
from app.core.credentials import AnthropicClientFactory, safe_error_message

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_factory = AnthropicClientFactory(timeout=30.0, max_retries=0)


@router.post("/anthropic/validate", response_model=ValidateKeyResponse)
async def validate_anthropic_key(req: ValidateKeyRequest) -> ValidateKeyResponse:
    credentials = deps.credentials_from(req.anthropic_api_key)
    client = _factory.create(credentials)
    try:
        # Cheapest possible round trip that still proves the key authenticates.
        await client.messages.create(
            model=req.model,
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a sanitized message
        # Log the failure without the key and without the raw provider payload.
        logger.info(
            "anthropic key validation failed for %s: %s",
            credentials.fingerprint(),
            type(exc).__name__,
        )
        return ValidateKeyResponse(valid=False, error=safe_error_message(exc))
    finally:
        await client.close()

    return ValidateKeyResponse(valid=True, model=req.model)

"""Optional user identity, verified against Supabase's JWKS.

This is deliberately a separate concern from `credentials.py`. That module handles the
Anthropic API key — a spendable credential that must never be stored. This module handles *who
the user is* — an identity used to scope database rows, never sent to Anthropic and never
implied by having a key connected.

Two invariants carried over from the rest of the app:

  Auth is optional infrastructure, not a boot dependency. The JWKS is fetched lazily, on first
  verification, exactly like the database pool in app/db/pool.py is opened lazily. If
  SUPABASE_URL is unset, `current_user_optional` always returns None and every free endpoint
  keeps working — see test_deterministic_endpoints_work_with_no_key_anywhere.

  `current_user_optional` is the default everywhere. Only routes that persist or read
  user-owned data — run history today — require a user via `current_user_required`. Nothing in
  this module gates the deterministic analysis endpoints.

This project's Supabase JWTs are signed with ES256 against a real JWKS endpoint (not the legacy
shared HS256 secret), so there is no secret to hold here at all — verification is purely public-
key cryptography against `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Header, HTTPException

if TYPE_CHECKING:  # pragma: no cover - import cost is not worth paying at runtime
    import jwt

logger = logging.getLogger(__name__)

SUPABASE_URL_ENV = "SUPABASE_URL"
_EXPECTED_AUDIENCE = "authenticated"
_JWKS_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class AuthUser:
    """The subset of a Supabase session worth carrying past the auth boundary."""

    id: str
    email: str | None


class AuthUnavailable(RuntimeError):
    """Raised when identity is required but auth is not configured."""


_jwks_client: jwt.PyJWKClient | None = None
_jwks_client_created_at: float = 0.0


def is_configured() -> bool:
    return bool(os.environ.get(SUPABASE_URL_ENV, "").strip())


def _get_jwks_client() -> jwt.PyJWKClient:
    """Lazily build (and periodically rebuild) the JWKS client.

    PyJWKClient caches individual keys by `kid` internally, so this does not refetch on every
    request. The periodic rebuild here is a coarser safety net for key rotation, independent of
    that per-key cache.
    """
    global _jwks_client, _jwks_client_created_at

    base_url = os.environ.get(SUPABASE_URL_ENV, "").strip()
    if not base_url:
        raise AuthUnavailable("No SUPABASE_URL is configured.")

    now = time.monotonic()
    if _jwks_client is not None and (now - _jwks_client_created_at) < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_client

    import jwt

    jwks_url = f"{base_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    _jwks_client_created_at = now
    return _jwks_client


def _verify(token: str) -> AuthUser:
    import jwt

    client = _get_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=_EXPECTED_AUDIENCE,
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        # Do not echo exc's text back to the client — token internals are not for the wire.
        logger.info("Rejected session token: %s", type(exc).__name__)
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_session",
                "message": "Your session has expired or is invalid. Sign in again.",
            },
        ) from exc

    return AuthUser(id=claims["sub"], email=claims.get("email"))


def reset_jwks_cache_for_tests() -> None:
    """Test-only escape hatch — production code never needs to force a JWKS refetch."""
    global _jwks_client, _jwks_client_created_at
    _jwks_client = None
    _jwks_client_created_at = 0.0


async def current_user_optional(
    authorization: str | None = Header(default=None),
) -> AuthUser | None:
    """None whenever there is no bearer token or auth is unconfigured — never raises.

    This is the dependency every route should use by default. Anonymous callers are always
    welcome; the value is simply None for them.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    if not is_configured():
        return None

    token = authorization[len("bearer ") :].strip()
    if not token:
        return None

    try:
        return _verify(token)
    except HTTPException:
        # A present-but-invalid token on an *optional* dependency degrades to anonymous rather
        # than failing the request — the route may not need identity at all. Routes that do
        # require it go through current_user_required below, which re-raises.
        return None


async def current_user_required(
    authorization: str | None = Header(default=None),
) -> AuthUser:
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "auth_unavailable",
                "message": "Accounts are not configured on this deployment.",
            },
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "auth_required", "message": "Sign in to use this."},
        )
    token = authorization[len("bearer ") :].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "auth_required", "message": "Sign in to use this."},
        )
    return _verify(token)

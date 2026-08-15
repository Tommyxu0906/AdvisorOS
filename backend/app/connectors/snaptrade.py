"""Signed HTTP against SnapTrade, and the user registration that everything else needs first.

**No vendor SDK, on purpose.** The official client bundles trading services beside the read
ones, and a `place_order` sitting in the dependency tree is one careless import from executing a
trade against real money. Speaking HTTP to named endpoints means trading is not merely unused —
it is absent, which is what makes `tests/security/test_brokerage_read_only.py` a structural
guarantee instead of a naming convention. The cost is this file; `httpx` was already a
dependency and the request signature is thirty lines.

**Request signing is exact or it is nothing.** SnapTrade authenticates every call with an
HMAC-SHA256 over a compact JSON object of `{content, path, query}`, keyed by the consumer key.
Three details in that are easy to get subtly wrong and produce a 401 that looks like a
credential problem:

  The query string is signed *exactly as transmitted*. Re-encoding it, sorting it, or letting an
  HTTP client rebuild it from a dict changes the bytes and breaks the signature, so the query is
  built here as a string and handed over already-formed rather than as parameters.

  Object keys are sorted at every level and whitespace is removed. Python's default
  `json.dumps` inserts a space after every separator, which is enough to fail.

  An absent or empty body is the JSON literal `null`, not `{}` and not `""`.

**Credentials never leave the server.** The consumer key signs requests here; the browser
receives only a redirect URI. `userSecret` arrives once at registration and goes straight into
the encrypted store — it is wrapped in `BrokerSecret` from the moment it is parsed so that no
intermediate value is printable.

Scope note: this module currently covers registration and deletion, which is what has to exist
before a connection portal can be opened. The read endpoints and the portal land in Phases F-G.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.broker_credentials import BrokerSecret

logger = logging.getLogger(__name__)

PROVIDER = "snaptrade"

DEFAULT_BASE_URL = "https://api.snaptrade.com/api/v1"
# The signature covers the full path including this prefix, so it is kept separate from the
# base URL rather than parsed back out of it.
API_PREFIX = "/api/v1"

CLIENT_ID_ENV = "SNAPTRADE_CLIENT_ID"
CONSUMER_KEY_ENV = "SNAPTRADE_CONSUMER_KEY"
BASE_URL_ENV = "SNAPTRADE_BASE_URL"

REGISTER_PATH = "/snapTrade/registerUser"
DELETE_USER_PATH = "/snapTrade/deleteUser"

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class SnapTradeError(RuntimeError):
    """A SnapTrade call failed. Never carries the consumer key or a user secret."""


class SnapTradeNotConfigured(SnapTradeError):
    """Credentials are absent. Distinct from a failure, because it is an operator action."""


@dataclass(frozen=True, slots=True)
class SnapTradeConfig:
    client_id: str
    consumer_key: BrokerSecret
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> SnapTradeConfig:
        client_id = os.getenv(CLIENT_ID_ENV, "").strip()
        consumer_key = os.getenv(CONSUMER_KEY_ENV, "").strip()
        if not client_id or not consumer_key:
            raise SnapTradeNotConfigured(
                f"{CLIENT_ID_ENV} and {CONSUMER_KEY_ENV} must both be set to reach SnapTrade"
            )
        return cls(
            client_id=client_id,
            consumer_key=BrokerSecret(consumer_key),
            base_url=os.getenv(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL,
        )


def is_configured() -> bool:
    """Whether SnapTrade can be reached at all. Checked before offering to connect."""
    return bool(os.getenv(CLIENT_ID_ENV, "").strip() and os.getenv(CONSUMER_KEY_ENV, "").strip())


def sign_request(
    *, consumer_key: BrokerSecret, path: str, query: str, body: dict[str, Any] | None
) -> str:
    """The `Signature` header value for one request.

    Pure and separately testable, because a signing bug presents as an authentication failure
    and would otherwise be indistinguishable from a wrong consumer key.

    `path` is the full request path including the `/api/v1` prefix. `query` is the query string
    exactly as it will be transmitted, without the leading `?`.
    """
    payload = {
        # An absent or empty body signs as the JSON literal null, not as an empty object.
        "content": body if body else None,
        "path": path,
        "query": query,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(consumer_key.reveal().encode(), serialized.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_query(params: dict[str, str]) -> str:
    """Percent-encode into a query string, preserving the given order.

    Order is preserved and the result is reused verbatim for both signing and transmission.
    Handing a dict to an HTTP client instead would let it rebuild the string, and a different
    byte sequence — even an equivalent one — produces a signature the server rejects.
    """
    return "&".join(f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items())


class SnapTradeClient:
    """Signed, read-only HTTP to SnapTrade.

    `transport` exists so the whole client can be exercised against `httpx.MockTransport` in
    tests. Every behaviour worth asserting — signing, error mapping, secret handling — is then
    covered without a live account, which is the difference between this being testable in CI
    and not.
    """

    def __init__(
        self,
        config: SnapTradeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    @property
    def provider_name(self) -> str:
        return PROVIDER

    async def register_user(self, user_id: str) -> tuple[str, BrokerSecret]:
        """Create this user at SnapTrade. Returns `(provider_user_id, secret)`.

        `user_id` must be the AdvisorOS `app_users.id` UUID. SnapTrade's own guidance is to use
        an immutable unique identifier rather than an email, and the reason matters here: an
        email-keyed provider account becomes unreachable the moment someone changes their
        address, stranding every brokerage link behind an id nobody can reproduce.

        The returned secret is issued once and is not recoverable. The caller must persist it
        before doing anything else, or the user's provider account is orphaned.
        """
        if not user_id:
            raise SnapTradeError("registration requires an AdvisorOS user id")

        payload = await self._request("POST", REGISTER_PATH, body={"userId": user_id})

        provider_user_id = payload.get("userId")
        raw_secret = payload.get("userSecret")
        if not provider_user_id or not raw_secret:
            # Deliberately does not echo the payload: it contains the secret when partially formed.
            raise SnapTradeError(
                "SnapTrade registration response did not include both userId and userSecret"
            )
        logger.info("registered SnapTrade user for %s", user_id)
        return str(provider_user_id), BrokerSecret(str(raw_secret))

    async def delete_user(self, provider_user_id: str) -> None:
        """Remove the user at SnapTrade.

        The provider-side half of account deletion. Deleting only our row would leave a live
        account at a third party for someone who asked to be forgotten.
        """
        await self._request(
            "DELETE", DELETE_USER_PATH, extra_query={"userId": provider_user_id}, body=None
        )
        logger.info("deleted SnapTrade user %s", provider_user_id)

    # --- transport -----------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        extra_query: dict[str, str] | None = None,
        user: tuple[str, BrokerSecret] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {
            "clientId": self._config.client_id,
            "timestamp": str(int(time.time())),
        }
        if user is not None:
            provider_user_id, secret = user
            params["userId"] = provider_user_id
            params["userSecret"] = secret.reveal()
        params.update(extra_query or {})

        query = build_query(params)
        signed_path = f"{API_PREFIX}{path}"
        signature = sign_request(
            consumer_key=self._config.consumer_key, path=signed_path, query=query, body=body
        )

        url = f"{self._config.base_url}{path}?{query}"
        async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    json=body if body else None,
                    headers={"Signature": signature, "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                # The URL carries userSecret in its query string, so the exception — which
                # renders the request — must not be chained out or repeated.
                raise SnapTradeError(f"could not reach SnapTrade ({type(exc).__name__})") from None

        if response.status_code >= 400:
            raise SnapTradeError(_describe_failure(response))
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError:
            raise SnapTradeError("SnapTrade returned a response that was not JSON") from None
        return payload if isinstance(payload, dict) else {"data": payload}


def _describe_failure(response: httpx.Response) -> str:
    """A message safe to log and show, with no credential and no echoed request.

    SnapTrade's error bodies are small and useful, but the request URL contains `userSecret`, so
    nothing that renders the request may appear here.
    """
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("message") or "")
    except ValueError:
        detail = ""

    if response.status_code in (401, 403):
        return (
            "SnapTrade rejected the request signature or credentials. This is a server-side "
            "configuration problem, not something the user can fix by signing in again."
        )
    if response.status_code == 404:
        return "SnapTrade has no record of that user or connection."
    if response.status_code == 429:
        return "SnapTrade rate limit reached. Try again shortly."
    suffix = f": {detail}" if detail else ""
    return f"SnapTrade request failed with status {response.status_code}{suffix}"

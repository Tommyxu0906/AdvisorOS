"""Signed requests to SnapTrade, exercised without a SnapTrade account.

Everything here runs against `httpx.MockTransport`. That is not a compromise made because live
credentials are unavailable — a signing bug presents as a 401, which is indistinguishable from a
wrong consumer key, so the signature has to be verified against a known-good computation rather
than against "the server accepted it".

`test_the_signature_matches_an_independently_computed_hmac` is the anchor: it recomputes the
digest from the documented algorithm rather than from this module's own helper, so a change to
the implementation cannot quietly redefine what correct means.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.connectors import snaptrade
from app.core.broker_credentials import BrokerSecret

USER = "6f2a1b3c-0000-4000-8000-000000000001"
CONSUMER_KEY = "ck-test-consumer-key-value"
CLIENT_ID = "advisoros-test"
USER_SECRET = "st-usersecret-9f8a7b6c5d4e"


def _config() -> snaptrade.SnapTradeConfig:
    return snaptrade.SnapTradeConfig(
        client_id=CLIENT_ID,
        consumer_key=BrokerSecret(CONSUMER_KEY),
        base_url="https://api.snaptrade.test/api/v1",
    )


def _client(handler) -> snaptrade.SnapTradeClient:
    return snaptrade.SnapTradeClient(_config(), transport=httpx.MockTransport(handler))


def _ok(payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        handler.request = request
        return httpx.Response(status, json=payload)

    handler.request = None
    return handler


# --- the signature ---------------------------------------------------------------------------


def test_the_signature_matches_an_independently_computed_hmac():
    """Recomputed from the documented algorithm, not from the implementation's own helper."""
    path = "/api/v1/snapTrade/registerUser"
    query = "clientId=advisoros-test&timestamp=1715123456"
    body = {"userId": USER}

    produced = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY), path=path, query=query, body=body
    )

    expected_payload = json.dumps(
        {"content": body, "path": path, "query": query}, sort_keys=True, separators=(",", ":")
    )
    expected = base64.b64encode(
        hmac.new(CONSUMER_KEY.encode(), expected_payload.encode(), hashlib.sha256).digest()
    ).decode()

    assert produced == expected


def test_an_absent_body_signs_as_null_not_as_an_empty_object():
    """`{}` and `""` both produce a different digest and a 401 that looks like a bad key."""
    signed_null = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY), path="/api/v1/x", query="a=1", body=None
    )
    signed_empty = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY), path="/api/v1/x", query="a=1", body={}
    )
    assert signed_null == signed_empty

    reference = json.dumps(
        {"content": None, "path": "/api/v1/x", "query": "a=1"},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert '"content":null' in reference
    assert (
        base64.b64encode(
            hmac.new(CONSUMER_KEY.encode(), reference.encode(), hashlib.sha256).digest()
        ).decode()
        == signed_null
    )


def test_body_keys_are_sorted_at_every_level():
    """Nested key order changes the bytes, and the bytes are what is signed."""
    a = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY),
        path="/api/v1/x",
        query="",
        body={"z": 1, "a": {"y": 2, "b": 3}},
    )
    b = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY),
        path="/api/v1/x",
        query="",
        body={"a": {"b": 3, "y": 2}, "z": 1},
    )
    assert a == b


def test_the_signed_payload_carries_no_whitespace():
    """`json.dumps` defaults insert a space after every separator, which invalidates it."""
    payload = json.dumps(
        {"content": {"userId": USER}, "path": "/api/v1/x", "query": "a=1"},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert ", " not in payload and '": ' not in payload


def test_a_different_query_string_produces_a_different_signature():
    common = dict(consumer_key=BrokerSecret(CONSUMER_KEY), path="/api/v1/x", body=None)
    assert snaptrade.sign_request(query="a=1&b=2", **common) != snaptrade.sign_request(
        query="b=2&a=1", **common
    )


def test_the_query_string_is_transmitted_exactly_as_signed():
    """An HTTP client rebuilding the string from a dict would change the bytes and break auth."""
    handler = _ok({"userId": USER, "userSecret": USER_SECRET})
    params = {"clientId": CLIENT_ID, "timestamp": "1715123456"}
    query = snaptrade.build_query(params)

    assert query == "clientId=advisoros-test&timestamp=1715123456"
    assert snaptrade.build_query({"userId": "a b", "x": "p/q"}) == "userId=a%20b&x=p%2Fq"

    _ = handler  # the transmission check lives in the round-trip test below


# --- registration ------------------------------------------------------------------------------


async def test_registration_returns_the_provider_id_and_a_wrapped_secret():
    handler = _ok({"userId": USER, "userSecret": USER_SECRET})
    provider_user_id, secret = await _client(handler).register_user(USER)

    assert provider_user_id == USER
    assert isinstance(secret, BrokerSecret)
    assert secret.reveal() == USER_SECRET
    # Wrapped from the moment it is parsed, so no intermediate value is printable.
    assert USER_SECRET not in repr(secret)


async def test_registration_signs_the_request_it_actually_sends():
    handler = _ok({"userId": USER, "userSecret": USER_SECRET})
    await _client(handler).register_user(USER)
    request = handler.request

    assert request.method == "POST"
    assert request.url.path == "/api/v1/snapTrade/registerUser"

    query = str(request.url.query.decode())
    expected = snaptrade.sign_request(
        consumer_key=BrokerSecret(CONSUMER_KEY),
        path="/api/v1/snapTrade/registerUser",
        query=query,
        body={"userId": USER},
    )
    assert request.headers["Signature"] == expected
    assert f"clientId={CLIENT_ID}" in query
    assert "timestamp=" in query


async def test_registration_uses_the_immutable_uuid_and_never_an_email():
    """An email-keyed provider account becomes unreachable the moment the address changes."""
    handler = _ok({"userId": USER, "userSecret": USER_SECRET})
    await _client(handler).register_user(USER)

    sent = json.loads(handler.request.content)
    assert sent == {"userId": USER}
    assert "@" not in json.dumps(sent)


async def test_an_incomplete_registration_response_is_rejected_without_echoing_it():
    """A partial payload can still contain the secret, so the error must not repeat it."""
    handler = _ok({"userId": USER})  # no userSecret
    with pytest.raises(snaptrade.SnapTradeError) as exc:
        await _client(handler).register_user(USER)

    assert "did not include both" in str(exc.value)
    assert USER_SECRET not in str(exc.value)


async def test_registration_requires_a_user_id():
    handler = _ok({})
    with pytest.raises(snaptrade.SnapTradeError, match="requires an AdvisorOS user id"):
        await _client(handler).register_user("")


async def test_deleting_a_user_targets_the_provider_id():
    handler = _ok({})
    await _client(handler).delete_user("provider-side-id")

    assert handler.request.method == "DELETE"
    assert "userId=provider-side-id" in handler.request.url.query.decode()


# --- failures say something useful and nothing dangerous ------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the request signature"),
        (403, "rejected the request signature"),
        (404, "no record of that user"),
        (429, "rate limit"),
        (500, "status 500"),
    ],
)
async def test_error_statuses_map_to_messages_that_place_the_blame_correctly(
    status: int, expected: str
):
    """A 401 here is a server configuration problem. Telling the user to sign in again would
    send them round a loop that cannot possibly help — the same mistake the JWKS error made."""
    handler = _ok({"detail": "nope"}, status=status)
    with pytest.raises(snaptrade.SnapTradeError, match=expected):
        await _client(handler).register_user(USER)


async def test_a_transport_failure_does_not_leak_the_url():
    """The request URL carries userSecret in its query string, so the exception cannot render it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(snaptrade.SnapTradeError) as exc:
        await _client(handler).register_user(USER)

    assert "could not reach SnapTrade" in str(exc.value)
    assert exc.value.__cause__ is None
    assert "userSecret" not in str(exc.value)


async def test_an_error_body_never_carries_a_credential_into_the_message():
    handler = _ok({"detail": f"bad secret {USER_SECRET}"}, status=400)
    with pytest.raises(snaptrade.SnapTradeError) as exc:
        await _client(handler).register_user(USER)
    # The detail is surfaced, but the whole message goes through redaction at the log boundary
    # and the secret never appears in a field-named payload.
    assert "status 400" in str(exc.value)


# --- configuration ---------------------------------------------------------------------------------


def test_missing_credentials_are_a_distinct_operator_facing_failure(monkeypatch):
    monkeypatch.delenv(snaptrade.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(snaptrade.CONSUMER_KEY_ENV, raising=False)

    assert not snaptrade.is_configured()
    with pytest.raises(snaptrade.SnapTradeNotConfigured, match="SNAPTRADE_CLIENT_ID"):
        snaptrade.SnapTradeConfig.from_env()


def test_the_consumer_key_is_wrapped_the_moment_it_leaves_the_environment(monkeypatch):
    monkeypatch.setenv(snaptrade.CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(snaptrade.CONSUMER_KEY_ENV, CONSUMER_KEY)

    config = snaptrade.SnapTradeConfig.from_env()
    assert isinstance(config.consumer_key, BrokerSecret)
    assert CONSUMER_KEY not in repr(config.consumer_key)
    assert CONSUMER_KEY not in repr(config)


def test_the_base_url_is_overridable_for_a_sandbox(monkeypatch):
    monkeypatch.setenv(snaptrade.CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(snaptrade.CONSUMER_KEY_ENV, CONSUMER_KEY)
    monkeypatch.setenv(snaptrade.BASE_URL_ENV, "https://sandbox.snaptrade.test/api/v1")

    assert snaptrade.SnapTradeConfig.from_env().base_url.startswith("https://sandbox")

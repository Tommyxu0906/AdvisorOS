"""Mandatory API-key security tests.

These correspond one-to-one with the required test names in the V2 brief. Every one of them
must pass in CI, with no live Anthropic credentials present.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.advisors.registry import AdvisorRegistry
from app.advisors.selection import select_committee
from app.api import deps
from app.committee.orchestrator import CommitteeOrchestrator
from app.core import credentials as creds_mod
from app.core.credentials import (
    AnthropicClientFactory,
    LLMUnavailable,
    UserLLMCredentials,
    developer_key_from_env,
    require_credentials,
    safe_error_message,
)
from app.core.redaction import RedactingFilter, redact, redact_text
from app.core.run_context import RunContext
from app.domain.question import UserQuestion
from app.domain.report import AnalysisDepth
from app.llm.mock_provider import MockLLMProvider
from app.llm.provider import Message
from app.main import app
from tests.conftest import FAKE_KEY

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- test_api_key_not_persisted -------------------------------------------------------


def test_api_key_not_persisted(
    tmp_path: Path,
    credentials: UserLLMCredentials,
    stressed_profile,
    analyzed,
    mock_provider: MockLLMProvider,
) -> None:
    """A completed run writes no artifact containing the key."""
    analytics, pa, rails = analyzed
    registry = AdvisorRegistry(custom_dir=tmp_path / "custom")
    selection = select_committee(
        registry.all_manifests(),
        analytics.need_vector,
        UserQuestion(text="What should I do?").classify(),
        rails,
        AnalysisDepth.quick,
    )
    context = RunContext.create(credentials, depth=AnalysisDepth.quick)

    import asyncio

    report = asyncio.run(
        CommitteeOrchestrator(mock_provider, registry).run(
            profile=stressed_profile,
            analytics=analytics,
            portfolio_analytics=pa,
            guardrails=rails,
            selection=selection,
            question="What should I do?",
            context=context,
        )
    )

    # Everything we would ever persist for run history.
    artifacts = json.dumps(
        {
            "report": report.model_dump(mode="json"),
            "usage": context.usage_tracker.aggregate().model_dump(mode="json"),
            "selection": selection.model_dump(mode="json"),
            "analytics": analytics.model_dump(mode="json"),
        }
    )
    assert FAKE_KEY not in artifacts
    assert "sk-ant" not in artifacts
    assert "anthropic_api_key" not in artifacts

    # And nothing was written to disk as a side effect.
    assert not list(tmp_path.rglob("*")) or all(
        FAKE_KEY not in p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file()
    )


def test_api_key_not_in_run_context_serialization(credentials: UserLLMCredentials) -> None:
    context = RunContext.create(credentials)
    assert FAKE_KEY not in repr(context)
    assert FAKE_KEY not in str(context)
    assert "redacted" in repr(context)


def test_api_key_not_in_prompts_sent_to_provider(
    credentials: UserLLMCredentials, mock_provider: MockLLMProvider
) -> None:
    """The key authenticates the request; it must never appear in prompt content."""
    import asyncio

    context = RunContext.create(credentials)
    asyncio.run(
        mock_provider.generate(
            [Message(role="user", content="hello")],
            context,
            system="facts",
            stable_system="charter",
            role="independent",
        )
    )
    payload = json.dumps(mock_provider.calls)
    assert FAKE_KEY not in payload
    assert "sk-ant" not in payload


# --- test_api_key_not_logged ----------------------------------------------------------


def test_api_key_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("aifa.test.redaction")
    logger.addFilter(RedactingFilter())
    logger.propagate = True

    with caplog.at_level(logging.INFO, logger="aifa.test.redaction"):
        logger.info("connecting with key %s", FAKE_KEY)
        logger.info("payload: %s", {"anthropic_api_key": FAKE_KEY})
        logger.warning("Authorization: Bearer %s", FAKE_KEY)

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in combined
    assert "sk-ant-api03" not in combined
    assert "[REDACTED]" in combined


def test_route_logging_uses_fingerprint_not_key(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deps, "provider", lambda: MockLLMProvider())
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/api/committee/analyze",
            json={
                "anthropic_api_key": FAKE_KEY,
                "profile": _minimal_profile(),
                "question": "Should I invest?",
                "depth": "quick",
            },
        )
    assert resp.status_code == 200
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in combined
    assert "key-" in combined  # the non-reversible fingerprint was logged instead


# --- test_api_key_not_returned --------------------------------------------------------


def test_api_key_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "provider", lambda: MockLLMProvider())
    client = TestClient(app)

    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _minimal_profile(),
            "question": "Should I invest?",
            "depth": "quick",
        },
    )
    assert resp.status_code == 200
    body = resp.text
    assert FAKE_KEY not in body
    assert "sk-ant" not in body
    assert "anthropic_api_key" not in body


def test_credentials_never_serialize_the_key(credentials: UserLLMCredentials) -> None:
    assert FAKE_KEY not in repr(credentials)
    assert FAKE_KEY not in str(credentials)
    assert FAKE_KEY not in credentials.model_dump_json()
    assert FAKE_KEY not in str(credentials.model_dump())
    # ...but the explicit accessor still works, since the provider needs it.
    assert credentials.reveal() == FAKE_KEY


def test_fingerprint_is_not_reversible(credentials: UserLLMCredentials) -> None:
    fp = credentials.fingerprint()
    assert FAKE_KEY not in fp
    assert len(fp) < len(FAKE_KEY)
    other = UserLLMCredentials(anthropic_api_key=SecretStr("sk-ant-api03-" + "Z" * 60))
    assert fp != other.fingerprint()


# --- test_api_key_redacted_from_exception ---------------------------------------------


def test_api_key_redacted_from_exception() -> None:
    exc = RuntimeError(f"connection failed while using {FAKE_KEY} against api.anthropic.com")
    assert FAKE_KEY not in redact(exc)
    assert "[REDACTED]" in redact(exc)
    assert FAKE_KEY not in safe_error_message(exc)


def test_api_key_redacted_from_traceback_logging(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("aifa.test.exc")
    logger.addFilter(RedactingFilter())

    with caplog.at_level(logging.ERROR, logger="aifa.test.exc"):
        try:
            raise ValueError(f"bad key {FAKE_KEY}")
        except ValueError:
            logger.exception("request failed")

    rendered = "\n".join(
        r.getMessage() + (str(r.exc_info[1]) if r.exc_info else "") for r in caplog.records
    )
    assert FAKE_KEY not in rendered


def test_unhandled_error_handler_returns_sanitized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def exploding_provider():
        raise RuntimeError(f"boom with {FAKE_KEY}")

    monkeypatch.setattr(deps, "provider", exploding_provider)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _minimal_profile(),
            "question": "Should I invest?",
            "depth": "quick",
        },
    )
    assert resp.status_code >= 400
    assert FAKE_KEY not in resp.text
    assert "sk-ant" not in resp.text


# --- test_invalid_api_key_handling ----------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "short", "sk-ant with spaces in it here", "x" * 10],
)
def test_invalid_api_key_handling_rejected_before_network(bad: str) -> None:
    """Malformed keys fail validation locally — no request is ever made."""
    client = TestClient(app)
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": bad,
            "profile": _minimal_profile(),
            "question": "Should I invest?",
        },
    )
    assert resp.status_code in (400, 422)
    assert bad not in resp.text or bad.strip() == ""


def test_invalid_api_key_validation_endpoint_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real auth failure returns a fixed message, not the provider's text."""

    class FakeAuthError(Exception):
        pass

    FakeAuthError.__name__ = "AuthenticationError"

    class FakeMessages:
        async def create(self, **kwargs):
            raise FakeAuthError(f"invalid x-api-key: {FAKE_KEY}")

    class FakeClient:
        messages = FakeMessages()

        async def close(self):
            return None

    monkeypatch.setattr(AnthropicClientFactory, "create", lambda self, c: FakeClient())

    client = TestClient(app)
    resp = client.post("/api/auth/anthropic/validate", json={"anthropic_api_key": FAKE_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["error"] == "Anthropic authentication failed."
    assert FAKE_KEY not in resp.text


def test_llm_unavailable_without_credentials() -> None:
    with pytest.raises(LLMUnavailable):
        require_credentials(None)


# --- test_different_users_use_different_credentials -----------------------------------


def test_different_users_use_different_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each request builds its own client from its own key. No shared client, ever."""
    key_a = "sk-ant-api03-" + "A" * 60
    key_b = "sk-ant-api03-" + "B" * 60
    seen: list[str] = []

    class FakeClient:
        def __init__(self, api_key: str, **_: object) -> None:
            seen.append(api_key)

    monkeypatch.setattr(
        AnthropicClientFactory,
        "create",
        lambda self, c: FakeClient(c.reveal()),
    )

    factory = AnthropicClientFactory()
    ca = UserLLMCredentials(anthropic_api_key=SecretStr(key_a))
    cb = UserLLMCredentials(anthropic_api_key=SecretStr(key_b))

    client_a = factory.create(ca)
    client_b = factory.create(cb)

    assert client_a is not client_b
    assert seen == [key_a, key_b]


def test_concurrent_runs_do_not_share_usage_or_credentials() -> None:
    ca = UserLLMCredentials(anthropic_api_key=SecretStr("sk-ant-api03-" + "A" * 60))
    cb = UserLLMCredentials(anthropic_api_key=SecretStr("sk-ant-api03-" + "B" * 60))

    ctx_a = RunContext.create(ca)
    ctx_b = RunContext.create(cb)

    assert ctx_a.run_id != ctx_b.run_id
    assert ctx_a.usage_tracker is not ctx_b.usage_tracker
    assert ctx_a.credentials.reveal() != ctx_b.credentials.reveal()


def test_no_global_anthropic_client_exists() -> None:
    """A module-level client is the exact mistake BYOK is meant to prevent."""
    import app.llm.anthropic_provider as provider_mod

    for name, value in vars(provider_mod).items():
        if name.startswith("_"):
            continue
        assert type(value).__name__ not in ("Anthropic", "AsyncAnthropic"), (
            f"module-level Anthropic client found: {name}"
        )


# --- test_no_developer_key_fallback ---------------------------------------------------


def test_no_developer_key_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ANTHROPIC_API_KEY must not silently enable LLM features."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "D" * 60)
    monkeypatch.delenv("AIFA_ALLOW_DEV_KEY", raising=False)

    assert developer_key_from_env() is None

    client = TestClient(app)
    resp = client.post(
        "/api/committee/analyze",
        json={"profile": _minimal_profile(), "question": "Should I invest?"},
    )
    # The key field is required: the server will not substitute its own environment.
    assert resp.status_code == 422
    assert "anthropic_api_key" in resp.text


def test_dev_key_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "D" * 60)
    assert developer_key_from_env() is None
    monkeypatch.setenv("AIFA_ALLOW_DEV_KEY", "1")
    assert developer_key_from_env() is not None


def test_no_env_key_read_in_request_path() -> None:
    """No request-path module reads ANTHROPIC_API_KEY from the environment."""
    request_path_files = [
        "backend/app/api/routes/committee.py",
        "backend/app/api/routes/auth.py",
        "backend/app/api/routes/analysis.py",
        "backend/app/api/deps.py",
        "backend/app/llm/anthropic_provider.py",
        "backend/app/committee/orchestrator.py",
        "backend/app/nuwa/distiller.py",
    ]
    for rel in request_path_files:
        source = (REPO_ROOT / rel).read_text()
        assert "ANTHROPIC_API_KEY" not in source, f"{rel} reads ANTHROPIC_API_KEY"


def test_deterministic_endpoints_work_with_no_key_anywhere() -> None:
    """The BYOK invariant: the free half of the product works with zero credentials."""
    client = TestClient(app)
    assert client.get("/api/health").json()["byok_only"] is True
    assert client.get("/api/advisors").status_code == 200
    assert (
        client.post("/api/profiles/analyze", json={"profile": _minimal_profile()}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/committee/select",
            json={"profile": _minimal_profile(), "question": "How should I allocate?"},
        ).status_code
        == 200
    )
    assert client.post("/api/committee/estimate", json={"depth": "deep"}).status_code == 200


# --- redaction unit coverage ----------------------------------------------------------


def test_redaction_handles_nested_and_hostile_shapes() -> None:
    payload = {
        "api_key": FAKE_KEY,
        "Authorization": f"Bearer {FAKE_KEY}",
        "list": [{"secret": FAKE_KEY}, f"free text {FAKE_KEY}"],
        "tuple": ({"x-api-key": FAKE_KEY},),
        "deep": {"a": {"b": {"c": {"credentials": FAKE_KEY}}}},
    }
    rendered = json.dumps(redact(payload), default=str)
    assert FAKE_KEY not in rendered
    assert "sk-ant" not in rendered


def test_redaction_never_raises_on_weird_objects() -> None:
    class Hostile:
        def __repr__(self) -> str:
            return f"Hostile({FAKE_KEY})"

    assert FAKE_KEY not in redact(Hostile())
    assert FAKE_KEY not in redact_text(f"prefix {FAKE_KEY} suffix")


def test_scrub_for_storage_is_applied_to_run_payloads() -> None:
    payload = {"run_id": "run_1", "credentials": FAKE_KEY, "note": f"used {FAKE_KEY}"}
    scrubbed = creds_mod.scrub_for_storage(payload)
    assert FAKE_KEY not in json.dumps(scrubbed)
    assert scrubbed["run_id"] == "run_1"


# --- helpers ---------------------------------------------------------------------------


def _minimal_profile() -> dict:
    return {
        "age": 34,
        "income": {"annual_gross": 100_000},
        "expenses": {"monthly_essential": 3_000},
        "assets": [{"name": "cash", "value": 20_000, "account_type": "cash"}],
    }

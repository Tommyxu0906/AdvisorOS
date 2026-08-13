"""End-to-end committee runs and API behavior, all against the mock provider."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.advisors.selection import select_committee
from app.api import deps
from app.committee.orchestrator import CommitteeError, CommitteeOrchestrator
from app.core.run_context import RunContext
from app.domain.question import UserQuestion
from app.domain.report import AnalysisDepth
from app.llm.mock_provider import MockLLMProvider
from app.main import app
from app.nuwa.distiller import (
    DistillationDepth,
    DistillationError,
    DistillationRequest,
    NuwaDistiller,
    slugify,
)
from tests.conftest import FAKE_KEY

QUESTION = "Should I sell some NVDA right now and pay off my credit card?"


def _run(depth, registry, provider, stressed_profile, analyzed, credentials):
    import asyncio

    analytics, pa, rails = analyzed
    selection = select_committee(
        registry.all_manifests(),
        analytics.need_vector,
        UserQuestion(text=QUESTION).classify(),
        rails,
        depth,
    )
    context = RunContext.create(credentials, depth=depth)
    report = asyncio.run(
        CommitteeOrchestrator(provider, registry).run(
            profile=stressed_profile,
            analytics=analytics,
            portfolio_analytics=pa,
            guardrails=rails,
            selection=selection,
            question=QUESTION,
            context=context,
        )
    )
    return report, selection, context


@pytest.mark.parametrize("depth", list(AnalysisDepth))
def test_every_depth_produces_a_report(
    depth, registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    report, selection, context = _run(
        depth, registry, mock_provider, stressed_profile, analyzed, credentials
    )
    assert report.summary
    assert report.recommended_actions
    assert report.depth is depth
    assert len(report.analyses) == len(selection.selected)
    assert report.guardrails


@pytest.mark.parametrize("depth", list(AnalysisDepth))
def test_actual_call_count_matches_the_advertised_estimate(
    depth, registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    """The pre-run estimate the user sees must match what actually happens."""
    _, selection, context = _run(
        depth, registry, mock_provider, stressed_profile, analyzed, credentials
    )
    usage = context.usage_tracker.aggregate()
    assert usage.total_calls == depth.expected_calls(len(selection.selected))


def test_cost_rises_monotonically_with_depth(
    registry, stressed_profile, analyzed, credentials
) -> None:
    costs = []
    for depth in AnalysisDepth:
        _, _, context = _run(
            depth, registry, MockLLMProvider(), stressed_profile, analyzed, credentials
        )
        usage = context.usage_tracker.aggregate()
        costs.append(usage.estimated_cost_usd)
    assert all(c is not None for c in costs)
    assert costs == sorted(costs)


def test_quick_mode_skips_cross_examination(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    report, _, context = _run(
        AnalysisDepth.quick, registry, mock_provider, stressed_profile, analyzed, credentials
    )
    assert report.critiques == []
    assert report.risk_challenge is None
    stages = {line.label for line in context.usage_tracker.aggregate().by_stage}
    assert stages == {"independent", "synthesis"}


def test_deep_mode_runs_every_stage(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    report, _, context = _run(
        AnalysisDepth.deep, registry, mock_provider, stressed_profile, analyzed, credentials
    )
    stages = {line.label for line in context.usage_tracker.aggregate().by_stage}
    assert stages == {
        "independent",
        "cross_examination",
        "revised_memo",
        "risk_challenge",
        "synthesis",
    }
    assert report.critiques
    assert report.risk_challenge is not None
    assert report.revised_analyses


def test_guardrails_reach_the_prompts(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    _run(AnalysisDepth.quick, registry, mock_provider, stressed_profile, analyzed, credentials)
    prompts = "\n".join(str(c["system"]) for c in mock_provider.calls)
    assert "BLOCKING" in prompts
    assert "HIGH_APR_DEBT" in prompts
    assert "must not recommend" in "\n".join(str(c["stable_system"]) for c in mock_provider.calls)


def test_advisor_runtime_profiles_reach_the_stable_prefix(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    """Advisor context goes in the cacheable prefix; user data does not."""
    _run(AnalysisDepth.quick, registry, mock_provider, stressed_profile, analyzed, credentials)
    independent = [c for c in mock_provider.calls if c["role"] == "independent"]
    for call in independent:
        assert "You are reasoning in the style of" in call["stable_system"]
        # The volatile half holds the numbers.
        assert "Computed financial facts" in call["system"]
        assert "Computed financial facts" not in call["stable_system"]


def test_source_material_is_never_sent(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    """Nuwa provenance and evidence notes stay out of runtime prompts."""
    _run(AnalysisDepth.balanced, registry, mock_provider, stressed_profile, analyzed, credentials)
    everything = "\n".join(str(c["stable_system"]) + str(c["system"]) for c in mock_provider.calls)
    for m in registry.all_manifests():
        assert m.provenance not in everything


def test_advisor_failure_degrades_rather_than_kills_the_run(
    registry, stressed_profile, analyzed, credentials
) -> None:
    provider = MockLLMProvider(fail_on_roles={"cross_examination"})
    report, _, context = _run(
        AnalysisDepth.balanced, registry, provider, stressed_profile, analyzed, credentials
    )
    assert report.summary  # still produced
    assert report.critiques == []
    assert context.usage_tracker.aggregate().failed_calls > 0


def test_synthesis_failure_is_fatal_and_reports_usage(
    registry, stressed_profile, analyzed, credentials
) -> None:
    provider = MockLLMProvider(fail_on_roles={"synthesis"})
    with pytest.raises(CommitteeError):
        _run(AnalysisDepth.quick, registry, provider, stressed_profile, analyzed, credentials)


def test_total_advisor_failure_is_reported_honestly(
    registry, stressed_profile, analyzed, credentials
) -> None:
    provider = MockLLMProvider(fail_on_roles={"independent"})
    with pytest.raises(CommitteeError, match="Every advisor call failed"):
        _run(AnalysisDepth.quick, registry, provider, stressed_profile, analyzed, credentials)


def test_report_carries_a_disclaimer(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    report, _, _ = _run(
        AnalysisDepth.quick, registry, mock_provider, stressed_profile, analyzed, credentials
    )
    assert "not personalized investment advice" in report.disclaimer


# --- Nuwa -----------------------------------------------------------------------------


def test_distillation_produces_a_registrable_advisor(registry, credentials, tmp_path) -> None:
    import asyncio

    registry._custom_dir = tmp_path
    distiller = NuwaDistiller(MockLLMProvider(), registry)
    context = RunContext.create(credentials)
    result = asyncio.run(
        distiller.distill(
            DistillationRequest(subject="Benjamin Graham", depth=DistillationDepth.quick),
            context,
        )
    )
    assert result.manifest.advisor_id == "benjamin_graham"
    assert result.manifest.origin.value == "custom"
    assert result.manifest.blind_spots and result.manifest.honest_boundaries
    assert result.runtime_profile_tokens > 0
    assert "benjamin_graham" in registry


def test_distillation_call_count_matches_depth(registry, credentials, tmp_path) -> None:
    registry._custom_dir = tmp_path
    counts = []
    for depth in DistillationDepth:
        context = RunContext.create(credentials)
        asyncio.run(
            NuwaDistiller(MockLLMProvider(), registry).distill(
                DistillationRequest(subject="Test Subject", depth=depth), context, register=False
            )
        )
        calls = context.usage_tracker.aggregate().total_calls
        # expected_calls() is an upper bound: a short research plan yields fewer passes.
        assert calls == depth.expected_calls()
        counts.append(calls)
    assert counts == sorted(counts)


def test_distillation_tolerates_a_short_research_plan(registry, credentials, tmp_path) -> None:
    """Fewer planned questions means fewer passes, not a failed run."""

    class ShortPlanProvider(MockLLMProvider):
        def _payload_for(self, role, advisor_id, schema):
            payload = super()._payload_for(role, advisor_id, schema)
            if role == "nuwa_plan":
                payload["questions"] = ["Only one question."]
            return payload

    registry._custom_dir = tmp_path
    context = RunContext.create(credentials)
    result = asyncio.run(
        NuwaDistiller(ShortPlanProvider(), registry).distill(
            DistillationRequest(subject="Test", depth=DistillationDepth.deep),
            context,
            register=False,
        )
    )
    assert result.research_pass_count == 1
    assert context.usage_tracker.aggregate().total_calls < DistillationDepth.deep.expected_calls()


def test_distillation_is_not_rerun_when_advisor_joins_a_committee(
    registry, mock_provider, stressed_profile, analyzed, credentials
) -> None:
    """A committee run must never invoke a Nuwa stage."""
    _run(AnalysisDepth.deep, registry, mock_provider, stressed_profile, analyzed, credentials)
    roles = {c["role"] for c in mock_provider.calls}
    assert not any(r.startswith("nuwa_") for r in roles)


def test_distillation_rejects_a_profile_with_no_limits(registry, credentials, tmp_path) -> None:
    import asyncio

    class NoLimitsProvider(MockLLMProvider):
        def _payload_for(self, role, advisor_id, schema):
            payload = super()._payload_for(role, advisor_id, schema)
            if role == "nuwa_synthesis":
                payload["blind_spots"] = []
            return payload

    registry._custom_dir = tmp_path
    with pytest.raises(DistillationError, match="blind spots"):
        asyncio.run(
            NuwaDistiller(NoLimitsProvider(), registry).distill(
                DistillationRequest(subject="Someone", depth=DistillationDepth.quick),
                RunContext.create(credentials),
            )
        )


def test_slugify() -> None:
    assert slugify("Benjamin Graham") == "benjamin_graham"
    assert slugify("  J.M. Keynes!! ") == "j_m_keynes"
    assert slugify("???") == "custom_advisor"


# --- API ------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(deps, "provider", lambda: MockLLMProvider())
    return TestClient(app)


def _profile_payload() -> dict:
    return {
        "age": 34,
        "income": {"annual_gross": 145_000, "employer_match_pct": 0.04},
        "expenses": {"monthly_essential": 4_200, "monthly_discretionary": 1_500},
        "debts": [{"name": "card", "balance": 9_000, "apr": 0.229, "minimum_monthly_payment": 280}],
        "assets": [{"name": "savings", "value": 11_000, "account_type": "cash"}],
        "goals": [{"name": "house", "years_until_needed": 2.0}],
    }


def test_capabilities_endpoint_documents_the_byok_split(client: TestClient) -> None:
    caps = client.get("/api/capabilities").json()
    assert "POST /api/committee/analyze" in caps["requires_api_key"]
    assert "POST /api/committee/select" in caps["free"]
    assert caps["key_storage"].startswith("none")


def test_select_then_analyze_agree_on_the_committee(client: TestClient) -> None:
    payload = {"profile": _profile_payload(), "question": QUESTION, "depth": "balanced"}
    selected = client.post("/api/committee/select", json=payload).json()
    ran = client.post(
        "/api/committee/analyze", json={**payload, "anthropic_api_key": FAKE_KEY}
    ).json()
    assert [s["advisor_id"] for s in selected["selection"]["selected"]] == [
        s["advisor_id"] for s in ran["selection"]["selected"]
    ]


def test_estimate_matches_actual_call_count(client: TestClient) -> None:
    payload = {"profile": _profile_payload(), "question": QUESTION, "depth": "balanced"}
    selection = client.post("/api/committee/select", json=payload).json()["selection"]
    estimate = client.post(
        "/api/committee/estimate",
        json={"depth": "balanced", "advisor_count": len(selection["selected"])},
    ).json()
    ran = client.post(
        "/api/committee/analyze", json={**payload, "anthropic_api_key": FAKE_KEY}
    ).json()
    assert estimate["expected_llm_calls"] == ran["usage"]["total_calls"]
    assert "estimate" in estimate["caveat"].lower()


def test_analyze_returns_full_cost_breakdown(client: TestClient) -> None:
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _profile_payload(),
            "question": QUESTION,
            "depth": "balanced",
        },
    )
    usage = resp.json()["usage"]
    assert usage["total_calls"] > 0
    assert usage["estimated_cost_usd"] > 0
    assert usage["pricing_version"]
    assert usage["by_stage"] and usage["by_advisor"]


def test_advisor_override_is_honored(client: TestClient) -> None:
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _profile_payload(),
            "question": QUESTION,
            "depth": "quick",
            "advisor_ids": ["bogle", "housel", "munger"],
        },
    )
    assert resp.status_code == 200
    assert set(s["advisor_id"] for s in resp.json()["selection"]["selected"]) <= {
        "bogle",
        "housel",
        "munger",
    }


def test_unknown_advisor_override_is_a_404(client: TestClient) -> None:
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _profile_payload(),
            "question": QUESTION,
            "advisor_ids": ["nobody"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "advisor_not_found"


def test_committee_failure_returns_incurred_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the user was billed for partial work, tell them so."""
    monkeypatch.setattr(deps, "provider", lambda: MockLLMProvider(fail_on_roles={"synthesis"}))
    client = TestClient(app)
    resp = client.post(
        "/api/committee/analyze",
        json={
            "anthropic_api_key": FAKE_KEY,
            "profile": _profile_payload(),
            "question": QUESTION,
            "depth": "quick",
        },
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "committee_failed"
    assert detail["usage"]["total_calls"] > 0

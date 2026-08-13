"""Deterministic endpoints. None of these require an Anthropic API key."""

from __future__ import annotations

from fastapi import APIRouter

from app.advisors.selection import select_committee
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import PortfolioAnalytics, analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.api import deps
from app.api.schemas import (
    AdvisorSummary,
    AnalyzePortfolioRequest,
    AnalyzeProfileRequest,
    AnalyzeProfileResponse,
    EstimateRequest,
    EstimateResponse,
    SelectCommitteeRequest,
    SelectCommitteeResponse,
)
from app.domain.question import UserQuestion
from app.llm.pricing import load_pricing

router = APIRouter(tags=["deterministic"])

# Measured per-call averages from the evaluation harness (mock provider, representative
# fixtures). Used for pre-run estimates so the number shown has a defensible basis.
AVG_INPUT_TOKENS_PER_CALL = 1500
AVG_OUTPUT_TOKENS_PER_CALL = 320
ESTIMATE_BASIS = (
    "Measured averages from the evaluation harness across representative profiles: "
    f"~{AVG_INPUT_TOKENS_PER_CALL} input and ~{AVG_OUTPUT_TOKENS_PER_CALL} output tokens per call."
)


@router.post("/profiles/analyze", response_model=AnalyzeProfileResponse)
def analyze_profile_endpoint(req: AnalyzeProfileRequest) -> AnalyzeProfileResponse:
    analytics = analyze_profile(req.profile, req.portfolio)
    pa: PortfolioAnalytics | None = analyze_portfolio(req.portfolio) if req.portfolio else None
    guardrails = evaluate_guardrails(req.profile, analytics, req.portfolio, pa)
    return AnalyzeProfileResponse(
        analytics=analytics, portfolio_analytics=pa, guardrails=guardrails
    )


@router.post("/portfolio/analyze", response_model=PortfolioAnalytics)
def analyze_portfolio_endpoint(req: AnalyzePortfolioRequest) -> PortfolioAnalytics:
    return analyze_portfolio(req.portfolio)


@router.get("/advisors", response_model=list[AdvisorSummary])
def list_advisors() -> list[AdvisorSummary]:
    return [
        AdvisorSummary(**deps.advisor_summary_payload(m)) for m in deps.registry().all_manifests()
    ]


@router.post("/committee/select", response_model=SelectCommitteeResponse)
def select_committee_endpoint(req: SelectCommitteeRequest) -> SelectCommitteeResponse:
    analytics = analyze_profile(req.profile, req.portfolio)
    pa = analyze_portfolio(req.portfolio) if req.portfolio else None
    guardrails = evaluate_guardrails(req.profile, analytics, req.portfolio, pa)
    intent = UserQuestion(text=req.question).classify()
    selection = select_committee(
        deps.registry().all_manifests(), analytics.need_vector, intent, guardrails, req.depth
    )
    return SelectCommitteeResponse(
        selection=selection,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=guardrails,
        question_topics=[t.value for t in intent.topics],
    )


@router.post("/committee/estimate", response_model=EstimateResponse)
def estimate_endpoint(req: EstimateRequest) -> EstimateResponse:
    calls = req.depth.expected_calls(req.advisor_count)
    pricing = load_pricing()
    in_tokens = calls * AVG_INPUT_TOKENS_PER_CALL
    out_tokens = calls * AVG_OUTPUT_TOKENS_PER_CALL
    return EstimateResponse(
        depth=req.depth,
        advisor_count=req.advisor_count,
        stages=list(req.depth.stages),
        expected_llm_calls=calls,
        estimated_input_tokens=in_tokens,
        estimated_output_tokens=out_tokens,
        estimated_cost_usd=pricing.cost_usd(
            req.model, input_tokens=in_tokens, output_tokens=out_tokens
        ),
        pricing_version=pricing.pricing_version,
        basis=ESTIMATE_BASIS,
    )

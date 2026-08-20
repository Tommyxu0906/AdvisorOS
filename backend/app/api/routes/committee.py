"""Endpoints that spend the user's tokens. Every one requires a key in the request body.

Identity (via `current_user_optional`) is orthogonal to that key — see
app/core/supabase_auth.py. An anonymous caller runs the committee exactly as before; a signed-in
caller additionally gets the run saved to their history, best-effort, after the response they
paid for has already been assembled.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.advisors.registry import AdvisorNotFound
from app.advisors.selection import select_committee
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.api import deps
from app.api.schemas import (
    AdvisorSummary,
    ConsultRequest,
    ConsultResponse,
    DistillRequest,
    DistillResponse,
    RunCommitteeRequest,
    RunCommitteeResponse,
)
from app.committee.orchestrator import CommitteeError, CommitteeOrchestrator
from app.consult.service import consult as run_consult
from app.core.run_context import RunContext
from app.core.supabase_auth import AuthUser, current_user_optional
from app.db.repositories.runs import save_run_best_effort
from app.domain.question import UserQuestion
from app.market_data import service as market_data
from app.nuwa.distiller import (
    DistillationError,
    DistillationRequest,
    NuwaDistiller,
)
from app.policy.engine import compute_scenario

router = APIRouter(tags=["llm"])
logger = logging.getLogger(__name__)


@router.post("/committee/analyze", response_model=RunCommitteeResponse)
async def run_committee(
    req: RunCommitteeRequest,
    user: AuthUser | None = Depends(current_user_optional),
) -> RunCommitteeResponse:
    credentials = deps.credentials_from(req.anthropic_api_key)
    registry = deps.registry()

    # Real price history is fetched only here, on the paid path. The free deterministic
    # endpoints are called on every keystroke behind a 300ms debounce, where a network round
    # trip per symbol would be felt; one run's worth of latency is not.
    portfolio = await market_data.enrich_portfolio(req.portfolio) if req.portfolio else None

    analytics = analyze_profile(req.profile, portfolio)
    pa = analyze_portfolio(portfolio) if portfolio else None
    guardrails = evaluate_guardrails(req.profile, analytics, portfolio, pa)
    intent = UserQuestion(text=req.question).classify()

    manifests = registry.all_manifests()
    if req.advisor_ids:
        try:
            manifests = [registry.manifest(a) for a in req.advisor_ids]
        except AdvisorNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "advisor_not_found", "message": f"Unknown advisor: {exc.args[0]}"},
            ) from exc

    selection = select_committee(manifests, analytics.need_vector, intent, guardrails, req.depth)

    context = RunContext.create(credentials, depth=req.depth, model=req.model)
    orchestrator = CommitteeOrchestrator(deps.provider(), registry)

    logger.info(
        "committee run %s starting: depth=%s advisors=%s key=%s",
        context.run_id,
        req.depth.value,
        selection.advisor_ids,
        credentials.fingerprint(),
    )

    try:
        report = await orchestrator.run(
            profile=req.profile,
            analytics=analytics,
            portfolio_analytics=pa,
            guardrails=guardrails,
            selection=selection,
            question=req.question,
            context=context,
        )
    except CommitteeError as exc:
        # The user was billed for whatever ran before the failure — save that spend to their
        # history too, same as a successful run, so it isn't just lost from their view.
        if user is not None:
            await save_run_best_effort(
                owner_user_id=user.id,
                run_id=context.run_id,
                question=req.question,
                question_topics=[t.value for t in intent.topics],
                depth=req.depth.value,
                model=req.model,
                status="failed",
                error_message=str(exc),
                profile=req.profile,
                portfolio=portfolio,
                analytics=analytics,
                portfolio_analytics=pa,
                guardrails=guardrails,
                selection=selection,
                report=None,
                usage=context.usage_tracker.aggregate(),
            )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "committee_failed",
                "message": str(exc),
                "usage": context.usage_tracker.aggregate().model_dump(mode="json"),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 - sanitized before it reaches the client
        raise deps.provider_error(exc) from exc

    if user is not None:
        await save_run_best_effort(
            owner_user_id=user.id,
            run_id=context.run_id,
            question=req.question,
            question_topics=[t.value for t in intent.topics],
            depth=req.depth.value,
            model=req.model,
            status="succeeded",
            error_message=None,
            profile=req.profile,
            portfolio=portfolio,
            analytics=analytics,
            portfolio_analytics=pa,
            guardrails=guardrails,
            selection=selection,
            report=report,
            usage=context.usage_tracker.aggregate(),
        )

    return RunCommitteeResponse(
        report=report,
        selection=selection,
        usage=context.usage_tracker.aggregate(),
        analytics=analytics,
        portfolio_analytics=pa,
        scenario=compute_scenario(req.profile, analytics, portfolio, pa, guardrails),
    )


@router.post("/advisors/distill", response_model=DistillResponse)
async def distill_advisor(req: DistillRequest) -> DistillResponse:
    credentials = deps.credentials_from(req.anthropic_api_key)
    registry = deps.registry()

    context = RunContext.create(credentials, model=req.model)
    distiller = NuwaDistiller(deps.provider(), registry)

    logger.info(
        "distillation %s starting: subject=%r depth=%s key=%s",
        context.run_id,
        req.subject,
        req.depth.value,
        credentials.fingerprint(),
    )

    try:
        result = await distiller.distill(
            DistillationRequest(
                subject=req.subject,
                focus_areas=req.focus_areas,
                depth=req.depth,
                advisor_id=req.advisor_id,
            ),
            context,
        )
    except DistillationError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "distillation_failed",
                "message": str(exc),
                "usage": context.usage_tracker.aggregate().model_dump(mode="json"),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise deps.provider_error(exc) from exc

    return DistillResponse(
        advisor=AdvisorSummary(**deps.advisor_summary_payload(result.manifest)),
        warnings=result.warnings,
        research_pass_count=result.research_pass_count,
        usage=context.usage_tracker.aggregate(),
    )


@router.post("/committee/consult", response_model=ConsultResponse)
async def consult_committee(
    req: ConsultRequest,
    registry=Depends(deps.registry),
) -> ConsultResponse:
    """One turn of a multi-turn consultation over the *computed* scenario.

    The scenario is recomputed here from the profile in the request rather than accepted from
    the client. That is the difference between a committee arguing about this household's real
    numbers and one arguing about whatever the browser last held: a scenario the client could
    supply would let a chat turn move the figures without anything recomputing.
    """
    credentials = deps.credentials_from(req.anthropic_api_key)

    try:
        advisors = [registry.runtime_profile(a) for a in req.advisor_ids]
    except AdvisorNotFound as exc:
        raise HTTPException(
            status_code=404, detail={"code": "advisor_not_found", "message": str(exc)}
        ) from exc

    analytics = analyze_profile(req.profile)
    portfolio = req.portfolio if req.portfolio and req.portfolio.holdings else None
    pa = analyze_portfolio(portfolio) if portfolio else None
    guardrails = evaluate_guardrails(req.profile, analytics)
    scenario = compute_scenario(req.profile, analytics, portfolio, pa, guardrails)

    context = RunContext.create(credentials, model=req.model)

    try:
        result = await run_consult(
            # Resolved here rather than via Depends so that replacing deps.provider — which is
            # how every test injects the mock — actually takes effect.
            provider=deps.provider(),
            context=context,
            advisors=advisors,
            profile=req.profile,
            analytics=analytics,
            portfolio_analytics=pa,
            scenario=scenario,
            guardrails=guardrails,
            history=req.history,
            question=req.question,
        )
    except Exception as exc:  # noqa: BLE001 - sanitized before it reaches the client
        raise deps.provider_error(exc) from exc

    return ConsultResponse(
        responses=result.responses,
        candidates=result.candidates,
        synthesis=result.synthesis,
        scenario=scenario,
        guardrails=guardrails,
        usage=context.usage_tracker.aggregate(),
    )

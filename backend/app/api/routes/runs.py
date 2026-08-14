"""Run history. Every route here requires a signed-in user — there is no anonymous history to
read, since nothing is saved for an anonymous run in the first place.

No Anthropic key touches this router at all; it is pure identity + storage, the other half of
the split described in app/core/supabase_auth.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import RunDetail, RunSummary
from app.core.supabase_auth import AuthUser, current_user_required
from app.db.pool import StorageUnavailable
from app.db.repositories import runs as runs_repo

router = APIRouter(prefix="/runs", tags=["runs"])


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "storage_unavailable",
            "message": "Run history is not available on this deployment right now.",
        },
    )


@router.get("", response_model=list[RunSummary])
async def list_my_runs(user: AuthUser = Depends(current_user_required)) -> list[RunSummary]:
    try:
        rows = await runs_repo.list_runs(user.id)
    except StorageUnavailable as exc:
        raise _storage_unavailable() from exc

    return [
        RunSummary(
            run_id=r["run_id"],
            status=r["status"],
            created_at=r["created_at"].isoformat(),
            depth=r["depth"],
            model=r["model"],
            question=r["question"],
            summary=r["summary"],
            advisor_ids=r["advisor_ids"],
            guardrail_max_severity=r["guardrail_max_severity"],
            total_calls=r["total_calls"],
            estimated_cost_usd=(
                float(r["estimated_cost_usd"]) if r["estimated_cost_usd"] is not None else None
            ),
        )
        for r in rows
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_my_run(run_id: str, user: AuthUser = Depends(current_user_required)) -> RunDetail:
    try:
        row = await runs_repo.get_run(user.id, run_id)
    except StorageUnavailable as exc:
        raise _storage_unavailable() from exc

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": "No run with that ID for your account."},
        )

    return RunDetail(
        run_id=row["run_id"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
        depth=row["depth"],
        model=row["model"],
        question=row["question"],
        summary=row["summary"],
        error_message=row["error_message"],
        total_calls=row["total_calls"],
        total_input_tokens=row["total_input_tokens"],
        total_output_tokens=row["total_output_tokens"],
        estimated_cost_usd=(
            float(row["estimated_cost_usd"]) if row["estimated_cost_usd"] is not None else None
        ),
        pricing_version=row["pricing_version"],
        profile_snapshot=row["profile_snapshot"],
        portfolio_snapshot=row["portfolio_snapshot"],
        analytics=row["analytics"],
        portfolio_analytics=row["portfolio_analytics"],
        guardrails=row["guardrails"],
        selection=row["selection"],
        report=row["report"],
    )

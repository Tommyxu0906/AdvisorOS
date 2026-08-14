"""Persistence for committee runs.

A run is an immutable audit record — see supabase/migrations/0006_committee_runs.sql for the
reasoning behind the normalize/JSONB split, and 0010_run_list_projections.sql for the small set
of columns duplicated out of the JSONB purely so list_runs() doesn't have to parse a full report
just to render one row of a history page.

Every write in this module goes through `scrub_for_storage()` before it touches the database, in
one call covering every JSONB payload at once. That is the same gate `core/credentials.py` was
built for and left unused until now — see tests/integration/test_run_persistence.py for proof it
actually strips a planted secret.

Ownership: `committee_runs.user_id` is the only place a user id is stored. `run_cost_lines` and
`run_llm_calls` carry no owner column of their own — the RLS policies in
0008_rls_policies.sql reach them through a join back to `committee_runs`, and this module
follows the same shape rather than denormalizing an owner column those tables don't have.

Saving is always best-effort from the caller's point of view. A user who just paid for a
committee run must get their report back whether or not history-saving succeeds — see how
`api/routes/committee.py` wraps the call to `save_run_best_effort`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.credentials import scrub_for_storage
from app.db import pool
from app.db.pool import StorageUnavailable
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import CommitteeReport, Guardrail, GuardrailSeverity
from app.llm.usage import RunUsage

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    GuardrailSeverity.info: 0,
    GuardrailSeverity.caution: 1,
    GuardrailSeverity.blocking: 2,
}


async def save_run(
    *,
    owner_user_id: str,
    run_id: str,
    question: str,
    question_topics: list[str],
    depth: str,
    model: str,
    status: str,
    error_message: str | None,
    profile: FinancialProfile,
    portfolio: Portfolio | None,
    analytics: Any,
    portfolio_analytics: Any,
    guardrails: list[Guardrail],
    selection: Any,
    report: CommitteeReport | None,
    usage: RunUsage,
) -> None:
    """Persist one committee run. Raises StorageUnavailable if there is nowhere to write it —
    callers are expected to catch that and treat it as "history wasn't saved", not a failure of
    the run itself.
    """
    # One scrub call over everything destined for a JSONB column, rather than one per field —
    # a stray key pasted into `notes` or surfaced in a provider `error` string is caught
    # regardless of which snapshot it ended up in.
    scrubbed = scrub_for_storage(
        {
            "profile_snapshot": profile.model_dump(mode="json"),
            "portfolio_snapshot": portfolio.model_dump(mode="json") if portfolio else None,
            "analytics": analytics.model_dump(mode="json"),
            "portfolio_analytics": (
                portfolio_analytics.model_dump(mode="json") if portfolio_analytics else None
            ),
            "guardrails": [g.model_dump(mode="json") for g in guardrails],
            "selection": selection.model_dump(mode="json"),
            "report": report.model_dump(mode="json") if report else None,
        }
    )

    advisor_ids = [a.advisor_id for a in selection.selected]
    guardrail_codes = [g.code for g in guardrails]
    max_severity = (
        max((g.severity for g in guardrails), key=lambda s: _SEVERITY_RANK[s]).value
        if guardrails
        else None
    )
    summary = report.summary if report else ""
    violation_count = len(report.guardrail_violations) if report else 0

    pg_pool = await pool.get_pool()
    async with pg_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            insert into public.committee_runs (
                run_id, user_id, status, depth, model,
                question, question_topics, summary, advisor_ids,
                guardrail_codes, guardrail_max_severity, guardrail_violation_count,
                profile_snapshot, portfolio_snapshot, analytics,
                portfolio_analytics, guardrails, selection, report,
                total_calls, failed_calls, total_input_tokens, total_output_tokens,
                total_cache_read_tokens, total_cache_creation_tokens, total_latency_ms,
                estimated_cost_usd, pricing_version, unpriced_models, error_message
            ) values (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12,
                $13::jsonb, $14::jsonb, $15::jsonb,
                $16::jsonb, $17::jsonb, $18::jsonb, $19::jsonb,
                $20, $21, $22, $23,
                $24, $25, $26,
                $27, $28, $29, $30
            )
            """,
            run_id,
            owner_user_id,
            status,
            depth,
            model,
            question,
            question_topics,
            summary,
            advisor_ids,
            guardrail_codes,
            max_severity,
            violation_count,
            json.dumps(scrubbed["profile_snapshot"]),
            json.dumps(scrubbed["portfolio_snapshot"]),
            json.dumps(scrubbed["analytics"]),
            json.dumps(scrubbed["portfolio_analytics"]),
            json.dumps(scrubbed["guardrails"]),
            json.dumps(scrubbed["selection"]),
            json.dumps(scrubbed["report"]),
            usage.total_calls,
            usage.failed_calls,
            usage.total_input_tokens,
            usage.total_output_tokens,
            usage.total_cache_read_tokens,
            usage.total_cache_creation_tokens,
            usage.total_latency_ms,
            usage.estimated_cost_usd,
            usage.pricing_version,
            usage.unpriced_models,
            error_message,
        )

        if usage.by_stage or usage.by_advisor:
            await conn.executemany(
                """
                insert into public.run_cost_lines
                    (run_id, kind, label, calls, input_tokens, output_tokens, estimated_cost_usd)
                values ($1, $2, $3, $4, $5, $6, $7)
                """,
                [
                    (
                        run_id,
                        "stage",
                        c.label,
                        c.calls,
                        c.input_tokens,
                        c.output_tokens,
                        c.estimated_cost_usd,
                    )
                    for c in usage.by_stage
                ]
                + [
                    (
                        run_id,
                        "advisor",
                        c.label,
                        c.calls,
                        c.input_tokens,
                        c.output_tokens,
                        c.estimated_cost_usd,
                    )
                    for c in usage.by_advisor
                ],
            )

        if usage.calls:
            # No owner column here on purpose — see the module docstring. Ownership is reached
            # through the run_id -> committee_runs.user_id join, matching 0008's RLS policies.
            await conn.executemany(
                """
                insert into public.run_llm_calls (
                    run_id, call_id, role, advisor_id, model,
                    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                    latency_ms, started_at, error
                ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                [
                    (
                        run_id,
                        c.call_id,
                        c.role,
                        c.advisor_id,
                        c.model,
                        c.input_tokens,
                        c.output_tokens,
                        c.cache_read_tokens,
                        c.cache_creation_tokens,
                        c.latency_ms,
                        c.started_at,
                        c.error,
                    )
                    for c in usage.calls
                ],
            )


async def save_run_best_effort(**kwargs: Any) -> None:
    """save_run(), but a storage problem is logged and swallowed rather than raised.

    Use this from request handlers: the user already ran (and paid for) the committee — a
    failure to record history must never turn into a failure response for that.
    """
    if not pool.is_configured():
        return
    try:
        await save_run(**kwargs)
    except StorageUnavailable as exc:
        logger.warning("Could not save run history for %s: %s", kwargs.get("run_id"), exc)
    except Exception:  # noqa: BLE001 - history-saving must never break the run response
        logger.exception("Unexpected error saving run history for %s", kwargs.get("run_id"))


async def list_runs(owner_user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Summaries only — no snapshots or report body. For a run list view."""
    rows = await pool.fetch(
        """
        select run_id, status, created_at, depth, model, question, summary,
               advisor_ids, guardrail_max_severity, total_calls, estimated_cost_usd
        from public.committee_runs
        where user_id = $1
        order by created_at desc
        limit $2
        """,
        owner_user_id,
        limit,
    )
    return [dict(r) for r in rows]


async def get_run(owner_user_id: str, run_id: str) -> dict[str, Any] | None:
    """Full detail for one run, scoped to its owner. None if missing or not theirs."""
    row = await pool.fetchrow(
        """
        select run_id, status, created_at, depth, model, question, summary,
               profile_snapshot, portfolio_snapshot, analytics,
               portfolio_analytics, guardrails, selection, report,
               total_calls, total_input_tokens, total_output_tokens, estimated_cost_usd,
               pricing_version, error_message
        from public.committee_runs
        where user_id = $1 and run_id = $2
        """,
        owner_user_id,
        run_id,
    )
    if row is None:
        return None
    result = dict(row)
    for jsonb_field in (
        "profile_snapshot",
        "portfolio_snapshot",
        "analytics",
        "portfolio_analytics",
        "guardrails",
        "selection",
        "report",
    ):
        if result[jsonb_field] is not None:
            result[jsonb_field] = json.loads(result[jsonb_field])
    return result

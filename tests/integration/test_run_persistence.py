"""save_run / list_runs / get_run against a real Postgres.

Skipped entirely when DATABASE_URL is unset — see scripts/validate_migrations.sh for how to
stand up a scratch database locally (apply supabase/local/auth_stub.sql, then every file under
supabase/migrations/, in order). Not run in CI today because CI has no Postgres service yet;
that is a known gap, not an oversight — see the plan's "App-side changes" table.

The one thing every test here cares about: `scrub_for_storage()` actually ran before anything
hit the wire. If it silently stopped being called, a planted secret in `notes` or a provider
`error` string would show up verbatim in the database.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.advisors.selection import select_committee
from app.committee.orchestrator import CommitteeOrchestrator
from app.core.run_context import RunContext
from app.db import pool
from app.db.repositories import runs as runs_repo
from app.domain.question import UserQuestion
from app.domain.report import AnalysisDepth

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="no DATABASE_URL — see scripts/validate_migrations.sh to stand up a scratch database",
)


@pytest.fixture(autouse=True)
async def _clean_pool():
    """Each test in this file gets its own connections; nothing here should outlive the test."""
    yield
    await pool.close_pool()


@pytest.fixture
async def owner_id() -> str:
    """A fresh auth.users row per test, so tests never see each other's runs."""
    new_id = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", new_id, f"{new_id}@example.com"
    )
    return new_id


async def test_save_run_scrubs_before_insert(
    owner_id,
    registry,
    mock_provider,
    stressed_profile,
    concentrated_portfolio,
    analyzed,
    credentials,
):
    """A key-shaped string planted in free text must not survive into the stored row."""
    planted_key = "sk-ant-api03-" + "P" * 60
    profile = stressed_profile.model_copy(update={"notes": f"remember my key {planted_key}"})

    analytics, pa, rails = analyzed
    question = "Should I sell some NVDA and pay off my credit card?"
    intent = UserQuestion(text=question).classify()
    selection = select_committee(
        registry.all_manifests(), analytics.need_vector, intent, rails, AnalysisDepth.quick
    )
    context = RunContext.create(credentials, depth=AnalysisDepth.quick)
    report = await CommitteeOrchestrator(mock_provider, registry).run(
        profile=profile,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=rails,
        selection=selection,
        question=question,
        context=context,
    )

    await runs_repo.save_run(
        owner_user_id=owner_id,
        run_id=context.run_id,
        question=question,
        question_topics=[t.value for t in intent.topics],
        depth="quick",
        model=context.model_config.model,
        status="succeeded",
        error_message=None,
        profile=profile,
        portfolio=concentrated_portfolio,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=rails,
        selection=selection,
        report=report,
        usage=context.usage_tracker.aggregate(),
    )

    stored = await runs_repo.get_run(owner_id, context.run_id)
    assert stored is not None
    assert planted_key not in stored["profile_snapshot"]["notes"]
    assert "[REDACTED]" in stored["profile_snapshot"]["notes"]
    # The rest of the note survives — scrubbing is targeted, not a wholesale wipe.
    assert "remember my key" in stored["profile_snapshot"]["notes"]


async def test_saved_run_round_trips_through_list_and_get(
    owner_id,
    registry,
    mock_provider,
    stressed_profile,
    concentrated_portfolio,
    analyzed,
    credentials,
):
    analytics, pa, rails = analyzed
    question = "Is my portfolio too concentrated?"
    intent = UserQuestion(text=question).classify()
    selection = select_committee(
        registry.all_manifests(), analytics.need_vector, intent, rails, AnalysisDepth.balanced
    )
    context = RunContext.create(credentials, depth=AnalysisDepth.balanced)
    report = await CommitteeOrchestrator(mock_provider, registry).run(
        profile=stressed_profile,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=rails,
        selection=selection,
        question=question,
        context=context,
    )
    usage = context.usage_tracker.aggregate()

    await runs_repo.save_run(
        owner_user_id=owner_id,
        run_id=context.run_id,
        question=question,
        question_topics=[t.value for t in intent.topics],
        depth="balanced",
        model=context.model_config.model,
        status="succeeded",
        error_message=None,
        profile=stressed_profile,
        portfolio=concentrated_portfolio,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=rails,
        selection=selection,
        report=report,
        usage=usage,
    )

    summaries = await runs_repo.list_runs(owner_id)
    assert len(summaries) == 1
    assert summaries[0]["run_id"] == context.run_id
    assert summaries[0]["summary"] == report.summary
    assert summaries[0]["advisor_ids"] == selection.advisor_ids
    assert summaries[0]["total_calls"] == usage.total_calls

    detail = await runs_repo.get_run(owner_id, context.run_id)
    assert detail is not None
    assert detail["report"]["summary"] == report.summary
    assert len(detail["report"]["analyses"]) == len(report.analyses)
    assert detail["profile_snapshot"]["age"] == stressed_profile.age
    assert detail["selection"]["depth"] == "balanced"

    # Another user's empty list proves ownership scoping, not just "it returns something".
    other_id = str(uuid.uuid4())
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2)", other_id, f"{other_id}@example.com"
    )
    assert await runs_repo.list_runs(other_id) == []
    assert await runs_repo.get_run(other_id, context.run_id) is None


async def test_failed_run_is_still_saved_with_the_usage_already_spent(
    owner_id, registry, stressed_profile, analyzed, credentials
):
    """The user was billed for whatever ran before the failure — that spend must not vanish."""
    from app.llm.usage import LLMCallUsage

    analytics, pa, rails = analyzed
    question = "What should I do?"
    intent = UserQuestion(text=question).classify()
    selection = select_committee(
        registry.all_manifests(), analytics.need_vector, intent, rails, AnalysisDepth.quick
    )
    context = RunContext.create(credentials, depth=AnalysisDepth.quick)
    # Simulate partial spend without a real provider call — one advisor answered, then the
    # run failed before synthesis.
    context.usage_tracker.record(
        LLMCallUsage(
            call_id=context.new_call_id(),
            run_id=context.run_id,
            role="independent",
            advisor_id=selection.selected[0].advisor_id,
            model=context.model_config.model,
            input_tokens=500,
            output_tokens=100,
        )
    )
    usage = context.usage_tracker.aggregate()

    await runs_repo.save_run(
        owner_user_id=owner_id,
        run_id=context.run_id,
        question=question,
        question_topics=[t.value for t in intent.topics],
        depth="quick",
        model=context.model_config.model,
        status="failed",
        error_message="Simulated provider outage mid-run.",
        profile=stressed_profile,
        portfolio=None,
        analytics=analytics,
        portfolio_analytics=pa,
        guardrails=rails,
        selection=selection,
        report=None,
        usage=usage,
    )

    stored = await runs_repo.get_run(owner_id, context.run_id)
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error_message"] == "Simulated provider outage mid-run."
    assert stored["report"] is None
    assert stored["total_input_tokens"] == 500
    assert stored["total_output_tokens"] == 100

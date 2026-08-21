"""Request/response models for the HTTP API.

Note which requests carry `anthropic_api_key` and which do not — that split is the BYOK
architecture made visible at the API boundary. Response models never contain a key field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.advisors.selection import CommitteeSelection
from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.consult.models import (
    AdvisorConsultResponse,
    ChatMessage,
    ConsultSynthesis,
    DecisionCandidate,
)
from app.core.run_context import DEFAULT_MODEL
from app.domain.needs import ExpertiseVector
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import AnalysisDepth, CommitteeReport, Guardrail
from app.llm.usage import RunUsage
from app.nuwa.distiller import DistillationDepth
from app.policy.engine import PortfolioScenario


class CredentialedRequest(BaseModel):
    """Base for every request that will spend the user's tokens."""

    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: SecretStr = Field(description="Held in request memory only; never stored.")
    model: str = Field(default=DEFAULT_MODEL)


# --- auth --------------------------------------------------------------------------------


class ValidateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: SecretStr
    model: str = Field(default=DEFAULT_MODEL)


class ValidateKeyResponse(BaseModel):
    """Deliberately minimal. Never echoes the key or any provider detail."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    error: str | None = None
    model: str | None = None


# --- deterministic endpoints (no key) ----------------------------------------------------


class AnalyzeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: FinancialProfile
    portfolio: Portfolio | None = None


class AnalyzeProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analytics: ProfileAnalytics
    portfolio_analytics: PortfolioAnalytics | None = None
    guardrails: list[Guardrail]
    requires_api_key: bool = False


class AnalyzePortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio: Portfolio


class PolicyParameterSummary(BaseModel):
    """One threshold a persona carries, with where it came from.

    Flattened rather than exposing PolicyParameter whole: the interface needs the number, the
    band, and the provenance label, and shipping the internal shape would make every future
    change to it an API change.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None = None
    low: float | None = None
    high: float | None = None
    direction: str
    provenance: str
    confidence: float
    source_labels: list[str] = Field(default_factory=list)
    note: str = ""


class AdvisorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    subject: str
    origin: str
    one_line: str
    expertise: ExpertiseVector
    topic_affinity: list[str]
    mental_models: list[str]
    heuristics: list[str]
    reasoning_rules: list[str]
    blind_spots: list[str]
    honest_boundaries: list[str]
    disagrees_with: list[str]
    runtime_profile_tokens: int
    provenance: str
    # Typed thresholds, when the persona carries any. A persona with none contributes prose and
    # nothing to the arithmetic, and the interface should be able to say which it is.
    policy_parameters: list[PolicyParameterSummary] = Field(default_factory=list)


class SelectCommitteeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: FinancialProfile
    portfolio: Portfolio | None = None
    question: str = Field(min_length=1, max_length=4000)
    depth: AnalysisDepth = AnalysisDepth.balanced


class SelectCommitteeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: CommitteeSelection
    analytics: ProfileAnalytics
    portfolio_analytics: PortfolioAnalytics | None
    guardrails: list[Guardrail]
    question_topics: list[str]
    requires_api_key: bool = False
    # The deterministic decision layer. Present on the FREE path on purpose: candidate actions
    # and their arithmetic cost nothing to compute and need no API key, so the paid tier buys
    # the argument about them rather than the actions themselves.
    scenario: PortfolioScenario | None = None


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: AnalysisDepth = AnalysisDepth.balanced
    advisor_count: int = Field(default=3, ge=1, le=6)
    model: str = Field(default=DEFAULT_MODEL)


class EstimateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: AnalysisDepth
    advisor_count: int
    stages: list[str]
    expected_llm_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float | None
    pricing_version: str
    basis: str
    caveat: str = (
        "An estimate from measured per-call averages, not a quote. Actual usage depends on "
        "your profile size and the model's output length. You are billed by Anthropic directly."
    )


# --- LLM endpoints (key required) --------------------------------------------------------


class RunCommitteeRequest(CredentialedRequest):
    profile: FinancialProfile
    portfolio: Portfolio | None = None
    question: str = Field(min_length=1, max_length=4000)
    depth: AnalysisDepth = AnalysisDepth.balanced
    advisor_ids: list[str] | None = Field(
        default=None, description="Override the deterministic selection."
    )


class RunCommitteeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: CommitteeReport
    selection: CommitteeSelection
    usage: RunUsage
    analytics: ProfileAnalytics
    portfolio_analytics: PortfolioAnalytics | None
    # Sits beside the report rather than inside it. The scenario is deterministic and is
    # computed before any model runs; folding it into CommitteeReport would blur the one
    # distinction this architecture is built on — what code decided versus what Claude wrote.
    scenario: PortfolioScenario | None = None


class DistillRequest(CredentialedRequest):
    subject: str = Field(min_length=2, max_length=200)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    depth: DistillationDepth = DistillationDepth.standard
    advisor_id: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")


class DistillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor: AdvisorSummary
    warnings: list[str]
    research_pass_count: int
    usage: RunUsage


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: dict[str, Any] | None = None


class RunSummary(BaseModel):
    """One row of a signed-in user's run history. No snapshots, no report body — see RunDetail
    for that. Field types mirror the projection columns on public.committee_runs."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    created_at: str
    depth: str
    model: str
    question: str
    summary: str
    advisor_ids: list[str]
    guardrail_max_severity: str | None
    total_calls: int
    estimated_cost_usd: float | None


class RunDetail(BaseModel):
    """A saved run, reconstructed from its stored JSONB snapshots.

    Deliberately not typed as CommitteeReport / ProfileAnalytics / etc: those models are
    `extra="forbid"`, so a snapshot from before a schema change would fail to parse. Returning
    the stored JSON as-is keeps an old run viewable even if the current Pydantic models have
    since grown a field.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    created_at: str
    depth: str
    model: str
    question: str
    summary: str
    error_message: str | None
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float | None
    pricing_version: str

    profile_snapshot: dict[str, Any]
    portfolio_snapshot: dict[str, Any] | None
    analytics: dict[str, Any]
    portfolio_analytics: dict[str, Any] | None
    guardrails: list[Any]
    selection: dict[str, Any]
    report: dict[str, Any] | None


class Quote(BaseModel):
    """One delayed price. `as_of` is when the price was true, not when it was fetched — the two
    can be hours apart for a cached quote pulled outside market hours."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    price: float
    previous_close: float | None
    change_pct: float | None
    as_of: str
    is_delayed: bool
    source: str


class QuotesResponse(BaseModel):
    """Only the symbols the provider actually priced appear in `quotes`. Anything it could not
    resolve — a private holding, "CASH", a typo — is listed in `unpriced` instead of failing the
    request, so a portfolio of mixed market and non-market assets still gets an answer."""

    model_config = ConfigDict(extra="forbid")

    quotes: list[Quote]
    unpriced: list[str]


class SaveProfileRequest(BaseModel):
    """What a signed-in user is storing. No Anthropic key field, deliberately — saving a profile
    costs nothing and spends nothing, so this endpoint never sees a credential."""

    model_config = ConfigDict(extra="forbid")

    profile: FinancialProfile
    portfolio: Portfolio | None = None


class SavedProfileResponse(BaseModel):
    """`profile` is null when the user has an account but has never saved anything — a normal
    first-login state, not an error."""

    model_config = ConfigDict(extra="forbid")

    profile: FinancialProfile | None
    portfolio: Portfolio | None


# --- advisory consultation -------------------------------------------------------------


class ConsultRequest(CredentialedRequest):
    """One turn of the consultation.

    The whole conversation is sent each time. That is a deliberate v1 choice: chat state lives
    in browser memory, exactly like the API key, so there is no table, no migration, and nothing
    of a household's finances persisted server-side for a feature that is still a prototype.
    """

    profile: FinancialProfile
    portfolio: Portfolio | None = None
    question: str = Field(min_length=1, max_length=4000)
    advisor_ids: list[str] = Field(
        default_factory=lambda: ["buffett", "munger"],
        min_length=1,
        max_length=4,
        description="Built-in lenses. Nothing is re-distilled to answer a question.",
    )
    history: list[ChatMessage] = Field(
        default_factory=list, max_length=40, description="Prior turns, oldest first."
    )
    model: str = DEFAULT_MODEL


class ConsultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responses: list[AdvisorConsultResponse]
    candidates: list[DecisionCandidate]
    synthesis: ConsultSynthesis
    # Recomputed server-side from the profile in this request, never taken from the client: a
    # scenario the browser could edit would let a chat turn silently move the numbers.
    scenario: PortfolioScenario | None = None
    guardrails: list[Guardrail] = Field(default_factory=list)
    usage: RunUsage

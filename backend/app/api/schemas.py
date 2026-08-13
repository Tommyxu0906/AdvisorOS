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
from app.core.run_context import DEFAULT_MODEL
from app.domain.needs import ExpertiseVector
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import AnalysisDepth, CommitteeReport, Guardrail
from app.llm.usage import RunUsage
from app.nuwa.distiller import DistillationDepth


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


class AdvisorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    subject: str
    origin: str
    one_line: str
    expertise: ExpertiseVector
    topic_affinity: list[str]
    blind_spots: list[str]
    honest_boundaries: list[str]
    runtime_profile_tokens: int
    provenance: str


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

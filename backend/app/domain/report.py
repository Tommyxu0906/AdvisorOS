"""Committee output structures."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class GuardrailSeverity(str, Enum):
    info = "info"
    caution = "caution"
    blocking = "blocking"


class Guardrail(BaseModel):
    """A hard financial rule enforced by code, not by the model."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: GuardrailSeverity
    message: str
    detail: str = ""

    def render(self) -> str:
        return f"[{self.severity.value.upper()}] {self.code}: {self.message}"


class AnalysisDepth(str, Enum):
    quick = "quick"
    balanced = "balanced"
    deep = "deep"

    @property
    def advisor_count(self) -> int:
        """Target committee size for this depth."""
        return {AnalysisDepth.quick: 3, AnalysisDepth.balanced: 3, AnalysisDepth.deep: 4}[self]

    @property
    def min_advisor_count(self) -> int:
        """Floor the optimizer may trim to.

        Never below 3: a committee needs at least three perspectives for cross-examination and
        disagreement to mean anything. Trimming to one would be cheaper and useless.
        """
        return 3

    @property
    def stages(self) -> tuple[str, ...]:
        return {
            AnalysisDepth.quick: ("independent", "synthesis"),
            AnalysisDepth.balanced: (
                "independent",
                "cross_examination",
                "risk_challenge",
                "synthesis",
            ),
            AnalysisDepth.deep: (
                "independent",
                "cross_examination",
                "revised_memo",
                "risk_challenge",
                "synthesis",
            ),
        }[self]

    def expected_calls(self, advisors: int | None = None) -> int:
        n = advisors if advisors is not None else self.advisor_count
        if self is AnalysisDepth.quick:
            return n + 1
        if self is AnalysisDepth.balanced:
            return n + n + 1 + 1
        return n + n + n + 1 + 1


class AdvisorAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    display_name: str
    thesis: str
    reasoning: str
    recommendations: list[str] = Field(default_factory=list)
    risks_flagged: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    declined: bool = False
    declined_reason: str = ""


class Critique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_advisor_id: str
    target_advisor_id: str
    agreement: str = ""
    disagreement: str = ""
    strength: float = Field(default=0.5, ge=0, le=1, description="How strongly they disagree")


class RiskChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: list[str] = Field(default_factory=list)
    unaddressed_risks: list[str] = Field(default_factory=list)
    worst_case: str = ""


class CommitteeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime
    depth: AnalysisDepth
    question: str

    summary: str
    consensus: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    analyses: list[AdvisorAnalysis] = Field(default_factory=list)
    revised_analyses: list[AdvisorAnalysis] = Field(default_factory=list)
    critiques: list[Critique] = Field(default_factory=list)
    risk_challenge: RiskChallenge | None = None

    guardrails: list[Guardrail] = Field(default_factory=list)
    guardrail_violations: list[str] = Field(
        default_factory=list,
        description="Guardrails the synthesized report appears to contradict (post-hoc check)",
    )

    disclaimer: str = (
        "This is an educational analysis produced by AI personas, not personalized investment "
        "advice from a licensed advisor. Verify anything material with a qualified professional."
    )

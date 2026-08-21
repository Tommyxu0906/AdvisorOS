"""Types for the advisory consultation: candidates, stances, and what the constraint layer did.

The shape here encodes the one architectural claim that makes this an investment committee
rather than a chatbot with a finance skin: **the lenses have a real decision contribution, and
the arithmetic still wins.**

    Decision engine   -> feasible candidate scenarios
    Advisor lenses    -> rank / support / oppose / abstain over those candidates
    Constraint layer  -> reject impossible preferences, and say it did
    Synthesis         -> select and explain a feasible candidate

The failure mode this is built against is subtler than "the model hallucinates a trade". It is
the model preferring a course of action that arithmetic already ruled out, and the interface
presenting that preference as the answer because it reads well. So a preference that gets
overridden is not discarded — it is recorded, along with what overrode it, and shown.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Stance(str, Enum):
    endorse = "endorse"
    oppose = "oppose"
    mixed = "mixed"
    abstain = "abstain"

    @property
    def is_a_view(self) -> bool:
        """Abstention is the absence of a view, not a quiet vote for the status quo."""
        return self is not Stance.abstain


class ConfidenceSignal(str, Enum):
    """A band, never a float.

    The raw number the model states is uncalibrated — on a comparable benchmark, predictions
    made at 0.6–0.7 stated confidence were correct 42.9% of the time. A number wrong by roughly
    seventeen points of probability has no business appearing beside a financial recommendation,
    so the model is asked for a band and the band is all that ever leaves the server.
    """

    low = "low"
    medium = "medium"
    high = "high"


class CandidateKind(str, Enum):
    act = "act"
    """Carry out the computed scenario as it stands."""

    hold = "hold"
    """Change nothing. Only ever offered when no blocking guardrail requires action."""

    alternative_threshold = "alternative_threshold"
    """What the same portfolio implies under a threshold on the other side of the flip point."""


class DecisionCandidate(BaseModel):
    """One course of action the lenses may rank.

    Deliberately a small, honest set rather than an optimizer's output. The engine computes one
    scenario and a sensitivity sweep; that supports "act", "do nothing", and "the threshold a
    reasonable person might pick instead". Manufacturing more options would mean inventing
    portfolios nobody computed.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    kind: CandidateKind
    label: str
    summary: str

    action_ids: list[str] = Field(
        default_factory=list, description="Empty for hold. These are the only ids a lens may cite."
    )

    feasible: bool = True
    blocked_by: list[str] = Field(
        default_factory=list,
        description="Guardrail codes that forbid this candidate. Non-empty means infeasible.",
    )

    @model_validator(mode="after")
    def _blocked_means_infeasible(self) -> DecisionCandidate:
        if self.blocked_by and self.feasible:
            raise ValueError(
                f"{self.candidate_id}: blocked by {self.blocked_by} but marked feasible — "
                "a candidate cannot be both"
            )
        return self


class ConsultDepth(str, Enum):
    """How many rounds the committee runs, and therefore what a turn costs.

    Each level adds real model calls rather than only changing the wording of one, so the cost
    difference the interface implies is a cost difference that actually happens:

        quick     one pass per lens                          n calls
        balanced  plus a cross-examination round             2n calls
        deep      plus a self-challenge round                3n calls

    Cross-examination is where a committee earns its name — until the lenses have seen each
    other's answers they are parallel monologues. The prompt for that round is deliberately
    written to discourage converging under peer pressure, because a committee that agrees after
    being shown a disagreement has produced one opinion at n times the price.
    """

    quick = "quick"
    balanced = "balanced"
    deep = "deep"

    @property
    def rounds(self) -> int:
        return {ConsultDepth.quick: 1, ConsultDepth.balanced: 2, ConsultDepth.deep: 3}[self]

    @property
    def description(self) -> str:
        return {
            ConsultDepth.quick: "each framework answers independently",
            ConsultDepth.balanced: "plus one round after they have read each other",
            ConsultDepth.deep: "plus a round arguing against their own position",
        }[self]


class AdvisorConsultResponse(BaseModel):
    """One lens's structured contribution. Prose alone would not be rankable."""

    model_config = ConfigDict(extra="forbid")

    advisor_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    stance: Stance
    supported_action_ids: list[str] = Field(default_factory=list)
    opposed_action_ids: list[str] = Field(default_factory=list)
    preferred_candidate_id: str | None = None

    rationale: str = ""
    risks_or_missing_information: list[str] = Field(default_factory=list)
    confidence_signal: ConfidenceSignal = ConfidenceSignal.low

    declined: bool = False
    declined_reason: str = ""

    # --- what the constraint layer had to do to this response ------------------------
    corrections: list[str] = Field(
        default_factory=list,
        description="Invented ids dropped, infeasible preferences overridden. Shown, not hidden.",
    )
    parse_failed: bool = False

    @model_validator(mode="after")
    def _abstention_names_nothing(self) -> AdvisorConsultResponse:
        if self.stance is Stance.abstain and self.preferred_candidate_id is not None:
            raise ValueError(
                f"{self.advisor_id}: abstained but named a preferred candidate — abstention is "
                "the absence of a view, and recording both lets a reader keep whichever suits"
            )
        return self

    @property
    def was_corrected(self) -> bool:
        return bool(self.corrections)


class ChatRole(str, Enum):
    user = "user"
    committee = "committee"


class ChatMessage(BaseModel):
    """One turn. Held in browser memory for v1 — no table, no migration, no persistence."""

    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    text: str = Field(default="", max_length=4000)
    advisor_responses: list[AdvisorConsultResponse] = Field(default_factory=list)


class ConsultSynthesis(BaseModel):
    """The committee's position, after arithmetic has had the last word."""

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str
    selected_label: str
    headline: str

    endorsing: list[str] = Field(default_factory=list)
    opposing: list[str] = Field(default_factory=list)
    abstaining: list[str] = Field(default_factory=list)

    overrides: list[str] = Field(
        default_factory=list,
        description=(
            "Where a lens preferred something the constraints forbid. Reported rather than "
            "quietly dropped — a preference overruled by arithmetic is information."
        ),
    )
    unresolved_disagreement: bool = False

    @property
    def was_unanimous(self) -> bool:
        return not self.opposing and not self.unresolved_disagreement

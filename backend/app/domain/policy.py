"""Decision parameters that carry where they came from.

The first version of this was a plain float: `max_single_name_weight: float = 0.20`. That is
enough to compute with and not enough to be honest with, and the difference matters more here
than almost anywhere else in the system.

Distillation can establish, from documented material, that Buffett is willing to hold a few
businesses in size while Bogle is not. It cannot establish that Buffett's cap for a 27-year-old
retail investor is 25%. Hand-authoring `buffett.max_single_name_weight = 0.25` converts a real,
evidence-backed *direction* into a fabricated *number*, and the resulting report looks more
quantitative precisely where it became less true. A float has nowhere to record that distinction.

So a parameter separates four things that a single number conflates:

  What the evidence supports — often a `direction` and a plausible `low`/`high` band, not a point.
  Where it came from — `provenance`, which distinguishes what the subject said from what
  AdvisorOS decided in order to have something to compute with.
  How sure we are — `confidence`, and `as_of`, because a distillation is a snapshot of public
  expression at a moment, not a standing relationship with the person.
  When it applies — `applicable_scope`, since "concentration is fine" is a claim about
  high-conviction business ownership, not about every position anyone holds.

`resolve()` is where this pays off. When a persona has no value, the policy still runs — but the
number it runs on is labelled `house_default`, and everything downstream can say so. The report
then reads "AdvisorOS applies a 20% threshold for this scenario; Buffett's documented direction
is a tolerance for concentration" rather than putting a made-up cap in a dead man's mouth.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Provenance(str, Enum):
    """Where a parameter's value actually comes from."""

    direct = "direct"
    """The subject stated this threshold, in these terms."""

    derived = "derived"
    """Inferred from documented decisions or statements. A reading, not a quote."""

    house_default = "house_default"
    """AdvisorOS's own number, used so a policy can run. Not attributable to the subject."""

    unknown = "unknown"
    """No basis. The policy declines rather than guessing."""

    @property
    def attributable_to_subject(self) -> bool:
        return self in (Provenance.direct, Provenance.derived)


class Direction(str, Enum):
    """What the evidence supports even when it fixes no number.

    This is usually the strongest honest claim available: that a subject leaned toward
    concentration is well documented; that their threshold was 25% is not.
    """

    tolerates = "tolerates"
    avoids = "avoids"
    neutral = "neutral"


class PolicyScope(str, Enum):
    """Decisions a persona is willing to weigh in on.

    Mirrors `honest_boundaries`, which are prose the model reads, in a form the policy engine can
    branch on. An advisor whose boundaries say "will not pick individual securities" should not
    be made to emit a per-symbol trim just because the engine can compute one — declining is a
    contribution, and the committee charter already treats it as such.
    """

    concentration = "concentration"
    allocation = "allocation"
    debt = "debt"
    liquidity = "liquidity"


class PolicyParameterName(str, Enum):
    single_name_concentration = "single_name_concentration"
    top3_concentration = "top3_concentration"
    equity_share_at_40 = "equity_share_at_40"
    equity_decline_per_decade = "equity_decline_per_decade"
    defensive_floor = "defensive_floor"
    max_expense_ratio = "max_expense_ratio"
    emergency_fund_months = "emergency_fund_months"


class PolicyParameter(BaseModel):
    """One decision threshold, with its evidence attached."""

    model_config = ConfigDict(extra="forbid")

    name: PolicyParameterName
    # None is a legitimate, common answer: the evidence supports a direction but fixes no number.
    value: float | None = None
    low: float | None = None
    high: float | None = None

    direction: Direction = Direction.neutral
    provenance: Provenance = Provenance.unknown
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    source_labels: list[str] = Field(
        default_factory=list,
        description="Evidence labels on the manifest this reading rests on",
    )
    as_of: str | None = Field(
        default=None,
        description="When the underlying material was current, e.g. '2026-08'",
    )
    applicable_scope: list[str] = Field(
        default_factory=list,
        description="Conditions under which the subject's position holds, in their own terms",
    )
    note: str = ""

    @model_validator(mode="after")
    def _evidence_supports_the_claim(self) -> PolicyParameter:
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"{self.name}: low {self.low} exceeds high {self.high}")
        # A number with no stated origin is the failure mode this whole module exists to
        # prevent, so it is rejected rather than quietly defaulted.
        if self.value is not None and self.provenance is Provenance.unknown:
            raise ValueError(
                f"{self.name}: a value of {self.value} needs a provenance — "
                "direct, derived, or house_default"
            )
        if self.provenance.attributable_to_subject and self.confidence <= 0:
            raise ValueError(
                f"{self.name}: {self.provenance.value} evidence must carry a confidence above 0"
            )
        return self


class ResolvedParameter(BaseModel):
    """The number a policy will actually use, and whether it belongs to the persona.

    Policies compute with `value`. Everything user-facing reads `provenance` first, because
    "Bogle's 5% rule" and "the 20% AdvisorOS uses when an advisor has no stated view" must never
    be rendered the same way.
    """

    model_config = ConfigDict(extra="forbid")

    name: PolicyParameterName
    value: float
    provenance: Provenance
    direction: Direction = Direction.neutral
    confidence: float = 0.0
    source_labels: list[str] = Field(default_factory=list)
    as_of: str | None = None
    applicable_scope: list[str] = Field(default_factory=list)

    @property
    def is_house_number(self) -> bool:
        return self.provenance is Provenance.house_default

    def attribution(self, display_name: str, *, is_house_run: bool = False) -> str:
        """One clause naming whose threshold this is. Used verbatim in action rationales.

        `is_house_run` exists because the house-default wording distinguishes an AdvisorOS number
        from *an advisor's* number, and there is no advisor when the engine runs on its own
        behalf. Without it the sentence negates itself — "an AdvisorOS threshold ... not
        AdvisorOS's number" — which is what shipped to the browser before anyone read the
        rendered output end to end.
        """
        if self.provenance is Provenance.direct:
            return f"{display_name} states this threshold directly"
        if self.provenance is Provenance.derived:
            confidence = f"{self.confidence:.0%} confidence"
            return (
                f"read from {display_name}'s documented decisions ({confidence}), "
                "not a figure they published"
            )
        if is_house_run:
            return "an AdvisorOS threshold, applied because no advisor has supplied one"
        return (
            f"an AdvisorOS threshold used so the scenario can be computed, "
            f"not {display_name}'s number"
        )


class PolicyProfile(BaseModel):
    """Every decision parameter a persona carries, plus what it will opine on.

    Empty is a valid and honest state: a persona with no evidence-backed parameters still
    reasons, still critiques, and still declines — it simply contributes no thresholds of its
    own, and the policies run on house numbers that say so.
    """

    model_config = ConfigDict(extra="forbid")

    parameters: dict[PolicyParameterName, PolicyParameter] = Field(default_factory=dict)
    scopes: list[PolicyScope] = Field(
        default_factory=lambda: [
            PolicyScope.concentration,
            PolicyScope.allocation,
            PolicyScope.debt,
            PolicyScope.liquidity,
        ],
        description="Decisions this persona will weigh in on; see PolicyScope",
    )
    # A qualitative reading that survives even when no number does — the flag that lets a
    # rationale say "concentration is acceptable here" without inventing a cap.
    allows_concentration_on_conviction: bool = False

    def covers(self, scope: PolicyScope) -> bool:
        return scope in self.scopes

    def resolve(self, name: PolicyParameterName, house_default: float) -> ResolvedParameter:
        """The value a policy should use, carrying an honest account of where it came from."""
        parameter = self.parameters.get(name)
        if parameter is None or parameter.value is None:
            return ResolvedParameter(
                name=name,
                value=house_default,
                provenance=Provenance.house_default,
                # A direction with no number is still real evidence and still worth carrying:
                # it is what lets the report distinguish a persona who tolerates concentration
                # from one who has no view, even though both compute on the house threshold.
                direction=parameter.direction if parameter else Direction.neutral,
                confidence=parameter.confidence if parameter else 0.0,
                source_labels=list(parameter.source_labels) if parameter else [],
                as_of=parameter.as_of if parameter else None,
                applicable_scope=list(parameter.applicable_scope) if parameter else [],
            )
        return ResolvedParameter(
            name=name,
            value=parameter.value,
            provenance=parameter.provenance,
            direction=parameter.direction,
            confidence=parameter.confidence,
            source_labels=list(parameter.source_labels),
            as_of=parameter.as_of,
            applicable_scope=list(parameter.applicable_scope),
        )

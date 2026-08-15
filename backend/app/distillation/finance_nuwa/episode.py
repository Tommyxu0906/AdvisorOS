"""One decision, the conditions it was taken under, and a wall between them and what followed.

A decision episode is the unit this whole approach rests on: at a point in time, facing a
knowable situation, the subject did something specific. Replay a persona against the situation,
compare its answer to what actually happened, and you have a score that "sounds like Buffett"
can never give you.

That only works if the persona cannot see the future, and the failure is seductive rather than
obvious. Suppose an episode records that Apple was bought in 2016 Q1 and, anywhere in the same
object, that the position went on to compound for years. Every summary written from that object,
every retrieval snippet, every "context" string, now carries the answer. The persona scores
brilliantly and has learned nothing, because hindsight was in the prompt. Worse, the resulting
model looks validated, which is more dangerous than looking unvalidated.

So this module does not ask anyone to remember the rule:

    EpisodeInputs   — everything knowable on the decision date, and nothing else. A validator
                      rejects any observation stamped later than that date, so an episode
                      carrying future information cannot be constructed at all.

    EpisodeOutcome  — what happened next. Lives on the episode for scoring and analysis, and is
                      structurally unreachable from the object handed to a replay actor.

    DecisionEpisode.for_replay() — the only supported way to build actor input. It returns
                      inputs alone, so leaking the outcome takes deliberate effort rather than
                      an oversight.

The other thing recorded here is **whose decision it was**. An institutional filing shows what
an entity held, not which person chose it: large books are run by several managers, and index
or cash-management positions may reflect no view at all. Attribution is a first-class field
with a confidence, because training a Buffett persona on a colleague's trades teaches a
confident model of the wrong person.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.distillation.finance_nuwa.drift import ActionBasis, ObservedAction


class AttributionBasis(str, Enum):
    """How well we know this decision belongs to the subject we are modelling."""

    self_described = "self_described"
    """The subject said they made it, in public, about this position."""

    sole_decision_maker = "sole_decision_maker"
    """The subject is documented as the only person who could have made it."""

    entity_filing = "entity_filing"
    """An entity's disclosure. The subject leads the entity but may not have chosen this."""

    inferred = "inferred"
    """Attributed by reasoning rather than record. The weakest basis."""

    @property
    def is_personal(self) -> bool:
        return self in (AttributionBasis.self_described, AttributionBasis.sole_decision_maker)


class Observation(BaseModel):
    """One fact, with the date it became knowable.

    `observed_at` is not decoration and is not the date the fact was *about* — it is the date it
    entered the public record. A quarterly report describes a quarter that has already ended but
    only becomes knowable when it is filed, and using the period end would hand a replay several
    weeks of information nobody had.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: Any = None
    observed_at: date
    source: str = ""
    source_kind: str = Field(
        default="", description="filing | letter | interview | market_data | commentary"
    )


class EpisodeInputs(BaseModel):
    """The situation as it stood. The only object a replay actor is ever given.

    Every observation is checked against `as_of`. Nothing dated later can be attached, so an
    episode that would leak the future fails to construct rather than failing silently.
    """

    model_config = ConfigDict(extra="forbid")

    as_of: date

    symbol: str = Field(min_length=1)
    starting_weight: float | None = Field(default=None, ge=0, le=1)
    starting_value: float | None = Field(default=None, ge=0)

    portfolio_context: list[Observation] = Field(
        default_factory=list, description="Concentration, cash, other positions, book size"
    )
    market_context: list[Observation] = Field(
        default_factory=list, description="Regime, rates, index levels, volatility"
    )
    fundamentals: list[Observation] = Field(
        default_factory=list, description="Company financials as reported by this date"
    )
    valuation: list[Observation] = Field(
        default_factory=list, description="Multiples, yields, implied assumptions"
    )

    @model_validator(mode="after")
    def _nothing_from_the_future(self) -> EpisodeInputs:
        for field in ("portfolio_context", "market_context", "fundamentals", "valuation"):
            for observation in getattr(self, field):
                if observation.observed_at > self.as_of:
                    raise ValueError(
                        f"{field}: '{observation.label}' became knowable on "
                        f"{observation.observed_at}, after the {self.as_of} decision date — "
                        "an episode carrying hindsight would score well and teach nothing"
                    )
        return self

    def all_observations(self) -> list[Observation]:
        return [
            *self.portfolio_context,
            *self.market_context,
            *self.fundamentals,
            *self.valuation,
        ]


class EpisodeOutcome(BaseModel):
    """What happened after. For scoring and analysis — never for prediction.

    Deliberately a separate model rather than optional fields on the episode. Fields on one
    object get serialized together, summarized together, and pasted into prompts together; a
    separate type has to be reached for on purpose.
    """

    model_config = ConfigDict(extra="forbid")

    horizon_months: int = Field(ge=0)
    position_return: float | None = None
    benchmark_return: float | None = None
    subsequent_action: ObservedAction | None = Field(
        default=None, description="What they did next, if the record shows it"
    )
    note: str = ""

    @property
    def excess_return(self) -> float | None:
        if self.position_return is None or self.benchmark_return is None:
            return None
        return self.position_return - self.benchmark_return


class DecisionEpisode(BaseModel):
    """One observed decision, ready to replay against.

    Scoring weight should come from `training_weight`, not from treating every episode alike: an
    exact share-count change the subject publicly explained is far better evidence than a
    value-inferred move at an entity the subject merely chairs.
    """

    model_config = ConfigDict(extra="forbid")

    advisor_id: str = Field(pattern=r"^[a-z0-9_]+$")
    episode_id: str = Field(min_length=1)

    inputs: EpisodeInputs
    observed_action: ObservedAction
    action_basis: ActionBasis
    magnitude_low: float | None = Field(
        default=None, ge=0, description="Fraction of the position traded, lower bound"
    )
    magnitude_high: float | None = Field(default=None, ge=0)

    attribution: AttributionBasis = AttributionBasis.entity_filing
    attribution_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    rationale_sources: list[Observation] = Field(
        default_factory=list,
        description="Public explanation of this decision. May postdate it — see the validator.",
    )

    outcome: EpisodeOutcome | None = None

    @model_validator(mode="after")
    def _coherent(self) -> DecisionEpisode:
        if (
            self.magnitude_low is not None
            and self.magnitude_high is not None
            and self.magnitude_low > self.magnitude_high
        ):
            raise ValueError(
                f"{self.episode_id}: magnitude range {self.magnitude_low}-{self.magnitude_high} "
                "is inverted"
            )
        if self.observed_action is ObservedAction.hold and (
            self.magnitude_low or self.magnitude_high
        ):
            raise ValueError(f"{self.episode_id}: a hold has no traded magnitude")
        # Personal attribution is a strong claim and must not be made with weak confidence —
        # the entire value of an episode is who it belongs to.
        if self.attribution.is_personal and self.attribution_confidence < 0.5:
            raise ValueError(
                f"{self.episode_id}: {self.attribution.value} attribution claims the subject "
                f"personally decided this, which {self.attribution_confidence:.0%} confidence "
                "does not support"
            )
        return self

    def for_replay(self) -> EpisodeInputs:
        """The only supported way to build actor input.

        Returns the inputs and nothing else. A caller who wants to leak the outcome into a
        prompt has to go and get it, which is the difference between a mistake and a decision.
        """
        return self.inputs

    @property
    def training_weight(self) -> float:
        """How much this episode should count, in [0, 1].

        Two independent discounts, multiplied because they compound: an inferred action at an
        entity the subject merely chairs is doubly uncertain, and averaging the two would hide
        that. Exactness of the observation is not the same question as whose decision it was.
        """
        by_basis = {
            ActionBasis.share_count: 1.0,
            ActionBasis.stated: 0.9,
            ActionBasis.drift_adjusted_value: 0.6,
            ActionBasis.raw_value: 0.3,
        }[self.action_basis]
        return round(by_basis * self.attribution_confidence, 4)

    @property
    def is_load_bearing(self) -> bool:
        """Worth training on unreviewed. Deliberately strict."""
        return self.training_weight >= 0.5

"""What a persona is allowed to say about one position, and what it may not.

Three constraints, and each exists because the obvious alternative produces a number that reads
as a result and is not one.

**No prose-only answer.** A paragraph about margin of safety cannot be scored, and turning one
into a label after the fact means the scorer is doing the predicting. The output is a typed
object or it is a parse failure, and parse failures are counted rather than retried into
compliance.

**Abstention is a real answer.** A distilled philosophy genuinely has nothing to say about most
individual quarters — "buy understandable businesses at sensible prices" does not determine
whether to trim Coca-Cola in 2019 Q2. Forcing an opinion on all 505 episodes manufactures 505
opinions, and their accuracy would measure the forcing. So `abstain` is first-class, coverage is
reported beside accuracy, and a persona that answers 40% of the time at 0.42 macro F1 is a
different and more honest object than one that answers everything at 0.28.

**Self-reported confidence is not a probability.** The quant baselines are already overconfident
— the 0.6-0.7 bucket was right 42.9% of the time — and a language model's stated 0.8 has even
less claim to be a frequency. It is kept as `confidence`, a raw score, and it may not be called a
probability until a calibration curve has been measured. The type says so in the field name and
the docstring so that nobody has to remember.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.distillation.finance_nuwa.task import INCUMBENT_CLASSES

# Bumped when the mapping from model output to a label changes — for example if abstention
# started resolving to hold. Two results computed under different mappings are not comparable,
# and the version is what makes that visible instead of arguable.
DECISION_MAPPING_VERSION = "abstain-is-not-hold-v1"


class BehavioralAction(str, Enum):
    """The four things that can be done with a position already held."""

    hold = "hold"
    increase = "increase"
    reduce = "reduce"
    exit = "exit"


class ReasonCode(str, Enum):
    """Why, in categories a disagreement analysis can count.

    Free text would be more expressive and unusable: the question this benchmark eventually has
    to answer is whether the persona contributes information the quant features do not already
    carry, and answering it means grouping hundreds of explanations. These are the axes on which
    Berkshire's documented positions differ from a generic portfolio-state model.
    """

    concentration_tolerance = "concentration_tolerance"
    """Comfortable with a position size a diversification rule would trim."""

    long_holding_horizon = "long_holding_horizon"
    """The holding period is the point; turnover is the cost."""

    hold_through_drawdown = "hold_through_drawdown"
    """A price fall is not itself a reason to act, and may be a reason not to."""

    conviction_scaling = "conviction_scaling"
    """Size follows certainty about the business, not recent price action."""

    exit_discipline = "exit_discipline"
    """The thesis broke, the business changed, or the price left any defensible range."""

    valuation_discipline = "valuation_discipline"
    """The price relative to what the business earns is doing the work."""

    circle_of_competence = "circle_of_competence"
    """Outside what the framework claims to be able to judge."""

    capital_allocation = "capital_allocation"
    """The decision is about where the money goes next, not about this security alone."""

    other = "other"


class BehavioralPrediction(BaseModel):
    """One persona's answer for one position, in a form that can be scored.

    `abstain=True` means the framework does not determine an action here. It is not a hedge and
    it is not a hold: a hold is a claim that doing nothing is right, and abstention is a claim
    that this evidence cannot tell. Collapsing them would silently convert every "I don't know"
    into the majority class and inflate accuracy at 69% prevalence.
    """

    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1)
    action: BehavioralAction | None = None
    abstain: bool = False

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Self-reported and NOT a calibrated probability. Raw score until a "
        "calibration curve has been measured on validation.",
    )
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Labels of the distilled material the answer leans on. Never a URL, never "
        "free-form quotation of source text.",
    )
    note: str = Field(default="", max_length=400)

    # Set by the harness, not by the model.
    parse_failed: bool = False
    raw_text: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> BehavioralPrediction:
        if self.abstain and self.action is not None:
            raise ValueError(
                f"{self.episode_id}: abstaining and naming an action are different answers, and "
                "recording both lets a scorer pick whichever one was right"
            )
        if not self.abstain and self.action is None and not self.parse_failed:
            raise ValueError(
                f"{self.episode_id}: an answer must be an action or an abstention. Silence is "
                "scored as a parse failure, which is counted separately on purpose"
            )
        return self

    @property
    def answered(self) -> bool:
        return self.action is not None and not self.abstain

    @property
    def label(self) -> str | None:
        """The scoreable label, or None when there is nothing to score.

        Deliberately not defaulting to hold — see the class docstring. Everything downstream that
        wants a full-coverage number must say so by supplying its own fallback, which makes the
        choice visible in the code that made it.
        """
        return self.action.value if self.answered else None


class PredictionSet(BaseModel):
    """Every answer for one run, with the shape of the non-answers reported rather than dropped."""

    model_config = ConfigDict(extra="forbid")

    predictions: list[BehavioralPrediction] = Field(default_factory=list)
    decision_mapping_version: str = DECISION_MAPPING_VERSION

    @property
    def answered(self) -> list[BehavioralPrediction]:
        return [p for p in self.predictions if p.answered]

    @property
    def coverage(self) -> float:
        """Share of episodes the persona was willing to answer. Reported beside every score."""
        if not self.predictions:
            return 0.0
        return round(len(self.answered) / len(self.predictions), 4)

    @property
    def abstention_rate(self) -> float:
        if not self.predictions:
            return 0.0
        return round(sum(1 for p in self.predictions if p.abstain) / len(self.predictions), 4)

    @property
    def parse_failure_rate(self) -> float:
        """Counted separately from abstention. A model that could not produce valid output has
        not declined to answer; it has failed, and averaging the two hides a broken prompt."""
        if not self.predictions:
            return 0.0
        return round(sum(1 for p in self.predictions if p.parse_failed) / len(self.predictions), 4)

    def aligned(self, truth: dict[str, str]) -> tuple[list[str], list[str]]:
        """(actual, predicted) over answered episodes only, in a stable order."""
        pairs = [
            (truth[p.episode_id], p.label)
            for p in sorted(self.answered, key=lambda p: p.episode_id)
            if p.episode_id in truth
        ]
        return [a for a, _ in pairs], [b for _, b in pairs]


# The schema handed to the model. Written out rather than generated from the pydantic model
# because the two serve different masters: this one has to be small and unambiguous to a language
# model, and the pydantic model has to be strict against everything that comes back.
PREDICTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "abstain": {
            "type": "boolean",
            "description": "True when the framework does not determine an action here. Not a "
            "hedge — prefer it over guessing.",
        },
        "action": {
            "type": ["string", "null"],
            "enum": [*INCUMBENT_CLASSES, None],
            "description": "Null when abstaining.",
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason_codes": {
            "type": "array",
            "items": {"type": "string", "enum": [c.value for c in ReasonCode]},
            "maxItems": 3,
        },
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "note": {"type": "string", "maxLength": 400},
    },
    "required": ["abstain", "action", "confidence", "reason_codes"],
    "additionalProperties": False,
}

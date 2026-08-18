"""Whether the persona knows something the features do not, or just says it differently.

A higher macro F1 is not the question. A persona could beat the quant model by being a slightly
better function of exactly the same inputs — position size, holding duration, recent price action
— and that would be worth very little, because those inputs are already in the feature vector and
a bigger tree would eventually find them too.

The question is whether the persona is *right in different places*. So the four cells:

    both right          the easy cases, and mostly the large long-held positions
    both wrong          the ceiling: neither information set determines these
    quant right only    the persona talked itself out of a straightforward reading
    persona right only  the interesting cell, and the only evidence for the architecture

The last cell is what a retrieval or refinement stage would be built to enlarge, and its *size*
is only half the finding. If the persona's wins are spread evenly across reason codes, it is
probably adding noise that sometimes lands. If they concentrate — holding through a drawdown, or
tolerating a concentration a diversification rule would trim — then the framework is contributing
a specific behavioural disposition that the features do not encode, and that is a result worth
building on.

Run on validation only. Doing this on held-out would be reading the answers in order to decide
what to build next, which is the same act as tuning on it.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.prediction import ReasonCode


class DisagreementCell(str, Enum):
    both_correct = "both_correct"
    both_wrong = "both_wrong"
    quant_only = "quant_correct_persona_wrong"
    persona_only = "persona_correct_quant_wrong"


class DisagreementCase(BaseModel):
    """One episode where the two disagreed, kept with enough context to read by hand."""

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    actual: str
    quant: str
    persona: str
    cell: DisagreementCell
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    confidence: float = 0.0
    weight: float | None = None
    quarters_held: int | None = None
    drawdown: float | None = None


class DisagreementReport(BaseModel):
    """The 2x2, and what the persona's wins were made of."""

    model_config = ConfigDict(extra="forbid")

    model_a: str = "quant"
    model_b: str = "persona"
    split: str = "validation"
    n: int = 0

    both_correct: int = 0
    both_wrong: int = 0
    quant_only: int = 0
    persona_only: int = 0

    # Only over episodes both models answered — the persona may abstain, and counting an
    # abstention as a loss would make coverage look like accuracy.
    compared: int = 0
    persona_abstained: int = 0

    persona_win_reasons: dict[str, int] = Field(default_factory=dict)
    persona_loss_reasons: dict[str, int] = Field(default_factory=dict)
    cases: list[DisagreementCase] = Field(default_factory=list)

    @property
    def net_persona_gain(self) -> int:
        """Episodes the persona wins minus those it loses. Zero means it is a different route to
        the same answers, whatever the headline scores look like."""
        return self.persona_only - self.quant_only

    @property
    def reason_concentration(self) -> float:
        """Share of the persona's wins carried by its single commonest reason code.

        High means a specific disposition is doing the work — a claim that can be checked and
        built on. Low means the wins are scattered, which is what adding noise looks like when it
        occasionally lands.
        """
        if not self.persona_win_reasons:
            return 0.0
        return round(
            max(self.persona_win_reasons.values()) / sum(self.persona_win_reasons.values()), 4
        )

    def render(self) -> str:
        lines = [
            f"DISAGREEMENT  ·  {self.model_a} vs {self.model_b}  ·  {self.split}",
            f"  compared {self.compared} of {self.n}"
            f"   (persona abstained on {self.persona_abstained})",
            "",
            f"  both correct                 {self.both_correct:>5}",
            f"  both wrong                   {self.both_wrong:>5}   <- ceiling for both",
            f"  quant right, persona wrong   {self.quant_only:>5}",
            f"  persona right, quant wrong   {self.persona_only:>5}   <- the only evidence "
            "for the architecture",
            f"  net persona gain             {self.net_persona_gain:>5}",
        ]
        if self.persona_win_reasons:
            lines += ["", "  What the persona's wins were made of:"]
            for code, count in sorted(self.persona_win_reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {code:<28} {count:>4}")
            lines.append(
                f"    concentrated in one reason: {self.reason_concentration:.0%}"
                + (
                    "  (a specific disposition, worth building on)"
                    if self.reason_concentration >= 0.4
                    else "  (scattered — consistent with noise that sometimes lands)"
                )
            )
        if self.persona_loss_reasons:
            lines += ["", "  What its losses were made of:"]
            for code, count in sorted(self.persona_loss_reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {code:<28} {count:>4}")
        return "\n".join(lines)


def analyse(
    truth: dict[str, str],
    quant: dict[str, str],
    predictions,
    features: dict[str, dict] | None = None,
    *,
    split: str = "validation",
    max_cases: int = 40,
) -> DisagreementReport:
    """Build the 2x2 over episodes both models actually answered.

    Abstentions are excluded from the comparison and counted separately. Scoring an abstention as
    a loss would fold coverage into accuracy, and the whole point of making abstention
    first-class was to keep those two facts apart.
    """
    features = features or {}
    by_id = {p.episode_id: p for p in predictions.predictions}

    report = DisagreementReport(split=split, n=len(truth))
    win_reasons: Counter = Counter()
    loss_reasons: Counter = Counter()
    cases: list[DisagreementCase] = []

    for episode_id in sorted(truth):
        prediction = by_id.get(episode_id)
        if prediction is None or episode_id not in quant:
            continue
        if not prediction.answered:
            report.persona_abstained += 1
            continue

        report.compared += 1
        actual = truth[episode_id]
        quant_right = quant[episode_id] == actual
        persona_right = prediction.label == actual

        if quant_right and persona_right:
            report.both_correct += 1
            cell = DisagreementCell.both_correct
        elif quant_right:
            report.quant_only += 1
            cell = DisagreementCell.quant_only
            loss_reasons.update(c.value for c in prediction.reason_codes)
        elif persona_right:
            report.persona_only += 1
            cell = DisagreementCell.persona_only
            win_reasons.update(c.value for c in prediction.reason_codes)
        else:
            report.both_wrong += 1
            cell = DisagreementCell.both_wrong

        if (
            cell in (DisagreementCell.persona_only, DisagreementCell.quant_only)
            and len(cases) < max_cases
        ):
            row = features.get(episode_id, {})
            cases.append(
                DisagreementCase(
                    episode_id=episode_id,
                    actual=actual,
                    quant=quant[episode_id],
                    persona=prediction.label or "",
                    cell=cell,
                    reason_codes=list(prediction.reason_codes),
                    confidence=prediction.confidence,
                    weight=row.get("weight"),
                    quarters_held=row.get("quarters_held"),
                    drawdown=row.get("drawdown_from_peak"),
                )
            )

    report.persona_win_reasons = dict(win_reasons)
    report.persona_loss_reasons = dict(loss_reasons)
    report.cases = cases
    return report

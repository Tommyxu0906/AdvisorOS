"""How a behavioural result is allowed to be reported.

Written before any model exists, which is the only time it can be written honestly. Once there is
a number, every choice about how to score it becomes a choice about how good the number looks.

Four rules are built into the types here rather than left to discipline.

**Accuracy is not reportable on its own.** The natural class prevalence is 69% hold, so a model
that answers "hold" unconditionally scores 69% and has learned nothing. Macro F1 and balanced
accuracy weight the classes equally, which is what makes them able to distinguish a behavioural
model from a constant.

**A probability is only calibrated against the prevalence it was fitted to.** The matched view
deliberately discards most holds so that a trade and a hold can be compared under similar
conditions — which changes the class prior on purpose. A confidence produced under that prior is
not a real-world probability, so `Calibration` records which view it came from and the report
refuses to present matched-view calibration as if it described deployment.

**Thin classes need intervals, not point estimates.** There are 85 ENTER episodes across eleven
years. A per-class F1 computed on 28 held-out examples moves by a lot when two of them change, and
quoting it to three decimals implies a precision the sample cannot support.

**Nothing collapses into one number.** There is no weighted blend of these metrics anywhere in
this module, and adding one would let a weak result on the deployable benchmark be averaged away
by a strong one on the research upper bound.
"""

from __future__ import annotations

import math
import random
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

# The five behavioural classes, in a fixed order so every confusion matrix in every report reads
# the same way. Order is chosen to run sell -> hold -> buy, which makes the diagonal legible.
CLASSES = ("exit", "reduce", "hold", "increase", "enter")

BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 20240816
CONFIDENCE_BINS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)


class ClassMetrics(BaseModel):
    """Precision, recall and F1 for one class, with the support behind them."""

    model_config = ConfigDict(extra="forbid")

    label: str
    support: int
    predicted: int
    precision: float
    recall: float
    f1: float

    # Percentile bootstrap over episodes. Absent when no interval was requested.
    f1_low: float | None = None
    f1_high: float | None = None
    recall_low: float | None = None
    recall_high: float | None = None

    @property
    def interval_width(self) -> float | None:
        if self.f1_low is None or self.f1_high is None:
            return None
        return round(self.f1_high - self.f1_low, 4)


class CalibrationBin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: float
    high: float
    count: int
    mean_confidence: float
    accuracy: float

    @property
    def gap(self) -> float:
        return round(self.accuracy - self.mean_confidence, 4)


class Calibration(BaseModel):
    """Whether a stated confidence means what it says.

    `view` is not decoration. A probability fitted on the matched view was fitted under a class
    prior that was changed on purpose, so it cannot be read as a real-world action frequency —
    and the field is here so that a table cannot be copied out of context without it.
    """

    model_config = ConfigDict(extra="forbid")

    view: str
    log_loss: float
    brier: float
    expected_calibration_error: float
    bins: list[CalibrationBin] = Field(default_factory=list)

    @property
    def is_deployable_prior(self) -> bool:
        return self.view.endswith("natural")


class Evaluation(BaseModel):
    """One model, one view, one split. Never blended with another."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    view: str
    split: str
    information_set: str = "public_observer"

    n: int = 0
    accuracy: float = 0.0
    balanced_accuracy: float = 0.0
    macro_f1: float = 0.0
    macro_f1_low: float | None = None
    macro_f1_high: float | None = None

    per_class: list[ClassMetrics] = Field(default_factory=list)
    confusion: dict[str, dict[str, int]] = Field(default_factory=dict)
    calibration: Calibration | None = None

    majority_class_rate: float = Field(
        default=0.0, description="What answering the commonest label every time would score"
    )

    def render(self) -> str:
        lines = [
            f"{self.model_name}  ·  {self.view}  ·  {self.split}  ·  n={self.n}",
            f"  macro F1 {self.macro_f1:.3f}"
            + (
                f"  [{self.macro_f1_low:.3f}, {self.macro_f1_high:.3f}]"
                if self.macro_f1_low is not None
                else ""
            )
            + f"   balanced acc {self.balanced_accuracy:.3f}"
            f"   accuracy {self.accuracy:.3f}   (majority {self.majority_class_rate:.3f})",
            f"  {'class':<10}{'supp':>6}{'pred':>6}{'prec':>8}{'recall':>8}{'F1':>8}"
            f"{'F1 95% CI':>20}",
        ]
        for metric in self.per_class:
            interval = (
                f"[{metric.f1_low:.2f}, {metric.f1_high:.2f}]" if metric.f1_low is not None else ""
            )
            lines.append(
                f"  {metric.label:<10}{metric.support:>6}{metric.predicted:>6}"
                f"{metric.precision:>8.3f}{metric.recall:>8.3f}{metric.f1:>8.3f}"
                f"{interval:>20}"
            )
        return "\n".join(lines)

    def render_confusion(self) -> str:
        lines = ["  confusion (rows = actual, columns = predicted)", "  " + " " * 10]
        header = "  " + " " * 10 + "".join(f"{c:>10}" for c in CLASSES)
        lines = ["  confusion (rows = actual, columns = predicted)", header]
        for actual in CLASSES:
            row = self.confusion.get(actual, {})
            lines.append(f"  {actual:<10}" + "".join(f"{row.get(p, 0):>10}" for p in CLASSES))
        return "\n".join(lines)


class PairedComparison(BaseModel):
    """The same episodes, scored under two information sets.

    Two accuracies side by side cannot separate "the model does not know the policy" from "nobody
    outside the firm could have known the holdings". The four cells can: the off-diagonal where
    the oracle is right and the public observer is wrong is reporting lag, and the cell where both
    are wrong is the part no amount of information fixes.
    """

    model_config = ConfigDict(extra="forbid")

    model_name: str
    split: str
    n: int = 0

    both_correct: int = 0
    public_wrong_oracle_correct: int = 0
    both_wrong: int = 0
    public_correct_oracle_wrong: int = 0

    public_macro_f1: float = 0.0
    oracle_macro_f1: float = 0.0

    @property
    def information_lag_share(self) -> float:
        """Errors the extra information would have fixed, as a share of all public errors."""
        errors = self.public_wrong_oracle_correct + self.both_wrong
        return round(self.public_wrong_oracle_correct / errors, 4) if errors else 0.0

    def render(self) -> str:
        return "\n".join(
            [
                f"{self.model_name}  ·  public vs oracle  ·  {self.split}  ·  n={self.n}",
                f"  both correct                  {self.both_correct:>6}",
                f"  public wrong / oracle correct {self.public_wrong_oracle_correct:>6}"
                "   <- reporting lag",
                f"  both wrong                    {self.both_wrong:>6}   <- policy or model error",
                f"  public correct / oracle wrong {self.public_correct_oracle_wrong:>6}",
                f"  macro F1  public {self.public_macro_f1:.3f}"
                f"  ·  oracle {self.oracle_macro_f1:.3f}"
                f"  ·  lag share of public errors {self.information_lag_share:.1%}",
                "  These are two measurements of different questions and are never averaged.",
            ]
        )


# --- the arithmetic ---------------------------------------------------------------------------


def confusion_matrix(actual: list[str], predicted: list[str]) -> dict[str, dict[str, int]]:
    matrix = {a: dict.fromkeys(CLASSES, 0) for a in CLASSES}
    for a, p in zip(actual, predicted, strict=True):
        matrix[a][p] += 1
    return matrix


def class_metrics(actual: list[str], predicted: list[str], label: str) -> ClassMetrics:
    tp = sum(1 for a, p in zip(actual, predicted, strict=True) if a == label and p == label)
    fp = sum(1 for a, p in zip(actual, predicted, strict=True) if a != label and p == label)
    fn = sum(1 for a, p in zip(actual, predicted, strict=True) if a == label and p != label)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return ClassMetrics(
        label=label,
        support=tp + fn,
        predicted=tp + fp,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
    )


def macro_f1(actual: list[str], predicted: list[str]) -> float:
    """Unweighted mean F1 over classes *present in the truth*.

    Averaging over absent classes would score a model on a question the split never asked, and
    with 28 held-out ENTER episodes the difference is not academic.
    """
    present = [c for c in CLASSES if c in set(actual)]
    if not present:
        return 0.0
    return round(sum(class_metrics(actual, predicted, c).f1 for c in present) / len(present), 4)


def balanced_accuracy(actual: list[str], predicted: list[str]) -> float:
    present = [c for c in CLASSES if c in set(actual)]
    if not present:
        return 0.0
    return round(sum(class_metrics(actual, predicted, c).recall for c in present) / len(present), 4)


def log_loss(actual: list[str], probabilities: list[dict[str, float]], *, eps=1e-15) -> float:
    if not actual:
        return 0.0
    total = 0.0
    for a, probs in zip(actual, probabilities, strict=True):
        total -= math.log(max(probs.get(a, 0.0), eps))
    return round(total / len(actual), 4)


def brier_score(actual: list[str], probabilities: list[dict[str, float]]) -> float:
    """Multiclass Brier: mean squared error against the one-hot truth."""
    if not actual:
        return 0.0
    total = 0.0
    for a, probs in zip(actual, probabilities, strict=True):
        total += sum((probs.get(c, 0.0) - (1.0 if c == a else 0.0)) ** 2 for c in CLASSES)
    return round(total / len(actual), 4)


def calibration(
    actual: list[str], probabilities: list[dict[str, float]], *, view: str
) -> Calibration:
    """Confidence against realised accuracy, bucketed.

    Bucketed on the *predicted* class's probability rather than on every class's, because the
    question a reader has is "when this thing says it is 80% sure, is it right 80% of the time".
    """
    bins: list[CalibrationBin] = []
    ece = 0.0
    for low, high in zip(CONFIDENCE_BINS, CONFIDENCE_BINS[1:], strict=False):
        members = [
            (a, probs)
            for a, probs in zip(actual, probabilities, strict=True)
            if low <= max(probs.values(), default=0.0) < high
        ]
        if not members:
            continue
        confidences = [max(probs.values()) for _, probs in members]
        correct = [1.0 if max(probs, key=lambda c: probs[c]) == a else 0.0 for a, probs in members]
        mean_confidence = sum(confidences) / len(confidences)
        accuracy = sum(correct) / len(correct)
        ece += (len(members) / len(actual)) * abs(accuracy - mean_confidence)
        bins.append(
            CalibrationBin(
                low=low,
                high=min(high, 1.0),
                count=len(members),
                mean_confidence=round(mean_confidence, 4),
                accuracy=round(accuracy, 4),
            )
        )

    return Calibration(
        view=view,
        log_loss=log_loss(actual, probabilities),
        brier=brier_score(actual, probabilities),
        expected_calibration_error=round(ece, 4),
        bins=bins,
    )


def bootstrap_intervals(
    actual: list[str],
    predicted: list[str],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    """Percentile bootstrap over episodes, for per-class F1/recall and for macro F1.

    Seeded, so a reported interval is reproducible. Resampling is over episodes rather than over
    classes: the uncertainty being described is "we observed these particular decisions", and for
    ENTER there are 85 of them in eleven years.
    """
    rng = random.Random(seed)
    n = len(actual)
    if n == 0:
        return {}, (0.0, 0.0)

    per_class: dict[str, list[tuple[float, float]]] = {c: [] for c in CLASSES}
    macro: list[float] = []

    for _ in range(samples):
        indices = [rng.randrange(n) for _ in range(n)]
        a = [actual[i] for i in indices]
        p = [predicted[i] for i in indices]
        macro.append(macro_f1(a, p))
        for label in CLASSES:
            metric = class_metrics(a, p, label)
            per_class[label].append((metric.f1, metric.recall))

    def percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index], 4)

    intervals = {
        label: (
            percentile([f for f, _ in pairs], 0.025),
            percentile([f for f, _ in pairs], 0.975),
            percentile([r for _, r in pairs], 0.025),
            percentile([r for _, r in pairs], 0.975),
        )
        for label, pairs in per_class.items()
    }
    return intervals, (percentile(macro, 0.025), percentile(macro, 0.975))


def evaluate(
    actual: list[str],
    predicted: list[str],
    *,
    model_name: str,
    view: str,
    split: str,
    probabilities: list[dict[str, float]] | None = None,
    information_set: str = "public_observer",
    with_intervals: bool = True,
) -> Evaluation:
    """Everything reportable about one model on one view, and nothing blended."""
    counts = Counter(actual)
    per_class = [class_metrics(actual, predicted, c) for c in CLASSES]

    macro_low = macro_high = None
    if with_intervals and actual:
        intervals, (macro_low, macro_high) = bootstrap_intervals(actual, predicted)
        per_class = [
            metric.model_copy(
                update={
                    "f1_low": intervals[metric.label][0],
                    "f1_high": intervals[metric.label][1],
                    "recall_low": intervals[metric.label][2],
                    "recall_high": intervals[metric.label][3],
                }
            )
            for metric in per_class
        ]

    return Evaluation(
        model_name=model_name,
        view=view,
        split=split,
        information_set=information_set,
        n=len(actual),
        accuracy=round(
            sum(1 for a, p in zip(actual, predicted, strict=True) if a == p) / len(actual), 4
        )
        if actual
        else 0.0,
        balanced_accuracy=balanced_accuracy(actual, predicted),
        macro_f1=macro_f1(actual, predicted),
        macro_f1_low=macro_low,
        macro_f1_high=macro_high,
        per_class=[m for m in per_class if m.support or m.predicted],
        confusion=confusion_matrix(actual, predicted),
        calibration=(
            calibration(actual, probabilities, view=view) if probabilities is not None else None
        ),
        majority_class_rate=round(max(counts.values()) / len(actual), 4) if actual else 0.0,
    )


def compare_information_sets(
    actual: dict[str, str],
    public: dict[str, str],
    oracle: dict[str, str],
    *,
    model_name: str,
    split: str,
) -> PairedComparison:
    """Pairwise over shared episode ids, so the two columns describe the same decisions."""
    shared = sorted(set(public) & set(oracle) & set(actual))
    cells = Counter((public[i] == actual[i], oracle[i] == actual[i]) for i in shared)
    truth = [actual[i] for i in shared]

    return PairedComparison(
        model_name=model_name,
        split=split,
        n=len(shared),
        both_correct=cells[(True, True)],
        public_wrong_oracle_correct=cells[(False, True)],
        both_wrong=cells[(False, False)],
        public_correct_oracle_wrong=cells[(True, False)],
        public_macro_f1=macro_f1(truth, [public[i] for i in shared]),
        oracle_macro_f1=macro_f1(truth, [oracle[i] for i in shared]),
    )

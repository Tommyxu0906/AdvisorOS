"""What ordinary quantitative state already explains, before any persona is involved.

This exists to make a later claim falsifiable. "FinanceNuwa reaches 0.61 macro F1" means nothing
on its own; it means something against a logistic regression that has never heard of Buffett and
reaches 0.42, and it means something quite different against a boosted tree that reaches 0.60. The
second outcome would be the more interesting finding — that most of what looks like a distinctive
investing temperament is position size, holding duration and recent price action.

**Implemented from scratch rather than pulled in.** The environment has no array library, and
adding one plus a modelling stack to fit four small models on two thousand rows would be a large
dependency bought for very little. Everything here is deterministic: no random initialisation, no
subsampling, no shuffling. The same data produces the same coefficients, which is what lets a
model config be hashed and a held-out result be checked later.

**Missingness is preserved, not imputed.** A position being opened this quarter has no previous
weight and no trailing return, and that is a fact about the decision rather than a gap to fill.
Every feature is encoded as a value plus a `_missing` indicator; the value slot holds 0.0 when
absent, which is not a claim about magnitude because the indicator sits beside it and the model
can use it. Imputing the median instead would tell the model that a brand-new position was
averagely sized, which is false for all 85 ENTER episodes at once.

**Standardisation uses training statistics only.** Fitting the scaler on all rows is a real leak,
small and almost invisible, and it is exactly the kind that survives review.

**Class weighting is offered, and chosen on validation.** At 69% hold prevalence an unweighted
softmax fit simply predicts hold for everything and scores 0.155 macro F1 — the same as the
constant baseline. Reporting that as "what a quant model can do" would be building a strawman for
a persona to beat later, which is the opposite of what this pass is for. Balanced weighting is a
standard remedy, it is selected on validation like any other hyperparameter, and both settings are
reported so the choice is visible.
"""

from __future__ import annotations

import math
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from app.distillation.finance_nuwa.artifact import EpisodeRow
from app.distillation.finance_nuwa.evaluation import CLASSES

# Nested on purpose: each set adds a family to the one before it, so the difference between two
# rows of the ablation table is attributable to the family that was added.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "position": ("weight", "rank", "quarters_held"),
    "position+price": (
        "weight",
        "rank",
        "quarters_held",
        "trailing_return_1q",
        "trailing_return_4q",
        "drawdown_from_peak",
        "relative_return_4q",
    ),
    "position+price+portfolio": (
        "weight",
        "rank",
        "quarters_held",
        "trailing_return_1q",
        "trailing_return_4q",
        "drawdown_from_peak",
        "relative_return_4q",
        "hhi",
        "top5_concentration",
        "portfolio_positions",
    ),
}


class Design(BaseModel):
    """An encoded feature matrix and the scaling that produced it."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list)
    rows: list[list[float]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)


class Scaler(BaseModel):
    """Per-column mean and spread, fitted on training rows and nothing else."""

    model_config = ConfigDict(extra="forbid")

    means: list[float] = Field(default_factory=list)
    scales: list[float] = Field(default_factory=list)

    @classmethod
    def fit(cls, rows: list[list[float]]) -> Scaler:
        if not rows:
            return cls()
        width = len(rows[0])
        means, scales = [], []
        for j in range(width):
            column = [row[j] for row in rows]
            mean = sum(column) / len(column)
            variance = sum((v - mean) ** 2 for v in column) / len(column)
            means.append(mean)
            # A constant column would divide by zero; 1.0 leaves it centred and inert.
            scales.append(math.sqrt(variance) or 1.0)
        return cls(means=means, scales=scales)

    def apply(self, rows: list[list[float]]) -> list[list[float]]:
        if not self.means:
            return rows
        return [
            [(v - m) / s for v, m, s in zip(row, self.means, self.scales, strict=True)]
            for row in rows
        ]


def encode(rows: list[EpisodeRow], feature_set: str) -> Design:
    """Value plus missing-indicator per feature. Nothing is imputed."""
    names = FEATURE_SETS[feature_set]
    columns = [c for name in names for c in (name, f"{name}_missing")]
    matrix, labels, ids = [], [], []
    for row in rows:
        encoded: list[float] = []
        for name in names:
            value = row.features.get(name)
            encoded.append(0.0 if value is None else float(value))
            encoded.append(1.0 if value is None else 0.0)
        matrix.append(encoded)
        labels.append(row.observed_action)
        ids.append(row.episode_id)
    return Design(columns=columns, rows=matrix, labels=labels, episode_ids=ids)


def class_weights(labels: list[str], *, balanced: bool) -> dict[str, float]:
    """Inverse-frequency weights, or none.

    Without these the majority class dominates the gradient and the fit degenerates to a constant.
    With them, every class contributes equally to the loss — which is the same thing macro F1
    measures, so the model is being fitted to the metric it is judged by rather than to accuracy.
    """
    if not balanced:
        return dict.fromkeys(CLASSES, 1.0)
    counts = Counter(labels)
    present = [c for c in CLASSES if counts.get(c)]
    total = len(labels)
    return {c: (total / (len(present) * counts[c]) if counts.get(c) else 0.0) for c in CLASSES}


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_ids: list[str] = Field(default_factory=list)
    predicted: list[str] = Field(default_factory=list)
    probabilities: list[dict[str, float]] = Field(default_factory=list)


# --- Baseline A: always hold ---------------------------------------------------------------


class AlwaysHold:
    """The floor. On the natural view it scores whatever the hold prevalence happens to be.

    Reported on both views precisely because the two numbers differ so much: 69% on the natural
    view and 44% on the matched one describe the same trivial model, and anyone quoting one of
    them without the view has said almost nothing.
    """

    name = "always_hold"

    def fit(self, design: Design) -> AlwaysHold:
        return self

    def predict(self, design: Design) -> Prediction:
        probs = {c: (1.0 if c == "hold" else 0.0) for c in CLASSES}
        return Prediction(
            episode_ids=list(design.episode_ids),
            predicted=["hold"] * len(design.rows),
            probabilities=[dict(probs) for _ in design.rows],
        )


# --- Baseline B: the empirical class prior --------------------------------------------------


class ClassPrior:
    """Predicts the training frequencies, ignoring every feature.

    Its argmax is the same constant as `AlwaysHold`, but its probabilities are the honest
    base rates — so it is the reference any calibration claim has to beat. A model whose log loss
    is worse than this has learned nothing that helps and has become confident about it.
    """

    name = "class_prior"

    def __init__(self) -> None:
        self.prior: dict[str, float] = {}

    def fit(self, design: Design) -> ClassPrior:
        counts = Counter(design.labels)
        total = sum(counts.values()) or 1
        self.prior = {c: counts.get(c, 0) / total for c in CLASSES}
        return self

    def predict(self, design: Design) -> Prediction:
        best = max(self.prior, key=lambda c: self.prior[c]) if self.prior else "hold"
        return Prediction(
            episode_ids=list(design.episode_ids),
            predicted=[best] * len(design.rows),
            probabilities=[dict(self.prior) for _ in design.rows],
        )


# --- Baseline C: multinomial logistic regression ---------------------------------------------


class MultinomialLogistic:
    """Softmax regression by full-batch gradient descent, with L2.

    Full batch rather than stochastic so the fit is deterministic to the last digit. At two
    thousand rows and twenty columns there is no reason to want the noise.
    """

    name = "logistic"

    def __init__(
        self,
        *,
        learning_rate: float = 0.5,
        iterations: int = 400,
        l2: float = 0.01,
        balanced: bool = True,
    ) -> None:
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2 = l2
        self.balanced = balanced
        self.scaler = Scaler()
        self.weights: list[list[float]] = []
        self.bias: list[float] = []
        self.classes: list[str] = []

    @property
    def config(self) -> dict[str, float | int | str]:
        return {
            "model": self.name,
            "learning_rate": self.learning_rate,
            "iterations": self.iterations,
            "l2": self.l2,
            "balanced": self.balanced,
        }

    def fit(self, design: Design) -> MultinomialLogistic:
        self.classes = [c for c in CLASSES if c in set(design.labels)]
        self.scaler = Scaler.fit(design.rows)
        x = self.scaler.apply(design.rows)
        n, d = len(x), len(design.columns)
        k = len(self.classes)
        index = {c: i for i, c in enumerate(self.classes)}
        y = [index[label] for label in design.labels]
        weights = class_weights(design.labels, balanced=self.balanced)
        sample_weight = [weights[label] for label in design.labels]
        weight_total = sum(sample_weight) or 1.0

        self.weights = [[0.0] * d for _ in range(k)]
        self.bias = [0.0] * k
        if n == 0:
            return self

        for _ in range(self.iterations):
            grad_w = [[0.0] * d for _ in range(k)]
            grad_b = [0.0] * k
            for row, target, weight in zip(x, y, sample_weight, strict=True):
                probs = self._softmax(row)
                for c in range(k):
                    error = weight * (probs[c] - (1.0 if c == target else 0.0))
                    if error:
                        grad_b[c] += error
                        weights_c = grad_w[c]
                        for j, value in enumerate(row):
                            weights_c[j] += error * value
            for c in range(k):
                self.bias[c] -= self.learning_rate * grad_b[c] / weight_total
                for j in range(d):
                    gradient = grad_w[c][j] / weight_total + self.l2 * self.weights[c][j]
                    self.weights[c][j] -= self.learning_rate * gradient
        return self

    def predict(self, design: Design) -> Prediction:
        x = self.scaler.apply(design.rows)
        predicted, probabilities = [], []
        for row in x:
            probs = self._softmax(row)
            distribution = {c: 0.0 for c in CLASSES}
            for name, value in zip(self.classes, probs, strict=True):
                distribution[name] = round(value, 6)
            predicted.append(max(distribution, key=lambda c: distribution[c]))
            probabilities.append(distribution)
        return Prediction(
            episode_ids=list(design.episode_ids),
            predicted=predicted,
            probabilities=probabilities,
        )

    def _softmax(self, row: list[float]) -> list[float]:
        scores = [
            self.bias[c] + sum(w * v for w, v in zip(self.weights[c], row, strict=True))
            for c in range(len(self.classes))
        ]
        highest = max(scores) if scores else 0.0
        exponentials = [math.exp(s - highest) for s in scores]
        total = sum(exponentials) or 1.0
        return [e / total for e in exponentials]


# --- Baseline D: gradient-boosted regression trees ---------------------------------------------


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = 0.0
    column: int | None = None
    threshold: float = 0.0
    left: _Node | None = None
    right: _Node | None = None

    def predict(self, row: list[float]) -> float:
        if self.column is None:
            return self.value
        branch = self.left if row[self.column] <= self.threshold else self.right
        return branch.predict(row) if branch is not None else self.value


def _fit_tree(
    x: list[list[float]],
    residuals: list[float],
    indices: list[int],
    *,
    depth: int,
    max_depth: int,
    min_leaf: int,
    thresholds: list[list[float]],
) -> _Node:
    mean = sum(residuals[i] for i in indices) / len(indices) if indices else 0.0
    if depth >= max_depth or len(indices) < 2 * min_leaf:
        return _Node(value=mean)

    total = sum(residuals[i] for i in indices)
    count = len(indices)
    best = (0.0, None, 0.0)  # gain, column, threshold

    for column, candidates in enumerate(thresholds):
        for threshold in candidates:
            left_sum = left_count = 0.0
            for i in indices:
                if x[i][column] <= threshold:
                    left_sum += residuals[i]
                    left_count += 1
            right_count = count - left_count
            if left_count < min_leaf or right_count < min_leaf:
                continue
            right_sum = total - left_sum
            # Variance reduction for squared error reduces to this, and it avoids recomputing
            # the sums of squares on every candidate.
            gain = left_sum**2 / left_count + right_sum**2 / right_count - total**2 / count
            if gain > best[0]:
                best = (gain, column, threshold)

    gain, column, threshold = best
    if column is None:
        return _Node(value=mean)

    left_indices = [i for i in indices if x[i][column] <= threshold]
    right_indices = [i for i in indices if x[i][column] > threshold]
    return _Node(
        value=mean,
        column=column,
        threshold=threshold,
        left=_fit_tree(
            x,
            residuals,
            left_indices,
            depth=depth + 1,
            max_depth=max_depth,
            min_leaf=min_leaf,
            thresholds=thresholds,
        ),
        right=_fit_tree(
            x,
            residuals,
            right_indices,
            depth=depth + 1,
            max_depth=max_depth,
            min_leaf=min_leaf,
            thresholds=thresholds,
        ),
    )


class GradientBoostedTrees:
    """Multiclass softmax boosting over shallow regression trees.

    Candidate split points are quantiles of each column rather than every distinct value — the
    histogram trick every serious implementation uses. It is much faster and, at this sample
    size, the difference in fit is far below the width of the bootstrap intervals.

    Trees see the missing-indicator columns like any other feature, so "this position is new"
    is available as a split rather than being smuggled in as a zero.
    """

    name = "boosted_trees"

    def __init__(
        self,
        *,
        rounds: int = 60,
        learning_rate: float = 0.3,
        max_depth: int = 3,
        min_leaf: int = 10,
        bins: int = 16,
        balanced: bool = True,
    ) -> None:
        self.rounds = rounds
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.bins = bins
        self.balanced = balanced
        self.classes: list[str] = []
        self.trees: list[list[_Node]] = []
        self.initial: list[float] = []

    @property
    def config(self) -> dict[str, float | int | str]:
        return {
            "model": self.name,
            "rounds": self.rounds,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "min_leaf": self.min_leaf,
            "bins": self.bins,
            "balanced": self.balanced,
        }

    def _thresholds(self, x: list[list[float]]) -> list[list[float]]:
        width = len(x[0]) if x else 0
        out: list[list[float]] = []
        for column in range(width):
            values = sorted({row[column] for row in x})
            if len(values) <= self.bins:
                candidates = values[:-1]
            else:
                step = len(values) / (self.bins + 1)
                candidates = [values[int(step * (i + 1))] for i in range(self.bins)]
            out.append(sorted(set(candidates)))
        return out

    def fit(self, design: Design) -> GradientBoostedTrees:
        x = design.rows
        self.classes = [c for c in CLASSES if c in set(design.labels)]
        k = len(self.classes)
        n = len(x)
        self.trees = []
        if n == 0 or k == 0:
            return self

        counts = Counter(design.labels)
        # Start from the log prior rather than zero, so the first tree corrects behaviour instead
        # of rediscovering that hold is common.
        self.initial = [math.log(max(counts.get(c, 0), 1) / n) for c in self.classes]
        index = {c: i for i, c in enumerate(self.classes)}
        y = [index[label] for label in design.labels]
        weights = class_weights(design.labels, balanced=self.balanced)
        sample_weight = [weights[label] for label in design.labels]

        scores = [list(self.initial) for _ in range(n)]
        thresholds = self._thresholds(x)
        all_indices = list(range(n))

        for _ in range(self.rounds):
            probabilities = [self._softmax(row) for row in scores]
            round_trees: list[_Node] = []
            for c in range(k):
                # Weighted residuals, so a rare class is not averaged out of every leaf.
                residuals = [
                    sample_weight[i] * ((1.0 if y[i] == c else 0.0) - probabilities[i][c])
                    for i in range(n)
                ]
                tree = _fit_tree(
                    x,
                    residuals,
                    all_indices,
                    depth=0,
                    max_depth=self.max_depth,
                    min_leaf=self.min_leaf,
                    thresholds=thresholds,
                )
                round_trees.append(tree)
                for i in range(n):
                    scores[i][c] += self.learning_rate * tree.predict(x[i])
            self.trees.append(round_trees)
        return self

    def predict(self, design: Design) -> Prediction:
        predicted, probabilities = [], []
        for row in design.rows:
            scores = list(self.initial)
            for round_trees in self.trees:
                for c, tree in enumerate(round_trees):
                    scores[c] += self.learning_rate * tree.predict(row)
            probs = self._softmax(scores)
            distribution = {c: 0.0 for c in CLASSES}
            for name, value in zip(self.classes, probs, strict=True):
                distribution[name] = round(value, 6)
            predicted.append(max(distribution, key=lambda c: distribution[c]))
            probabilities.append(distribution)
        return Prediction(
            episode_ids=list(design.episode_ids),
            predicted=predicted,
            probabilities=probabilities,
        )

    @staticmethod
    def _softmax(scores: list[float]) -> list[float]:
        highest = max(scores) if scores else 0.0
        exponentials = [math.exp(s - highest) for s in scores]
        total = sum(exponentials) or 1.0
        return [e / total for e in exponentials]

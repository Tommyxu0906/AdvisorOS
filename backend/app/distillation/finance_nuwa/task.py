"""The question this benchmark asks, frozen before anyone knows who answers it well.

The naive framing was five classes over every episode, and the baseline pass showed why that
number cannot be the headline. All 85 ENTER episodes have every position feature missing and none
have them present, because a security opened this quarter was by definition absent from the last
book anyone could read. A classifier that answers ENTER whenever the position features are absent
scores recall 1.000 on that class while knowing nothing about investing, and it lifts the macro
average by roughly a fifth of a class for free.

The deeper objection is that the ENTER task as posed is not a task. The row exists *because* the
security turned up in the filing; an observer standing at the decision window does not know which
of thousands of securities to ask about. Scoring "guess ENTER, having been told something
happened here" measures the construction of the candidate set.

So the primary benchmark is the question a deployed system could actually be asked:

    INCUMBENT SECURITY POLICY
    Given a position visible in the last public book — hold it, add to it, trim it, or close it?

Four classes, one population, and it is frozen here rather than chosen later. The five-class
results stay computable and are labelled secondary, because deleting them would make the history
harder to check rather than easier.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

# Bumped only when the question changes. A metric computed under one version cannot be compared
# with one computed under another, whatever the two numbers look like side by side.
TASK_VERSION = "incumbent-security-policy-v1"

# The primary task. Ordered sell -> hold -> buy so a confusion matrix reads along its diagonal.
INCUMBENT_CLASSES = ("exit", "reduce", "hold", "increase")

# Retained for audit and history, never as a headline.
SECONDARY_CLASSES = ("exit", "reduce", "hold", "increase", "enter")

# --- the task that is deliberately not being attempted -----------------------------------------
#
# ENTER is a different modelling problem and calling it a fifth class hides that. Predicting an
# initiation means ranking an investable universe: which securities exist, which are plausible at
# this size, which the manager could hold at all. That needs a candidate generator and
# company-level fundamentals, and it produces a ranking metric rather than a classification.
#
# Filling the missing position features would not fix it — there is no prior weight to impute,
# because there was no prior position. It would replace an honest absence with a fabricated
# average, and it would do so for every ENTER episode simultaneously.
#
# Left out of FinanceNuwa v1 entirely, and named so it can be picked up as its own task.
DISCOVERY_TASK = "security-discovery-and-initiation"

# --- the set nobody may look at ------------------------------------------------------------------
#
# The 2022-2024 held-out set has now influenced project-level thinking: the baseline run opened
# it once, as planned, and its results shaped how the task itself is framed above. Pretending
# later comparisons on it are pristine would be the same self-deception this pipeline keeps
# refusing elsewhere.
#
# So a genuinely untouched set is reserved now, while it costs nothing to reserve. Quarters from
# 2025 onward are not in any berkshire-v2.x artifact, must not be fetched into one, and must not
# be inspected while FinanceNuwa is being developed. The guard is a check rather than a note —
# see `refuse_reserved_quarters`.
CHALLENGE_SET_RESERVED_FROM = date(2025, 1, 1)


class TaskDefinition(BaseModel):
    """What is being predicted, and on which rows. Hashed into every result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = TASK_VERSION
    classes: tuple[str, ...] = INCUMBENT_CLASSES
    population: str = "positions visible in the last public book at the decision window opening"
    excluded: str = (
        f"new-to-book rows, which belong to {DISCOVERY_TASK} rather than to position management"
    )
    reserved_from: date = CHALLENGE_SET_RESERVED_FROM


TASK = TaskDefinition()


def is_incumbent(features: dict) -> bool:
    """Whether an observer at the cutoff could see this position at all.

    `weight` is the test rather than any other feature because it is the one that exists exactly
    when the security appears in the last public book. A position absent from that book has no
    weight, no rank and no holding duration, and the three are missing together.
    """
    return features.get("weight") is not None


def partition(rows: list, *, feature_key: str = "features") -> tuple[list, list]:
    """Split rows into (incumbent, new_to_book). Reported separately, never blended."""
    incumbent, new_to_book = [], []
    for row in rows:
        features = getattr(row, feature_key, None)
        if features is None and isinstance(row, dict):
            features = row.get(feature_key, {})
        (incumbent if is_incumbent(features or {}) else new_to_book).append(row)
    return incumbent, new_to_book


def refuse_reserved_quarters(period_ends: list[date]) -> None:
    """Raise if anything from the reserved window has been pulled into a dataset.

    A structural check rather than a comment, because the reservation is only worth anything
    while nobody has looked. By the time someone notices a 2025 quarter in the training data, the
    thing it was reserved for is already gone.
    """
    reserved = sorted(p for p in period_ends if p >= CHALLENGE_SET_RESERVED_FROM)
    if reserved:
        raise ValueError(
            f"{len(reserved)} quarter(s) from {reserved[0]} onward are reserved as the "
            "prospective challenge set and must not enter a berkshire-v2.x artifact. They are "
            "the only evaluation left that no project decision has been informed by."
        )


class TaskResult(BaseModel):
    """A result, with the question it answers attached to it.

    The task version travels with the number so that a table cannot be copied into a comparison
    where the rows were scored on a different population.
    """

    model_config = ConfigDict(extra="forbid")

    task_version: str = TASK_VERSION
    model_name: str
    view: str
    split: str
    population: str = "incumbent"
    macro_f1: float = 0.0
    balanced_accuracy: float = 0.0
    n: int = 0
    is_primary: bool = Field(
        default=True, description="False for the five-class figures, which are audit history"
    )

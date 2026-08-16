"""Turning a run of filings into a dataset that can actually support a claim.

Three ways this goes wrong before any modelling starts, all of them quiet.

**Only trades become episodes.** The obvious builder emits an episode whenever something
changed, which produces a dataset where every example is an action. A model fitted to that
learns that acting is normal and will trade constantly, because it has never once been shown a
quarter where the right answer was to sit still. Holding is the overwhelming majority of real
investing, and it has to be in the data — that is what `hold` control episodes are for.

**But not every hold is worth keeping.** Emitting one for every unchanged position every quarter
buries the actions under thousands of examples where nothing was under consideration, and a
classifier that answers "hold" unconditionally will score beautifully. So holds are *sampled by
salience*: kept when something happened that would have made a reasonable investor think about
acting — the position moved sharply, or grew large, or fell hard. A hold under pressure is
evidence about a decision rule. A hold in a quiet quarter in a 1% position is evidence about
nothing.

**Exact magnitudes are false precision.** Reporting that a position was cut by 37.2% invites
regression against a number whose true value depends on trade dates the filing does not contain.
Direction plus a bucket is what the data supports.

The split is chronological and the held-out set is not merely a convention. Refining a persona
against 20 cases until it gets 18 right is memorisation dressed as learning, so `held_out()`
requires an explicit unlock and `for_refinement()` cannot reach it at all.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.distillation.finance_nuwa.drift import ActionClassification, ObservedAction

# --- magnitude ------------------------------------------------------------------------------


class MagnitudeBucket(str, Enum):
    """How much of the position moved. Buckets, because the exact figure is not knowable."""

    none = "none"
    small = "small"
    """Under a tenth of the position — a trim, not a change of mind."""

    medium = "medium"
    """A tenth to a half. A deliberate resizing."""

    large = "large"
    """Over half, short of closing it."""

    full = "full"
    """Opened from nothing or closed to nothing."""


# Boundaries as fractions of the starting position. House conventions chosen to match how these
# moves get described in practice, not derived from anything.
SMALL_CEILING = 0.10
MEDIUM_CEILING = 0.50


def bucket_magnitude(action: ObservedAction, fraction_traded: float | None) -> MagnitudeBucket:
    """Bucket a trade by the share of the position it moved."""
    if action is ObservedAction.hold:
        return MagnitudeBucket.none
    if action in (ObservedAction.enter, ObservedAction.exit):
        return MagnitudeBucket.full
    if fraction_traded is None:
        return MagnitudeBucket.small
    size = abs(fraction_traded)
    if size < SMALL_CEILING:
        return MagnitudeBucket.small
    if size < MEDIUM_CEILING:
        return MagnitudeBucket.medium
    return MagnitudeBucket.large


# --- which holds are worth keeping ------------------------------------------------------------

# A hold is interesting when something was pulling against it. These weights say which pressures
# count for how much; they are an AdvisorOS convention and the sampler reports them so a reader
# can disagree with the selection rather than having to reverse-engineer it.
SALIENCE_LARGE_POSITION = 0.35
SALIENCE_BIG_MOVE = 0.40
SALIENCE_CONCENTRATION = 0.25

# Below this, a hold is treated as a quiet quarter and dropped.
SALIENCE_FLOOR = 0.30

# Ceiling on holds per action, so the majority class cannot swamp the dataset.
MAX_HOLD_RATIO = 2.0


class HoldSalience(BaseModel):
    """Why a particular hold was kept, or why it was not."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    score: float = Field(ge=0.0)
    reasons: list[str] = Field(default_factory=list)

    @property
    def is_informative(self) -> bool:
        return self.score >= SALIENCE_FLOOR


def score_hold(
    classification: ActionClassification,
    *,
    period_return: float | None = None,
    large_position_threshold: float = 0.05,
    big_move_threshold: float = 0.20,
) -> HoldSalience:
    """How much pressure there was to act, for a position that was left alone.

    A hold through a 40% drawdown in a top-five position is a decision someone made on purpose.
    A hold in a 0.3% position during a flat quarter is not evidence of anything, and keeping
    thousands of them lets a model score well by never acting.
    """
    score = 0.0
    reasons: list[str] = []
    weight = classification.end_weight or classification.start_weight or 0.0

    if weight >= large_position_threshold:
        score += SALIENCE_LARGE_POSITION
        reasons.append(f"{weight:.0%} of the book — large enough that holding is a choice")

    if period_return is not None and abs(period_return) >= big_move_threshold:
        score += SALIENCE_BIG_MOVE
        direction = "rose" if period_return > 0 else "fell"
        reasons.append(f"{direction} {abs(period_return):.0%} and was still not touched")

    # Holding something that has grown into a concentration is a live decision every quarter.
    if weight >= 0.15:
        score += SALIENCE_CONCENTRATION
        reasons.append("position is concentrated by any common standard")

    return HoldSalience(symbol=classification.symbol, score=round(score, 4), reasons=reasons)


def select_holds(
    holds: list[HoldSalience], *, action_count: int, max_ratio: float = MAX_HOLD_RATIO
) -> list[HoldSalience]:
    """Keep the informative holds, capped relative to how many actions exist.

    Both filters are needed. The floor removes quiet quarters; the cap stops a subject who
    rarely trades from producing a dataset that is 98% holds, where predicting "hold" always
    would look like a solved problem.
    """
    informative = sorted(
        (h for h in holds if h.is_informative), key=lambda h: h.score, reverse=True
    )
    if action_count <= 0:
        return informative[: int(max_ratio)] if informative else []
    return informative[: int(action_count * max_ratio)]


# --- the split ---------------------------------------------------------------------------------


class Split(str, Enum):
    train = "train"
    validation = "validation"
    held_out = "held_out"


class DatasetManifest(BaseModel):
    """What the dataset contains, per split. Written into any result that cites it.

    Episode ids are recorded rather than counts alone so that a reported score can be checked
    against the exact examples it came from, and so nobody has to take "we used a held-out set"
    on trust.
    """

    model_config = ConfigDict(extra="forbid")

    advisor_id: str
    train_end: date
    validation_end: date

    train_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)
    held_out_ids: list[str] = Field(default_factory=list)

    action_counts: dict[str, int] = Field(default_factory=dict)
    hold_ratio: float = 0.0
    mean_training_weight: float = 0.0

    @property
    def total(self) -> int:
        return len(self.train_ids) + len(self.validation_ids) + len(self.held_out_ids)

    def imbalance_warning(self) -> str | None:
        """A dataset a trivial classifier would ace should say so on its own manifest."""
        if not self.action_counts:
            return None
        largest = max(self.action_counts.values())
        share = largest / max(1, sum(self.action_counts.values()))
        if share >= 0.75:
            dominant = max(self.action_counts, key=lambda k: self.action_counts[k])
            return (
                f"{share:.0%} of episodes are '{dominant}' — a model that always answers "
                f"'{dominant}' would score {share:.0%}, so accuracy is not a meaningful metric "
                "here and per-class F1 should be reported instead"
            )
        return None


class EpisodeDataset(BaseModel):
    """Episodes split by time, with the held-out set behind a deliberate unlock.

    Chronological rather than random, because a random split lets a persona refined on 2019 Q3 be
    tested on 2019 Q2 — the same market, often the same position, and an answer it has already
    been shown. Time is the only split that reflects how the model would actually be used.
    """

    model_config = ConfigDict(extra="forbid")

    advisor_id: str = Field(pattern=r"^[a-z0-9_]+$")
    episodes: list = Field(default_factory=list)
    train_end: date
    validation_end: date

    @model_validator(mode="after")
    def _boundaries_ordered(self) -> EpisodeDataset:
        if self.train_end > self.validation_end:
            raise ValueError(
                f"train ends {self.train_end}, after validation ends {self.validation_end}"
            )
        return self

    def split_of(self, episode) -> Split:
        when = self._when(episode)
        if when <= self.train_end:
            return Split.train
        if when <= self.validation_end:
            return Split.validation
        return Split.held_out

    def for_refinement(self) -> list:
        """Everything a patcher is allowed to see: train plus validation.

        The held-out set is unreachable from here on purpose. Refining against 20 cases until 18
        pass is memorisation, and the only defence is that the scoring examples were never
        available to the thing being scored.
        """
        return [e for e in self.episodes if self.split_of(e) is not Split.held_out]

    def training(self) -> list:
        return [e for e in self.episodes if self.split_of(e) is Split.train]

    def validation(self) -> list:
        return [e for e in self.episodes if self.split_of(e) is Split.validation]

    def held_out(self, *, i_am_scoring_a_locked_persona: bool = False) -> list:
        """The examples no refinement step may read.

        The keyword is a speed bump, not security — anyone can pass it. That is the point: it
        cannot be reached by accident, and it shows up in review as a line someone had to write.
        """
        if not i_am_scoring_a_locked_persona:
            raise PermissionError(
                "held_out() is for scoring a persona that is already frozen. If you are "
                "refining, call for_refinement(). Pass i_am_scoring_a_locked_persona=True once "
                "the persona version under test can no longer change."
            )
        return [e for e in self.episodes if self.split_of(e) is Split.held_out]

    def manifest(self) -> DatasetManifest:
        counts: dict[str, int] = {}
        for episode in self.episodes:
            key = episode.observed_action.value
            counts[key] = counts.get(key, 0) + 1

        holds = counts.get(ObservedAction.hold.value, 0)
        weights = [e.training_weight for e in self.episodes]

        return DatasetManifest(
            advisor_id=self.advisor_id,
            train_end=self.train_end,
            validation_end=self.validation_end,
            train_ids=[e.episode_id for e in self.training()],
            validation_ids=[e.episode_id for e in self.validation()],
            held_out_ids=[
                e.episode_id for e in self.episodes if self.split_of(e) is Split.held_out
            ],
            action_counts=counts,
            hold_ratio=round(holds / len(self.episodes), 4) if self.episodes else 0.0,
            mean_training_weight=round(sum(weights) / len(weights), 4) if weights else 0.0,
        )

    @staticmethod
    def _when(episode) -> date:
        """Order by when the decision happened, falling back to what the inputs could see."""
        return episode.decision_window_end or episode.inputs.as_of


# --- matched controls ---------------------------------------------------------------------------

# Widths of the cells a hold and a trade must share to count as comparable. Deliberately coarse:
# tighter cells find no match at all in a book of forty-odd positions, and a matched design with
# no matches is just a smaller unmatched one.
WEIGHT_BUCKET_EDGES = (0.01, 0.03, 0.07, 0.15)
RETURN_BUCKET_EDGES = (-0.20, -0.05, 0.05, 0.20)


class MatchStratum(BaseModel):
    """The cell a hold and a trade must share before one can serve as a control for the other.

    Salience sampling alone leaves a gap that is easy to miss: if the kept holds are the big,
    volatile positions and the trades are whatever else happened, then position size separates
    the classes almost perfectly and a model scores well by learning "large means hold". It would
    have learned the sampler, not the investor.

    Matching closes that. Each hold is paired against a trade in the same size band, after a
    similar move, in a similar market — so those features carry no class information and the
    only thing left to separate them is behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    weight_bucket: int
    return_bucket: int
    regime: str

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.weight_bucket, self.return_bucket, self.regime)


def stratum_for(
    *, weight: float | None, trailing_return: float | None, regime: str
) -> MatchStratum:
    return MatchStratum(
        weight_bucket=_bucket(weight, WEIGHT_BUCKET_EDGES),
        return_bucket=_bucket(trailing_return, RETURN_BUCKET_EDGES),
        regime=regime,
    )


class MatchedSelection(BaseModel):
    """Which holds were kept, and what the matching could not achieve.

    `unmatched_actions` is reported rather than hidden. A stratum with trades and no available
    hold is a real limit on the design, and quietly dropping those trades would bias the dataset
    in precisely the direction the matching was meant to remove.
    """

    model_config = ConfigDict(extra="forbid")

    kept: list[str] = Field(default_factory=list, description="Identifiers of selected holds")
    unmatched_actions: int = 0
    strata_used: int = 0
    holds_available: int = 0

    @property
    def match_rate(self) -> float:
        total = len(self.kept) + self.unmatched_actions
        return round(len(self.kept) / total, 4) if total else 0.0


def select_matched_holds(
    action_strata: list[tuple[str, MatchStratum]],
    hold_strata: list[tuple[str, MatchStratum, float]],
    *,
    per_action: int = 1,
) -> MatchedSelection:
    """Pick holds that look like the trades, cell by cell.

    `hold_strata` carries each hold's salience so that ties inside a cell resolve toward the
    holds that were under real pressure — matching decides *which cell*, salience decides which
    member of it. Both filters matter and neither subsumes the other.
    """
    pool: dict[tuple[int, int, str], list[tuple[str, float]]] = {}
    for identifier, stratum, salience in hold_strata:
        pool.setdefault(stratum.key, []).append((identifier, salience))
    for candidates in pool.values():
        candidates.sort(key=lambda pair: pair[1], reverse=True)

    kept: list[str] = []
    unmatched = 0
    used: set[tuple[int, int, str]] = set()

    for _, stratum in action_strata:
        available = pool.get(stratum.key, [])
        taken = 0
        while available and taken < per_action:
            identifier, _ = available.pop(0)
            kept.append(identifier)
            taken += 1
        if taken:
            used.add(stratum.key)
        else:
            unmatched += 1

    return MatchedSelection(
        kept=kept,
        unmatched_actions=unmatched,
        strata_used=len(used),
        holds_available=sum(len(v) for v in pool.values()) + len(kept),
    )


def _bucket(value: float | None, edges: tuple[float, ...]) -> int:
    """Index of the band a value falls in. -1 keeps 'unknown' as its own cell rather than
    silently pooling it with the smallest band."""
    if value is None:
        return -1
    for index, edge in enumerate(edges):
        if value < edge:
            return index
    return len(edges)

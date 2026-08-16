"""How much of the label set rests on a number somebody chose.

`SHARE_TOLERANCE = 0.005` decides, for every position in every quarter, whether a share-count
change was a decision or noise. It is a house convention. It was not derived from anything, and
it is load-bearing for the target variable itself — which makes it a different kind of parameter
from the ones inside a model. Get a model hyperparameter wrong and the model is worse; get this
wrong and the thing the model is being asked to predict is a different thing.

So the honest move is to measure the dependence and publish it. Rebuild the labels across a range
of tolerances, count how many flip, and say which ones. If a hundred economically meaningful
trades change class between 0% and 0.5%, that belongs in the dataset audit where a reader can see
it, not in a constant nobody reads.

**This must never be resolved by scoring.** Running a model at each tolerance and keeping the one
that scores best is not tuning — it is choosing the definition of the target to suit the
predictor, which will improve every metric and mean nothing. Nothing in this module imports a
model, and the report it produces contains no accuracy of any kind.

The direction of the effect is worth stating in advance, because it makes the numbers readable: a
*lower* tolerance turns holds into trades (any wobble becomes a decision), and a *higher* one
turns trades into holds. Zero is not the neutral choice — it is the choice that treats a
single-share rounding difference as an investment decision.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# The sweep. 0 is included as the reference point precisely because it is not neutral: everything
# else is reported as a difference from "every share counts".
TOLERANCE_SWEEP = (0.0, 0.0005, 0.0010, 0.0025, 0.0050)

# Above this share of labels moving, the choice of tolerance is doing more work than a house
# convention should, and the audit says so rather than leaving a reader to divide two numbers.
MATERIAL_FLIP_SHARE = 0.05


class ToleranceRow(BaseModel):
    """One tolerance, and the label set it produces."""

    model_config = ConfigDict(extra="forbid")

    tolerance: float
    total_episodes: int = 0
    class_counts: dict[str, int] = Field(default_factory=dict)

    flips_vs_zero: int = 0
    flipped_episode_ids: list[str] = Field(default_factory=list)

    train_counts: dict[str, int] = Field(default_factory=dict)
    validation_counts: dict[str, int] = Field(default_factory=dict)
    held_out_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def flip_share(self) -> float:
        return round(self.flips_vs_zero / self.total_episodes, 4) if self.total_episodes else 0.0


class ToleranceReport(BaseModel):
    """The sweep, and whether the chosen value is doing too much work."""

    model_config = ConfigDict(extra="forbid")

    chosen: float
    rows: list[ToleranceRow] = Field(default_factory=list)

    @property
    def chosen_row(self) -> ToleranceRow | None:
        return next((r for r in self.rows if r.tolerance == self.chosen), None)

    @property
    def is_material(self) -> bool:
        """Whether the chosen tolerance moves enough labels to need stating before freezing."""
        row = self.chosen_row
        return bool(row and row.flip_share >= MATERIAL_FLIP_SHARE)

    def render(self) -> str:
        lines = [
            "SHARE-TOLERANCE SENSITIVITY",
            "  A dataset-definition robustness check, not a hyperparameter search. No model was",
            "  run at any of these values, and none may be chosen by downstream performance.",
            "",
            f"  {'tol':>7}  {'total':>6}  {'hold':>6}  {'incr':>6}  {'red':>6}  "
            f"{'enter':>6}  {'exit':>6}  {'flips':>6}  {'share':>7}",
        ]
        for row in self.rows:
            counts = row.class_counts
            lines.append(
                f"  {row.tolerance:>6.2%}  {row.total_episodes:>6}  "
                f"{counts.get('hold', 0):>6}  {counts.get('increase', 0):>6}  "
                f"{counts.get('reduce', 0):>6}  {counts.get('enter', 0):>6}  "
                f"{counts.get('exit', 0):>6}  {row.flips_vs_zero:>6}  {row.flip_share:>7.2%}"
            )

        chosen = self.chosen_row
        lines += ["", f"  Chosen: {self.chosen:.2%}"]
        if chosen is None:
            lines.append("  WARNING: the chosen tolerance was not swept.")
        elif self.is_material:
            lines.append(
                f"  FLAG: {chosen.flips_vs_zero} labels ({chosen.flip_share:.1%}) differ from the "
                "zero-tolerance reading. The convention is deciding a material share of the "
                "target and must be stated wherever a score is."
            )
        else:
            lines.append(
                f"  {chosen.flips_vs_zero} labels ({chosen.flip_share:.1%}) differ from the "
                "zero-tolerance reading — the convention is not carrying the dataset."
            )
        if chosen is not None:
            lines += [
                "",
                "  Per split at the chosen tolerance:",
                f"    train       {_fmt(chosen.train_counts)}",
                f"    validation  {_fmt(chosen.validation_counts)}",
                f"    held out    {_fmt(chosen.held_out_counts)}",
            ]
        return "\n".join(lines)


def _fmt(counts: dict[str, int]) -> str:
    if not counts:
        return "(none)"
    return "  ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def compare_to_zero(baseline: dict[str, str], candidate: dict[str, str]) -> tuple[int, list[str]]:
    """Episodes whose label differs from the zero-tolerance reading.

    Keyed on episode id rather than counted by class, because two offsetting changes leave the
    class totals identical while a hundred individual labels moved — and a totals-only report
    would call that stability.
    """
    flipped = sorted(
        key for key in set(baseline) | set(candidate) if baseline.get(key) != candidate.get(key)
    )
    return len(flipped), flipped

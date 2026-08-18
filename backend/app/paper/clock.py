"""The replay clock: three dates per step, and the rule that separates deciding from filling.

Every step carries:

    decision_date    everything the model may see is dated at or before this
    execution_date   the close orders fill at
    next_mark_date   where the resulting portfolio is valued for the next round

**The v1 execution rule is NEXT_CLOSE, and it is the default.** A decision made from the close
of day *t* fills at the close of day *t+1*.

The alternative — filling at the same close the decision was computed from — is the classic
replay bug and it is worth being explicit about why. If a policy trims a position because its
price rose, and the fill happens at that same rising close, the simulation has bought and sold
at a price that was only knowable after the decision was made. Every such trade looks free. The
error does not announce itself; it shows up as a strategy that appears to trade well.

`SAME_CLOSE` exists as an option because it is legitimate in one case: a decision genuinely made
intraday against data through the previous close, filling at today's close. It is never the
default, and choosing it is recorded on the run so a result can never be quietly compared
against one produced the other way.

The clock does no market-calendar arithmetic of its own. Dates come from the observations that
actually exist in the local data, so a replay never fills on a day the market did not trade —
and a fixture with a two-week gap replays across the gap rather than inventing sessions in it.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ExecutionRule(str, Enum):
    next_close = "next_close"
    """Decide on the close of t, fill on the close of t+1. The v1 default."""

    same_close = "same_close"
    """Fill on the same close the decision saw. Legitimate only for an intraday decision."""

    @property
    def description(self) -> str:
        if self is ExecutionRule.next_close:
            return "orders fill at the next available close after the decision date"
        return (
            "orders fill at the same close the decision was computed from; legitimate only when "
            "the decision was genuinely made intraday against earlier data"
        )


class ReplayStep(BaseModel):
    """One round's three dates, fixed before anything is computed."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    decision_date: date
    execution_date: date
    next_mark_date: date | None = Field(
        default=None, description="None on the final step: there is no round after it to mark for"
    )

    def model_post_init(self, _context: object) -> None:
        if self.execution_date < self.decision_date:
            raise ValueError(
                f"step {self.index}: execution {self.execution_date} precedes decision "
                f"{self.decision_date} — orders cannot fill before they exist"
            )
        if self.next_mark_date is not None and self.next_mark_date < self.execution_date:
            raise ValueError(
                f"step {self.index}: mark {self.next_mark_date} precedes execution "
                f"{self.execution_date}"
            )


class ReplayClock(BaseModel):
    """The full schedule for one run, built once from dates the data actually contains."""

    model_config = ConfigDict(extra="forbid")

    rule: ExecutionRule = ExecutionRule.next_close
    steps: list[ReplayStep] = Field(default_factory=list)

    @property
    def start_date(self) -> date | None:
        return self.steps[0].decision_date if self.steps else None

    @property
    def end_date(self) -> date | None:
        return self.steps[-1].execution_date if self.steps else None

    @classmethod
    def build(
        cls,
        decision_dates: list[date],
        available_dates: list[date],
        *,
        rule: ExecutionRule = ExecutionRule.next_close,
    ) -> ReplayClock:
        """Pair each decision date with the session it fills on.

        A decision date whose fill would fall outside the available data is dropped rather than
        filled at the last known price. Carrying it would put a trade on a date the data cannot
        support, and the run would end with a position established at a price nobody observed.
        """
        sessions = sorted(set(available_dates))
        steps: list[ReplayStep] = []

        for decision_date in sorted(set(decision_dates)):
            if rule is ExecutionRule.same_close:
                execution_date = _on_or_before(sessions, decision_date)
            else:
                execution_date = _strictly_after(sessions, decision_date)

            if execution_date is None:
                continue

            steps.append(
                ReplayStep(
                    index=len(steps),
                    decision_date=decision_date,
                    execution_date=execution_date,
                )
            )

        # The mark for round n is the execution date of round n+1: that is the next moment the
        # portfolio is valued, and valuing it anywhere else would report a return over a window
        # no decision was exposed to.
        for current, following in zip(steps, steps[1:], strict=False):
            current.next_mark_date = following.execution_date
        if steps:
            final = _strictly_after(sessions, steps[-1].execution_date)
            steps[-1].next_mark_date = final or steps[-1].execution_date

        return cls(rule=rule, steps=steps)


def _on_or_before(sessions: list[date], target: date) -> date | None:
    candidates = [d for d in sessions if d <= target]
    return candidates[-1] if candidates else None


def _strictly_after(sessions: list[date], target: date) -> date | None:
    for session in sessions:
        if session > target:
            return session
    return None


def periodic_decision_dates(sessions: list[date], every: int) -> list[date]:
    """Every nth available session. A simple, inspectable schedule for v1.

    Sessions rather than calendar days, so a fixture with gaps produces the number of decisions
    its data can actually support instead of a schedule with holes in it.
    """
    if every <= 0:
        raise ValueError("`every` must be positive")
    ordered = sorted(set(sessions))
    return ordered[::every]

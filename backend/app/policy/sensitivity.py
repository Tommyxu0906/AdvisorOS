"""How much of a recommendation is the person's situation, and how much is the threshold?

Provenance fixed one half of the false-precision problem: a number now says where it came from.
This fixes the other half, which is arguably worse because it survives perfect provenance. A
threshold can be honestly labelled "an AdvisorOS number" and still be the entire reason the
recommendation exists. Nothing in the report tells the reader that, so a conclusion resting
on a coin-flip parameter reads exactly like one that would hold under any threshold at all.

So the policy is re-run across the whole plausible range of the threshold and asked one question:
**where does the conclusion change?** Three outcomes, all worth saying out loud.

**The threshold barely matters.** The position is so oversized that every threshold anyone might
argue for produces the same action. The recommendation is then about the portfolio, not about the
parameter, and it deserves to be stated with confidence.

**The threshold is everything.** The conclusion flips from trim to hold a point or two from the
value in use. That is a *fragile* recommendation: the direction may be worth something, the size
is noise, and presenting a share count would be false precision no provenance label can repair.

**Neither — the portfolio's own arithmetic is binding.** A two-holding portfolio cannot put any
position below 50%, so every threshold from 1% to 50% yields the identical trim. This is the most
interesting case, because it means advisors who genuinely disagree about concentration would all
arrive at the same action here, and their disagreement is moot rather than unresolved. A report
that presents that as a considered judgment about the right cap is misleading even though every
individual number in it is correct.

The sweep re-runs the real policy rather than inverting its arithmetic in closed form. Solving
`acts(cap)` analytically is easy today and would silently stop describing the policy the moment
the policy grew a rule this module did not know about. Re-running costs a hundred calls of pure
arithmetic and cannot drift.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.analytics import counterfactual
from app.analytics.portfolio_analytics import PortfolioAnalytics, analyze_portfolio
from app.analytics.profile_analytics import ProfileAnalytics
from app.domain.action import ActionKind, ActionSet, ProposedAction
from app.domain.policy import (
    Direction,
    PolicyParameter,
    PolicyParameterName,
    PolicyProfile,
    PolicyScope,
    Provenance,
)
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail
from app.policy import concentration

NAME = PolicyParameterName.single_name_concentration

# Resolution of the flip search. One percentage point is finer than any threshold anyone
# defends in prose, and the whole sweep is pure arithmetic, so there is no reason to be coarser.
SEARCH_STEP = 0.01
SEARCH_MAX = 1.00

# What the report shows. The flip is found at SEARCH_STEP; these are the rows a reader scans.
DISPLAY_CAPS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)

# How close the flip has to be before the recommendation is called fragile. This is an AdvisorOS
# convention and nobody's finding: three percentage points is roughly the width of the
# disagreement between reasonable people about where a concentration limit belongs, so a
# conclusion that does not survive it is being carried by the parameter rather than the facts.
FRAGILE_BAND = 0.03


class Binding(str, Enum):
    """What is actually forcing the action at a given threshold."""

    threshold = "threshold"
    """The cap is doing the work. Move it and the answer moves."""

    arithmetic_floor = "arithmetic_floor"
    """1/n is above the cap, so the cap is slack and every advisor lands in the same place."""

    nothing = "nothing"
    """No position exceeds the applicable limit; the policy proposes nothing."""


class SweepPoint(BaseModel):
    """The policy's answer at one threshold."""

    model_config = ConfigDict(extra="forbid")

    cap: float
    acts: bool
    binding: Binding
    proceeds_usd: float = 0.0
    largest_weight_after: float


class Sensitivity(BaseModel):
    """Where the conclusion changes, and whether it changes near enough to matter."""

    model_config = ConfigDict(extra="forbid")

    parameter: PolicyParameterName = NAME
    baseline: float
    baseline_provenance: Provenance
    baseline_acts: bool
    binding_at_baseline: Binding
    position_count: int

    points: list[SweepPoint] = Field(default_factory=list)
    flip_at: float | None = Field(
        default=None,
        description="Lowest threshold at which the policy stops acting; None if it never acts",
    )
    declined: bool = Field(
        default=False, description="This persona does not opine on concentration at all"
    )

    @property
    def distance_to_flip(self) -> float | None:
        if self.flip_at is None or not self.baseline_acts:
            return None
        return abs(self.flip_at - self.baseline)

    @computed_field(
        description="Would a threshold a reasonable person might pick instead reverse this?"
    )
    @property
    def fragile(self) -> bool:
        """The conclusion would reverse under a threshold a reasonable person might pick.

        Deliberately *not* suppressed when the arithmetic floor binds. The tempting shortcut is
        to say the cap cannot matter if the floor is what sets the trim target — but the floor
        only binds below it. With five positions, a 19% cap, and a 21% largest holding, every
        threshold from 1% to 19% gives the identical trim while 22% reverses the conclusion
        entirely. Insensitive downward and fragile upward is one situation, not two, and the
        distance to the reversal is what describes it.
        """
        distance = self.distance_to_flip
        return distance is not None and distance <= FRAGILE_BAND

    @computed_field(description="Plain sentences for the report, ready to render")
    @property
    def summary(self) -> list[str]:
        """Serialized so the wording is authored once, here, rather than reimplemented in TSX."""
        return self.summary_lines()

    def summary_lines(self) -> list[str]:
        """Plain sentences for the report. This is the part the user actually reads."""
        if self.declined:
            return ["This perspective declines to weigh in on position sizing."]
        if not self.baseline_acts:
            if self.flip_at is None:
                return ["No threshold in the tested range implies trimming any position."]
            return [
                f"At the {self.baseline:.0%} threshold in use, no position needs trimming. "
                f"That changes only below {self.flip_at:.0%}."
            ]

        lines: list[str] = []
        if self.binding_at_baseline is Binding.arithmetic_floor:
            floor = 1.0 / self.position_count
            lines.append(
                f"The {self.baseline:.0%} threshold is not what sets the size here. With "
                f"{self.position_count} positions, no single name can be trimmed below "
                f"{floor:.0%}, so every threshold at or below {floor:.0%} produces the identical "
                "trim — advisors who disagree about concentration limits would all arrive at the "
                "same action."
            )
        if self.flip_at is not None:
            lines.append(
                f"Thresholds up to {self.flip_at - SEARCH_STEP:.0%} imply trimming; at "
                f"{self.flip_at:.0%} and above the same portfolio implies holding. The "
                f"{self.baseline:.0%} threshold in use sits "
                f"{(self.flip_at - self.baseline) * 100:.0f} points from that reversal."
            )

        if self.fragile:
            lines.append(
                "This conclusion is fragile: a threshold a reasonable person might pick instead "
                "reverses it. Treat the direction as more reliable than the size."
            )
        return lines


def sweep_concentration(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None,
    portfolio_analytics: PortfolioAnalytics | None,
    guardrails: list[Guardrail],
    policy_profile: PolicyProfile,
    *,
    advisor_id: str = "house",
    display_name: str = "AdvisorOS",
) -> Sensitivity:
    """Re-run the concentration policy across the threshold range and find where it turns over."""
    resolved = policy_profile.resolve(NAME, concentration.HOUSE_SINGLE_NAME_CAP)
    positions = len(portfolio_analytics.weights) if portfolio_analytics else 0

    if not policy_profile.covers(PolicyScope.concentration):
        return Sensitivity(
            baseline=resolved.value,
            baseline_provenance=resolved.provenance,
            baseline_acts=False,
            binding_at_baseline=Binding.nothing,
            position_count=positions,
            declined=True,
        )

    def propose_at(cap: float) -> list[ProposedAction]:
        return concentration.propose(
            profile,
            analytics,
            portfolio,
            portfolio_analytics,
            guardrails,
            _with_cap(policy_profile, cap),
            advisor_id=advisor_id,
            display_name=display_name,
        )

    def acts_at(cap: float) -> bool:
        return any(a.kind is ActionKind.trim_position for a in propose_at(cap))

    def binding_at(cap: float, *, acting: bool) -> Binding:
        if not acting:
            return Binding.nothing
        # `solve_trim_targets` raises an unreachable cap to 1/n. When it does, the cap is slack.
        if positions and cap < 1.0 / positions - 1e-12:
            return Binding.arithmetic_floor
        return Binding.threshold

    def outcome_at(cap: float) -> SweepPoint:
        actions = propose_at(cap)
        acting = any(a.kind is ActionKind.trim_position for a in actions)
        before = portfolio_analytics.largest_weight if portfolio_analytics else 0.0
        if not acting or portfolio is None or portfolio_analytics is None:
            return SweepPoint(
                cap=cap,
                acts=False,
                binding=Binding.nothing,
                largest_weight_after=before,
            )
        _, after_portfolio, _ = counterfactual.apply(profile, portfolio, ActionSet(actions=actions))
        after = analyze_portfolio(after_portfolio)
        return SweepPoint(
            cap=cap,
            acts=True,
            binding=binding_at(cap, acting=True),
            proceeds_usd=round(portfolio_analytics.total_value - after.total_value, 2),
            largest_weight_after=after.largest_weight,
        )

    # `acts` is monotone in the cap — a higher limit can only mean fewer positions exceed it — so
    # the first False on an ascending scan is the reversal point.
    flip_at: float | None = None
    acted_somewhere = False
    steps = int(round(SEARCH_MAX / SEARCH_STEP))
    for i in range(1, steps + 1):
        cap = round(i * SEARCH_STEP, 4)
        if acts_at(cap):
            acted_somewhere = True
            continue
        if acted_somewhere:
            flip_at = cap
        break

    baseline_acts = acts_at(resolved.value)
    display = sorted({*DISPLAY_CAPS, round(resolved.value, 4)})

    return Sensitivity(
        baseline=resolved.value,
        baseline_provenance=resolved.provenance,
        baseline_acts=baseline_acts,
        binding_at_baseline=binding_at(resolved.value, acting=baseline_acts),
        position_count=positions,
        points=[outcome_at(cap) for cap in display],
        flip_at=flip_at if acted_somewhere else None,
    )


def _with_cap(policy_profile: PolicyProfile, cap: float) -> PolicyProfile:
    """The same persona, with the swept threshold substituted in.

    The substituted value is always `house_default`, whatever the persona's own provenance was.
    A value chosen by a sweep is not a threshold anyone stated, and labelling it as one — even
    inside a throwaway trial object — is the exact confusion this module exists to prevent.
    """
    base = policy_profile.parameters.get(NAME)
    trial = PolicyParameter(
        name=NAME,
        value=cap,
        direction=base.direction if base else Direction.neutral,
        provenance=Provenance.house_default,
        source_labels=list(base.source_labels) if base else [],
        as_of=base.as_of if base else None,
        applicable_scope=list(base.applicable_scope) if base else [],
        note="swept value for sensitivity analysis; not an attributed threshold",
    )
    return PolicyProfile(
        parameters={**policy_profile.parameters, NAME: trial},
        scopes=list(policy_profile.scopes),
        allows_concentration_on_conviction=policy_profile.allows_concentration_on_conviction,
    )

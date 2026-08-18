"""The one call that turns a household's numbers into candidate actions and their consequences.

Everything under `policy/` and `analytics/counterfactual.py` was built, tested, and then reached
no user: no HTTP route called any of it, and `CommitteeReport.recommendations` stayed a
`list[str]`. This module is the seam that fixes that. It composes the pieces in the only order
that makes sense and returns one object a route can serialize.

    guardrails ─┐
    analytics ──┼─→ persona policy ─→ house claims the proceeds ─→ ActionSet
    portfolio ──┘                                                     │
                                   ┌──────────────────────────────────┤
                                   ▼                                  ▼
                            counterfactual                      sensitivity
                     (does the plan do what it says?)   (would a different threshold
                                                          have said something else?)

Two decisions worth stating, because both are about not overclaiming.

**One scenario, computed on house thresholds — not six.** The policy layer is parameterized by
each advisor's `PolicyProfile`, so in principle every persona yields a different plan. In
practice none of the six built-in manifests carries authored policy parameters yet, so all of
them resolve to the same house defaults. Computing six action sets and labelling them with six
names would render an agreement that is really just an absence of data. Until parameters are
authored from evidence, the honest output is a single scenario that says the threshold belongs
to AdvisorOS. `compute_scenario` accepts a `policy_profile` so that changes the day the evidence
exists, and not before.

**A scenario is not a recommendation.** What comes back is "here is what a stated threshold
implies for these holdings, here is what it would cost, and here is how little it would take for
the answer to change". `Counterfactual.holds_up` is the bar for whether it is worth showing at
all; `Sensitivity.fragile` is the warning that survives even when it clears.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.analytics.counterfactual import Counterfactual
from app.analytics.counterfactual import evaluate as evaluate_counterfactual
from app.analytics.portfolio_analytics import PortfolioAnalytics
from app.analytics.profile_analytics import ProfileAnalytics
from app.domain.action import ActionSet
from app.domain.policy import PolicyProfile
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.domain.report import Guardrail
from app.policy import concentration
from app.policy.sensitivity import Sensitivity, sweep_concentration

HOUSE_DISPLAY_NAME = "AdvisorOS"


class PortfolioScenario(BaseModel):
    """Candidate actions, what they would do, and how load-bearing the threshold was.

    Serialized straight onto the free-tier response. Every field is either computed arithmetic
    or a label saying whose number drove it — there is nothing here a model produced.
    """

    model_config = ConfigDict(extra="forbid")

    action_set: ActionSet
    counterfactual: Counterfactual
    sensitivity: Sensitivity | None = Field(
        default=None, description="None when there is no portfolio to sweep"
    )

    policy_owner: str = Field(
        default=HOUSE_DISPLAY_NAME,
        description="Whose thresholds produced this. Rendered next to every action.",
    )
    is_house_policy: bool = Field(
        default=True,
        description="True when no advisor supplied an evidence-backed threshold for this",
    )

    @computed_field(description="Whether the policy produced anything at all")
    @property
    def has_actions(self) -> bool:
        return bool(self.action_set.actions)

    @computed_field(description="Actions exist and they survive their own counterfactual")
    @property
    def worth_showing(self) -> bool:
        """A plan that fails its own counterfactual is a policy bug, not a difference of view."""
        return self.has_actions and self.counterfactual.holds_up

    @computed_field(description="One sentence for the top of the panel")
    @property
    def headline(self) -> str:
        """Honest in all four states, and authored here so the UI cannot soften it."""
        if not self.has_actions:
            return "Nothing in this portfolio exceeds the thresholds AdvisorOS applies."
        if not self.counterfactual.holds_up:
            return (
                "A scenario was computed but did not survive its own arithmetic, so it is "
                "shown with the problems listed rather than as a candidate."
            )
        if self.sensitivity is not None and self.sensitivity.fragile:
            return (
                "This scenario reverses under a threshold a reasonable person might pick "
                "instead — the direction is more reliable than the size."
            )
        return "Computed from your holdings against the thresholds named on each line."


def compute_scenario(
    profile: FinancialProfile,
    analytics: ProfileAnalytics,
    portfolio: Portfolio | None,
    portfolio_analytics: PortfolioAnalytics | None,
    guardrails: list[Guardrail],
    *,
    policy_profile: PolicyProfile | None = None,
    advisor_id: str = "house",
    display_name: str = HOUSE_DISPLAY_NAME,
) -> PortfolioScenario:
    """Run the deterministic decision layer over one household.

    Pure, and free in both senses: no I/O, no model, no API key. This is what makes the
    free tier produce candidate actions rather than only diagnostics.
    """
    profile_policy = policy_profile if policy_profile is not None else PolicyProfile()

    actions = concentration.propose(
        profile,
        analytics,
        portfolio,
        portfolio_analytics,
        guardrails,
        profile_policy,
        advisor_id=advisor_id,
        display_name=display_name,
    )
    action_set = ActionSet(actions=actions)

    sweep = None
    if portfolio is not None and portfolio_analytics is not None:
        sweep = sweep_concentration(
            profile,
            analytics,
            portfolio,
            portfolio_analytics,
            guardrails,
            profile_policy,
            advisor_id=advisor_id,
            display_name=display_name,
        )

    return PortfolioScenario(
        action_set=action_set,
        counterfactual=evaluate_counterfactual(profile, portfolio, action_set),
        sensitivity=sweep,
        policy_owner=display_name,
        # The threshold is the house's whenever the persona did not supply one it can defend.
        is_house_policy=sweep.baseline_provenance.name == "house_default" if sweep else True,
    )

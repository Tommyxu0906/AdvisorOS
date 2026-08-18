"""The baseline: the deterministic engine, with no investor view at all.

This exists to answer one question the other three providers cannot — **how much does the
investor-policy layer actually change?** Without it, a replay of `FrozenPolicyProvider` reports
a path and there is nothing to attribute it to: the concentration trims would have happened
anyway, and a reader has no way to separate the engine's arithmetic from the persona's opinion.

It is deliberately **not** a persona. `DecisionEngineOnlyProvider` returns zero stances rather
than a stance of `hold` on everything, and the difference matters:

  - Zero stances means the investor layer contributed nothing, and the comparison can say so.
  - A `hold` on every position is a *view* — it says "I looked and would change nothing" — and it
    would make the baseline's abstention rate 0% and its coverage 100%, numbers that describe a
    provider that does not exist.

Coverage of a provider with no opinions is undefined rather than perfect, and `InvestorView`
already reports 0.0 for an empty stance list, which reads correctly here: nothing was covered
because nothing was asked.

The engine still runs in full. House guardrails, concentration trims, tax ranges, the
counterfactual and the sensitivity sweep are all deterministic and all still apply — this
removes the directional overlay, not the decision engine.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.domain.policy import PolicyProfile
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.provider import InvestorView

BASELINE_PROVIDER_ID = "engine_only"


class DecisionEngineOnlyProvider(BaseModel):
    """Satisfies `InvestorDecisionProvider` while contributing no directional signal.

    Implemented as a provider rather than as a flag on the engine so that the replay loop has
    exactly one shape. A branch inside the loop that skipped the provider call would be a second
    code path, and the baseline's whole value is that it went through the same machinery.
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str = BASELINE_PROVIDER_ID
    display_name: str = "Decision engine only (no investor signal)"

    def decide(self, profile: FinancialProfile, portfolio: Portfolio) -> InvestorView:
        return InvestorView(
            provider_id=self.provider_id,
            display_name=self.display_name,
            # No stances at all. See the module docstring on why this is not a wall of holds.
            stances=[],
            # No thresholds either: the engine runs on house numbers and every rationale says so.
            policy=PolicyProfile(),
            is_language_model=False,
            determinism_key="baseline=no-investor-signal",
        )

"""Paper trading: the forward-looking half of how this system is tested.

Historical replay (`app/distillation/finance_nuwa`) asks whether a policy resembles Berkshire.
This package asks a different question — whether the whole chain holds together when it runs
forward on a portfolio, one decision at a time, with every step typed and checked:

    portfolio snapshot
      -> InvestorDecisionProvider   (what would this investor do with each position?)
      -> InvestorView
      -> compute_scenario()          (the deterministic decision engine)
      -> feasibility + counterfactual
      -> ActionSet
      -> PaperBroker                 (simulated execution, never a real venue)

Neither question answers the other. A policy can look exactly like Berkshire and still produce
an action set that cannot be carried out; an action set can be perfectly feasible and reflect
nobody's investment philosophy. Keeping the two harnesses separate is what stops one from being
quoted as evidence for the other.

**This package is not `app/connectors/`.** That package reads real brokerage accounts and its
security tests assert, by parsing its source, that no order-placing identifier appears anywhere
inside it. A paper broker legitimately submits orders, so it lives here instead. The separation
is the point: real-money access stays structurally incapable of trading, and paper execution
gets its own, narrower guarantee — see `PAPER_ONLY` in `broker.py` and the tests that enforce it.
"""

from app.paper.broker import (
    PAPER_ONLY,
    HarnessMode,
    OrderSide,
    PaperAccount,
    PaperBroker,
    PaperFill,
    PaperOrder,
    PaperPosition,
    RejectedOrder,
)
from app.paper.mock_broker import MockPaperBroker
from app.paper.provider import (
    InvestorDecisionProvider,
    InvestorStance,
    InvestorView,
)

__all__ = [
    "PAPER_ONLY",
    "HarnessMode",
    "InvestorDecisionProvider",
    "InvestorStance",
    "InvestorView",
    "MockPaperBroker",
    "OrderSide",
    "PaperAccount",
    "PaperBroker",
    "PaperFill",
    "PaperOrder",
    "PaperPosition",
    "RejectedOrder",
]

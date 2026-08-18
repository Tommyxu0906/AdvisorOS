"""A deterministic stand-in whose only job is to exercise the plumbing.

This is the CI provider. It has no investment content and is written so that nobody can mistake
it for having any: the stance for a symbol is a hash of the symbol and the seed, and the note on
every stance says so.

That is a deliberate choice rather than laziness. A mock that guessed plausibly — holding the
big positions, trimming the concentrated one — would produce runs that look like results, and
someone reading a log six months from now would have no way to tell. A mock that is obviously
arbitrary can only ever demonstrate that the pipeline runs, which is exactly what it is for.

What it does guarantee is coverage of the paths that matter: it abstains on roughly one symbol
in seven, so the abstention path is exercised on every run rather than only when someone
remembers to test it.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from app.distillation.finance_nuwa.prediction import BehavioralAction, ReasonCode
from app.domain.policy import PolicyProfile
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.provider import InvestorStance, InvestorView, held_symbols

_WHEEL = (
    BehavioralAction.hold,
    BehavioralAction.reduce,
    BehavioralAction.increase,
    BehavioralAction.exit,
)

_REASONS = (
    ReasonCode.long_holding_horizon,
    ReasonCode.valuation_discipline,
    ReasonCode.hold_through_drawdown,
    ReasonCode.concentration_tolerance,
)

MOCK_NOTE = "[mock] arbitrary deterministic placeholder, not a judgment about this security"


class MockInvestorPolicy(BaseModel):
    """Seeded, reproducible, and content-free."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = "mock_investor"
    display_name: str = "Mock investor (placeholder)"
    seed: str = "paper-harness-v1"

    def decide(self, profile: FinancialProfile, portfolio: Portfolio) -> InvestorView:
        stances = [self._stance(symbol) for symbol in held_symbols(portfolio)]
        return InvestorView(
            provider_id=self.provider_id,
            display_name=self.display_name,
            stances=stances,
            # No thresholds: the engine should run on house defaults and say so. A mock supplying
            # policy numbers would put invented thresholds into rationales users read.
            policy=PolicyProfile(),
            is_language_model=False,
            determinism_key=f"seed={self.seed}",
        )

    def _stance(self, symbol: str) -> InvestorStance:
        digest = hashlib.sha256(f"{self.seed}:{symbol}".encode()).hexdigest()
        value = int(digest[:8], 16)

        if value % 7 == 0:
            return InvestorStance(
                symbol=symbol,
                abstain=True,
                confidence=0.0,
                note=MOCK_NOTE,
            )

        return InvestorStance(
            symbol=symbol,
            action=_WHEEL[value % len(_WHEEL)],
            confidence=round(0.3 + (value % 60) / 100.0, 2),
            reason_codes=[_REASONS[value % len(_REASONS)]],
            note=MOCK_NOTE,
        )

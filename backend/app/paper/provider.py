"""What an investor would do with each position, and where that opinion came from.

The harness depends on this interface and on nothing below it. That constraint is deliberate:
it is what lets the whole forward loop run with no API key, no network, and no per-run cost,
while leaving room for a paid provider later without the loop noticing.

**Naming discipline.** None of the v1 implementations is "local Nuwa inference", and the code
refuses to let that phrase become true by accident: `InvestorView.is_language_model` must be set
by any provider that actually runs a language model, and a test asserts that every provider
shipping today reports `False`. A deterministic rule table is a deterministic rule table. Calling
it inference would misrepresent what produced the numbers, which is the one thing this project
has consistently refused to do.

The vocabulary — `BehavioralAction`, `ReasonCode` — is imported from the FinanceNuwa package
rather than redefined. Two enums meaning the same four decisions would guarantee that a
historical replay and a forward run eventually disagree about what "reduce" means.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.distillation.finance_nuwa.prediction import BehavioralAction, ReasonCode
from app.domain.policy import PolicyProfile
from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile


class InvestorStance(BaseModel):
    """One investor's view of one position the household already holds.

    Parallel to `BehavioralPrediction` but keyed on a symbol rather than an episode id, because
    that type answers "what did Berkshire do next in 2018Q1" and this one answers "what would
    this policy do with the AAPL you are holding now". Sharing the enums keeps the two
    comparable; sharing the whole type would smuggle `parse_failed` and `raw_text` — concepts
    that only mean something when a model emitted text — into a path where no model ran.

    Abstention is first-class here for the same reason it is there. At the class balance these
    portfolios have, silently converting "no view" into "hold" hands the provider a free correct
    answer on most positions and makes every coverage number a lie.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    action: BehavioralAction | None = None
    abstain: bool = False

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "The provider's own stated strength. Uncalibrated unless a provider says otherwise, "
            "and never rendered as a probability."
        ),
    )
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    note: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _abstention_is_not_an_answer(self) -> InvestorStance:
        if self.abstain and self.action is not None:
            raise ValueError(
                f"{self.symbol}: a stance cannot both abstain and name an action — "
                "recording both lets a scorer keep whichever turned out right"
            )
        if not self.abstain and self.action is None:
            raise ValueError(
                f"{self.symbol}: a stance with no action must say so by abstaining, "
                "so that silence is never counted as a hold"
            )
        return self

    @property
    def is_actionable(self) -> bool:
        """Would carrying this out move anything? A hold and an abstention both would not."""
        return self.action in (
            BehavioralAction.increase,
            BehavioralAction.reduce,
            BehavioralAction.exit,
        )


class InvestorView(BaseModel):
    """Everything one provider has to say about one portfolio at one moment.

    Carries the thresholds as well as the opinions. `compute_scenario` is parameterized by a
    `PolicyProfile`, so a provider that has evidence-backed numbers can drive the concentration
    policy with them, and one that does not contributes an empty profile and the engine runs on
    house defaults that say so in every rationale. Both are honest; only pretending to be the
    other would not be.
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    stances: list[InvestorStance] = Field(default_factory=list)
    policy: PolicyProfile = Field(default_factory=PolicyProfile)

    is_language_model: bool = Field(
        default=False,
        description=(
            "True only when a language model produced these stances. Deterministic rule tables "
            "and refitted statistical models must leave this False — see the module docstring."
        ),
    )
    determinism_key: str = Field(
        default="",
        description=(
            "What makes this run reproducible: a config hash, a seed, an artifact digest. Empty "
            "means the provider does not claim reproducibility."
        ),
    )

    @property
    def abstentions(self) -> list[InvestorStance]:
        return [s for s in self.stances if s.abstain]

    @property
    def coverage(self) -> float:
        """Fraction of positions the provider was willing to answer on."""
        if not self.stances:
            return 0.0
        return round(1.0 - len(self.abstentions) / len(self.stances), 4)

    def stance_for(self, symbol: str) -> InvestorStance | None:
        upper = symbol.strip().upper()
        for stance in self.stances:
            if stance.symbol.strip().upper() == upper:
                return stance
        return None


@runtime_checkable
class InvestorDecisionProvider(Protocol):
    """The only thing the paper harness is allowed to depend on for a decision.

    Note what is absent: no `RunContext`, no credentials, no provider key, no async. A v1
    implementation must be able to answer from local artifacts alone, and a signature that
    accepted credentials would make it easy to forget that.
    """

    provider_id: str
    display_name: str

    def decide(
        self,
        profile: FinancialProfile,
        portfolio: Portfolio,
    ) -> InvestorView:
        """One view of every position in `portfolio`.

        Implementations should return a stance for each held symbol — abstaining where they have
        no basis — rather than silently omitting positions. An omitted symbol and an abstention
        are different claims, and only one of them is auditable.
        """
        ...


def held_symbols(portfolio: Portfolio) -> list[str]:
    """Distinct symbols, upper-cased, in first-seen order.

    Aggregated by symbol rather than by holding: the same stock in a taxable account and an IRA
    is one investment decision and two tax situations, and a provider asked twice about AAPL
    would be free to answer differently each time.
    """
    seen: list[str] = []
    for holding in portfolio.holdings:
        symbol = holding.symbol.strip().upper()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen

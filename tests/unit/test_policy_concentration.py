"""The concentration policy, and the property that keeps it honest.

`test_every_advisors_plan_survives_its_own_counterfactual` is the one that matters: whatever a
policy proposes, applying it must not introduce a blocking guardrail and must actually move the
number it was aimed at. A policy that fails that has recommended something whose stated purpose
it does not achieve, which is a bug rather than a difference of opinion.
"""

from __future__ import annotations

import pytest

from app.analytics import counterfactual
from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.domain.action import ActionKind, ActionSet
from app.domain.policy import (
    Direction,
    PolicyParameter,
    PolicyParameterName,
    PolicyProfile,
    PolicyScope,
    Provenance,
)
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import (
    AccountType,
    Asset,
    Debt,
    Expenses,
    FinancialProfile,
    Income,
    RiskTolerance,
)
from app.policy import concentration


def _profile(**overrides) -> FinancialProfile:
    """The flagship eval case: concentrated in NVDA, carrying a 22.9% card, thin reserve."""
    base = dict(
        age=34,
        dependents=1,
        income=Income(annual_gross=145_000),
        expenses=Expenses(monthly_essential=4_200, monthly_discretionary=1_500),
        debts=[Debt(name="credit card", balance=9_000, apr=0.229, minimum_monthly_payment=280)],
        assets=[Asset(name="savings", value=11_000, account_type=AccountType.cash, is_liquid=True)],
        risk_tolerance=RiskTolerance.moderate_aggressive,
    )
    base.update(overrides)
    return FinancialProfile(**base)


def _portfolio() -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                asset_class=AssetClass.us_equity,
                quantity=400,
                market_value=60_000,
                cost_basis=20_000,
            ),
            Holding(
                symbol="VTI",
                asset_class=AssetClass.us_equity,
                quantity=73,
                market_value=28_000,
                cost_basis=24_000,
            ),
        ]
    )


_UNSET = object()


def _profile_with_cap(
    cap: float | None, *, conviction: bool = False, scopes: list[PolicyScope] | None = None
) -> PolicyProfile:
    """A persona carrying an evidence-backed cap, or none at all.

    `cap=None` is the common real case: the subject has a documented direction on concentration
    but never published a threshold, so the policy runs on the house number.
    """
    parameters = {}
    if cap is not None:
        parameters[PolicyParameterName.single_name_concentration] = PolicyParameter(
            name=PolicyParameterName.single_name_concentration,
            value=cap,
            direction=Direction.avoids if cap < 0.15 else Direction.tolerates,
            provenance=Provenance.derived,
            confidence=0.8,
            source_labels=["test evidence"],
            as_of="2026-08",
        )
    return PolicyProfile(
        parameters=parameters,
        scopes=scopes if scopes is not None else list(PolicyScope),
        allows_concentration_on_conviction=conviction,
    )


def _diversified() -> Portfolio:
    """Five names, one of them oversized — where a cap below 1/n is actually reachable."""
    return Portfolio(
        holdings=[
            Holding(symbol="NVDA", quantity=400, market_value=60_000, cost_basis=20_000),
            Holding(symbol="VTI", quantity=26, market_value=10_000, cost_basis=9_000),
            Holding(symbol="BND", quantity=26, market_value=10_000, cost_basis=10_000),
            Holding(symbol="VXUS", quantity=26, market_value=10_000, cost_basis=9_500),
            Holding(symbol="VNQ", quantity=26, market_value=10_000, cost_basis=9_500),
        ]
    )


def _run(params: PolicyProfile, profile=None, portfolio=_UNSET):
    profile = profile or _profile()
    portfolio = _portfolio() if portfolio is _UNSET else portfolio
    analytics = analyze_profile(profile, portfolio)
    pa = analyze_portfolio(portfolio) if portfolio else None
    rails = evaluate_guardrails(profile, analytics, portfolio, pa)
    actions = concentration.propose(profile, analytics, portfolio, pa, rails, params)
    return profile, portfolio, actions


# --- the flagship question -------------------------------------------------------------


def test_it_answers_sell_nvda_or_pay_the_card_with_a_sequence():
    _, _, actions = _run(_profile_with_cap(0.20))

    kinds = [(a.kind, a.sequence) for a in actions]
    assert (ActionKind.trim_position, 0) in kinds
    assert (ActionKind.pay_down_debt, 1) in kinds

    trim = next(a for a in actions if a.kind is ActionKind.trim_position)
    pay = next(a for a in actions if a.kind is ActionKind.pay_down_debt)
    # The card is cleared in full, and the trim that funds it comes first.
    assert pay.amount_usd == 9_000
    assert trim.sequence < pay.sequence


def test_the_trim_is_solved_against_the_post_trim_total():
    """The proceeds leave the portfolio, so the target is not simply `cap * today's total`.

    NVDA 60k + VTI 28k = 88k. Selling NVDA down to `0.20 * 88k` would leave 17.6k + 28k = 45.6k,
    where NVDA is 39% — nowhere near the cap it was sold to reach. The target has to be solved
    against what the portfolio becomes.
    """
    _, portfolio, actions = _run(_profile_with_cap(0.20))
    trim = next(a for a in actions if a.symbol == "NVDA")

    # Two holdings cannot both sit under 20%, so the reachable floor is 50%: NVDA goes to 28k,
    # matching VTI, which means selling 32k — 213.33 shares at $150.
    assert trim.shares == pytest.approx(213.3333, abs=0.001)
    assert trim.amount_usd is None
    assert "the most any one can be trimmed to is 50%" in trim.rationale


def test_the_tax_cost_of_acting_is_stated_as_a_range():
    """$32,000 sold from a position two-thirds gain. Whether that is a long-term gain or ordinary
    income is genuinely unknown — no acquisition date is recorded — and the spread between those
    two treatments is far too wide to collapse into a single figure printed to the dollar."""
    _, _, actions = _run(_profile_with_cap(0.20))
    trim = next(a for a in actions if a.symbol == "NVDA")
    gain = 32_000 * (2 / 3)

    assert trim.estimated_tax is not None
    assert trim.estimated_tax.low_usd == pytest.approx(gain * 0.15, rel=0.01)
    assert trim.estimated_tax.high_usd == pytest.approx(gain * 0.32, rel=0.01)
    assert not trim.estimated_tax.is_certain

    assert "$3,200-$6,827" in trim.rationale
    assert "holding dates are not collected" in trim.rationale
    # The lot-selection caveat travels with the number rather than living in a footnote.
    assert "outside this range" in trim.rationale


# --- the disagreement is now a number ----------------------------------------------------


def test_two_advisors_produce_different_trims_from_the_same_portfolio():
    strict = _profile_with_cap(0.10)
    loose = _profile_with_cap(0.25)

    _, _, strict_actions = _run(strict, portfolio=_diversified())
    _, _, loose_actions = _run(loose, portfolio=_diversified())

    strict_trim = next(a for a in strict_actions if a.symbol == "NVDA")
    loose_trim = next(a for a in loose_actions if a.symbol == "NVDA")
    assert strict_trim.shares > loose_trim.shares

    # And a cap above the actual weight produces nothing rather than a token gesture.
    _, _, none_needed = _run(_profile_with_cap(0.70), portfolio=_diversified())
    assert none_needed == []


def test_a_reachable_cap_is_actually_reached():
    """Five holdings can satisfy a 20% cap, and the solved trim lands exactly on it."""
    params = _profile_with_cap(0.20)
    profile, portfolio, actions = _run(params, portfolio=_diversified())

    _, after_portfolio, _ = counterfactual.apply(profile, portfolio, ActionSet(actions=actions))
    after = analyze_portfolio(after_portfolio)
    assert after.weights["NVDA"] == pytest.approx(0.20, abs=1e-6)


def test_a_conviction_tolerant_persona_frames_the_same_number_differently():
    _, _, actions = _run(_profile_with_cap(0.25, conviction=True))
    trim = next(a for a in actions if a.symbol == "NVDA")
    assert "genuinely understands the business" in trim.rationale
    # The framing softens; the arithmetic does not.
    assert trim.shares > 0


# --- the property that keeps policies honest ---------------------------------------------


@pytest.mark.parametrize("cap", [0.05, 0.10, 0.20, 0.25, 0.40])
def test_every_advisors_plan_survives_its_own_counterfactual(cap: float):
    profile, portfolio, actions = _run(_profile_with_cap(cap))
    if not actions:
        return

    result = counterfactual.evaluate(profile, portfolio, ActionSet(actions=actions))

    assert result.feasible, [str(p) for p in result.infeasibilities]
    assert result.introduced_guardrails == []
    assert result.ineffective_actions == []
    assert result.unapplied == []
    assert result.holds_up


@pytest.mark.parametrize("cap", [0.05, 0.10, 0.20, 0.25, 0.40])
def test_the_plan_never_leaves_a_position_above_what_trimming_can_reach(cap: float):
    """Either the cap is met, or the arithmetic floor of 1/n is — never something in between."""
    params = _profile_with_cap(cap)
    for portfolio in (_portfolio(), _diversified()):
        profile, portfolio, actions = _run(params, portfolio=portfolio)
        if not actions:
            continue
        _, after_portfolio, _ = counterfactual.apply(profile, portfolio, ActionSet(actions=actions))
        after = analyze_portfolio(after_portfolio)
        reachable = max(cap, 1.0 / len(after.weights))
        assert after.largest_weight <= reachable + 1e-6


def test_clearing_the_card_resolves_the_blocking_guardrail():
    profile, portfolio, actions = _run(_profile_with_cap(0.20))
    result = counterfactual.evaluate(profile, portfolio, ActionSet(actions=actions))
    assert "HIGH_APR_DEBT" in result.resolved_guardrails


# --- edges -------------------------------------------------------------------------------


def test_no_portfolio_means_no_actions():
    _, _, actions = _run(_profile_with_cap(None), portfolio=None)
    assert actions == []


def test_a_position_without_a_share_count_is_sized_in_dollars():
    portfolio = Portfolio(
        holdings=[
            Holding(symbol="PRIVATECO", market_value=80_000),
            Holding(symbol="VTI", quantity=20, market_value=20_000),
        ]
    )
    _, _, actions = _run(_profile_with_cap(0.20), portfolio=portfolio)
    trim = next(a for a in actions if a.symbol == "PRIVATECO")
    assert trim.shares is None
    assert trim.amount_usd == 60_000


def test_an_unknown_cost_basis_is_reported_as_unknown_not_zero():
    portfolio = Portfolio(
        holdings=[
            Holding(symbol="NVDA", quantity=400, market_value=60_000),  # no cost_basis
            Holding(symbol="VTI", quantity=73, market_value=28_000),
        ]
    )
    _, _, actions = _run(_profile_with_cap(0.20), portfolio=portfolio)
    trim = next(a for a in actions if a.symbol == "NVDA")
    assert trim.estimated_tax is None
    assert "Tax cost is unknown" in trim.rationale


def test_a_position_held_in_a_roth_incurs_no_estimated_tax():
    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol="NVDA",
                quantity=400,
                market_value=60_000,
                cost_basis=20_000,
                account_type=AccountType.roth_ira,
            ),
            Holding(symbol="VTI", quantity=73, market_value=28_000, cost_basis=24_000),
        ]
    )
    _, _, actions = _run(_profile_with_cap(0.20), portfolio=portfolio)
    trim = next(a for a in actions if a.symbol == "NVDA")
    assert trim.estimated_tax is not None
    assert trim.estimated_tax.low_usd == 0.0
    # The one case where both ends legitimately coincide: no uncertainty left to express.
    assert trim.estimated_tax.is_certain


def test_proceeds_beyond_the_blocking_guardrails_are_left_unallocated():
    """Concentration is this policy's remit; inventing a destination for the rest is not."""
    profile = _profile(
        debts=[],
        assets=[Asset(name="savings", value=60_000, account_type=AccountType.cash, is_liquid=True)],
    )
    _, _, actions = _run(_profile_with_cap(0.20), profile=profile)
    assert {a.kind for a in actions} == {ActionKind.trim_position}


def test_a_position_inside_the_cap_explains_why_it_is_still_trimmed():
    """The second-order case, which otherwise reads as a contradiction.

    Proceeds leave the portfolio, so trimming the big names shrinks the book every surviving
    position is weighed against. A holding comfortably inside the threshold today can be over it
    afterwards. Stating only today's weight produces "VXUS is 12% of the portfolio, so under a
    20% threshold this implies reducing it" — nonsense on its face, and exactly the sentence that
    makes a computed panel look broken.
    """
    profile = FinancialProfile(
        age=41,
        income=Income(annual_gross=180_000),
        expenses=Expenses(monthly_essential=6_000),
        assets=[Asset(name="Cash", value=40_000, account_type="cash")],
    )
    # Five positions, 20% cap: the arithmetic floor is exactly 20%, so all but the smallest trim.
    portfolio = Portfolio(
        holdings=[
            Holding(
                symbol=symbol,
                name=symbol,
                asset_class="us_equity",
                quantity=qty,
                market_value=value,
                account_type="taxable",
            )
            for symbol, qty, value in [
                ("BIG", 310, 92_070),
                ("MID", 420, 77_280),
                ("SMALLISH", 480, 30_240),
                ("SMALL", 380, 27_740),
                ("TINY", 95, 21_090),
            ]
        ]
    )
    analytics = analyze_profile(profile)
    pa = analyze_portfolio(portfolio)

    actions = concentration.propose(
        profile, analytics, portfolio, pa, [], PolicyProfile(), display_name="AdvisorOS"
    )
    by_symbol = {a.symbol: a for a in actions if a.kind is ActionKind.trim_position}

    # SMALLISH is 12% of the book today — comfortably inside a 20% cap — and is still trimmed.
    assert "SMALLISH" in by_symbol
    assert pa.weights["SMALLISH"] < 0.20

    rationale = by_symbol["SMALLISH"].rationale
    assert "inside the 20% single-name threshold" in rationale
    assert "smaller book that remains" in rationale
    # And it names what the weight would actually become, rather than only today's figure.
    assert "about 29%" in rationale

    # The genuinely over-weight position keeps the plain wording.
    assert "is 37% of the portfolio. Under a 20%" in by_symbol["BIG"].rationale

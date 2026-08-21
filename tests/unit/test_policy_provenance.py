"""Where a threshold came from, and refusing to pretend it came from someone else.

The failure this guards against is subtle and expensive: a hand-authored
`buffett.max_single_name_weight = 0.25` makes the report look rigorous exactly where the
evidence ran out. Distillation can support "tolerates concentration"; it cannot support "caps
single names at 25% for a retail investor". These tests hold that line.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.analytics.guardrails import evaluate_guardrails
from app.analytics.portfolio_analytics import analyze_portfolio
from app.analytics.profile_analytics import analyze_profile
from app.domain.policy import (
    Direction,
    PolicyParameter,
    PolicyParameterName,
    PolicyProfile,
    PolicyScope,
    Provenance,
)
from app.domain.portfolio import AssetClass, Holding, Portfolio
from app.domain.profile import FinancialProfile
from app.policy import concentration, house

NAME = PolicyParameterName.single_name_concentration


def _profile() -> FinancialProfile:
    return FinancialProfile(
        age=34,
    )


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
                symbol="VTI", asset_class=AssetClass.us_equity, quantity=26, market_value=10_000
            ),
            Holding(symbol="BND", asset_class=AssetClass.bonds, quantity=26, market_value=10_000),
            Holding(
                symbol="VXUS",
                asset_class=AssetClass.intl_developed_equity,
                quantity=26,
                market_value=10_000,
            ),
            Holding(symbol="VNQ", asset_class=AssetClass.reit, quantity=26, market_value=10_000),
        ]
    )


def _run(policy_profile: PolicyProfile, display_name: str = "Warren Buffett"):
    profile, portfolio = _profile(), _portfolio()
    analytics = analyze_profile(profile, portfolio)
    pa = analyze_portfolio(portfolio)
    rails = evaluate_guardrails(profile, analytics, portfolio, pa)
    return concentration.propose(
        profile,
        analytics,
        portfolio,
        pa,
        rails,
        policy_profile,
        advisor_id="buffett",
        display_name=display_name,
    )


# --- a number always has an owner ---------------------------------------------------------


def test_a_value_without_provenance_is_rejected():
    with pytest.raises(ValidationError, match="needs a provenance"):
        PolicyParameter(name=NAME, value=0.25)


def test_claimed_evidence_must_carry_confidence():
    with pytest.raises(ValidationError, match="confidence above 0"):
        PolicyParameter(name=NAME, value=0.25, provenance=Provenance.derived, confidence=0.0)


def test_a_direction_with_no_number_is_a_valid_and_common_state():
    """The usual honest outcome: the lean is documented, the threshold is not."""
    parameter = PolicyParameter(
        name=NAME,
        direction=Direction.tolerates,
        provenance=Provenance.unknown,
        applicable_scope=["high-conviction business ownership"],
    )
    assert parameter.value is None


# --- resolution labels the fallback -------------------------------------------------------


def test_a_persona_without_a_number_runs_on_a_house_threshold_that_says_so():
    resolved = PolicyProfile().resolve(NAME, 0.20)
    assert resolved.value == 0.20
    assert resolved.is_house_number
    assert "not Warren Buffett's number" in resolved.attribution("Warren Buffett")


def test_a_documented_direction_survives_even_when_the_number_does_not():
    """The evidence that exists is carried; the evidence that does not is not invented."""
    profile = PolicyProfile(
        parameters={
            NAME: PolicyParameter(
                name=NAME,
                direction=Direction.tolerates,
                provenance=Provenance.derived,
                confidence=0.82,
                applicable_scope=["high-conviction business ownership"],
            )
        }
    )
    resolved = profile.resolve(NAME, 0.20)

    assert resolved.is_house_number  # the 20% is ours
    assert resolved.direction is Direction.tolerates  # the lean is theirs
    assert resolved.confidence == 0.82


def test_a_stated_threshold_is_attributed_to_the_subject():
    profile = PolicyProfile(
        parameters={
            NAME: PolicyParameter(
                name=NAME, value=0.05, provenance=Provenance.direct, confidence=0.95
            )
        }
    )
    resolved = profile.resolve(NAME, 0.20)
    assert resolved.value == 0.05
    assert not resolved.is_house_number
    assert "states this threshold directly" in resolved.attribution("John Bogle")


# --- the rationale a user reads ------------------------------------------------------------


def test_a_house_threshold_is_never_put_in_the_subjects_mouth():
    actions = _run(
        PolicyProfile(
            parameters={
                NAME: PolicyParameter(
                    name=NAME,
                    direction=Direction.tolerates,
                    provenance=Provenance.derived,
                    confidence=0.82,
                    applicable_scope=["high-conviction business ownership"],
                )
            }
        )
    )
    trim = next(a for a in actions if a.symbol == "NVDA")

    assert "not Warren Buffett's number" in trim.rationale
    # The real evidence is still reported, separately from the invented threshold.
    assert "is willing to hold concentrated positions" in trim.rationale
    assert "high-conviction business ownership" in trim.rationale
    assert "no threshold of theirs is on record" in trim.rationale


def test_a_real_threshold_is_reported_as_theirs():
    actions = _run(
        PolicyProfile(
            parameters={
                NAME: PolicyParameter(
                    name=NAME, value=0.05, provenance=Provenance.direct, confidence=0.95
                )
            }
        ),
        display_name="John Bogle",
    )
    trim = next(a for a in actions if a.symbol == "NVDA")
    assert "John Bogle states this threshold directly" in trim.rationale
    assert "AdvisorOS threshold" not in trim.rationale


def test_the_rationale_frames_a_scenario_rather_than_an_instruction():
    """ "Sell 40 shares" is advice; "this scenario implies reducing it" is analysis."""
    actions = _run(PolicyProfile())
    trim = next(a for a in actions if a.symbol == "NVDA")
    assert "this scenario implies reducing it" in trim.rationale


# --- scope: not every advisor answers every question ---------------------------------------


def test_a_persona_that_declines_position_sizing_produces_nothing():
    """Bogle refusing to size an individual security is a position, not a gap to fill."""
    declines = PolicyProfile(scopes=[PolicyScope.allocation, PolicyScope.debt])
    assert _run(declines) == []


def test_house_rules_are_attributed_to_the_house_not_the_advisor():
    profile = _profile()
    profile.horizon_years = 1.5  # near-term need is what the house acts on now
    analytics = analyze_profile(profile, _portfolio())
    pa = analyze_portfolio(_portfolio())
    rails = evaluate_guardrails(profile, analytics, _portfolio(), pa)

    actions = house.claim_first(profile, analytics, _portfolio(), rails)

    assert [a.proposed_by for a in actions] == ["house"]
    assert "a house rule, not an advisor's view" in actions[0].rationale

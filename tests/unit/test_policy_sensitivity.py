"""Whether a recommendation is carried by the person's numbers or by the threshold.

Provenance says where a number came from. It does not say whether the number is doing all the
work. A 20% cap can be honestly labelled an AdvisorOS number and still be the entire reason a
trim is being proposed — and if the same portfolio would be left alone at 22%, the share count
in the report is noise wearing a decimal point.

`test_a_conclusion_that_reverses_two_points_away_is_flagged_fragile` is the one that matters.
"""

from __future__ import annotations

import pytest

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
from app.domain.portfolio import Holding, Portfolio
from app.domain.profile import FinancialProfile
from app.policy import sensitivity
from app.policy.sensitivity import FRAGILE_BAND, Binding

NAME = PolicyParameterName.single_name_concentration


def _profile() -> FinancialProfile:
    return FinancialProfile(
        age=34,
    )


def _portfolio(*weights: tuple[str, float]) -> Portfolio:
    """A $100k portfolio with the given symbol/weight pairs."""
    return Portfolio(
        holdings=[
            Holding(symbol=s, quantity=100, market_value=w * 100_000, cost_basis=w * 60_000)
            for s, w in weights
        ]
    )


_TWO_HOLDING = (("NVDA", 0.68), ("VTI", 0.32))
_DOMINANT = (("NVDA", 0.60), ("VTI", 0.10), ("BND", 0.10), ("VXUS", 0.10), ("VNQ", 0.10))
_MARGINAL = (("AAPL", 0.22), ("VTI", 0.20), ("BND", 0.20), ("VXUS", 0.19), ("VNQ", 0.19))


def _sweep(holdings, policy: PolicyProfile | None = None) -> sensitivity.Sensitivity:
    profile = _profile()
    portfolio = _portfolio(*holdings)
    analytics = analyze_profile(profile, portfolio)
    pa = analyze_portfolio(portfolio)
    rails = evaluate_guardrails(profile, analytics, portfolio, pa)
    return sensitivity.sweep_concentration(
        profile,
        analytics,
        portfolio,
        pa,
        rails,
        policy or PolicyProfile(),
        display_name="Warren Buffett",
    )


def _cap(value: float, provenance: Provenance = Provenance.derived) -> PolicyProfile:
    return PolicyProfile(
        parameters={
            NAME: PolicyParameter(
                name=NAME,
                value=value,
                provenance=provenance,
                confidence=0.8,
                direction=Direction.tolerates,
            )
        }
    )


# --- the finding worth surfacing ----------------------------------------------------------


def test_a_conclusion_that_reverses_two_points_away_is_flagged_fragile():
    """AAPL at 22% under a 20% cap. At 22% the same portfolio is left alone."""
    result = _sweep(_MARGINAL)

    assert result.baseline_acts
    assert result.flip_at == pytest.approx(0.22)
    assert result.distance_to_flip == pytest.approx(0.02)
    assert result.fragile

    text = " ".join(result.summary_lines())
    assert "2 points from that reversal" in text
    assert "direction as more reliable than the size" in text


def test_a_dominant_position_is_robust_to_every_threshold_anyone_would_pick():
    """NVDA at 60% of five holdings. No plausible cap leaves it alone."""
    result = _sweep(_DOMINANT)

    assert result.flip_at == pytest.approx(0.60)
    assert result.distance_to_flip == pytest.approx(0.40)
    assert not result.fragile
    assert result.binding_at_baseline is Binding.threshold
    assert "40 points from that reversal" in " ".join(result.summary_lines())


def test_when_the_arithmetic_floor_binds_the_disagreement_is_moot():
    """Two holdings cannot both sit under 20%, so every advisor's cap yields the same trim.

    This is the case a report would otherwise present as a considered judgment about the right
    threshold, while every number in it stayed individually correct.
    """
    result = _sweep(_TWO_HOLDING)

    assert result.binding_at_baseline is Binding.arithmetic_floor
    assert not result.fragile
    text = " ".join(result.summary_lines())
    assert "no single name can be trimmed below 50%" in text
    assert "would all arrive at the same action" in text
    # Every displayed threshold acts, and all of them land in the same place.
    assert all(p.acts for p in result.points)
    assert len({round(p.largest_weight_after, 6) for p in result.points if p.cap <= 0.50}) == 1


def test_the_floor_binding_does_not_suppress_a_nearby_reversal():
    """Insensitive below the floor and fragile above it is one situation, not two.

    Five positions put the floor at 20%. A 19% cap is slack — every threshold from 1% to 19%
    gives the identical trim — yet 22% reverses the conclusion outright. Reporting that as
    robust because "the cap is not what sets the size" would be exactly backwards.
    """
    result = _sweep(_MARGINAL, _cap(0.19))

    assert result.binding_at_baseline is Binding.arithmetic_floor
    assert result.flip_at == pytest.approx(0.22)
    assert result.fragile


# --- the sweep describes the real policy ---------------------------------------------------


def test_acting_is_monotone_in_the_threshold():
    """A higher limit can only mean fewer positions exceed it. If this ever fails, the flip
    search is scanning a function that has no single crossing point."""
    for holdings in (_TWO_HOLDING, _DOMINANT, _MARGINAL):
        acts = [p.acts for p in _sweep(holdings).points]
        assert acts == sorted(acts, reverse=True), holdings


def test_the_reversal_point_is_exact_on_both_sides():
    result = _sweep(_MARGINAL)
    flip = result.flip_at
    assert flip is not None

    below = _sweep(_MARGINAL, _cap(round(flip - sensitivity.SEARCH_STEP, 4)))
    at = _sweep(_MARGINAL, _cap(flip))
    assert below.baseline_acts
    assert not at.baseline_acts


def test_trimming_less_is_what_a_looser_threshold_means():
    points = {p.cap: p for p in _sweep(_DOMINANT).points if p.acts}
    proceeds = [points[c].proceeds_usd for c in sorted(points)]
    assert proceeds == sorted(proceeds, reverse=True)
    assert all(p > 0 for p in proceeds)


# --- honesty properties --------------------------------------------------------------------


def test_a_swept_value_is_never_attributed_to_the_persona():
    """A threshold chosen by a sweep is nobody's stated view, including inside a trial object."""
    trial = sensitivity._with_cap(_cap(0.25, Provenance.direct), 0.07)
    parameter = trial.parameters[NAME]

    assert parameter.value == 0.07
    assert parameter.provenance is Provenance.house_default
    assert not parameter.provenance.attributable_to_subject
    # The persona's real evidence survives the substitution; only the number is replaced.
    assert parameter.direction is Direction.tolerates


def test_the_baseline_reports_whose_threshold_it_swept_around():
    stated = _sweep(_MARGINAL, _cap(0.15, Provenance.direct))
    assert stated.baseline == pytest.approx(0.15)
    assert stated.baseline_provenance is Provenance.direct

    house = _sweep(_MARGINAL)
    assert house.baseline_provenance is Provenance.house_default


def test_a_persona_that_declines_position_sizing_is_not_swept():
    result = _sweep(_DOMINANT, PolicyProfile(scopes=[PolicyScope.allocation]))
    assert result.declined
    assert result.points == []
    assert not result.fragile
    assert "declines to weigh in" in " ".join(result.summary_lines())


def test_a_portfolio_under_every_threshold_says_so_rather_than_flagging_fragility():
    result = _sweep((("VTI", 0.34), ("BND", 0.33), ("VXUS", 0.33)), _cap(0.40))
    assert not result.baseline_acts
    assert not result.fragile
    assert "no position needs trimming" in " ".join(result.summary_lines())


def test_no_portfolio_is_handled_without_a_sweep():
    profile = _profile()
    analytics = analyze_profile(profile, None)
    rails = evaluate_guardrails(profile, analytics, None, None)
    result = sensitivity.sweep_concentration(profile, analytics, None, None, rails, PolicyProfile())
    assert not result.baseline_acts
    assert result.position_count == 0
    assert not result.fragile


def test_the_fragility_band_is_the_only_thing_separating_the_two_verdicts():
    """Guards the constant against being quietly widened until nothing is ever fragile."""
    marginal = _sweep(_MARGINAL)
    dominant = _sweep(_DOMINANT)
    assert marginal.distance_to_flip <= FRAGILE_BAND < dominant.distance_to_flip

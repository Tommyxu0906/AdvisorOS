"""Separating what an investor did from what the market did to them.

`test_a_position_that_rose_without_being_touched_is_not_a_purchase` is the test this module
exists for. Reading raw weight changes as decisions does not produce random noise — it
manufactures buying in whatever went up and selling in whatever went down, which is
momentum-chasing behaviour attributed to a subject who may have been doing the reverse. A
persona trained on that learns an artifact of the arithmetic.
"""

from __future__ import annotations

import pytest

from app.distillation.finance_nuwa.drift import (
    ActionBasis,
    ObservedAction,
    PositionSnapshot,
    classify_portfolio,
    classify_position,
    detect_suspected_split,
)
from app.distillation.finance_nuwa.identity import SecurityKey


def key(name: str, title: str = "COM") -> SecurityKey:
    """A legible stand-in for a CUSIP, padded to the nine characters the type requires."""
    return SecurityKey(cusip=f"{name:0<9}"[:9], title_of_class=title)


def snap(name: str, value: float, shares: float | None = None) -> PositionSnapshot:
    return PositionSnapshot(security=key(name), market_value=value, shares=shares)


def _by_symbol(results):
    return {r.security: r for r in results}


# --- the whole point -------------------------------------------------------------------------


def test_a_position_that_rose_without_being_touched_is_not_a_purchase():
    """AAPL 10% -> 18.2% purely because it doubled while the rest of the book stood still."""
    start = [snap("AAPL", 10_000), snap("BND", 90_000)]
    end = [snap("AAPL", 20_000), snap("BND", 90_000)]

    result = _by_symbol(
        classify_portfolio(start, end, returns={key("AAPL"): 1.0, key("BND"): 0.0})
    )[key("AAPL")]

    assert result.action is ObservedAction.hold
    assert result.basis is ActionBasis.drift_adjusted_value
    assert result.start_weight == pytest.approx(0.10)
    assert result.end_weight == pytest.approx(0.1818, abs=1e-3)
    # The no-trade counterfactual lands on exactly the observed weight, so nothing was decided.
    assert result.drift_weight == pytest.approx(result.end_weight, abs=1e-9)
    assert result.active_delta == pytest.approx(0.0, abs=1e-9)
    assert "not a decision" in result.explanation


def test_a_position_that_fell_but_was_topped_up_reads_as_buying():
    """The case raw weights get backwards: the weight *dropped* and they were still buying."""
    start = [snap("AAPL", 20_000), snap("BND", 80_000)]
    end = [snap("AAPL", 12_000), snap("BND", 80_000)]  # halved, then bought back to 12k

    result = _by_symbol(
        classify_portfolio(start, end, returns={key("AAPL"): -0.5, key("BND"): 0.0})
    )[key("AAPL")]

    assert result.end_weight < result.start_weight  # naive reading: "they sold"
    assert result.action is ObservedAction.increase  # drift-adjusted: they bought
    assert result.active_delta > 0


def test_the_no_trade_total_is_computed_across_the_whole_portfolio():
    """A position's drift weight depends on what everything else did.

    Computing it per-position assumes the rest of the book was flat. In a quarter when the market
    moved, that error lands on every name at once and always in the same direction.
    """
    start = [snap("AAPL", 50_000), snap("SPY", 50_000)]
    # Both doubled and nothing was traded, so both weights are unchanged at 50%.
    end = [snap("AAPL", 100_000), snap("SPY", 100_000)]

    results = _by_symbol(
        classify_portfolio(start, end, returns={key("AAPL"): 1.0, key("SPY"): 1.0})
    )

    for name in ("AAPL", "SPY"):
        assert results[key(name)].action is ObservedAction.hold
        assert results[key(name)].drift_weight == pytest.approx(0.5)
        assert results[key(name)].active_delta == pytest.approx(0.0, abs=1e-9)


# --- share counts are a fact, weights are a reading -------------------------------------------


def test_share_counts_are_used_in_preference_to_any_inference():
    start = [snap("AAPL", 10_000, shares=100), snap("BND", 90_000, shares=900)]
    end = [snap("AAPL", 20_000, shares=100), snap("BND", 90_000, shares=900)]

    result = _by_symbol(classify_portfolio(start, end, returns={key("AAPL"): 1.0}))[key("AAPL")]

    assert result.basis is ActionBasis.share_count
    assert result.action is ObservedAction.hold
    assert result.share_change_pct == pytest.approx(0.0)
    assert result.is_trustworthy


def test_a_share_count_reduction_is_a_sale_however_the_price_moved():
    start = [snap("AAPL", 10_000, shares=100)]
    end = [snap("AAPL", 15_000, shares=75)]  # sold a quarter of it; price rose

    result = _by_symbol(classify_portfolio(start, end))[key("AAPL")]

    assert result.action is ObservedAction.reduce
    assert result.share_change_pct == pytest.approx(-0.25)
    assert "down 25" in result.explanation


def test_a_value_only_observation_is_marked_as_the_weaker_evidence():
    start = [snap("AAPL", 10_000), snap("BND", 90_000)]
    end = [snap("AAPL", 20_000), snap("BND", 90_000)]

    exact = _by_symbol(
        classify_portfolio(
            [snap("AAPL", 10_000, shares=100), snap("BND", 90_000, shares=900)],
            [snap("AAPL", 20_000, shares=100), snap("BND", 90_000, shares=900)],
        )
    )[key("AAPL")]
    inferred = _by_symbol(classify_portfolio(start, end, returns={key("AAPL"): 1.0}))[key("AAPL")]

    assert exact.basis.is_exact and exact.is_trustworthy
    assert not inferred.basis.is_exact and not inferred.is_trustworthy


def test_with_no_returns_only_implausibly_large_moves_are_called_decisions():
    """Nothing separates price from trading here, so the bar for claiming a decision is high."""
    modest = _by_symbol(classify_portfolio([snap("AAPL", 10_000)], [snap("AAPL", 12_000)]))[
        key("AAPL")
    ]
    assert modest.action is ObservedAction.hold
    assert modest.basis is ActionBasis.raw_value
    assert "no return data" in modest.explanation

    large = _by_symbol(classify_portfolio([snap("AAPL", 10_000)], [snap("AAPL", 30_000)]))[
        key("AAPL")
    ]
    assert large.action is ObservedAction.increase
    assert "remains an inference" in large.explanation


# --- opening and closing -----------------------------------------------------------------------


def test_entering_and_exiting_are_their_own_actions():
    start = [snap("AAPL", 10_000, shares=100), snap("BND", 90_000, shares=900)]
    end = [snap("BND", 90_000, shares=900), snap("MSFT", 10_000, shares=25)]

    results = _by_symbol(classify_portfolio(start, end))

    assert results[key("AAPL")].action is ObservedAction.exit
    assert results[key("AAPL")].end_weight == 0.0
    assert results[key("MSFT")].action is ObservedAction.enter
    assert results[key("MSFT")].start_weight == 0.0
    assert results[key("BND")].action is ObservedAction.hold


def test_a_symbol_absent_from_both_disclosures_is_not_invented():
    results = classify_portfolio([snap("AAPL", 100)], [snap("AAPL", 100, shares=None)])
    assert {r.security for r in results} == {key("AAPL")}


# --- splits ------------------------------------------------------------------------------------


def test_an_unadjusted_split_is_flagged_rather_than_read_as_a_purchase():
    """Twice the shares at half the price is the same money and no decision."""
    start = snap("AAPL", 10_000, shares=100)
    end = snap("AAPL", 10_200, shares=200)

    assert detect_suspected_split(start, end)
    result = _by_symbol(classify_portfolio([start], [end]))[key("AAPL")]

    assert result.suspected_split
    assert not result.is_trustworthy  # exact basis, but not safe to train on unreviewed
    assert "unadjusted split" in result.explanation


def test_a_declared_split_factor_removes_the_artifact():
    start = [snap("AAPL", 10_000, shares=100)]
    end = [snap("AAPL", 10_200, shares=200)]

    result = _by_symbol(classify_portfolio(start, end, split_factors={key("AAPL"): 2.0}))[
        key("AAPL")
    ]

    assert result.action is ObservedAction.hold
    assert not result.suspected_split
    assert result.is_trustworthy


def test_a_genuine_doubling_is_indistinguishable_and_says_so():
    """Doubling a position and a 2:1 split both double the share count. The value is what tells
    them apart, so a doubling that also doubles the money is not flagged."""
    start = snap("AAPL", 10_000, shares=100)
    end = snap("AAPL", 20_000, shares=200)  # bought more at the same price

    assert not detect_suspected_split(start, end)
    result = _by_symbol(classify_portfolio([start], [end]))[key("AAPL")]
    assert result.action is ObservedAction.increase
    assert result.is_trustworthy


# --- the semantics of the enum -------------------------------------------------------------------


def test_hold_is_a_decision_but_not_a_transaction():
    assert not ObservedAction.hold.is_active
    assert ObservedAction.hold.direction == 0
    for action in (ObservedAction.enter, ObservedAction.increase):
        assert action.is_active and action.direction == 1
    for action in (ObservedAction.reduce, ObservedAction.exit):
        assert action.is_active and action.direction == -1


def test_a_position_cannot_claim_value_with_no_shares():
    with pytest.raises(ValueError, match="zero shares"):
        PositionSnapshot(security=key("AAPL"), market_value=1_000, shares=0)


# --- a realistic quarter -------------------------------------------------------------------------


def test_a_quarter_with_four_different_behaviours_is_read_correctly():
    """One book, one period: something held through a rally, something genuinely added to,
    something trimmed into strength, and something closed."""
    start = [
        snap("AAPL", 40_000, shares=400),
        snap("KO", 20_000, shares=400),
        snap("XOM", 20_000, shares=200),
        snap("IBM", 20_000, shares=200),
    ]
    end = [
        snap("AAPL", 60_000, shares=400),  # +50% price, untouched
        snap("KO", 30_000, shares=600),  # bought 50% more shares
        snap("XOM", 15_000, shares=100),  # halved the shares as it rose
        # IBM gone
    ]

    results = _by_symbol(classify_portfolio(start, end))

    assert results[key("AAPL")].action is ObservedAction.hold
    assert results[key("KO")].action is ObservedAction.increase
    assert results[key("XOM")].action is ObservedAction.reduce
    assert results[key("IBM")].action is ObservedAction.exit

    # And every one of them is exact, because share counts were disclosed.
    assert all(r.basis.is_exact for r in results.values())


def test_a_single_position_can_be_classified_on_its_own():
    """`classify_position` exists so the arithmetic is checkable one name at a time — but the
    caller then owns the portfolio totals, which is exactly the trap `classify_portfolio` closes.
    Same numbers as the flagship case, supplied by hand."""
    result = classify_position(
        key("AAPL"),
        start=snap("AAPL", 10_000),
        end=snap("AAPL", 20_000),
        period_return=1.0,
        start_total=100_000,
        end_total=110_000,
        drift_total=110_000,
    )

    assert result.action is ObservedAction.hold
    assert result.drift_weight == pytest.approx(20_000 / 110_000)
    assert result.active_delta == pytest.approx(0.0, abs=1e-9)


def test_supplying_the_wrong_drift_total_produces_the_error_the_portfolio_helper_prevents():
    """Pretending the rest of the book was flat when it doubled invents a decision that was
    never taken. This is why `classify_portfolio` computes the total across every position."""
    wrong = classify_position(
        key("AAPL"),
        start=snap("AAPL", 50_000),
        end=snap("AAPL", 100_000),
        period_return=1.0,
        start_total=100_000,
        end_total=200_000,
        drift_total=150_000,  # assumes the other 50k stayed flat; it also doubled
    )

    # Nothing was traded, but the bad total puts the no-trade weight at 67% against an observed
    # 50%, so the arithmetic reports a sale that never happened.
    assert wrong.action is ObservedAction.reduce
    assert wrong.drift_weight == pytest.approx(100_000 / 150_000)
    assert wrong.active_delta == pytest.approx(0.5 - (100_000 / 150_000), abs=1e-9)

    # The portfolio helper, given the same book, gets it right.
    correct = _by_symbol(
        classify_portfolio(
            [snap("AAPL", 50_000), snap("SPY", 50_000)],
            [snap("AAPL", 100_000), snap("SPY", 100_000)],
            returns={key("AAPL"): 1.0, key("SPY"): 1.0},
        )
    )[key("AAPL")]
    assert correct.action is ObservedAction.hold

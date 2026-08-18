"""The local market data layer: the format, the boundary, and what it refuses to invent."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.portfolio import Holding, Portfolio
from app.paper.market import (
    REQUIRED_COLUMNS,
    LocalHistoricalMarketDataProvider,
)

FIXTURES = "data/market/fixtures"


@pytest.fixture(scope="module")
def market() -> LocalHistoricalMarketDataProvider:
    return LocalHistoricalMarketDataProvider.from_directory(FIXTURES)


def one(symbol: str, quantity: float = 100) -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(
                symbol=symbol,
                name=symbol,
                asset_class="us_equity",
                quantity=quantity,
                market_value=quantity * 100.0,
                account_type="taxable",
            )
        ]
    )


# --- format ------------------------------------------------------------------------------


def test_the_format_mirrors_daily_bars_rather_than_inventing_a_second_shape():
    """adj_close is separate from close because returns must come from the adjusted series."""
    assert REQUIRED_COLUMNS == ("trade_date", "symbol", "close", "adj_close", "source")


def test_a_file_missing_a_required_column_is_rejected(tmp_path):
    path = tmp_path / "BAD.csv"
    path.write_text("trade_date,symbol,close\n2024-01-02,BAD,100\n")
    with pytest.raises(ValueError, match="missing columns"):
        LocalHistoricalMarketDataProvider.from_directory(tmp_path)


def test_duplicate_rows_for_one_symbol_date_are_refused(tmp_path):
    """Two rows for one date make every trailing window silently wrong."""
    path = tmp_path / "DUP.csv"
    path.write_text(
        "trade_date,symbol,close,adj_close,source\n"
        "2024-01-02,DUP,100,100,t\n"
        "2024-01-02,DUP,101,101,t\n"
    )
    with pytest.raises(ValueError, match="more than one row"):
        LocalHistoricalMarketDataProvider.from_directory(tmp_path)


def test_an_empty_directory_is_an_error_rather_than_an_empty_market(tmp_path):
    with pytest.raises(ValueError, match="no usable market data"):
        LocalHistoricalMarketDataProvider.from_directory(tmp_path)


def test_a_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalHistoricalMarketDataProvider.from_directory(tmp_path / "nope")


def test_the_content_hash_changes_when_the_data_changes(tmp_path, market):
    import shutil
    from pathlib import Path

    source = Path(FIXTURES)
    if not source.is_absolute():
        source = Path(__file__).resolve().parents[2] / FIXTURES
    copy = tmp_path / "copy"
    shutil.copytree(source, copy)

    same = LocalHistoricalMarketDataProvider.from_directory(copy)
    assert same.content_sha256 == market.content_sha256

    (copy / "RISE.csv").open("a").write("2024-12-02,RISE,1,1,t\n")
    changed = LocalHistoricalMarketDataProvider.from_directory(copy)
    assert changed.content_sha256 != market.content_sha256


# --- the boundary ------------------------------------------------------------------------


def test_every_accessor_filters_on_as_of(market):
    early, late = date(2024, 2, 1), date(2024, 8, 1)
    assert market.get_price("RISE", early) < market.get_price("RISE", late)
    assert market.observation_date("RISE", early) <= early
    assert len(market.get_return_series("RISE", date(2024, 1, 2), early)) < len(
        market.get_return_series("RISE", date(2024, 1, 2), late)
    )


def test_a_date_before_all_data_yields_nothing_rather_than_the_first_bar(market):
    assert market.get_price("RISE", date(2020, 1, 1)) is None
    assert market.build_price_series("RISE", date(2020, 1, 1), 252) is None


def test_returns_are_computed_from_adjusted_prices(tmp_path):
    """A split in `close` that `adj_close` already handles must not register as a move."""
    path = tmp_path / "SPLIT.csv"
    path.write_text(
        "trade_date,symbol,close,adj_close,source\n"
        "2024-01-02,SPLIT,100,50,t\n"
        "2024-01-03,SPLIT,50,50,t\n"  # 2-for-1 split: unadjusted halves, adjusted is flat
        "2024-01-04,SPLIT,55,55,t\n"
    )
    provider = LocalHistoricalMarketDataProvider.from_directory(tmp_path)
    series = provider.build_price_series("SPLIT", date(2024, 1, 4), 252)
    assert series is not None
    # First return is 0.0 from adj_close, not -50% from close.
    assert series.returns[0] == pytest.approx(0.0)
    assert series.returns[1] == pytest.approx(0.1)


def test_marking_uses_close_not_adjusted_close(tmp_path):
    """A position is worth its unadjusted price; adjusted prices are for returns."""
    path = tmp_path / "ADJ.csv"
    path.write_text("trade_date,symbol,close,adj_close,source\n2024-01-02,ADJ,200,100,t\n")
    provider = LocalHistoricalMarketDataProvider.from_directory(tmp_path)
    assert provider.get_price("ADJ", date(2024, 1, 2)) == 200
    assert provider.get_adjusted_price("ADJ", date(2024, 1, 2)) == 100


def test_a_lookback_bounds_the_series_length(market):
    short = market.build_price_series("RISE", date(2024, 8, 1), lookback=10)
    assert short is not None and len(short.returns) == 10


# --- hydration ---------------------------------------------------------------------------


def test_hydration_marks_holdings_to_the_as_of_price(market):
    result = market.hydrate_portfolio(one("RISE", 100), date(2024, 3, 1))
    expected = market.get_price("RISE", date(2024, 3, 1)) * 100
    assert result.portfolio.holdings[0].market_value == pytest.approx(expected, rel=1e-6)


def test_hydration_attaches_only_history_visible_at_the_date(market):
    early = market.hydrate_portfolio(one("RISE"), date(2024, 2, 1))
    late = market.hydrate_portfolio(one("RISE"), date(2024, 8, 1))
    assert len(early.portfolio.price_series[0].returns) < len(
        late.portfolio.price_series[0].returns
    )


def test_an_unpriced_symbol_keeps_its_value_and_is_reported(market):
    """Not dropped — that shrinks the book. Not zeroed — that claims it became worthless."""
    result = market.hydrate_portfolio(one("NOSUCH", 10), date(2024, 3, 1))
    assert result.unpriced == ["NOSUCH"]
    assert result.portfolio.holdings[0].market_value == 1000.0
    assert result.priced_coverage == 0.0


def test_priced_coverage_is_a_fraction_and_never_a_silent_none(market):
    portfolio = Portfolio(
        holdings=one("RISE", 10).holdings + one("NOSUCH", 10).holdings,
    )
    result = market.hydrate_portfolio(portfolio, date(2024, 3, 1))
    assert result.priced_coverage == 0.5


def test_provenance_records_where_each_price_came_from(market):
    result = market.hydrate_portfolio(one("RISE"), date(2024, 3, 1))
    provenance = result.provenance["RISE"]
    assert provenance.source == "fixture"
    assert provenance.observed_at <= date(2024, 3, 1)
    assert provenance.as_of == date(2024, 3, 1)
    assert provenance.observations > 0
    assert "RISE" in provenance.describe()


def test_staleness_is_measured_against_the_decision_date(market):
    """A weekend is not stale; a two-week data gap is."""
    fresh = market.hydrate_portfolio(one("RISE"), date(2024, 3, 1))
    assert not fresh.provenance["RISE"].is_stale
    assert fresh.stale == []

    # Mid-gap read on the GAP symbol: the last observation is nearly two weeks old.
    gapped = market.hydrate_portfolio(one("GAP"), date(2024, 4, 5))
    assert gapped.provenance["GAP"].staleness_days > 7
    assert gapped.stale == ["GAP"]


def test_market_context_reports_what_was_available(market):
    context = market.get_market_context(date(2024, 3, 1))
    assert context.as_of == date(2024, 3, 1)
    assert context.symbols_available > 0
    assert context.latest_observation is not None and context.latest_observation <= date(2024, 3, 1)


def test_market_context_before_any_data_reports_everything_missing(market):
    context = market.get_market_context(date(2020, 1, 1))
    assert context.symbols_available == 0
    assert len(context.symbols_missing) == len(market.bars)

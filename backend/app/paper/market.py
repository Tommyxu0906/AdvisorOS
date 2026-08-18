"""Point-in-time market data for offline replay, and the boundary that keeps it honest.

`QuantBehaviorProvider` needs price history to say anything: without it, feature coverage sits
at 29% and every position correctly abstains. This module is where that history comes from
during a replay — from local files, with a hard cutoff, and never from the network.

**The one rule everything here exists to enforce:** no observation dated after `as_of` may reach
a decision. That is not a convention followed by careful callers, it is the only way to read
this data — every accessor takes `as_of` and filters on it, and there is no method that returns
the whole series. A replay that could see tomorrow's close is not a replay, it is a lookup, and
the numbers it produces would be indistinguishable from a working system right up until someone
believed them.

**Returns come from `adj_close`, never `close`.** This is the repo's existing invariant, stated
on `daily_bars.adj_close` in migration 0007: a split or dividend registers as a real one-day move
in unadjusted prices and would corrupt volatility, drawdown, and every trailing return computed
from them. `close` is carried for marking positions, because that is what a portfolio is worth.

**Missing data stays missing.** A symbol with no local history yields no price series and no
price features, and `QuantBehaviorProvider` then abstains on it. Fabricating a zero return to
fill the gap would turn "nobody knows" into "this went nowhere", which is a claim about a stock.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.portfolio import Holding, Portfolio, PriceSeries

TRADING_DAYS_PER_YEAR = 252

# Columns of the local format, mirroring `daily_bars` rather than inventing a second shape.
REQUIRED_COLUMNS = ("trade_date", "symbol", "close", "adj_close", "source")

DEFAULT_MARKET_DIR = "data/market"


class FeatureProvenance(BaseModel):
    """Where one hydrated number came from and how old it was when it was used.

    `observed_at` and `as_of` are separate because their difference is the whole point: a price
    observed three weeks before the decision that used it is stale, and a replay that reports
    only the value gives no way to notice.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    source: str
    observed_at: date = Field(description="Trade date of the observation actually used")
    as_of: date = Field(description="The decision date it was read for")
    observations: int = Field(default=0, description="Bars visible at as_of")

    @property
    def staleness_days(self) -> int:
        return (self.as_of - self.observed_at).days

    @property
    def is_stale(self) -> bool:
        """More than a week old. Calendar days, so a normal weekend never trips it."""
        return self.staleness_days > 7

    def describe(self) -> str:
        state = f"{self.staleness_days}d stale" if self.is_stale else "fresh"
        return (
            f"{self.symbol} @ {self.observed_at} ({state}, {self.observations} bars, {self.source})"
        )


class MarketContext(BaseModel):
    """What was knowable about the market as a whole at one date."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    symbols_available: int = 0
    symbols_missing: list[str] = Field(default_factory=list)
    latest_observation: date | None = None


class HydrationResult(BaseModel):
    """A portfolio marked to a date, plus an account of what could and could not be filled."""

    model_config = ConfigDict(extra="forbid")

    portfolio: Portfolio
    as_of: date
    provenance: dict[str, FeatureProvenance] = Field(default_factory=dict)
    unpriced: list[str] = Field(
        default_factory=list,
        description="Symbols with no observation at or before as_of. Left at their prior value.",
    )

    @property
    def priced_coverage(self) -> float:
        """Fraction of held symbols that could be marked. Never silently None."""
        symbols = {h.symbol.strip().upper() for h in self.portfolio.holdings if h.symbol.strip()}
        if not symbols:
            return 0.0
        return round(1.0 - len(set(self.unpriced) & symbols) / len(symbols), 4)

    @property
    def stale(self) -> list[str]:
        return sorted(s for s, p in self.provenance.items() if p.is_stale)


@runtime_checkable
class MarketFeatureProvider(Protocol):
    """Point-in-time market reads. Every method takes `as_of` — there is no unbounded accessor."""

    provider_id: str

    def get_price(self, symbol: str, as_of: date) -> float | None:
        """Last close at or before `as_of`, or None. Unadjusted: this marks a position."""
        ...

    def get_return_series(self, symbol: str, start: date, end: date) -> list[float]:
        """Adjusted daily returns within the window. Empty when the history cannot support one."""
        ...

    def build_price_series(self, symbol: str, as_of: date, lookback: int) -> PriceSeries | None:
        """The last `lookback` adjusted returns visible at `as_of`."""
        ...

    def get_market_context(self, as_of: date) -> MarketContext: ...

    def hydrate_portfolio(self, portfolio: Portfolio, as_of: date) -> HydrationResult:
        """Mark every holding to `as_of` and attach the price history visible then."""
        ...


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: date
    close: float = Field(gt=0)
    adj_close: float = Field(gt=0)
    source: str


class LocalHistoricalMarketDataProvider(BaseModel):
    """Reads a directory of CSV files. Entirely offline; no client, no vendor, no fallback.

    One file per symbol keeps the fixtures inspectable — a failing replay test should be
    debuggable by opening the file in a spreadsheet — but a single combined file is accepted too,
    since both parse into the same in-memory shape.
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str = "local_csv"
    bars: dict[str, list[Observation]] = Field(default_factory=dict)
    content_sha256: str = ""

    # --- construction -------------------------------------------------------------------

    @classmethod
    def from_directory(
        cls, directory: str | Path = DEFAULT_MARKET_DIR
    ) -> LocalHistoricalMarketDataProvider:
        import hashlib

        resolved = Path(directory)
        if not resolved.is_absolute():
            resolved = _repo_root() / resolved
        if not resolved.exists():
            raise FileNotFoundError(f"no local market data directory at {resolved}")

        bars: dict[str, list[Observation]] = {}
        digest = hashlib.sha256()

        for path in sorted(resolved.glob("*.csv")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
            for symbol, observation in _read_csv(path):
                bars.setdefault(symbol, []).append(observation)

        if not bars:
            raise ValueError(f"{resolved} contains no usable market data rows")

        for symbol in bars:
            bars[symbol].sort(key=lambda o: o.trade_date)
            _refuse_duplicate_dates(symbol, bars[symbol])

        return cls(bars=bars, content_sha256=digest.hexdigest())

    # --- point-in-time reads ------------------------------------------------------------

    def _visible(self, symbol: str, as_of: date) -> list[Observation]:
        """Every observation at or before `as_of`. The information boundary, in one place.

        Deliberately the only way any other method reaches the data, so that adding a lookahead
        bug means editing this function rather than forgetting a filter somewhere.
        """
        series = self.bars.get(symbol.strip().upper())
        if not series:
            return []
        return [o for o in series if o.trade_date <= as_of]

    def get_price(self, symbol: str, as_of: date) -> float | None:
        visible = self._visible(symbol, as_of)
        return visible[-1].close if visible else None

    def get_adjusted_price(self, symbol: str, as_of: date) -> float | None:
        visible = self._visible(symbol, as_of)
        return visible[-1].adj_close if visible else None

    def observation_date(self, symbol: str, as_of: date) -> date | None:
        visible = self._visible(symbol, as_of)
        return visible[-1].trade_date if visible else None

    def next_trading_date(self, symbol: str, after: date) -> date | None:
        """First observation strictly after `after`. Used only to mark and to fill."""
        series = self.bars.get(symbol.strip().upper()) or []
        for observation in series:
            if observation.trade_date > after:
                return observation.trade_date
        return None

    def get_return_series(self, symbol: str, start: date, end: date) -> list[float]:
        visible = [o for o in self._visible(symbol, end) if o.trade_date >= start]
        return _returns_from(visible)

    def build_price_series(
        self, symbol: str, as_of: date, lookback: int = TRADING_DAYS_PER_YEAR
    ) -> PriceSeries | None:
        visible = self._visible(symbol, as_of)
        # lookback + 1 observations produce lookback returns.
        window = visible[-(lookback + 1) :] if lookback > 0 else visible
        returns = _returns_from(window)
        if not returns:
            return None
        return PriceSeries(
            symbol=symbol.strip().upper(),
            periods_per_year=TRADING_DAYS_PER_YEAR,
            returns=returns,
        )

    def get_market_context(self, as_of: date) -> MarketContext:
        available, missing, latest = 0, [], None
        for symbol in sorted(self.bars):
            visible = self._visible(symbol, as_of)
            if visible:
                available += 1
                latest = max(latest, visible[-1].trade_date) if latest else visible[-1].trade_date
            else:
                missing.append(symbol)
        return MarketContext(
            as_of=as_of,
            symbols_available=available,
            symbols_missing=missing,
            latest_observation=latest,
        )

    # --- hydration ----------------------------------------------------------------------

    def hydrate_portfolio(
        self, portfolio: Portfolio, as_of: date, *, lookback: int = TRADING_DAYS_PER_YEAR
    ) -> HydrationResult:
        """Mark holdings to `as_of` and attach the history visible then.

        A symbol with no observation keeps the market value it arrived with, and is reported in
        `unpriced` rather than dropped or zeroed. Dropping it would quietly shrink the portfolio;
        zeroing it would claim the position became worthless.
        """
        provenance: dict[str, FeatureProvenance] = {}
        unpriced: list[str] = []
        series: list[PriceSeries] = []

        holdings: list[Holding] = []
        for holding in portfolio.holdings:
            symbol = holding.symbol.strip().upper()
            if not symbol:
                holdings.append(holding.model_copy(deep=True))
                continue

            visible = self._visible(symbol, as_of)
            if not visible:
                unpriced.append(symbol)
                holdings.append(holding.model_copy(deep=True))
                continue

            observation = visible[-1]
            provenance[symbol] = FeatureProvenance(
                symbol=symbol,
                source=observation.source,
                observed_at=observation.trade_date,
                as_of=as_of,
                observations=len(visible),
            )

            market_value = (
                holding.quantity * observation.close
                if holding.quantity is not None
                else holding.market_value
            )
            holdings.append(holding.model_copy(update={"market_value": round(market_value, 6)}))

        for symbol in sorted({h.symbol.strip().upper() for h in holdings if h.symbol.strip()}):
            built = self.build_price_series(symbol, as_of, lookback)
            if built is not None:
                series.append(built)

        return HydrationResult(
            portfolio=Portfolio(
                holdings=holdings, price_series=series, currency=portfolio.currency
            ),
            as_of=as_of,
            provenance=provenance,
            unpriced=sorted(set(unpriced)),
        )


# --- helpers ----------------------------------------------------------------------------


def _returns_from(observations: list[Observation]) -> list[float]:
    """Adjusted returns. `close` is never used here — see the module docstring."""
    returns: list[float] = []
    for previous, current in zip(observations, observations[1:], strict=False):
        if previous.adj_close <= 0:
            continue
        returns.append(current.adj_close / previous.adj_close - 1.0)
    return returns


def _read_csv(path: Path) -> list[tuple[str, Observation]]:
    rows: list[tuple[str, Observation]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{path.name} is missing columns {missing}; expected {list(REQUIRED_COLUMNS)}"
            )
        for line in reader:
            symbol = (line["symbol"] or "").strip().upper()
            if not symbol:
                continue
            rows.append(
                (
                    symbol,
                    Observation(
                        trade_date=date.fromisoformat(line["trade_date"].strip()),
                        close=float(line["close"]),
                        adj_close=float(line["adj_close"]),
                        source=(line["source"] or "unknown").strip(),
                    ),
                )
            )
    return rows


def _refuse_duplicate_dates(symbol: str, observations: list[Observation]) -> None:
    """Two rows for one symbol-date make every window silently wrong."""
    seen: set[date] = set()
    for observation in observations:
        if observation.trade_date in seen:
            raise ValueError(
                f"{symbol} has more than one row for {observation.trade_date}; "
                "the local format is keyed on (symbol, trade_date)"
            )
        seen.add(observation.trade_date)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]

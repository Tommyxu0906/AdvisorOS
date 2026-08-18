"""Path metrics for a replay, and a standing refusal to let them read as performance.

Every number here describes **a historical simulation against local data with a frictionless
simulator**. None of it is alpha, and the labels say so wherever they leave this module.

What these metrics are genuinely useful for:

  - *path stability* — does the policy settle, or does it churn the same position every round
  - *guardrail behaviour* — how often does the engine have to refuse the investor layer
  - *provider differences* — does the investor overlay change the trades at all
  - *integration* — does the loop survive a falling market, a flat one, a data gap

What they cannot show, and what nothing in an offline replay could:

  - whether the policy would make money, since fills are instant, complete, and at the mark
  - whether the sample means anything, since a handful of rounds on a fixture is not evidence
  - anything at all about a live venue, where slippage, spread, and queue position exist

`annualized_return` is returned as `None` below a quarter of data rather than extrapolated. A
three-week fixture annualizes to numbers that are arithmetically correct and rhetorically absurd,
and someone eventually screenshots them.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

TRADING_DAYS_PER_YEAR = 252
MIN_DAYS_TO_ANNUALIZE = 63  # one quarter

SIMULATION_DISCLAIMER = (
    "Historical replay against local data, executed by a frictionless simulator. Not a backtest "
    "of a tradable strategy, not evidence of alpha, and not a claim about any live market."
)


class PathMetrics(BaseModel):
    """One provider's run, reduced to comparable numbers."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    provider_display_name: str

    rounds: int = 0
    days: int = 0

    starting_equity: float = 0.0
    ending_equity: float = 0.0

    cumulative_return: float = 0.0
    annualized_return: float | None = Field(
        default=None, description="None below a quarter of data rather than extrapolated"
    )
    max_drawdown: float = 0.0
    realized_volatility: float | None = Field(
        default=None, description="None below three marks; two points do not have a dispersion"
    )

    trades: int = 0
    turnover: float = Field(default=0.0, description="Traded notional over average equity")
    average_trade_size: float = 0.0

    rejected_actions: int = 0
    refused_actions: int = 0

    average_cash_weight: float = 0.0
    max_single_name_concentration: float = 0.0
    ending_concentration: float = 0.0

    abstention_rate: float = 0.0
    average_feature_coverage: float = 0.0
    stance_count: int = Field(
        default=0,
        description=(
            "Total stances across the run. Zero means the provider contributed no directional "
            "view at all, which makes coverage and abstention undefined rather than bad."
        ),
    )

    # Counts by attribution bucket, so "the investor layer did nothing" is visible as a number.
    investor_originated_actions: int = 0
    engine_originated_actions: int = 0

    @property
    def is_simulation(self) -> bool:
        """Always True. Present so a serialized metrics blob carries its own caveat."""
        return True

    def warnings(self) -> list[str]:
        """Things a reader should look at before looking at the return.

        Ordered by how badly each one would mislead someone who skipped straight to the P&L.
        """
        out: list[str] = []
        if self.turnover > 2.0:
            out.append(
                f"turnover {self.turnover:.1f}x — the policy is churning; in a real venue the "
                "spread alone would dominate this path"
            )
        if self.max_single_name_concentration > 0.5:
            out.append(
                f"peak single-name weight {self.max_single_name_concentration:.0%} — the plan "
                "ran concentrated despite the engine's trims"
            )
        if self.stance_count and self.abstention_rate > 0.5:
            out.append(
                f"abstained on {self.abstention_rate:.0%} of positions — most of this path is "
                "the decision engine, not the investor layer"
            )
        if self.stance_count and self.average_feature_coverage < 0.5:
            out.append(
                f"average feature coverage {self.average_feature_coverage:.0%} — the model was "
                "extrapolating from mostly-missing inputs"
            )
        if self.refused_actions > self.trades:
            out.append(
                f"{self.refused_actions} actions refused against {self.trades} executed — the "
                "investor layer is fighting the deterministic constraints"
            )
        if self.rejected_actions:
            out.append(
                f"{self.rejected_actions} orders rejected by the simulator — the feasibility "
                "check upstream should have caught these"
            )
        if self.rounds and self.trades / max(self.rounds, 1) > 3:
            out.append(
                f"{self.trades / self.rounds:.1f} trades per round — check for repeated reversals"
            )
        return out


def compute_path_metrics(
    *,
    provider_id: str,
    provider_display_name: str,
    equity_curve: list[float],
    cash_weights: list[float],
    concentrations: list[float],
    traded_notional: float,
    trades: int,
    rejected_actions: int,
    refused_actions: int,
    abstentions: int,
    stance_count: int,
    feature_coverages: list[float],
    investor_actions: int,
    engine_actions: int,
    days: int,
) -> PathMetrics:
    curve = [e for e in equity_curve if e is not None]
    start = curve[0] if curve else 0.0
    end = curve[-1] if curve else 0.0

    cumulative = (end / start - 1.0) if start > 0 else 0.0

    annualized: float | None = None
    if days >= MIN_DAYS_TO_ANNUALIZE and start > 0 and end > 0:
        years = days / 365.25
        annualized = (end / start) ** (1 / years) - 1.0 if years > 0 else None

    average_equity = sum(curve) / len(curve) if curve else 0.0

    return PathMetrics(
        provider_id=provider_id,
        provider_display_name=provider_display_name,
        rounds=max(len(curve) - 1, 0),
        days=days,
        starting_equity=round(start, 2),
        ending_equity=round(end, 2),
        cumulative_return=round(cumulative, 6),
        annualized_return=round(annualized, 6) if annualized is not None else None,
        max_drawdown=round(_max_drawdown(curve), 6),
        realized_volatility=_realized_volatility(curve),
        trades=trades,
        turnover=round(traded_notional / average_equity, 4) if average_equity > 0 else 0.0,
        average_trade_size=round(traded_notional / trades, 2) if trades else 0.0,
        rejected_actions=rejected_actions,
        refused_actions=refused_actions,
        average_cash_weight=round(sum(cash_weights) / len(cash_weights), 6)
        if cash_weights
        else 0.0,
        max_single_name_concentration=round(max(concentrations), 6) if concentrations else 0.0,
        ending_concentration=round(concentrations[-1], 6) if concentrations else 0.0,
        abstention_rate=round(abstentions / stance_count, 4) if stance_count else 0.0,
        average_feature_coverage=(
            round(sum(feature_coverages) / len(feature_coverages), 4) if feature_coverages else 0.0
        ),
        stance_count=stance_count,
        investor_originated_actions=investor_actions,
        engine_originated_actions=engine_actions,
    )


def _max_drawdown(curve: list[float]) -> float:
    """Largest peak-to-trough fall. Zero or negative."""
    if len(curve) < 2:
        return 0.0
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _realized_volatility(curve: list[float]) -> float | None:
    """Annualized standard deviation of step returns, or None when there are too few marks.

    The steps are replay rounds rather than days, so this is annualized by round count and is
    only comparable between runs on the *same* schedule. The comparison harness guarantees that
    by construction; anything else should not put two of these numbers side by side.
    """
    if len(curve) < 3:
        return None
    returns = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve)) if curve[i - 1] > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(len(returns)), 6)

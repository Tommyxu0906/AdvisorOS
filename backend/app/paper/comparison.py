"""Run every provider down the same path, and report what the investor layer actually changed.

The comparison is only meaningful if the *only* thing that differs is the provider. So the
starting portfolio, the clock, the market data, the broker semantics, and the decision-engine
configuration are all built once and handed to every run — and `identical_inputs` asserts it
afterwards from the recorded run metadata rather than trusting that they were.

`DecisionEngineOnly` is the reference column, not `Mock`. The question this harness exists to
answer is "how much does the investor-policy layer change?", and that question has a baseline
only if one run has no investor layer at all. Mock is present for plumbing and is labelled as
noise wherever it appears; ranking it on return would be ranking a hash function.

**Nothing here ranks providers by return.** Deltas are reported against the baseline across six
axes at once, and the warnings block is printed above the table rather than below it, because a
reader who sees a return first has already formed an opinion by the time they reach the caveats.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.portfolio import Portfolio
from app.domain.profile import FinancialProfile
from app.paper.attribution import ActionOrigin
from app.paper.engine_only import BASELINE_PROVIDER_ID, DecisionEngineOnlyProvider
from app.paper.frozen_policy import FrozenPolicyProvider
from app.paper.metrics import SIMULATION_DISCLAIMER
from app.paper.mock_policy import MockInvestorPolicy
from app.paper.provider import InvestorDecisionProvider
from app.paper.quant_policy import QuantBehaviorProvider
from app.paper.replay import OfflineReplayEngine, ReplayRun

# Mock is included for plumbing coverage only. Named here so the label travels with it.
NOISE_PROVIDERS = frozenset({"mock_investor"})


class BaselineDelta(BaseModel):
    """One provider against DecisionEngineOnly, on the axes that matter together."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    return_delta: float = 0.0
    drawdown_delta: float = 0.0
    turnover_delta: float = 0.0
    concentration_delta: float = 0.0
    action_count_delta: int = 0
    trade_count_delta: int = 0

    shared_actions: int = 0
    added_by_investor: int = 0
    suppressed_by_investor: int = 0
    refused_by_engine: int = 0


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclaimer: str = SIMULATION_DISCLAIMER
    runs: list[ReplayRun] = Field(default_factory=list)
    deltas: list[BaselineDelta] = Field(default_factory=list)

    @property
    def baseline(self) -> ReplayRun | None:
        for run in self.runs:
            if run.provider_id == BASELINE_PROVIDER_ID:
                return run
        return None

    def identical_inputs(self) -> bool:
        """Every run must have seen the same market data, rule, and dates. Checked, not assumed."""
        if len(self.runs) < 2:
            return True
        first = self.runs[0]
        return all(
            r.market_data_sha256 == first.market_data_sha256
            and r.execution_rule == first.execution_rule
            and r.start_date == first.start_date
            and r.end_date == first.end_date
            and r.starting_equity == first.starting_equity
            for r in self.runs
        )

    def digest(self) -> str:
        import hashlib

        return hashlib.sha256("".join(r.digest() for r in self.runs).encode()).hexdigest()

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def render(self) -> str:
        return render_comparison(self)


def build_providers(
    *, include: tuple[str, ...] = ("engine-only", "frozen", "quant", "mock")
) -> list[InvestorDecisionProvider]:
    """Baseline first, so it reads as the reference column rather than one row among four."""
    built: list[InvestorDecisionProvider] = []
    for name in include:
        if name == "engine-only":
            built.append(DecisionEngineOnlyProvider())
        elif name == "frozen":
            built.append(FrozenPolicyProvider.from_path())
        elif name == "quant":
            built.append(QuantBehaviorProvider.load())
        elif name == "mock":
            built.append(MockInvestorPolicy())
        else:
            raise ValueError(f"unknown provider {name!r}")
    return built


def compare(
    engine: OfflineReplayEngine,
    profile: FinancialProfile,
    starting_portfolio: Portfolio,
    providers: list[InvestorDecisionProvider],
) -> ComparisonResult:
    """One engine, one book, one clock — only the provider varies."""
    runs = [engine.run(profile, starting_portfolio.model_copy(deep=True), p) for p in providers]
    result = ComparisonResult(runs=runs)

    baseline = result.baseline
    if baseline is not None:
        for run in runs:
            if run.provider_id == baseline.provider_id:
                continue
            result.deltas.append(_delta(run, baseline))
    return result


def _delta(run: ReplayRun, baseline: ReplayRun) -> BaselineDelta:
    run_metrics, base_metrics = run.metrics, baseline.metrics
    assert run_metrics is not None and base_metrics is not None

    run_actions = _action_ids(run)
    base_actions = _action_ids(baseline)

    investor_added = sum(len(r.attribution.investor_originated) for r in run.rounds)
    refused = sum(len(r.attribution.refused) for r in run.rounds)

    return BaselineDelta(
        provider_id=run.provider_id,
        return_delta=round(run_metrics.cumulative_return - base_metrics.cumulative_return, 6),
        drawdown_delta=round(run_metrics.max_drawdown - base_metrics.max_drawdown, 6),
        turnover_delta=round(run_metrics.turnover - base_metrics.turnover, 4),
        concentration_delta=round(
            run_metrics.ending_concentration - base_metrics.ending_concentration, 6
        ),
        action_count_delta=len(run_actions) - len(base_actions),
        trade_count_delta=run_metrics.trades - base_metrics.trades,
        shared_actions=len(run_actions & base_actions),
        added_by_investor=investor_added,
        # An action the baseline produced and this run did not: the investor layer changed the
        # book enough that the engine no longer needed it.
        suppressed_by_investor=len(base_actions - run_actions),
        refused_by_engine=refused,
    )


def _action_ids(run: ReplayRun) -> set[str]:
    """Keyed by round so the same action id in two rounds counts twice."""
    return {f"{r.index}:{a.action_id}" for r in run.rounds for a in r.action_set.actions}


def render_comparison(result: ComparisonResult) -> str:
    lines: list[str] = []
    lines.append(SIMULATION_DISCLAIMER)
    lines.append("")

    if not result.identical_inputs():
        lines.append("!! runs did not share identical inputs — the comparison is not valid")
        lines.append("")

    # Warnings first, deliberately: a reader who sees a return first has already decided.
    flagged = [(r.provider_id, r.metrics.warnings()) for r in result.runs if r.metrics]
    flagged = [(pid, w) for pid, w in flagged if w]
    if flagged:
        lines.append("WHAT TO LOOK AT BEFORE THE RETURNS")
        for provider_id, warnings in flagged:
            for warning in warnings:
                lines.append(f"  {provider_id:<30} {warning}")
        lines.append("")

    header = (
        f"{'Provider':<32}{'Return':>9}{'MaxDD':>9}{'Turnover':>10}"
        f"{'Trades':>8}{'Abstain':>9}{'MaxConc':>9}{'Cover':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for run in result.runs:
        m = run.metrics
        if m is None:
            continue
        label = run.provider_id
        if run.provider_id in NOISE_PROVIDERS:
            label += " *"
        lines.append(
            f"{label:<32}{m.cumulative_return:>8.2%}{m.max_drawdown:>9.2%}"
            f"{m.turnover:>10.2f}{m.trades:>8}{m.abstention_rate:>8.0%}"
            f"{m.max_single_name_concentration:>9.1%}{m.average_feature_coverage:>8.0%}"
        )

    if any(r.provider_id in NOISE_PROVIDERS for r in result.runs):
        lines.append("")
        lines.append("  * plumbing check only — content-free by construction, not a competitor")

    if result.deltas:
        lines.append("")
        lines.append("VERSUS DecisionEngineOnly")
        sub = (
            f"{'Provider':<32}{'dReturn':>10}{'dMaxDD':>9}{'dTurn':>8}"
            f"{'dConc':>8}{'dActs':>7}{'Shared':>8}{'Added':>7}{'Suppr':>7}{'Refused':>9}"
        )
        lines.append(sub)
        lines.append("-" * len(sub))
        for delta in result.deltas:
            lines.append(
                f"{delta.provider_id:<32}{delta.return_delta:>9.2%}{delta.drawdown_delta:>9.2%}"
                f"{delta.turnover_delta:>8.2f}{delta.concentration_delta:>8.1%}"
                f"{delta.action_count_delta:>7}{delta.shared_actions:>8}"
                f"{delta.added_by_investor:>7}{delta.suppressed_by_investor:>7}"
                f"{delta.refused_by_engine:>9}"
            )

    lines.append("")
    lines.append("Providers are not ranked by return. A higher number over a fixture path with")
    lines.append("frictionless fills is not evidence of skill; the columns that carry information")
    lines.append("here are turnover, refusals, concentration, coverage, and abstention.")
    return "\n".join(lines)


def attribution_summary(run: ReplayRun) -> dict[str, int]:
    """Total actions by origin across the whole run."""
    totals = {origin.value: 0 for origin in ActionOrigin}
    for record in run.rounds:
        for origin, count in record.attribution.counts.items():
            totals[origin] += count
    return totals

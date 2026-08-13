"""Token usage tracking and cost attribution.

Every LLM response records a `LLMCallUsage`. The tracker aggregates them into a `RunUsage`
with a per-stage and per-advisor breakdown, which is what the UI shows after a run.

Nothing here ever touches credentials — usage records are safe to persist verbatim.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.llm.pricing import PricingTable, load_pricing


class LLMCallUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    run_id: str
    role: str = Field(description="Pipeline stage: independent, cross_examination, synthesis, ...")
    advisor_id: str | None = None
    model: str

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    latency_ms: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    def estimated_cost_usd(self, pricing: PricingTable | None = None) -> float | None:
        table = pricing or load_pricing()
        return table.cost_usd(
            self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
        )


class CostLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None


class RunUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    total_calls: int = 0
    failed_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_latency_ms: float = 0.0

    estimated_cost_usd: float | None = None
    pricing_version: str = ""
    unpriced_models: list[str] = Field(default_factory=list)

    by_stage: list[CostLine] = Field(default_factory=list)
    by_advisor: list[CostLine] = Field(default_factory=list)
    calls: list[LLMCallUsage] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (
            self.total_input_tokens
            + self.total_output_tokens
            + self.total_cache_read_tokens
            + self.total_cache_creation_tokens
        )

    def summary_line(self) -> str:
        cost = (
            f"${self.estimated_cost_usd:.4f}"
            if self.estimated_cost_usd is not None
            else "cost unknown"
        )
        return (
            f"{self.total_calls} LLM calls · {self.total_input_tokens:,} in / "
            f"{self.total_output_tokens:,} out · estimated {cost}"
        )


class UsageTracker:
    """Thread- and coroutine-safe accumulator for one run."""

    def __init__(self, run_id: str, pricing: PricingTable | None = None) -> None:
        self.run_id = run_id
        self._pricing = pricing or load_pricing()
        self._lock = threading.Lock()
        self._calls: list[LLMCallUsage] = []

    def record(self, usage: LLMCallUsage) -> None:
        with self._lock:
            self._calls.append(usage)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._calls)

    def aggregate(self) -> RunUsage:
        with self._lock:
            calls = list(self._calls)

        total = RunUsage(run_id=self.run_id, pricing_version=self._pricing.pricing_version)
        total.calls = calls
        total.total_calls = len(calls)
        total.failed_calls = sum(1 for c in calls if c.error)

        cost_sum = 0.0
        any_priced = False
        unpriced: set[str] = set()

        for c in calls:
            total.total_input_tokens += c.input_tokens
            total.total_output_tokens += c.output_tokens
            total.total_cache_read_tokens += c.cache_read_tokens
            total.total_cache_creation_tokens += c.cache_creation_tokens
            total.total_latency_ms += c.latency_ms

            cost = c.estimated_cost_usd(self._pricing)
            if cost is None:
                unpriced.add(c.model)
            else:
                cost_sum += cost
                any_priced = True

        total.estimated_cost_usd = cost_sum if any_priced else None
        total.unpriced_models = sorted(unpriced)
        total.by_stage = self._breakdown(calls, key=lambda c: c.role)
        total.by_advisor = self._breakdown(
            [c for c in calls if c.advisor_id], key=lambda c: c.advisor_id or ""
        )
        return total

    def _breakdown(self, calls: list[LLMCallUsage], key) -> list[CostLine]:  # noqa: ANN001
        buckets: dict[str, list[LLMCallUsage]] = {}
        for c in calls:
            buckets.setdefault(key(c), []).append(c)

        lines: list[CostLine] = []
        for label, group in sorted(buckets.items()):
            costs = [c.estimated_cost_usd(self._pricing) for c in group]
            priced = [c for c in costs if c is not None]
            lines.append(
                CostLine(
                    label=label,
                    calls=len(group),
                    input_tokens=sum(c.input_tokens for c in group),
                    output_tokens=sum(c.output_tokens for c in group),
                    estimated_cost_usd=sum(priced) if priced else None,
                )
            )
        return lines


def estimate_run_cost(
    model: str,
    *,
    calls: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    pricing: PricingTable | None = None,
) -> float | None:
    """Pre-run estimate. Labelled as an estimate everywhere it is displayed.

    This is a defensible token estimator, not a guess dressed as a price: the caller supplies
    measured per-call averages from the evaluation harness.
    """
    table = pricing or load_pricing()
    return table.cost_usd(
        model,
        input_tokens=calls * avg_input_tokens,
        output_tokens=calls * avg_output_tokens,
    )

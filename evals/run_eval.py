"""Evaluation harness.

Measures the things that actually distinguish this system: whether deterministic routing picks
the right advisors, whether guardrails fire, whether the personas stay distinguishable, and what
each depth mode costs.

    python evals/run_eval.py                 # mock provider, no key, no network
    python evals/run_eval.py --live          # real Anthropic calls on YOUR key (costs money)

The `--live` path reads ANTHROPIC_API_KEY *only here*, in a developer script that is not part of
the request path. Production never does this.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

from pydantic import SecretStr  # noqa: E402

from app.advisors.registry import AdvisorRegistry  # noqa: E402
from app.advisors.selection import select_committee  # noqa: E402
from app.analytics.guardrails import evaluate_guardrails  # noqa: E402
from app.analytics.portfolio_analytics import analyze_portfolio  # noqa: E402
from app.analytics.profile_analytics import analyze_profile  # noqa: E402
from app.committee.orchestrator import CommitteeError, CommitteeOrchestrator  # noqa: E402
from app.core.credentials import UserLLMCredentials  # noqa: E402
from app.core.run_context import RunContext  # noqa: E402
from app.domain.question import UserQuestion  # noqa: E402
from app.domain.report import AnalysisDepth  # noqa: E402
from app.llm.anthropic_provider import AnthropicBYOKProvider  # noqa: E402
from app.llm.mock_provider import MockLLMProvider  # noqa: E402
from evals.fixtures import EvalCase, all_cases  # noqa: E402

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "than",
    "that",
    "this",
    "these",
    "those",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "as",
    "at",
    "by",
    "from",
    "it",
    "its",
    "you",
    "your",
    "i",
    "we",
    "not",
    "no",
    "do",
    "does",
    "should",
    "would",
    "could",
    "can",
    "will",
    "may",
    "more",
    "most",
    "less",
    "any",
    "all",
    "some",
}


@dataclass
class CaseResult:
    case_id: str
    depth: str
    advisors: list[str] = field(default_factory=list)
    # Deterministic quality
    guardrail_recall: float = 0.0
    missing_guardrails: list[str] = field(default_factory=list)
    need_recall: float = 0.0
    advisor_hit: bool = False
    uncovered_dimensions: list[str] = field(default_factory=list)
    # Cost / efficiency
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    estimated_cost_usd: float | None = None
    latency_ms: float = 0.0
    advisor_context_tokens: int = 0
    # Output quality
    persona_overlap: float | None = None
    structured_output_ok: bool = False
    guardrail_violations: list[str] = field(default_factory=list)
    declined_count: int = 0
    error: str | None = None


def tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{4,}", text.lower()) if w not in STOPWORDS}


def pairwise_jaccard(texts: list[str]) -> float | None:
    """Mean pairwise lexical overlap. Lower means the personas stayed distinguishable."""
    sets = [tokenize(t) for t in texts if t.strip()]
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return None
    scores = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if union:
                scores.append(len(sets[i] & sets[j]) / len(union))
    return statistics.fmean(scores) if scores else None


async def run_case(
    case: EvalCase, depth: AnalysisDepth, provider, registry: AdvisorRegistry, credentials
) -> CaseResult:
    result = CaseResult(case_id=case.case_id, depth=depth.value)

    analytics = analyze_profile(case.profile, case.portfolio)
    pa = analyze_portfolio(case.portfolio) if case.portfolio else None
    rails = evaluate_guardrails(case.profile, analytics, case.portfolio, pa)
    intent = UserQuestion(text=case.question).classify()
    selection = select_committee(
        registry.all_manifests(), analytics.need_vector, intent, rails, depth
    )

    # --- deterministic scoring (free, no LLM) ---
    fired = {g.code for g in rails}
    if case.expected_guardrails:
        hit = case.expected_guardrails & fired
        result.guardrail_recall = len(hit) / len(case.expected_guardrails)
        result.missing_guardrails = sorted(case.expected_guardrails - fired)
    else:
        result.guardrail_recall = 1.0

    top_needs = {k for k, _ in analytics.need_vector.top(3)}
    if case.expected_top_needs:
        result.need_recall = len(case.expected_top_needs & top_needs) / len(case.expected_top_needs)
    else:
        result.need_recall = 1.0

    result.advisors = selection.advisor_ids
    result.advisor_hit = (
        bool(case.expected_any_advisor & set(selection.advisor_ids))
        if case.expected_any_advisor
        else True
    )
    result.uncovered_dimensions = selection.uncovered_dimensions
    result.advisor_context_tokens = sum(
        registry.runtime_profile(a).approx_tokens() for a in selection.advisor_ids
    )

    # --- LLM run ---
    context = RunContext.create(credentials, depth=depth)
    started = time.perf_counter()
    try:
        report = await CommitteeOrchestrator(provider, registry).run(
            profile=case.profile,
            analytics=analytics,
            portfolio_analytics=pa,
            guardrails=rails,
            selection=selection,
            question=case.question,
            context=context,
        )
        result.structured_output_ok = bool(report.summary and report.recommended_actions)
        result.guardrail_violations = report.guardrail_violations
        result.declined_count = sum(1 for a in report.analyses if a.declined)
        result.persona_overlap = pairwise_jaccard(
            [f"{a.thesis} {a.reasoning}" for a in report.analyses if not a.declined]
        )
    except CommitteeError as exc:
        result.error = str(exc)
    finally:
        result.latency_ms = (time.perf_counter() - started) * 1000
        usage = context.usage_tracker.aggregate()
        result.llm_calls = usage.total_calls
        result.input_tokens = usage.total_input_tokens
        result.output_tokens = usage.total_output_tokens
        result.cache_read_tokens = usage.total_cache_read_tokens
        result.estimated_cost_usd = usage.estimated_cost_usd

    return result


def summarize(results: list[CaseResult]) -> dict:
    by_depth: dict[str, list[CaseResult]] = {}
    for r in results:
        by_depth.setdefault(r.depth, []).append(r)

    depth_summary = {}
    for depth, rs in by_depth.items():
        overlaps = [r.persona_overlap for r in rs if r.persona_overlap is not None]
        costs = [r.estimated_cost_usd for r in rs if r.estimated_cost_usd is not None]
        depth_summary[depth] = {
            "cases": len(rs),
            "mean_llm_calls": round(statistics.fmean(r.llm_calls for r in rs), 2),
            "mean_input_tokens": round(statistics.fmean(r.input_tokens for r in rs)),
            "mean_output_tokens": round(statistics.fmean(r.output_tokens for r in rs)),
            "mean_cache_read_tokens": round(statistics.fmean(r.cache_read_tokens for r in rs)),
            "mean_estimated_cost_usd": round(statistics.fmean(costs), 5) if costs else None,
            "mean_latency_ms": round(statistics.fmean(r.latency_ms for r in rs), 1),
            "mean_advisor_context_tokens": round(
                statistics.fmean(r.advisor_context_tokens for r in rs)
            ),
            "mean_persona_overlap": round(statistics.fmean(overlaps), 4) if overlaps else None,
            "structured_output_rate": round(
                statistics.fmean(1.0 if r.structured_output_ok else 0.0 for r in rs), 3
            ),
            "runs_with_errors": sum(1 for r in rs if r.error),
        }

    return {
        "selection_accuracy": round(
            statistics.fmean(1.0 if r.advisor_hit else 0.0 for r in results), 3
        ),
        "guardrail_recall": round(statistics.fmean(r.guardrail_recall for r in results), 3),
        "need_vector_recall": round(statistics.fmean(r.need_recall for r in results), 3),
        "guardrail_violation_count": sum(len(r.guardrail_violations) for r in results),
        "by_depth": depth_summary,
    }


def print_report(results: list[CaseResult], summary: dict, live: bool) -> None:
    mode = "LIVE (real Anthropic calls)" if live else "MOCK (no network, no key)"
    print(f"\n{'=' * 78}\nAIFinancialAdvisor evaluation — {mode}\n{'=' * 78}\n")

    print("Deterministic routing quality (no LLM involved)")
    print(f"  Advisor selection accuracy : {summary['selection_accuracy']:.1%}")
    print(f"  Guardrail recall           : {summary['guardrail_recall']:.1%}")
    print(f"  Need-vector recall         : {summary['need_vector_recall']:.1%}")
    print(f"  Guardrail violations       : {summary['guardrail_violation_count']}")

    print("\nCost and quality by depth mode")
    header = (
        f"  {'depth':9s} {'calls':>6s} {'in tok':>9s} {'out tok':>8s} {'cache rd':>9s} "
        f"{'est cost':>10s} {'latency':>9s} {'ctx tok':>8s} {'overlap':>8s} {'struct':>7s}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for depth in ("quick", "balanced", "deep"):
        d = summary["by_depth"].get(depth)
        if not d:
            continue
        cost = f"${d['mean_estimated_cost_usd']:.4f}" if d["mean_estimated_cost_usd"] else "n/a"
        overlap = (
            f"{d['mean_persona_overlap']:.3f}" if d["mean_persona_overlap"] is not None else "n/a"
        )
        print(
            f"  {depth:9s} {d['mean_llm_calls']:>6.1f} {d['mean_input_tokens']:>9,} "
            f"{d['mean_output_tokens']:>8,} {d['mean_cache_read_tokens']:>9,} {cost:>10s} "
            f"{d['mean_latency_ms']:>8.0f}m {d['mean_advisor_context_tokens']:>8,} "
            f"{overlap:>8s} {d['structured_output_rate']:>7.0%}"
        )

    print("\nPer-case detail")
    for r in results:
        flag = "ERR" if r.error else ("!" if r.missing_guardrails or not r.advisor_hit else " ")
        print(
            f"  {flag} {r.case_id:28s} {r.depth:9s} "
            f"advisors={','.join(r.advisors):32s} "
            f"guardrails={r.guardrail_recall:.0%} needs={r.need_recall:.0%}"
        )
        if r.missing_guardrails:
            print(f"      missing guardrails: {', '.join(r.missing_guardrails)}")
        if r.uncovered_dimensions:
            print(f"      uncovered dimensions: {', '.join(r.uncovered_dimensions)}")
        if r.error:
            print(f"      error: {r.error}")

    if not live:
        print(
            "\nNote: token, cost, latency, and persona-overlap figures above come from the mock\n"
            "provider and characterize the pipeline's shape, not real model output. Re-run with\n"
            "--live and your own key for measured values."
        )
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AIFinancialAdvisor evaluation harness.")
    parser.add_argument(
        "--live", action="store_true", help="Use real Anthropic calls (costs money)."
    )
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--depths", default="quick,balanced,deep")
    parser.add_argument("--case", default=None, help="Run a single case by id.")
    parser.add_argument("--json", type=Path, default=None, help="Write raw results to this path.")
    args = parser.parse_args()

    if args.live:
        raw = os.environ.get("ANTHROPIC_API_KEY")
        if not raw:
            print(
                "--live requires ANTHROPIC_API_KEY in the environment. This is a developer "
                "script; the application itself never reads it.",
                file=sys.stderr,
            )
            return 2
        credentials = UserLLMCredentials(anthropic_api_key=SecretStr(raw))
        provider = AnthropicBYOKProvider()
    else:
        credentials = UserLLMCredentials(anthropic_api_key=SecretStr("sk-ant-mock-" + "0" * 40))
        provider = MockLLMProvider()

    registry = AdvisorRegistry()
    cases = [c for c in all_cases() if args.case is None or c.case_id == args.case]
    if not cases:
        print(f"No case matching '{args.case}'.", file=sys.stderr)
        return 2
    depths = [AnalysisDepth(d.strip()) for d in args.depths.split(",") if d.strip()]

    results: list[CaseResult] = []
    for case in cases:
        for depth in depths:
            results.append(await run_case(case, depth, provider, registry, credentials))

    summary = summarize(results)
    print_report(results, summary, args.live)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "live": args.live,
                    "model": args.model,
                    "summary": summary,
                    "results": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
        print(f"Wrote {args.json}")

    # Fail loudly if deterministic routing regressed — this is the CI gate.
    ok = (
        summary["selection_accuracy"] >= 0.9
        and summary["guardrail_recall"] >= 0.9
        and summary["guardrail_violation_count"] == 0
        and all(d["runs_with_errors"] == 0 for d in summary["by_depth"].values())
    )
    if not ok:
        print("EVALUATION GATE FAILED", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

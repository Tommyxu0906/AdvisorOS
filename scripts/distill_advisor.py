#!/usr/bin/env python
"""Run a real Nuwa distillation and write the manifest to disk.

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/distill_advisor.py "Warren Buffett" --advisor-id buffett_distilled --depth deep
    python scripts/distill_advisor.py "Charlie Munger" --advisor-id munger_distilled --depth deep

**This costs money.** It is the one expensive operation in the project, and the whole point of
the architecture is that it happens once per subject rather than per question. Upper bound is
`1 + research_passes + 1` calls:

    quick     2 passes   4 calls
    standard  4 passes   6 calls
    deep      7 passes   9 calls

The key is read from the environment and never written anywhere. Nothing here persists it, logs
it, or puts it in the manifest.

**On what a distilled Buffett actually gets you.** The synthesis schema produces the same ten
fields the hand-authored built-ins already carry, so this does not make the persona *deeper* — it
makes it *machine-produced*, with the research questions and findings that led to it recorded
rather than assumed. That difference matters for a subject nobody has hand-written, and it
matters for showing the process. It does not, on its own, make the advice better.

A distilled manifest lands in `backend/app/advisors/custom/` as a separate advisor. It does not
overwrite the hand-authored built-in, so both can be compared side by side — which is the only
way to tell whether the pipeline is producing anything the hand-written version did not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.advisors.registry import AdvisorRegistry  # noqa: E402
from app.core.credentials import UserLLMCredentials  # noqa: E402
from app.core.run_context import RunContext  # noqa: E402
from app.llm.anthropic_provider import AnthropicBYOKProvider  # noqa: E402
from app.nuwa.distiller import (  # noqa: E402
    DistillationDepth,
    DistillationRequest,
    NuwaDistiller,
)


async def run(args) -> int:
    raw_key = os.environ.get("ANTHROPIC_API_KEY")
    if not raw_key:
        print(
            "ANTHROPIC_API_KEY is not set.\n\n"
            "Distillation is the one operation that genuinely needs a key — it runs a planner,\n"
            "several research passes, and a synthesis call against Anthropic.\n\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            f"  python scripts/distill_advisor.py {args.subject!r} --depth {args.depth}\n",
            file=sys.stderr,
        )
        return 2

    depth = DistillationDepth(args.depth)
    credentials = UserLLMCredentials(anthropic_api_key=raw_key)
    context = RunContext.create(credentials, model=args.model)

    registry = AdvisorRegistry()
    distiller = NuwaDistiller(provider=AnthropicBYOKProvider(), registry=registry)

    request = DistillationRequest(
        subject=args.subject,
        focus_areas=args.focus or [],
        depth=depth,
        advisor_id=args.advisor_id,
    )

    print(f"subject     {args.subject}")
    print(f"advisor_id  {args.advisor_id or '(derived from subject)'}")
    print(f"depth       {depth.value} — up to {depth.expected_calls()} calls")
    print(f"model       {args.model}")
    print(f"focus       {', '.join(args.focus) if args.focus else '(general)'}")
    print("\nrunning — planner, research passes, then synthesis...\n")

    result = await distiller.distill(request, context, register=not args.dry_run)

    manifest = result.manifest
    print("=" * 72)
    print(f"{manifest.display_name}  ({manifest.advisor_id})")
    print("=" * 72)
    print(f"one_line            {manifest.one_line}")
    print(f"research passes     {result.research_pass_count}")
    print(f"runtime tokens      {result.runtime_profile_tokens}")
    for field in (
        "mental_models",
        "heuristics",
        "reasoning_rules",
        "blind_spots",
        "honest_boundaries",
        "evidence",
    ):
        print(f"{field:<20}{len(getattr(manifest, field))}")
    print(f"expertise           {json.dumps(manifest.expertise.model_dump())}")

    if result.warnings:
        print("\nwarnings:")
        for warning in result.warnings:
            print(f"  ! {warning}")

    usage = context.usage_tracker.aggregate()
    print(f"\ncost                ${usage.estimated_cost_usd:.4f} on your key")
    print(f"calls               {usage.call_count}")

    if args.dry_run:
        print("\n--dry-run: nothing was written to disk")
    else:
        path = ROOT / "backend/app/advisors/custom" / manifest.advisor_id / "manifest.json"
        print(f"\nwrote               {path.relative_to(ROOT)}")
        print("\nThis is a *custom* advisor and sits beside the hand-authored built-in rather")
        print("than replacing it, so the two can be compared.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject", help='e.g. "Warren Buffett"')
    parser.add_argument("--advisor-id", help="lowercase id, e.g. buffett_distilled")
    parser.add_argument("--depth", choices=[d.value for d in DistillationDepth], default="standard")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--focus", nargs="*", help="focus areas, e.g. valuation concentration")
    parser.add_argument("--dry-run", action="store_true", help="run but write nothing")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

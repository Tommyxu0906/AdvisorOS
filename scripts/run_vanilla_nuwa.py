#!/usr/bin/env python
"""Model Phase 1: does a distilled philosophy beat ordinary portfolio state?

    python scripts/run_vanilla_nuwa.py --provider mock      # no key, no network
    python scripts/run_vanilla_nuwa.py --provider anthropic # needs ANTHROPIC_API_KEY

Validation only. The held-out set was opened once for the quant baselines and is now closed
again: prompt wording, output schema, inference settings and the abstention threshold are all
selected here, and the config is hashed before any official held-out run.

The comparison is against the already-frozen quant baselines on exactly the same incumbent
population, the same split, and the same public-observer information boundary. The quant models
are refitted from their frozen configs rather than re-selected — changing them after seeing a
persona result would make the ladder meaningless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.credentials import UserLLMCredentials  # noqa: E402
from app.core.run_context import ModelConfig, RunContext  # noqa: E402
from app.distillation.finance_nuwa import disagreement  # noqa: E402
from app.distillation.finance_nuwa.access import (  # noqa: E402
    manifest_of,
    refinement_dataset,
    validation_dataset,
)
from app.distillation.finance_nuwa.baselines import (  # noqa: E402
    GradientBoostedTrees,
    MultinomialLogistic,
    encode,
)
from app.distillation.finance_nuwa.evaluation import calibration, evaluate  # noqa: E402
from app.distillation.finance_nuwa.prediction import PREDICTION_SCHEMA, PredictionSet  # noqa: E402
from app.distillation.finance_nuwa.task import TASK, is_incumbent  # noqa: E402
from app.distillation.finance_nuwa.vanilla_nuwa import (  # noqa: E402
    RunConfig,
    build_prompt,
    inputs_from_row,
    parse_prediction,
    prompt_hash,
)
from app.domain.advisor import AdvisorManifest  # noqa: E402
from app.llm.mock_provider import MockLLMProvider  # noqa: E402
from app.llm.provider import Message  # noqa: E402

NATURAL = "berkshire-v2.0-natural"
MANIFEST = "config/nuwa/berkshire_public_equity.manifest.json"
RESULT_NAME = "Berkshire Behavioral Reconstruction"

# Frozen in the baseline pass. Refitted here, never re-selected.
QUANT_CONFIGS = "data/berkshire/baselines/model_configs.json"


def load_manifest(path: Path) -> AdvisorManifest:
    """Underscore-prefixed keys are commentary for a human reader, not manifest fields.

    They carry the contamination caveat and the reason this is not the built-in `buffett`
    persona, so they belong in the file rather than in a commit message nobody reads later.
    """
    payload = json.loads(path.read_text())
    return AdvisorManifest.model_validate(
        {k: v for k, v in payload.items() if not k.startswith("_")}
    )


async def predict_all(provider, profile, rows, *, config: ModelConfig, concurrency: int = 8):
    """One call per episode, bounded. No episode sees another episode's answer."""
    # The mock provider never reaches the network, but the interface still demands a
    # RunContext — there is no ambient-credential path anywhere in this codebase, and the
    # benchmark exercises the same call path the real provider does.
    import os

    context = RunContext.create(
        UserLLMCredentials(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or f"sk-ant-{'mock' * 12}"
        ),
        model_config=config,
    )
    semaphore = asyncio.Semaphore(concurrency)
    stable, _ = build_prompt(profile, inputs_from_row(rows[0]))

    async def one(row):
        _, user = build_prompt(profile, inputs_from_row(row))
        async with semaphore:
            response = await provider.generate(
                [Message(role="user", content=user)],
                context,
                stable_system=stable,
                role="behavioral_prediction",
                advisor_id=profile.advisor_id,
                schema=PREDICTION_SCHEMA,
                max_tokens=config.max_tokens,
            )
        return parse_prediction(row.episode_id, response.parsed, response.text)

    predictions = await asyncio.gather(*(one(row) for row in rows))
    return PredictionSet(predictions=list(predictions)), context


def quant_predictions(name: str, params: dict, feature_set: str, train_rows, rows):
    model_class = MultinomialLogistic if name == "logistic" else GradientBoostedTrees
    model = model_class(**params).fit(encode(train_rows, feature_set))
    prediction = model.predict(encode(rows, feature_set))
    return dict(zip(prediction.episode_ids, prediction.predicted, strict=True))


def main() -> int:  # noqa: PLR0915 — one linear protocol; splitting it would hide the order
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/berkshire/dataset")
    parser.add_argument("--out", default="data/berkshire/vanilla_nuwa")
    parser.add_argument("--provider", choices=("mock", "anthropic"), default="mock")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--limit", type=int, default=0, help="0 = every validation episode")
    parser.add_argument("--abstention-threshold", type=float, default=0.0)
    args = parser.parse_args()

    root, out = Path(args.root), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    natural = root / NATURAL

    manifest = load_manifest(Path(MANIFEST))
    profile = manifest.to_runtime_profile()

    # The primary task, frozen in task.py before any of this was run.
    train = [r for r in refinement_dataset(natural) if is_incumbent(r.features)]
    validation = [r for r in validation_dataset(natural) if is_incumbent(r.features)]
    if args.limit:
        validation = validation[: args.limit]

    print(f"{RESULT_NAME} — Vanilla Nuwa benchmark (validation only)")
    print(f"  task          {TASK.version}  classes {list(TASK.classes)}")
    print(f"  population    {TASK.population}")
    print(f"  excluded      {TASK.excluded}")
    print(f"  dataset       {NATURAL} {manifest_of(natural).artifact_sha256[:12]}")
    print(f"  train {len(train)}   validation {len(validation)}")
    print(f"  framework     {profile.display_name}")
    print(f"  provider      {args.provider}")

    if args.provider == "anthropic":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "\nANTHROPIC_API_KEY is not set. This benchmark is BYOK like the rest of the "
                "project: export your own key in this shell and re-run. Nothing here reads a "
                "key from a file or a service.",
                file=sys.stderr,
            )
            return 2
        from app.llm.anthropic_provider import AnthropicBYOKProvider

        provider = AnthropicBYOKProvider()
    else:
        provider = MockLLMProvider()

    model_config = ModelConfig(model=args.model, effort=args.effort, max_tokens=1024)
    predictions, context = asyncio.run(
        predict_all(provider, profile, validation, config=model_config)
    )

    # Selected on validation, and recorded so it cannot be quietly changed later.
    if args.abstention_threshold > 0:
        predictions = PredictionSet(
            predictions=[
                p.model_copy(update={"abstain": True, "action": None})
                if p.answered and p.confidence < args.abstention_threshold
                else p
                for p in predictions.predictions
            ]
        )

    truth = {r.episode_id: r.observed_action for r in validation}
    features = {r.episode_id: r.features for r in validation}
    actual, predicted = predictions.aligned(truth)

    print("\n" + "=" * 78)
    print("VANILLA NUWA — validation")
    print("=" * 78)
    print(
        f"  coverage {predictions.coverage:.1%}"
        f"   abstained {predictions.abstention_rate:.1%}"
        f"   parse failures {predictions.parse_failure_rate:.1%}"
    )
    if not actual:
        print("  answered nothing — no score to report.")
        return 1

    score = evaluate(
        actual,
        predicted,
        model_name="vanilla_nuwa",
        view=NATURAL,
        split="validation/incumbent",
    )
    print("\n" + score.render())
    print(score.render_confusion())
    print("\n  Scores above are conditional on answering — coverage is reported beside them and")
    print("  never folded into them.")

    # Self-reported confidence, treated as a raw score until a curve says otherwise.
    answered = predictions.answered
    if answered:
        pseudo = [
            {p.label: p.confidence, **{c: 0.0 for c in TASK.classes if c != p.label}}
            for p in sorted(answered, key=lambda p: p.episode_id)
        ]
        curve = calibration(actual, pseudo, view=NATURAL)
        print("\n  Self-reported confidence — NOT a calibrated probability until this curve says")
        print(f"  it is. ECE {curve.expected_calibration_error:.4f}")
        print(f"    {'confidence':<14}{'n':>6}{'mean conf':>12}{'accuracy':>12}{'gap':>10}")
        for b in curve.bins:
            print(
                f"    {f'{b.low:.1f}-{b.high:.1f}':<14}{b.count:>6}"
                f"{b.mean_confidence:>12.3f}{b.accuracy:>12.3f}{b.gap:>10.3f}"
            )

    # --- the ladder ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("THE LADDER — same population, same split, same information boundary")
    print("=" * 78)
    answered_ids = {p.episode_id for p in answered}
    answered_rows = [r for r in validation if r.episode_id in answered_ids]

    ladder = [("always_hold", None)]
    configs = json.loads(Path(QUANT_CONFIGS).read_text()) if Path(QUANT_CONFIGS).exists() else {}
    for _key, entry in sorted(configs.items()):
        if entry["config"].get("view") == NATURAL:
            ladder.append((entry["config"]["model"], entry["config"]))

    print(f"  {'model':<20}{'macro F1':>10}{'bal acc':>10}{'n':>7}  note")
    rows_out = []
    for name, config in ladder:
        if config is None:
            quant = dict.fromkeys(answered_ids, "hold")
        else:
            quant = quant_predictions(
                name, config["params"], config["feature_set"], train, answered_rows
            )
        quant_actual = [truth[i] for i in sorted(answered_ids)]
        quant_predicted = [quant[i] for i in sorted(answered_ids)]
        quant_score = evaluate(
            quant_actual,
            quant_predicted,
            model_name=name,
            view=NATURAL,
            split="validation/incumbent",
            with_intervals=False,
        )
        print(
            f"  {name:<20}{quant_score.macro_f1:>10.3f}{quant_score.balanced_accuracy:>10.3f}"
            f"{quant_score.n:>7}  refitted from its frozen config, never re-selected"
        )
        rows_out.append({"model": name, **quant_score.model_dump(mode="json")})

    print(
        f"  {'vanilla_nuwa':<20}{score.macro_f1:>10.3f}{score.balanced_accuracy:>10.3f}"
        f"{score.n:>7}  on the episodes it chose to answer"
    )

    # --- does it know something different? -------------------------------------------
    best_quant = max(
        (e for e in configs.values() if e["config"].get("view") == NATURAL),
        key=lambda e: e["validation_macro_f1"],
        default=None,
    )
    if best_quant:
        quant = quant_predictions(
            best_quant["config"]["model"],
            best_quant["config"]["params"],
            best_quant["config"]["feature_set"],
            train,
            validation,
        )
        report = disagreement.analyse(truth, quant, predictions, features)
        print("\n" + "=" * 78)
        print(report.render())
        (out / "disagreement.json").write_text(report.model_dump_json(indent=2) + "\n")

    # --- freeze what produced this ----------------------------------------------------
    run_config = RunConfig(
        prompt_sha256=prompt_hash(profile),
        manifest_sha256=__import__("hashlib")
        .sha256(Path(MANIFEST).read_bytes())
        .hexdigest(),
        model=args.model,
        effort=args.effort,
        dataset_version=NATURAL,
        dataset_sha256=manifest_of(natural).artifact_sha256,
        abstention_threshold=args.abstention_threshold,
    )
    print("\n" + "=" * 78)
    print("FROZEN FOR A LATER HELD-OUT RUN")
    print("=" * 78)
    print(f"  prompt   {run_config.prompt_sha256[:12]}")
    print(f"  manifest {run_config.manifest_sha256[:12]}")
    print(f"  config   {run_config.config_sha256()[:12]}")
    print(f"  provider {args.provider}  model {args.model}  effort {args.effort}")
    if args.provider == "mock":
        print(
            "\n  NOTE: the mock provider answers deterministically and knows nothing about "
            "investing.\n  These numbers exercise the harness. They are not a result."
        )

    (out / "run_config.json").write_text(run_config.model_dump_json(indent=2) + "\n")
    (out / "predictions.json").write_text(predictions.model_dump_json(indent=2) + "\n")
    (out / "validation.json").write_text(
        json.dumps(
            {
                "result_name": RESULT_NAME,
                "task": TASK.model_dump(mode="json"),
                "coverage": predictions.coverage,
                "abstention_rate": predictions.abstention_rate,
                "parse_failure_rate": predictions.parse_failure_rate,
                "vanilla_nuwa": score.model_dump(mode="json"),
                "ladder": rows_out,
                "usage": context.usage_tracker.aggregate().summary_line(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n==> wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

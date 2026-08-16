#!/usr/bin/env python
"""Model Phase 0: how predictable is Berkshire behaviour from ordinary portfolio state?

    python scripts/run_baselines.py

Runs four cheap, deterministic baselines against the frozen views and reports them under the
evaluation protocol. No persona, no language model, no retrieval — that is the point. Every later
claim about distillation has to be an increment over whatever a model that has never heard of
Buffett already achieves from position size, holding duration and recent price action.

The workflow is fixed and enforced by the order of the code:

    train        fit
    validation   select hyperparameters and the feature set
    freeze       hash the config
    held out     one evaluation, once, after the hash is written

Held-out is opened at the end and never consulted before. If a config hash already exists for a
model, this refuses to overwrite it with a different one, for the same reason the dataset refuses:
a selection made after seeing the answer is not a selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.distillation.finance_nuwa.access import (  # noqa: E402
    manifest_of,
    refinement_dataset,
    validation_dataset,
)
from app.distillation.finance_nuwa.artifact import EPISODES_FILE, EpisodeRow, verify  # noqa: E402
from app.distillation.finance_nuwa.baselines import (  # noqa: E402
    FEATURE_SETS,
    AlwaysHold,
    ClassPrior,
    GradientBoostedTrees,
    MultinomialLogistic,
    encode,
)
from app.distillation.finance_nuwa.evaluation import (  # noqa: E402
    compare_information_sets,
    evaluate,
)

# The behavioural target is the filing entity. A held-out score here is Berkshire Behavioral
# Fidelity — how well a model reconstructs what the entity did — and never "Buffett accuracy".
# 13F does not say who chose a trade, so calling it that would assert an attribution the source
# cannot support, and it would be the same circularity the dataset was built to avoid.
RESULT_NAME = "Berkshire Behavioral Fidelity"

MATCHED = "berkshire-v2.0"
NATURAL = "berkshire-v2.0-natural"
ORACLE = "berkshire-v2.0-oracle"

# Small grids. The point of this pass is a floor to measure against, not a tuned model, and a
# wide search on 253 validation episodes would mostly be fitting the validation set.
# `balanced` is in the grid rather than fixed. Unweighted, a softmax fit at 69% hold prevalence
# degenerates to the constant baseline, and reporting that as "what a quant model achieves" would
# be constructing a strawman for a persona to beat later.
LOGISTIC_GRID = [
    {"learning_rate": 0.5, "iterations": 300, "l2": 0.01, "balanced": b} for b in (True, False)
] + [{"learning_rate": 1.0, "iterations": 600, "l2": 0.1, "balanced": True}]
BOOSTING_GRID = [
    {"rounds": 40, "learning_rate": 0.3, "max_depth": 2, "min_leaf": 20, "balanced": b}
    for b in (True, False)
] + [
    {"rounds": 80, "learning_rate": 0.2, "max_depth": 3, "min_leaf": 10, "balanced": True},
]


def is_incumbent(row: EpisodeRow) -> bool:
    """Whether an observer at the cutoff could see this position at all.

    This is the line the headline metric is reported on, and the reason is a structural artifact
    rather than a preference. All 85 ENTER episodes have every position feature missing, and no
    ENTER episode has them present — because a security opened this quarter was, by definition,
    not in the last book anyone could read. A classifier that answers ENTER whenever the position
    features are absent therefore gets recall 1.000 on that class without learning anything about
    investing, and it lifts macro F1 by roughly a fifth of one class for free.

    The deeper problem is that the ENTER task as posed is not deployable in the first place. The
    row exists because the security turned up in the current filing; an observer standing at the
    cutoff does not know which of the thousands of securities in existence to ask about. Scoring
    "guess ENTER, having been told Berkshire did something here" is close to tautological.

    So results are decomposed rather than blended: incumbent positions are the deployable
    question, and the new-to-book rows are reported beside them with this caveat attached.
    """
    return row.features.get("weight") is not None


def held_out_rows(directory: Path) -> list[EpisodeRow]:
    """Held-out *inputs*, deliberately not via `held_out_dataset`.

    That function pairs rows with outcomes and is the path for scoring a frozen persona against
    what happened next. This pass needs only the observed action, which is the label rather than
    an outcome, so it reads the rows directly and never opens the outcomes file at all.
    """
    ok, detail = verify(directory)
    if not ok:
        raise ValueError(f"refusing to read an unverified dataset: {detail}")
    rows = [
        EpisodeRow.model_validate_json(line)
        for line in (directory / EPISODES_FILE).read_text().splitlines()
        if line
    ]
    return [r for r in rows if r.split == "held_out"]


def config_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def select_on_validation(model_class, grid, train_rows, validation_rows, *, feature_set, view):
    """Fit each candidate on train, score on validation, keep the best macro F1.

    Macro F1 rather than accuracy, because at 69% hold prevalence accuracy would select the
    model that trades least — and "trades least" is the constant baseline wearing a coefficient
    vector.
    """
    train_design = encode(train_rows, feature_set)
    validation_design = encode(validation_rows, feature_set)
    results = []
    for params in grid:
        model = model_class(**params).fit(train_design)
        prediction = model.predict(validation_design)
        score = evaluate(
            validation_design.labels,
            prediction.predicted,
            model_name=model.name,
            view=view,
            split="validation",
            with_intervals=False,
        )
        results.append((score.macro_f1, score.balanced_accuracy, params, model))
    results.sort(key=lambda r: (-r[0], json.dumps(r[2], sort_keys=True)))
    return results[0], results


def main() -> int:  # noqa: PLR0915 — the protocol is an order, and hiding it in helpers hides it
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/berkshire/dataset")
    parser.add_argument("--out", default="data/berkshire/baselines")
    args = parser.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    natural, matched, oracle = root / NATURAL, root / MATCHED, root / ORACLE
    for directory in (natural, matched, oracle):
        ok, detail = verify(directory)
        if not ok:
            print(f"REFUSING: {directory.name} — {detail}", file=sys.stderr)
            return 2

    train = refinement_dataset(natural)
    validation = validation_dataset(natural)
    print(f"{RESULT_NAME} — Model Phase 0 baselines")
    print(
        f"  natural view {manifest_of(natural).artifact_sha256[:12]}  "
        f"train {len(train)}  validation {len(validation)}"
    )
    print(f"  matched view {manifest_of(matched).artifact_sha256[:12]}")
    print(f"  oracle view  {manifest_of(oracle).artifact_sha256[:12]}")

    report: dict = {"result_name": RESULT_NAME, "views": {}, "selection": {}, "held_out": {}}

    # --- Baselines A and B, on both views ------------------------------------------------
    print("\n" + "=" * 78)
    print("BASELINES A/B — no features at all")
    print("=" * 78)
    trivial_section = []
    for view_name, directory in ((NATURAL, natural), (MATCHED, matched)):
        view_train = refinement_dataset(directory)
        view_validation = validation_dataset(directory)
        train_design = encode(view_train, "position")
        validation_design = encode(view_validation, "position")
        for model in (AlwaysHold(), ClassPrior()):
            model.fit(train_design)
            prediction = model.predict(validation_design)
            score = evaluate(
                validation_design.labels,
                prediction.predicted,
                probabilities=prediction.probabilities,
                model_name=model.name,
                view=view_name,
                split="validation",
            )
            print("\n" + score.render())
            if score.calibration:
                print(
                    f"  log loss {score.calibration.log_loss:.4f}"
                    f"   Brier {score.calibration.brier:.4f}"
                    f"   ({'deployable prior' if score.calibration.is_deployable_prior else 'MATCHED PRIOR — not a real-world probability'})"
                )
            trivial_section.append(score.model_dump(mode="json"))
    report["views"]["trivial"] = trivial_section

    # --- Baselines C and D, with the feature ablation -------------------------------------
    # Run independently per view. The matched view asks whether a real hold can be told from a
    # real trade under similar conditions; the natural view asks what happens at the prevalence
    # that actually occurs. Fitting once and reporting on both would answer neither cleanly.
    print("\n" + "=" * 78)
    print("BASELINES C/D — feature ablation, selected on validation only")
    print("=" * 78)
    ablation = []
    best: dict[tuple[str, str], tuple] = {}
    for view_name, directory in ((NATURAL, natural), (MATCHED, matched)):
        view_train = refinement_dataset(directory)
        view_validation = validation_dataset(directory)
        print(f"\n--- {view_name}  (train {len(view_train)}, validation {len(view_validation)})")
        for model_class, grid in (
            (MultinomialLogistic, LOGISTIC_GRID),
            (GradientBoostedTrees, BOOSTING_GRID),
        ):
            print(f"\n{model_class.name}")
            print(f"  {'feature set':<26}{'val macro F1':>14}{'val bal acc':>13}  config")
            for feature_set in FEATURE_SETS:
                (score, balanced_acc, params, _), _ = select_on_validation(
                    model_class,
                    grid,
                    view_train,
                    view_validation,
                    feature_set=feature_set,
                    view=view_name,
                )
                print(
                    f"  {feature_set:<26}{score:>14.3f}{balanced_acc:>13.3f}  "
                    f"{json.dumps(params, sort_keys=True)}"
                )
                ablation.append(
                    {
                        "view": view_name,
                        "model": model_class.name,
                        "feature_set": feature_set,
                        "validation_macro_f1": score,
                        "validation_balanced_accuracy": balanced_acc,
                        "params": params,
                    }
                )
                key = (view_name, model_class.name)
                if key not in best or score > best[key][0]:
                    best[key] = (score, feature_set, params)
    report["selection"]["ablation"] = ablation

    # --- Freeze the configs before anything touches held-out -------------------------------
    print("\n" + "=" * 78)
    print("FREEZING MODEL CONFIGS")
    print("=" * 78)
    frozen: dict[str, dict] = {}
    for (view_name, name), (score, feature_set, params) in sorted(best.items()):
        payload = {
            "model": name,
            "view": view_name,
            "feature_set": feature_set,
            "params": params,
            "dataset_sha256": manifest_of(root / view_name).artifact_sha256,
            "feature_schema_version": manifest_of(root / view_name).feature_schema_version,
        }
        digest = config_hash(payload)
        frozen[f"{view_name}::{name}"] = {
            "config": payload,
            "config_sha256": digest,
            "validation_macro_f1": score,
        }
        print(f"  {view_name:<26}{name:<16}{feature_set:<26} val {score:.3f}  {digest[:12]}")

    config_path = out / "model_configs.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text())
        changed = [
            n
            for n in frozen
            if n in existing and existing[n]["config_sha256"] != frozen[n]["config_sha256"]
        ]
        if changed:
            print(
                f"\nREFUSING: {changed} already have a frozen config with a different hash. "
                "A configuration chosen after seeing a held-out result is not a selection; "
                "delete the file deliberately if the earlier run is being abandoned.",
                file=sys.stderr,
            )
            return 3
    config_path.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    report["selection"]["frozen_configs"] = frozen

    # --- Held out. Once. -------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("HELD OUT — one evaluation, after the configs above were hashed")
    print("=" * 78)

    # The floor, on the same rows the models are about to be scored on. Reported here rather
    # than only on validation because "0.267 macro F1" means nothing without the number a
    # constant achieves on exactly these episodes.
    for view_name, directory in ((NATURAL, natural), (MATCHED, matched)):
        rows = held_out_rows(directory)
        train_design = encode(refinement_dataset(directory), "position")
        for model in (AlwaysHold(), ClassPrior()):
            model.fit(train_design)
            for split_name, subset in (
                ("held_out", rows),
                ("held_out/incumbent", [r for r in rows if is_incumbent(r)]),
            ):
                design = encode(subset, "position")
                prediction = model.predict(design)
                score = evaluate(
                    design.labels,
                    prediction.predicted,
                    probabilities=prediction.probabilities,
                    model_name=model.name,
                    view=view_name,
                    split=split_name,
                )
                print("\n" + score.render())
                if score.calibration:
                    print(
                        f"  log loss {score.calibration.log_loss:.4f}"
                        f"   Brier {score.calibration.brier:.4f}"
                    )
                report["held_out"].setdefault(view_name, {}).setdefault(model.name, {})[
                    split_name
                ] = score.model_dump(mode="json")

    for key, entry in sorted(frozen.items()):
        view_name, name = key.split("::")
        directory = root / view_name
        feature_set = entry["config"]["feature_set"]
        params = entry["config"]["params"]
        model_class = MultinomialLogistic if name == "logistic" else GradientBoostedTrees
        model = model_class(**params).fit(encode(refinement_dataset(directory), feature_set))

        rows = held_out_rows(directory)
        # Three reports, not one. The split is on whether the position was visible at the cutoff,
        # which is a property of the data rather than of the model — see `is_incumbent`. The
        # frozen configs above are untouched: decomposing a report is not re-selecting a model,
        # and re-selecting one now would be selection informed by a held-out result.
        subsets = [
            ("held_out", rows),
            ("held_out/incumbent", [r for r in rows if is_incumbent(r)]),
            ("held_out/new_to_book", [r for r in rows if not is_incumbent(r)]),
        ]
        for split_name, subset in subsets[1:]:
            if not subset:
                continue
            subset_design = encode(subset, feature_set)
            subset_prediction = model.predict(subset_design)
            subset_score = evaluate(
                subset_design.labels,
                subset_prediction.predicted,
                probabilities=subset_prediction.probabilities,
                model_name=name,
                view=view_name,
                split=split_name,
            )
            print("\n" + subset_score.render())
            if split_name.endswith("new_to_book"):
                print(
                    "  NOT A DEPLOYABLE TASK: every row here is a position the observer could "
                    "not see at the cutoff, and the candidate set already implies something "
                    "happened. ENTER recall near 1.0 is row construction, not prediction."
                )
            report["held_out"].setdefault(view_name, {}).setdefault(name, {})[split_name] = (
                subset_score.model_dump(mode="json")
            )

        design = encode(rows, feature_set)
        prediction = model.predict(design)
        score = evaluate(
            design.labels,
            prediction.predicted,
            probabilities=prediction.probabilities,
            model_name=name,
            view=view_name,
            split="held_out",
        )
        print("\n" + score.render())
        print(score.render_confusion())
        if score.calibration:
            deployable = (
                "deployable prior"
                if score.calibration.is_deployable_prior
                else "MATCHED PRIOR — not a real-world probability"
            )
            print(f"\n  calibration ({deployable})")
            print(
                f"    log loss {score.calibration.log_loss:.4f}"
                f"   Brier {score.calibration.brier:.4f}"
                f"   ECE {score.calibration.expected_calibration_error:.4f}"
            )
            print(f"    {'confidence':<14}{'n':>6}{'mean conf':>12}{'accuracy':>12}{'gap':>10}")
            for b in score.calibration.bins:
                print(
                    f"    {f'{b.low:.1f}-{b.high:.1f}':<14}{b.count:>6}"
                    f"{b.mean_confidence:>12.3f}{b.accuracy:>12.3f}{b.gap:>10.3f}"
                )
        report["held_out"].setdefault(view_name, {}).setdefault(name, {})["held_out"] = (
            score.model_dump(mode="json")
        )

    # --- The same episodes under two information sets --------------------------------------
    print("\n" + "=" * 78)
    print("PUBLIC OBSERVER vs ORACLE OWN BOOK — same episodes, same architecture")
    print("=" * 78)
    natural_held_out = held_out_rows(natural)
    oracle_held_out = held_out_rows(oracle)
    truth = {r.episode_id: r.observed_action for r in natural_held_out}

    for key, entry in sorted(frozen.items()):
        view_name, name = key.split("::")
        if view_name != NATURAL:
            continue
        feature_set = entry["config"]["feature_set"]
        params = entry["config"]["params"]
        model_class = MultinomialLogistic if name == "logistic" else GradientBoostedTrees

        public_model = model_class(**params).fit(encode(refinement_dataset(natural), feature_set))
        oracle_model = model_class(**params).fit(encode(refinement_dataset(oracle), feature_set))
        public_prediction = public_model.predict(encode(natural_held_out, feature_set))
        oracle_prediction = oracle_model.predict(encode(oracle_held_out, feature_set))

        paired = compare_information_sets(
            truth,
            dict(zip(public_prediction.episode_ids, public_prediction.predicted, strict=True)),
            dict(zip(oracle_prediction.episode_ids, oracle_prediction.predicted, strict=True)),
            model_name=name,
            split="held_out",
        )
        print("\n" + paired.render())
        report["held_out"].setdefault("paired", {})[name] = paired.model_dump(mode="json")

    (out / "baseline_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\n==> wrote {out / 'baseline_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

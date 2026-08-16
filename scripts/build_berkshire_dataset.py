#!/usr/bin/env python
"""Build the episodes, freeze them, and audit what was frozen.

    python scripts/build_berkshire_dataset.py

Reads only from the immutable raw layer, so it is reproducible offline and a parser change costs
nothing at SEC. Runs the full chain in order — filing lineage, corporate actions, classification,
matched hold controls, point-in-time features — then writes an immutable, hashed artifact and
prints the audit that gates modelling.

The artifact is the point of this script now. A deterministic rebuild is not a frozen dataset: it
reproduces whatever the pipeline currently computes, so the same version name quietly comes to
mean a different set of rows the moment anything upstream changes. `artifact.freeze` refuses that.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.distillation.finance_nuwa.artifact import (  # noqa: E402
    ArtifactManifest,
    DatasetFrozenError,
    EpisodeRow,
    MatchedControl,
    WithheldRow,
    freeze,
    sha256_of_file,
    verify,
)
from app.distillation.finance_nuwa.audit import DatasetAudit  # noqa: E402
from app.distillation.finance_nuwa.builder import (  # noqa: E402
    BUILDER_VERSION,
    ReplayView,
    build_episode,
    classify_quarter_pair,
    measure_label_change,
)
from app.distillation.finance_nuwa.corporate_actions import (  # noqa: E402
    CorporateActionKind,
    LineageTable,
    apply_lineage,
    blocked_securities,
    detect_candidates,
    load_curated,
    split_factors_for,
    unresolved_blocking,
)
from app.distillation.finance_nuwa.dataset import (  # noqa: E402
    EpisodeDataset,
    score_hold,
    select_matched_holds,
    stratum_for,
)
from app.distillation.finance_nuwa.drift import SHARE_TOLERANCE, ObservedAction  # noqa: E402
from app.distillation.finance_nuwa.features import (  # noqa: E402
    FEATURE_SCHEMA_VERSION,
    build_features,
    regime_bucket,
    regime_features,
)
from app.distillation.finance_nuwa.identity import SecurityKey  # noqa: E402
from app.distillation.finance_nuwa.lineage import (  # noqa: E402
    CanonicalQuarter,
    PublicQuarterView,
    compose_quarter,
)
from app.distillation.finance_nuwa.quarantine import (  # noqa: E402
    ExclusionRegistry,
    quarantine_unresolved,
)
from app.distillation.finance_nuwa.sec_13f import (  # noqa: E402
    PARSER_VERSION,
    parse_information_table,
)
from app.distillation.finance_nuwa.store import FilingRef, QuarterLineage  # noqa: E402
from app.distillation.finance_nuwa.tolerance import (  # noqa: E402
    TOLERANCE_SWEEP,
    ToleranceReport,
    ToleranceRow,
    compare_to_zero,
)

ENTITY = "Berkshire Hathaway Inc"
# The subject of this dataset is the filing entity, not a person. 13F reports net holdings
# changes for Berkshire; it never says whether Buffett, Combs, Weschler or anyone else chose a
# given trade. Labelling these "buffett" would assert an attribution the source cannot support,
# and a Buffett-attributed subset must later be built from independent evidence — letters,
# interviews, annual-meeting statements — never from position size, otherManager, or a trade
# looking Buffett-like, all of which would make the evaluation circular.
TARGET = "berkshire_public_equity"
CIK = "1067983"
TRAIN_END = date(2019, 12, 31)
VALIDATION_END = date(2021, 12, 31)

# v2.0 rather than v1.1, and the reason is that nothing about v1.0 survives row for row. Episode
# ids changed with the identity schema, the row set changed once quarantine began withholding,
# and feature values changed when the point-in-time boundary moved to filter positions rather
# than whole quarters. The two cannot be joined or diffed meaningfully, which is what a major
# version is for. v1.0 keeps whatever it meant.
DATASET_VERSION = "berkshire-v2.0"


def quarter_start(period_end: date) -> date:
    return date(period_end.year, ((period_end.month - 1) // 3) * 3 + 1, 1)


def load_quarters(
    root: Path, source_manifest: Path
) -> tuple[list[CanonicalQuarter], dict, int, int]:
    """Canonical quarters, their original filing dates, raw row count and unit conflicts."""
    manifest = json.loads(source_manifest.read_text())
    raw = root / "raw"
    quarters: list[CanonicalQuarter] = []
    filed_at: dict[date, date] = {}
    raw_rows = unit_conflicts = 0
    amendments = quarters_amended = 0

    for entry in manifest["lineage"]:
        refs = [
            FilingRef(**{k: v for k, v in f.items() if not k.startswith("_")})
            for f in entry["filings"]
        ]
        lineage = QuarterLineage(
            period_end=date.fromisoformat(entry["period_end"]),
            filings=refs,
            canonical_accession=entry["canonical_accession"],
            reason=entry["reason"],
            needs_review=entry["needs_review"],
        )
        snapshots = {}
        for ref in refs:
            path = raw / ref.accession / "information_table.xml"
            if not path.exists():
                continue
            snapshot = parse_information_table(
                path.read_text(errors="replace"),
                entity=ENTITY,
                cik=CIK,
                accession=ref.accession,
                period_end=ref.period_end,
                filed_at=ref.filed_at,
                form_type=ref.form_type,
            )
            snapshots[ref.accession] = snapshot
            raw_rows += sum(p.row_count for p in snapshot.positions)
            unit_conflicts += 1 if snapshot.needs_review else 0

        canonical = compose_quarter(lineage, snapshots)
        if not canonical.is_usable:
            continue
        quarters.append(canonical)
        filed_at[canonical.period_end] = max(r.filed_at for r in refs if not r.is_amendment)
        amendments += lineage.amendment_count
        quarters_amended += 1 if lineage.amendment_count else 0

    quarters.sort(key=lambda q: q.period_end)
    return (
        quarters,
        {
            "filed_at": filed_at,
            "amendments": amendments,
            "quarters_amended": quarters_amended,
        },
        raw_rows,
        unit_conflicts,
    )


def label_map(
    quarters: list[CanonicalQuarter],
    table: LineageTable,
    registry: ExclusionRegistry,
    *,
    share_tolerance: float,
) -> dict[str, str]:
    """Episode id -> observed action, for every transition that survives quarantine.

    Deliberately stops before hold sampling. The sampler's selection depends on the labels, so
    running the sweep after it would mix the effect of the tolerance with the effect of which
    holds happened to be picked — and the question here is only what the tolerance does to the
    labels themselves.
    """
    labels: dict[str, str] = {}
    for previous, current in zip(quarters, quarters[1:], strict=False):
        blocked = blocked_securities(detect_candidates(previous, current, table=table))
        for built in classify_quarter_pair(
            previous,
            current,
            split_factors=split_factors_for(previous, current, table),
            successors=apply_lineage(previous, current, table),
            share_tolerance=share_tolerance,
        ):
            security = built.classification.security
            if registry.is_excluded(period_end=current.period_end, security=security):
                continue
            if security in blocked:
                continue
            episode_id = f"{TARGET}-{security.slug}-{current.period_end.isoformat()}"
            labels[episode_id] = built.classification.action.value
    return labels


def split_of(when: date) -> str:
    if when <= TRAIN_END:
        return "train"
    return "validation" if when <= VALIDATION_END else "held_out"


def sweep_tolerances(
    quarters: list[CanonicalQuarter],
    table: LineageTable,
    registry: ExclusionRegistry,
    *,
    chosen: float,
) -> ToleranceReport:
    """Rebuild the labels at each tolerance and report what moved.

    No model is constructed anywhere in this function, and none may be: choosing the tolerance by
    downstream accuracy would be fitting the definition of the target to the predictor.
    """
    baseline: dict[str, str] = {}
    rows: list[ToleranceRow] = []

    for tolerance in sorted({*TOLERANCE_SWEEP, chosen}):
        labels = label_map(quarters, table, registry, share_tolerance=tolerance)
        if tolerance == 0.0:
            baseline = labels
        flips, flipped = compare_to_zero(baseline, labels)

        per_split: dict[str, Counter] = {
            "train": Counter(),
            "validation": Counter(),
            "held_out": Counter(),
        }
        for episode_id, action in labels.items():
            per_split[split_of(date.fromisoformat(episode_id[-10:]))][action] += 1

        rows.append(
            ToleranceRow(
                tolerance=tolerance,
                total_episodes=len(labels),
                class_counts=dict(Counter(labels.values())),
                flips_vs_zero=flips,
                flipped_episode_ids=flipped,
                train_counts=dict(per_split["train"]),
                validation_counts=dict(per_split["validation"]),
                held_out_counts=dict(per_split["held_out"]),
            )
        )
    return ToleranceReport(chosen=chosen, rows=rows)


def main() -> int:  # noqa: PLR0915 — one linear pipeline; splitting it would hide the order
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/berkshire")
    parser.add_argument("--version", default=DATASET_VERSION)
    parser.add_argument("--source-manifest", default="berkshire-v1.0")
    parser.add_argument("--lineage", default="config/security_lineage/reviewed_v2.json")
    parser.add_argument("--tolerance", type=float, default=SHARE_TOLERANCE)
    args = parser.parse_args()

    root = Path(args.root)
    source_manifest = root / "episodes" / f"{args.source_manifest}.manifest.json"
    lineage_path = Path(args.lineage)

    quarters, meta, raw_rows, unit_conflicts = load_quarters(root, source_manifest)
    filed_at: dict[date, date] = meta["filed_at"]
    print(f"==> {len(quarters)} canonical quarters, {meta['amendments']} amendments")

    # --- corporate actions ----------------------------------------------------------
    # Curated entries only. Detection proposes; nothing here promotes a candidate.
    table = load_curated(lineage_path)
    candidates = [
        candidate
        for previous, current in zip(quarters, quarters[1:], strict=False)
        for candidate in detect_candidates(previous, current, table=table)
    ]
    by_kind: dict[str, int] = {}
    for candidate in candidates:
        by_kind[candidate.suspected.value] = by_kind.get(candidate.suspected.value, 0) + 1
    still_open = unresolved_blocking(candidates, table)
    print(
        f"==> {len(candidates)} detected candidates, {len(table.entries)} curated entries, "
        f"{len(still_open)} unresolved blocking"
    )
    for candidate in still_open:
        print(
            f"    UNRESOLVED {candidate.period_end} {candidate.suspected.value} "
            f"{candidate.from_security}->{candidate.to_security}"
        )

    # Derived from the unresolved list rather than hand-listed, so a newly detected ambiguity is
    # quarantined automatically instead of entering training data while someone writes it down.
    registry = quarantine_unresolved(still_open, dataset_version=args.version)
    print(f"==> quarantined {registry.transitions} transition(s), {registry.securities} securities")
    for exclusion in registry.exclusions:
        print(
            f"    QUARANTINE {exclusion.transition_period_end} "
            f"{exclusion.detected_kind.value} {len(exclusion.securities)} securities "
            f"({exclusion.status.value})"
        )

    # --- episodes -------------------------------------------------------------------
    episodes, holds, actions_meta = [], [], []
    magnitudes: dict[str, int] = {}
    context: dict[str, dict] = {}
    withheld: list[WithheldRow] = []
    late_positions = late_value = 0.0
    delays: list[int] = []
    divergent = compared = 0
    feature_lookahead = 0

    for index, (previous, current) in enumerate(zip(quarters, quarters[1:], strict=False)):
        history = quarters[: index + 1]
        blocked = blocked_securities(detect_candidates(previous, current, table=table))
        cutoff = quarter_start(current.period_end)
        public_history = [q for q in history if filed_at.get(q.period_end, q.period_end) <= cutoff]
        visible = [PublicQuarterView.of(q, as_of=cutoff) for q in public_history]
        regime = regime_bucket(regime_features(visible).get("book_return_1q"))

        # Measured here rather than asserted downstream: every book that contributes to a feature
        # must have been filed by the cutoff, and every position in it disclosed by then. The
        # second half is what was silently false before `PublicQuarterView` existed.
        book_leaks = sum(
            1
            for q, view in zip(public_history, visible, strict=True)
            if filed_at.get(q.period_end, q.period_end) > cutoff
            or any(p.disclosed_at > cutoff for p in view.positions)
        )
        # The disclosure evidence behind the stratum, kept per row so the matching check is a
        # measurement of the sample rather than a restatement of the loop above. Comparing the
        # source book's *period end* to the cutoff would have been vacuous — a quarter that ended
        # before the next quarter began always passes that — so it records the two dates that can
        # actually be violated: when the book was filed, and when its last position became public.
        source_book = visible[-1] if visible else None
        stratum_evidence = (
            {
                "filed_at": filed_at.get(source_book.period_end, source_book.period_end),
                "last_disclosed": max(
                    (p.disclosed_at for p in source_book.positions), default=cutoff
                ),
            }
            if source_book is not None
            else {"filed_at": cutoff, "last_disclosed": cutoff}
        )

        for position in current.late_disclosed:
            late_positions += 1
            late_value += position.market_value
            delays.append(position.disclosure_delay_days(current.period_end))

        successors = apply_lineage(previous, current, table)
        split_factors = split_factors_for(previous, current, table)
        lineage_refs = sorted(
            [f"successor:{src}->{dst}@{current.period_end}" for src, dst in successors.items()]
            + [f"split:{sec}x{ratio}@{current.period_end}" for sec, ratio in split_factors.items()]
        )

        for built in classify_quarter_pair(
            previous,
            current,
            split_factors=split_factors,
            successors=successors,
            share_tolerance=args.tolerance,
        ):
            security = built.classification.security
            # Quarantine is checked first so its count is real. Scoped to this transition: the
            # same security stays usable in clean quarters, and other securities in this quarter
            # are untouched.
            exclusion = registry.excluding(period_end=current.period_end, security=security)
            if exclusion is not None:
                withheld.append(
                    WithheldRow(
                        episode_id=f"{TARGET}-{security.slug}-{current.period_end.isoformat()}",
                        security=security.token,
                        decision_window_end=current.period_end,
                        exclusion_status=exclusion.status.value,
                        exclusion_scope=exclusion.scope.value,
                        exclusion_reason=exclusion.reason,
                        detected_kind=exclusion.detected_kind.value,
                    )
                )
                continue
            # Defensive: a blocking candidate that somehow escaped quarantine must not become a
            # label. Reaching this is a bug, and the audit's reaching-modelling gate would fail.
            if security in blocked:
                continue

            features = build_features(visible, security, as_of=cutoff)
            # The cohort is part of the cell: a control must come from the same split as the
            # action it stands in for, or the composition of held-out depends on trades that
            # happened during training.
            stratum = stratum_for(
                weight=features.weight,
                trailing_return=features.trailing_return_1q,
                regime=regime,
                cohort=split_of(current.period_end),
            )
            episode = build_episode(
                history,
                current,
                built,
                advisor_id=TARGET,
                entity=ENTITY,
                filed_at=filed_at.get(current.period_end, current.period_end),
                view=ReplayView.public_observer,
            )
            oracle = build_episode(
                history,
                current,
                built,
                advisor_id=TARGET,
                entity=ENTITY,
                filed_at=filed_at.get(current.period_end, current.period_end),
                view=ReplayView.oracle_own_book,
            )
            compared += 1
            divergent += 1 if episode.inputs.starting_value != oracle.inputs.starting_value else 0
            feature_lookahead += 1 if book_leaks else 0

            context[episode.episode_id] = {
                "security": security,
                "magnitude": built.magnitude.value,
                "features": features,
                "stratum": stratum,
                "cutoff": cutoff,
                "lineage_refs": lineage_refs,
                "action": built.classification.action.value,
                "stratum_evidence": stratum_evidence,
            }

            if built.classification.action is ObservedAction.hold:
                salience = score_hold(
                    built.classification, period_return=features.trailing_return_1q
                )
                context[episode.episode_id]["salience"] = salience.score
                holds.append((episode, stratum, salience.score))
            else:
                episodes.append(episode)
                actions_meta.append((episode.episode_id, stratum))
                magnitudes[built.magnitude.value] = magnitudes.get(built.magnitude.value, 0) + 1

    # --- what the amendments actually changed ---------------------------------------
    # Measured by rebuilding each affected transition without the amendment and diffing the
    # labels. Data cleaning that moves no labels is housekeeping; this says which moved.
    label_changes = fabricated_enters = 0
    for previous, current in zip(quarters, quarters[1:], strict=False):
        if not previous.late_disclosed:
            continue
        stripped = previous.model_copy(
            update={"positions": [p for p in previous.positions if not p.confidential_treatment]}
        )
        delta = measure_label_change(stripped, previous, current)
        if delta.changed:
            label_changes += 1
        fabricated_enters += delta.fabricated_enters_removed

    # --- matched hold controls ------------------------------------------------------
    selection = select_matched_holds(
        actions_meta,
        [(e.episode_id, s, score) for e, s, score in holds],
        per_action=1,
        action_classes={e: context[e]["action"] for e, _ in actions_meta},
    )
    kept = set(selection.kept)
    for episode, _, _ in holds:
        if episode.episode_id in kept:
            episodes.append(episode)
            magnitudes["none"] = magnitudes.get("none", 0) + 1

    # A control chosen from a book nobody could see would contaminate the sample itself, and no
    # check on episode inputs can detect that — features never enter `EpisodeInputs`. Re-derived
    # over the rows actually selected rather than inherited from the loop above.
    matching_lookahead = sum(
        1
        for episode_id in kept | {e for e, _ in actions_meta}
        if context[episode_id]["stratum_evidence"]["filed_at"] > context[episode_id]["cutoff"]
        or context[episode_id]["stratum_evidence"]["last_disclosed"]
        > context[episode_id]["cutoff"]
    )
    print(
        f"==> {len(episodes)} episodes  "
        f"({len(actions_meta)} trades, {len(kept)} matched holds from {len(holds)} available, "
        f"match rate {selection.match_rate:.0%})"
    )

    dataset = EpisodeDataset(
        advisor_id=TARGET,
        episodes=episodes,
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
    )
    dataset_manifest = dataset.manifest()
    split_by_id = {
        **{i: "train" for i in dataset_manifest.train_ids},
        **{i: "validation" for i in dataset_manifest.validation_ids},
        **{i: "held_out" for i in dataset_manifest.held_out_ids},
    }

    # --- how much of this rests on the tolerance ------------------------------------
    tolerance_report = sweep_tolerances(quarters, table, registry, chosen=args.tolerance)
    print()
    print(tolerance_report.render())

    # --- the frozen artifact --------------------------------------------------------
    rows: list[EpisodeRow] = []
    for episode in sorted(episodes, key=lambda e: e.episode_id):
        info = context[episode.episode_id]
        security: SecurityKey = info["security"]
        is_hold = episode.observed_action is ObservedAction.hold
        rows.append(
            EpisodeRow(
                episode_id=episode.episode_id,
                target_id=TARGET,
                security=security.token,
                security_cusip=security.cusip,
                security_title_of_class=security.title_of_class,
                observed_action=episode.observed_action.value,
                magnitude="none" if is_hold else info["magnitude"],
                action_basis=episode.action_basis.value,
                attribution_basis=episode.attribution.value,
                attribution_confidence=episode.attribution_confidence,
                training_weight=episode.training_weight,
                decision_window_start=episode.decision_window_start,
                decision_window_end=episode.decision_window_end,
                public_information_cutoff=episode.inputs.as_of,
                replay_view=ReplayView.public_observer.value,
                feature_source_period_end=info["features"].source_period_end,
                features=info["features"].as_dict(),
                is_matched_control=is_hold,
                matched_control=(
                    MatchedControl(
                        stratum=(
                            f"w{info['stratum'].weight_bucket}"
                            f"|r{info['stratum'].return_bucket}"
                            f"|{info['stratum'].regime}"
                        ),
                        matched_to=selection.pairs.get(episode.episode_id),
                        salience=info.get("salience"),
                    )
                    if is_hold
                    else None
                ),
                lineage_refs=info["lineage_refs"],
                split=split_by_id[episode.episode_id],
            )
        )

    artifact_dir = root / "dataset" / args.version
    manifest = ArtifactManifest(
        dataset_version=args.version,
        target_id=TARGET,
        parser_version=PARSER_VERSION,
        builder_version=BUILDER_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        lineage_version=json.loads(lineage_path.read_text())["version"],
        lineage_sha256=sha256_of_file(lineage_path),
        quarantine_version=args.version,
        quarantine_sha256=registry.content_sha256(),
        source_manifest_sha256=sha256_of_file(source_manifest),
        share_tolerance=args.tolerance,
        class_counts=dataset_manifest.action_counts,
        train_ids=dataset_manifest.train_ids,
        validation_ids=dataset_manifest.validation_ids,
        held_out_ids=dataset_manifest.held_out_ids,
    )
    try:
        result = freeze(artifact_dir, manifest=manifest, rows=rows, withheld=withheld)
    except DatasetFrozenError as error:
        print(f"\nFROZEN: {error}", file=sys.stderr)
        return 2
    print(
        f"\n==> {'wrote' if result.written else 'unchanged'} {artifact_dir} "
        f"({result.manifest.row_count} rows, sha256 {result.manifest.artifact_sha256[:12]})"
    )
    artifact_ok, artifact_detail = verify(artifact_dir)

    # Measured, not asserted. Construction validators already reject a leaking episode, so this
    # should be zero — which is exactly why re-deriving it is worth the six lines: a gate that
    # reports a hardcoded 0 proves nothing about the data it is supposed to be guarding.
    lookahead = 0
    for episode in episodes:
        window_start = episode.decision_window_start
        if window_start is not None and episode.inputs.as_of > window_start:
            lookahead += 1
            continue
        if any(o.observed_at > episode.inputs.as_of for o in episode.inputs.all_observations()):
            lookahead += 1

    hold_share_by_split = {}
    for name in ("train", "validation", "held_out"):
        in_split = [r for r in rows if r.split == name]
        controls = sum(1 for r in in_split if r.is_matched_control)
        hold_share_by_split[name] = round(controls / len(in_split), 4) if in_split else 0.0

    chosen_row = tolerance_report.chosen_row
    audit = DatasetAudit(
        dataset_version=args.version,
        entity=ENTITY,
        coverage=f"{quarters[0].period_end} to {quarters[-1].period_end}",
        canonical_quarters=len(quarters),
        raw_rows=raw_rows,
        canonical_positions=sum(len(q.positions) for q in quarters),
        unique_cusips=len({p.identity.key for q in quarters for p in q.positions}),
        ticker_mapping_coverage=0.0,
        amendments=meta["amendments"],
        quarters_with_amendments=meta["quarters_amended"],
        late_disclosed_positions=int(late_positions),
        late_disclosed_value=late_value,
        median_disclosure_delay_days=int(statistics.median(delays)) if delays else None,
        max_disclosure_delay_days=max(delays) if delays else None,
        detected_candidates=len(candidates),
        confirmed_cusip_changes=sum(
            1
            for e in table.entries
            if e.kind
            in (
                CorporateActionKind.cusip_change,
                CorporateActionKind.class_change,
                CorporateActionKind.class_relabel,
            )
        ),
        confirmed_splits=sum(1 for e in table.entries if e.kind is CorporateActionKind.split),
        merger_review_queue=by_kind.get(CorporateActionKind.merger.value, 0),
        unresolved_blocking_actions=len(still_open),
        quarantined_transitions=registry.transitions,
        quarantined_securities=registry.securities,
        episodes_removed_by_quarantine=len(withheld),
        # Computed: unresolved candidates that survived quarantine and would have reached
        # the modelling rows. Zero means withheld, never means resolved.
        unresolved_reaching_modelling=sum(
            1
            for c in still_open
            if not registry.is_excluded(period_end=c.period_end, security=c.from_security)
        ),
        action_counts=dataset_manifest.action_counts,
        magnitude_counts=magnitudes,
        share_count_grounded=sum(1 for e in episodes if e.action_basis.value == "share_count"),
        drift_inferred=sum(1 for e in episodes if e.action_basis.value == "drift_adjusted_value"),
        review_required=0,
        lookahead_violations=lookahead,
        feature_lookahead_violations=feature_lookahead,
        matching_lookahead_violations=matching_lookahead,
        value_unit_conflicts=unit_conflicts,
        amendment_induced_label_changes=label_changes,
        fabricated_enters_removed=fabricated_enters,
        public_vs_oracle_divergent_episodes=divergent,
        public_vs_oracle_total=compared,
        artifact_verified=artifact_ok,
        artifact_sha256=result.manifest.artifact_sha256,
        artifact_detail=artifact_detail,
        split_manifest_matches=result.manifest.split_matches([r.episode_id for r in rows]),
        tolerance_chosen=args.tolerance,
        tolerance_flips_vs_zero=chosen_row.flips_vs_zero if chosen_row else 0,
        tolerance_flip_share=chosen_row.flip_share if chosen_row else 0.0,
        tolerance_summary=(
            "FLAG: the tolerance decides a material share of the target"
            if tolerance_report.is_material
            else "the tolerance is not carrying the dataset"
        ),
        actions_requiring_controls=len(actions_meta),
        matched_holds=len(kept),
        match_rate=selection.match_rate,
        unmatched_actions=selection.unmatched_actions,
        unmatched_by_action=selection.unmatched_by_action,
        unmatched_by_weight_bucket=selection.unmatched_by_weight_bucket,
        unmatched_by_return_bucket=selection.unmatched_by_return_bucket,
        unmatched_by_regime=selection.unmatched_by_regime,
        match_coverage_by_split=hold_share_by_split,
        train_episodes=len(dataset_manifest.train_ids),
        validation_episodes=len(dataset_manifest.validation_ids),
        held_out_episodes=len(dataset_manifest.held_out_ids),
    )

    print()
    print(audit.render())

    (artifact_dir / "audit.json").write_text(audit.model_dump_json(indent=2))
    (artifact_dir / "tolerance.json").write_text(tolerance_report.model_dump_json(indent=2))
    return 0 if audit.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())

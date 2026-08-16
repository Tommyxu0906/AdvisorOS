#!/usr/bin/env python
"""Build episodes from the stored filings and audit the result.

    python scripts/build_berkshire_dataset.py

Reads only from the immutable raw layer, so it is reproducible offline and a parser change costs
nothing at SEC. Runs the full chain in order — filing lineage, corporate actions, classification,
matched hold controls, point-in-time features — then prints the audit that gates modelling.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.distillation.finance_nuwa.audit import DatasetAudit  # noqa: E402
from app.distillation.finance_nuwa.builder import (  # noqa: E402
    ReplayView,
    build_episode,
    classify_quarter_pair,
    measure_label_change,
)
from app.distillation.finance_nuwa.corporate_actions import (  # noqa: E402
    CorporateActionKind,
    apply_lineage,
    blocked_cusips,
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
from app.distillation.finance_nuwa.drift import ObservedAction  # noqa: E402
from app.distillation.finance_nuwa.features import (  # noqa: E402
    build_features,
    regime_bucket,
    regime_features,
)
from app.distillation.finance_nuwa.lineage import (  # noqa: E402
    PublicQuarterView,
    compose_quarter,
)
from app.distillation.finance_nuwa.quarantine import quarantine_unresolved  # noqa: E402
from app.distillation.finance_nuwa.sec_13f import parse_information_table  # noqa: E402
from app.distillation.finance_nuwa.store import FilingRef, QuarterLineage  # noqa: E402

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/berkshire")
    parser.add_argument("--version", default="berkshire-v1.0")
    parser.add_argument("--lineage", default="config/security_lineage/reviewed_v1.json")
    args = parser.parse_args()

    root = Path(args.root)
    manifest = json.loads((root / "episodes" / f"{args.version}.manifest.json").read_text())
    raw = root / "raw"

    # --- canonical quarters ---------------------------------------------------------
    quarters, amendments, quarters_amended, raw_rows, unit_conflicts = [], 0, 0, 0, 0
    filed_at: dict[date, date] = {}

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
    print(f"==> {len(quarters)} canonical quarters, {amendments} amendments")

    # --- corporate actions ----------------------------------------------------------
    # Curated entries only. Detection proposes; nothing here promotes a candidate.
    table = load_curated(args.lineage)
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
            f"{candidate.from_cusip}->{candidate.to_cusip}"
        )

    # Derived from the unresolved list rather than hand-listed, so a newly detected ambiguity is
    # quarantined automatically instead of entering training data while someone writes it down.
    registry = quarantine_unresolved(still_open, dataset_version=args.version)
    print(f"==> quarantined {registry.transitions} transition(s), {registry.securities} securities")
    for exclusion in registry.exclusions:
        print(
            f"    QUARANTINE {exclusion.transition_period_end} "
            f"{exclusion.detected_kind.value} {len(exclusion.cusips)} securities "
            f"({exclusion.status.value})"
        )

    # --- episodes -------------------------------------------------------------------
    episodes, holds, actions_meta = [], [], []
    magnitudes: dict[str, int] = {}
    late_positions = late_value = 0.0
    quarantined_episodes = 0
    delays: list[int] = []
    divergent = compared = 0

    for index, (previous, current) in enumerate(zip(quarters, quarters[1:], strict=False)):
        history = quarters[: index + 1]
        blocked = blocked_cusips(detect_candidates(previous, current, table=table))
        cutoff = date(current.period_end.year, ((current.period_end.month - 1) // 3) * 3 + 1, 1)
        visible = [
            PublicQuarterView.of(q, as_of=cutoff)
            for q in history
            if filed_at.get(q.period_end, q.period_end) <= cutoff
        ]
        regime = regime_bucket(regime_features(visible).get("book_return_1q"))

        for position in current.late_disclosed:
            late_positions += 1
            late_value += position.market_value
            delays.append(position.disclosure_delay_days(current.period_end))

        successors = apply_lineage(previous, current, table)
        for built in classify_quarter_pair(
            previous,
            current,
            split_factors=split_factors_for(previous, current, table),
            successors=successors,
        ):
            cusip = built.classification.symbol
            # Quarantine is checked first so its count is real. Scoped to this transition: the
            # same security stays usable in clean quarters, and other securities in this quarter
            # are untouched.
            if registry.is_excluded(period_end=current.period_end, cusip=cusip):
                quarantined_episodes += 1
                continue
            # Defensive: a blocking candidate that somehow escaped quarantine must not become a
            # label. Reaching this is a bug, and the audit's reaching-modelling gate would fail.
            if cusip in blocked:
                continue
            features = build_features(visible, cusip, as_of=cutoff)
            stratum = stratum_for(
                weight=features.weight,
                trailing_return=features.trailing_return_1q,
                regime=regime,
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

            if built.classification.action is ObservedAction.hold:
                salience = score_hold(
                    built.classification, period_return=features.trailing_return_1q
                )
                holds.append((episode, stratum, salience.score, built.magnitude.value))
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
        [(e.episode_id, s, score) for e, s, score, _ in holds],
        per_action=1,
    )
    kept = {identifier for identifier in selection.kept}
    for episode, _, _, magnitude in holds:
        if episode.episode_id in kept:
            episodes.append(episode)
            magnitudes[magnitude] = magnitudes.get(magnitude, 0) + 1
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

    audit = DatasetAudit(
        dataset_version=args.version,
        entity=ENTITY,
        coverage=f"{quarters[0].period_end} to {quarters[-1].period_end}",
        canonical_quarters=len(quarters),
        raw_rows=raw_rows,
        canonical_positions=sum(len(q.positions) for q in quarters),
        unique_cusips=len({p.identity.cusip for q in quarters for p in q.positions}),
        ticker_mapping_coverage=0.0,
        amendments=amendments,
        quarters_with_amendments=quarters_amended,
        late_disclosed_positions=int(late_positions),
        late_disclosed_value=late_value,
        median_disclosure_delay_days=int(statistics.median(delays)) if delays else None,
        max_disclosure_delay_days=max(delays) if delays else None,
        detected_candidates=len(candidates),
        confirmed_cusip_changes=sum(
            1
            for e in table.entries
            if e.kind in (CorporateActionKind.cusip_change, CorporateActionKind.class_change)
        ),
        confirmed_splits=sum(1 for e in table.entries if e.kind is CorporateActionKind.split),
        merger_review_queue=by_kind.get(CorporateActionKind.merger.value, 0),
        unresolved_blocking_actions=len(still_open),
        quarantined_transitions=registry.transitions,
        quarantined_securities=registry.securities,
        episodes_removed_by_quarantine=quarantined_episodes,
        # Computed: unresolved candidates that survived quarantine and would have reached
        # the modelling rows. Zero means withheld, never means resolved.
        unresolved_reaching_modelling=sum(
            1
            for c in still_open
            if not registry.is_excluded(period_end=c.period_end, cusip=c.from_cusip)
        ),
        action_counts=dataset_manifest.action_counts,
        magnitude_counts=magnitudes,
        share_count_grounded=sum(1 for e in episodes if e.action_basis.value == "share_count"),
        drift_inferred=sum(1 for e in episodes if e.action_basis.value == "drift_adjusted_value"),
        review_required=0,
        lookahead_violations=lookahead,
        value_unit_conflicts=unit_conflicts,
        amendment_induced_label_changes=label_changes,
        fabricated_enters_removed=fabricated_enters,
        public_vs_oracle_divergent_episodes=divergent,
        public_vs_oracle_total=compared,
        train_episodes=len(dataset_manifest.train_ids),
        validation_episodes=len(dataset_manifest.validation_ids),
        held_out_episodes=len(dataset_manifest.held_out_ids),
    )

    print()
    print(audit.render())

    target = root / "episodes" / f"{args.version}.audit.json"
    target.write_text(audit.model_dump_json(indent=2))
    return 0 if audit.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())

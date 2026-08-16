"""The report that decides whether this dataset is allowed to train anything.

Three gates, and they are not advisory. A dataset that fails any of them cannot produce a number
worth reporting, because the number would be measuring the pipeline rather than the investor:

    lookahead violations              must be 0
    value-unit conflicts              must be 0
    unresolved blocking actions       must be 0

The rest of the report is descriptive, and most of it exists because a reader's first question
about any behavioural dataset is some version of *how much of this did you actually observe,
and how much did you infer?* Counting labels by grounding answers that directly. So does
reporting the amendment-induced label change: data cleaning that moves no labels is
housekeeping, and saying which ones moved is the difference between claiming rigour and showing
it.

The public-versus-oracle divergence is here for the same reason. It is not a defect to be
minimised — it is the share of the problem that comes from the reporting lag rather than from
the persona, and knowing its size before modelling starts sets a ceiling on what the deployable
benchmark could possibly achieve.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field


class GateResult(BaseModel):
    """One hard requirement, and whether the data meets it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    observed: int
    required: int = 0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.observed == self.required


class DatasetAudit(BaseModel):
    """Everything a reader needs before deciding to believe a downstream score."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    entity: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- coverage
    coverage: str = ""
    canonical_quarters: int = 0
    missing_quarters: list[date] = Field(default_factory=list)
    raw_rows: int = 0
    canonical_positions: int = 0
    unique_cusips: int = 0
    ticker_mapping_coverage: float = 0.0

    # --- filing lineage
    amendments: int = 0
    quarters_with_amendments: int = 0
    late_disclosed_positions: int = 0
    late_disclosed_value: float = 0.0
    median_disclosure_delay_days: int | None = None
    max_disclosure_delay_days: int | None = None

    # --- corporate actions
    # Detected and confirmed are different claims and are never conflated: a candidate is an
    # arithmetic suspicion, a confirmation is someone having written down what it rests on.
    detected_candidates: int = 0
    confirmed_cusip_changes: int = 0
    confirmed_splits: int = 0
    merger_review_queue: int = 0
    unresolved_blocking_actions: int = 0

    # Quarantine. A quarantined episode is NOT a resolved one — it is explicitly withheld, and
    # the gate asks whether any unresolved action still reaches the modelling data rather than
    # whether any exists at all.
    quarantined_transitions: int = 0
    quarantined_securities: int = 0
    episodes_removed_by_quarantine: int = 0
    unresolved_reaching_modelling: int = 0

    # --- episodes
    action_counts: dict[str, int] = Field(default_factory=dict)
    magnitude_counts: dict[str, int] = Field(default_factory=dict)
    share_count_grounded: int = 0
    drift_inferred: int = 0
    review_required: int = 0

    # --- information integrity
    lookahead_violations: int = 0
    value_unit_conflicts: int = 0
    amendment_induced_label_changes: int = 0
    fabricated_enters_removed: int = 0
    public_vs_oracle_divergent_episodes: int = 0
    public_vs_oracle_total: int = 0

    # --- split
    train_episodes: int = 0
    validation_episodes: int = 0
    held_out_episodes: int = 0

    @property
    def gates(self) -> list[GateResult]:
        return [
            GateResult(
                name="lookahead violations",
                observed=self.lookahead_violations,
                detail="an episode whose inputs postdate its decision window would score well "
                "and teach nothing",
            ),
            GateResult(
                name="value-unit conflicts",
                observed=self.value_unit_conflicts,
                detail="the SEC rule and the implied-price check disagreeing means a quarter "
                "may be wrong by a factor of a thousand",
            ),
            GateResult(
                name="unresolved blocking reaching modelling data",
                observed=self.unresolved_reaching_modelling,
                detail="a split read as a purchase teaches conviction nobody showed. "
                "Quarantined transitions do not count here — they are withheld, not resolved",
            ),
        ]

    @property
    def passes(self) -> bool:
        return all(gate.passed for gate in self.gates)

    @property
    def total_episodes(self) -> int:
        return sum(self.action_counts.values())

    @property
    def grounding_share(self) -> float:
        """Fraction of labels that are observed fact rather than inference."""
        total = self.share_count_grounded + self.drift_inferred + self.review_required
        return round(self.share_count_grounded / total, 4) if total else 0.0

    @property
    def majority_class_rate(self) -> float:
        """What 'always answer the commonest label' would score.

        The number every reported accuracy has to be read against — and the reason the report
        prints it before any model exists, rather than after someone is attached to a result.
        """
        if not self.action_counts:
            return 0.0
        return round(max(self.action_counts.values()) / self.total_episodes, 4)

    def render(self) -> str:
        lines: list[str] = [
            f"FinanceNuwa — {self.entity} Dataset Audit",
            f"{self.dataset_version}   generated {self.generated_at:%Y-%m-%d %H:%M} UTC",
            "",
            "COVERAGE",
            f"  Period                    {self.coverage}",
            f"  Canonical quarters        {self.canonical_quarters}",
            f"  Missing quarters          {len(self.missing_quarters)}",
            f"  Raw rows -> positions     {self.raw_rows:,} -> {self.canonical_positions:,}",
            f"  Unique CUSIPs             {self.unique_cusips}",
            f"  Ticker mapping coverage   {self.ticker_mapping_coverage:.0%}"
            "  (not required: every label is share-count grounded)",
            "",
            "FILING LINEAGE",
            f"  Amendments                {self.amendments} across "
            f"{self.quarters_with_amendments} quarters",
            f"  Late-disclosed positions  {self.late_disclosed_positions}"
            f"  (${self.late_disclosed_value / 1e9:,.1f}bn)",
            f"  Disclosure delay          median {self.median_disclosure_delay_days}d,"
            f" max {self.max_disclosure_delay_days}d",
            "",
            "CORPORATE ACTIONS",
            f"  Detected candidates       {self.detected_candidates}",
            f"  Confirmed CUSIP changes   {self.confirmed_cusip_changes}",
            f"  Confirmed splits          {self.confirmed_splits}",
            f"  Merger review queue       {self.merger_review_queue}",
            f"  Unresolved blocking       {self.unresolved_blocking_actions}",
            f"  Quarantined transitions   {self.quarantined_transitions}"
            f"  ({self.quarantined_securities} securities,"
            f" {self.episodes_removed_by_quarantine} episodes withheld)",
            f"  Reaching modelling data   {self.unresolved_reaching_modelling}",
            "",
            "EPISODES",
        ]
        for label, count in sorted(self.action_counts.items(), key=lambda kv: -kv[1]):
            share = count / self.total_episodes if self.total_episodes else 0
            lines.append(f"  {label:<24}  {count:>5}  ({share:.1%})")
        lines += [
            "",
            f"  Share-count grounded      {self.share_count_grounded:>5}"
            f"  ({self.grounding_share:.1%})",
            f"  Drift inferred            {self.drift_inferred:>5}",
            f"  Review required           {self.review_required:>5}",
            "",
            "  Magnitude",
        ]
        for label, count in sorted(self.magnitude_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {label:<22}  {count:>5}")

        lines += [
            "",
            "INFORMATION INTEGRITY",
            f"  Lookahead violations      {self.lookahead_violations}",
            f"  Value-unit conflicts      {self.value_unit_conflicts}",
            f"  Amendment label changes   {self.amendment_induced_label_changes}"
            f"  ({self.fabricated_enters_removed} fabricated ENTERs removed)",
            f"  Public vs oracle differ   {self.public_vs_oracle_divergent_episodes}"
            f" of {self.public_vs_oracle_total}",
            "",
            "TEMPORAL SPLIT",
            f"  Refinement / train        {self.train_episodes}",
            f"  Validation                {self.validation_episodes}",
            f"  Held out (locked)         {self.held_out_episodes}",
            "",
            "BASELINE TO BEAT",
            f"  Always answer the majority class   {self.majority_class_rate:.1%}",
            "",
            "GATES",
        ]
        for gate in self.gates:
            mark = "PASS" if gate.passed else "FAIL"
            lines.append(f"  [{mark}] {gate.name:<34} {gate.observed} (required {gate.required})")
            if not gate.passed:
                lines.append(f"         {gate.detail}")

        lines += [
            "",
            (
                "VERDICT: dataset may proceed to modelling."
                if self.passes
                else "VERDICT: BLOCKED. Modelling must not begin until every gate passes."
            ),
        ]
        return "\n".join(lines)

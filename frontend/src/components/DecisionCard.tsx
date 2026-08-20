/**
 * The one screen that has to land: what the system currently recommends, and who disagrees.
 *
 * Everything else in the workspace is working material — diagnostics, a scenario, a transcript.
 * This is the conclusion, and it is deliberately structured rather than written, because the
 * interesting content is *structural*: a preferred option, a set of checks that either passed or
 * did not, two frameworks with recorded positions, and the specific thing they disagree about.
 *
 * Two rules it follows that a summary paragraph would quietly break.
 *
 * **The checks are read, not asserted.** Every tick comes from the counterfactual the engine
 * computed — `holds_up`, `resolved_guardrails`, the before/after on largest position weight. A
 * card that hard-coded "✓ Concentration improves" would keep claiming it on the day it stopped
 * being true.
 *
 * **A dissent is shown with its reason, including when it was overruled.** "Munger lens: opposed,
 * preferred HOLD, ruled out by HIGH_APR_DEBT" is more informative than a consensus, and hiding it
 * would make the committee look like a rubber stamp for the engine.
 */

import type {
  ConsultSynthesis,
  AdvisorConsultResponse,
  DecisionCandidate,
  PortfolioScenario,
} from "../types";
import { percent } from "../lib/units";
import { Card, SectionHeader, StatusBadge } from "../ui";

interface Check {
  ok: boolean;
  label: string;
  detail: string;
}

export function DecisionCard({
  synthesis,
  responses,
  candidates,
  scenario,
  onViewCalculations,
}: {
  synthesis: ConsultSynthesis;
  responses: AdvisorConsultResponse[];
  candidates: DecisionCandidate[];
  scenario: PortfolioScenario | null;
  onViewCalculations: () => void;
}) {
  const selected = candidates.find((c) => c.candidate_id === synthesis.selected_candidate_id);
  const checks = buildChecks(scenario);
  const answered = responses.filter((r) => !r.parse_failed);

  return (
    <Card tone="raised">
      <SectionHeader
        title="Current decision"
        hint="What the constraints permit, and where the committee stands on it."
        action={
          synthesis.unresolved_disagreement ? (
            <StatusBadge tone="warn">Unresolved</StatusBadge>
          ) : (
            <StatusBadge tone="neutral">Provisional</StatusBadge>
          )
        }
      />

      <p className="metric-label">Preferred feasible option</p>
      <p className="decision-headline">{selected?.label ?? synthesis.selected_label}</p>
      <p className="small" style={{ color: "var(--ink-soft)", maxWidth: "62ch" }}>
        {synthesis.headline}
      </p>

      <div className="decision-columns">
        <div>
          <p className="metric-label">System checks</p>
          <ul className="check-list">
            {checks.map((check) => (
              <li key={check.label} className={check.ok ? "check-ok" : "check-no"}>
                <span aria-hidden="true">{check.ok ? "✓" : "✕"}</span>
                <span>
                  <strong>{check.label}</strong>
                  <span className="tiny muted"> — {check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="metric-label">Committee</p>
          <ul className="position-list">
            {answered.map((r) => (
              <li key={r.advisor_id}>
                <div className="row-between" style={{ gap: 8 }}>
                  <strong className="small">{r.display_name} lens</strong>
                  <StatusBadge
                    tone={
                      r.stance === "endorse"
                        ? "good"
                        : r.stance === "oppose"
                          ? "risk"
                          : r.stance === "mixed"
                            ? "warn"
                            : "neutral"
                    }
                  >
                    {r.stance === "endorse"
                      ? "Support"
                      : r.stance === "oppose"
                        ? "Oppose"
                        : r.stance === "mixed"
                          ? "Partly"
                          : "No view"}
                  </StatusBadge>
                </div>
                {r.corrections.length > 0 && (
                  <p className="tiny muted" style={{ margin: "4px 0 0" }}>
                    {r.corrections[0]}
                  </p>
                )}
              </li>
            ))}
            {answered.length === 0 && (
              <li>
                <p className="tiny muted" style={{ margin: 0 }}>
                  No framework returned a readable position, so this rests on the computation
                  alone.
                </p>
              </li>
            )}
          </ul>
        </div>
      </div>

      {synthesis.endorsing.length > 0 && synthesis.opposing.length > 0 && (
        <div className="decision-disagreement">
          <p className="metric-label" style={{ marginBottom: 4 }}>
            Key disagreement
          </p>
          <p className="small" style={{ margin: 0, maxWidth: "62ch" }}>
            {synthesis.opposing.join(" and ")} would not carry out what{" "}
            {synthesis.endorsing.join(" and ")} backs. The selection above is what the computed
            constraints permit — it is not a verdict on who is right.
          </p>
        </div>
      )}

      <div className="row-between" style={{ marginTop: 16, gap: 10 }}>
        <span className="tiny muted">
          Educational analysis. Not a recommendation from a licensed advisor, and nothing here
          places a trade.
        </span>
        <button type="button" className="secondary" onClick={onViewCalculations}>
          View calculations
        </button>
      </div>
    </Card>
  );
}

/** Read from the counterfactual the engine computed. Nothing here is asserted by the UI. */
function buildChecks(scenario: PortfolioScenario | null): Check[] {
  if (!scenario) {
    return [
      { ok: false, label: "No scenario computed", detail: "there is nothing to check against" },
    ];
  }

  const cf = scenario.counterfactual;
  const checks: Check[] = [
    {
      ok: cf.holds_up,
      label: "Meets hard constraints",
      detail: cf.holds_up
        ? "survives recomputation, and introduces no new blocking guardrail"
        : "fails its own arithmetic — shown for inspection, not as a candidate",
    },
  ];

  const concentration = cf.changes.find((c) => c.label === "largest position weight");
  if (concentration) {
    checks.push({
      ok: concentration.improved === true,
      label: "Concentration improves",
      detail: `largest position ${percent(concentration.before, 1)} → ${percent(
        concentration.after,
        1,
      )}`,
    });
  }

  const debt = cf.changes.find((c) => c.label === "high-APR debt");
  if (debt) {
    checks.push({
      ok: debt.improved === true,
      label: "High-APR debt addressed",
      detail:
        debt.after === 0
          ? "cleared in full by this plan"
          : `reduced, ${debt.after > 0 ? "not cleared" : "cleared"}`,
    });
  }

  if (cf.resolved_guardrails.length > 0) {
    checks.push({
      ok: true,
      label: "Guardrails resolved",
      detail: cf.resolved_guardrails.join(", "),
    });
  }
  if (cf.introduced_guardrails.length > 0) {
    checks.push({
      ok: false,
      label: "New guardrail introduced",
      detail: cf.introduced_guardrails.join(", "),
    });
  }

  return checks;
}

/**
 * The computed scenario: what the thresholds imply, before anyone pays for an opinion about it.
 *
 * This panel is the deterministic half of the product made visible. The server has been
 * computing it on every keystroke and returning it on the free endpoint all along; until now
 * the browser threw it away.
 *
 * Four rules govern the wording, and each one exists to stop a computed number reading as more
 * authoritative than its inputs deserve.
 *
 * **Scenarios, not instructions.** "Sell 216 shares" is advice. "Under a 20% threshold this
 * implies reducing about 216 shares" is analysis, and analysis is what the disclaimer says this
 * product produces. The rationale arrives from the policy layer already phrased that way — this
 * component renders it whole and never re-summarizes it into something shorter and blunter.
 *
 * **Whose number.** A house rule and an advisor's view are labelled differently on purpose. The
 * 22.9% card paydown is AdvisorOS policy; presenting it under an investor's name would put words
 * in someone else's mouth.
 *
 * **Nothing is re-derived here.** `headline`, `holds_up`, `fragile` and the sensitivity
 * sentences are computed server-side and rendered as given. Recomputing any of them in TSX would
 * put one rule in two languages, and the copy that drifted would be the one users read.
 *
 * **Tax never appears without its assumption.** The range is wide because the data cannot
 * distinguish two lot treatments, and a figure shown without that caveat is a fabricated
 * precision.
 */

import type { MetricChange, PortfolioScenario, ProposedAction } from "../types";
import { money, percent } from "../lib/units";
import { Advanced, Card, InlineAlert, SectionHeader, StatusBadge } from "../ui";

const KIND_LABEL: Record<string, string> = {
  trim_position: "Reduce",
  add_position: "Add",
  rebalance_to_target: "Rebalance",
  pay_down_debt: "Pay down",
  build_emergency_fund: "Build reserve",
  redirect_cashflow: "Redirect cashflow",
  hold: "Hold",
};

/** Percent-formatted metrics, matched by the label the server sends. */
const RATE_LABELS = new Set([
  "savings rate",
  "largest position weight",
  "HHI",
]);

export function ScenarioPanel({ scenario }: { scenario: PortfolioScenario | null }) {
  if (!scenario) return null;

  const { action_set, counterfactual, sensitivity } = scenario;
  const actions = [...action_set.actions].sort((a, b) => a.sequence - b.sequence);

  return (
    <section id="computed-scenario">
      <SectionHeader
        title="What the thresholds imply"
        hint={scenario.headline}
        action={<StatusBadge tone="good">Computed · no AI call</StatusBadge>}
      />

      {!scenario.has_actions ? (
        <Card tone="sunk">
          <p className="small muted" style={{ margin: 0 }}>
            Nothing here exceeds the thresholds in use. The diagnostics above still apply — this
            section only reports what the position-sizing rules imply, and today they imply
            nothing.
          </p>
        </Card>
      ) : (
        <>
          {!counterfactual.holds_up && (
            <InlineAlert tone="warn" title="This scenario did not survive its own arithmetic">
              It is shown with the problems listed rather than as a candidate. That is a policy
              bug worth reporting, not a difference of view.
            </InlineAlert>
          )}

          <Card tone="quiet">
            <ol className="scenario-list">
              {actions.map((action) => (
                <ActionRow key={action.action_id} action={action} />
              ))}
            </ol>
          </Card>

          {counterfactual.changes.length > 0 && (
            <BeforeAfter changes={counterfactual.changes} />
          )}

          {sensitivity && !sensitivity.declined && sensitivity.summary.length > 0 && (
            <Card tone={sensitivity.fragile ? "quiet" : "sunk"}>
              <div className="row-between" style={{ marginBottom: 8 }}>
                <p className="metric-label" style={{ margin: 0 }}>
                  How load-bearing is the threshold?
                </p>
                {sensitivity.fragile && <StatusBadge tone="warn">Fragile</StatusBadge>}
              </div>
              {sensitivity.summary.map((line) => (
                <p key={line} className="small" style={{ marginTop: 0, marginBottom: 8 }}>
                  {line}
                </p>
              ))}
            </Card>
          )}

          <Advanced label="Why these numbers, and what they assume">
            <p>
              Every action above was computed in Python from your figures — no model proposed
              any of them, and none of it cost anything. Each line names the threshold that
              produced it and whose threshold it is.
            </p>
            <p className="muted">
              {scenario.is_house_policy
                ? `Thresholds here are AdvisorOS house numbers, applied because no advisor has supplied an evidence-backed one. They are a starting point for the argument, not a recommendation.`
                : `Thresholds here come from ${scenario.policy_owner}.`}
            </p>
            {counterfactual.infeasibilities.length > 0 && (
              <>
                <p className="metric-label" style={{ marginTop: 12 }}>
                  Feasibility problems
                </p>
                <ul className="bullet-list" style={{ fontSize: 14 }}>
                  {counterfactual.infeasibilities.map((problem) => (
                    <li key={`${problem.action_id}-${problem.reason}`}>{problem.message}</li>
                  ))}
                </ul>
              </>
            )}
            {counterfactual.resolved_guardrails.length > 0 && (
              <p className="small" style={{ marginTop: 12 }}>
                Carrying this out would resolve:{" "}
                {counterfactual.resolved_guardrails.join(", ")}.
              </p>
            )}
          </Advanced>
        </>
      )}
    </section>
  );
}

function ActionRow({ action }: { action: ProposedAction }) {
  const isHouse = action.proposed_by === "house";

  return (
    <li className="scenario-item">
      <div className="scenario-head">
        <span className="scenario-kind">{KIND_LABEL[action.kind] ?? action.kind}</span>
        {action.symbol && <strong className="scenario-symbol">{action.symbol}</strong>}
        <span className="scenario-size">{sizeOf(action)}</span>
        <StatusBadge tone={isHouse ? "neutral" : "info"}>
          {isHouse ? "AdvisorOS rule" : action.proposed_by}
        </StatusBadge>
      </div>

      {/* Rendered whole. The policy layer already phrased this as an implication. */}
      <p className="scenario-rationale">{action.rationale}</p>

      {action.estimated_tax && (
        <p className="tiny muted scenario-tax">
          Estimated tax {money(action.estimated_tax.low_usd)}–
          {money(action.estimated_tax.high_usd)} — a range, not an estimate with error bars.{" "}
          {action.estimated_tax.assumption}
        </p>
      )}
    </li>
  );
}

/** "Sell 216 shares" / "$9,000" / "to 20%" — whichever form the action was actually sized in. */
function sizeOf(action: ProposedAction): string {
  if (action.shares != null) {
    return `${action.shares.toLocaleString(undefined, { maximumFractionDigits: 2 })} shares`;
  }
  if (action.amount_usd != null) return money(action.amount_usd);
  if (action.target_weight != null) return `to ${percent(action.target_weight, 1)}`;
  return "";
}

function BeforeAfter({ changes }: { changes: MetricChange[] }) {
  const moved = changes.filter((c) => c.before !== c.after);
  if (moved.length === 0) return null;

  return (
    <Card tone="sunk">
      <p className="metric-label" style={{ marginBottom: 10 }}>
        What would change
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Measure</th>
              <th className="num">Now</th>
              <th className="num">After</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {moved.map((change) => (
              <tr key={change.label}>
                <td>{change.label}</td>
                <td className="num">{formatMetric(change.label, change.before)}</td>
                <td className="num">{formatMetric(change.label, change.after)}</td>
                <td>
                  {change.improved === true && <StatusBadge tone="good">Better</StatusBadge>}
                  {change.improved === false && <StatusBadge tone="risk">Worse</StatusBadge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tiny muted" style={{ marginTop: 10 }}>
        Recomputed by applying every action to a copy of your figures and running the same
        analysis again. An action that fails to move what it targeted is reported rather than
        quietly kept.
      </p>
    </Card>
  );
}

function formatMetric(label: string, value: number): string {
  if (RATE_LABELS.has(label)) return percent(value, 1);
  if (label.includes("months") || label.includes("positions")) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  return money(value);
}

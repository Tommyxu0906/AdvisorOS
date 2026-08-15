import type { Counterfactual, PortfolioScenario, ProposedAction, Sensitivity } from "../types";

/** Computed scenarios, shown before anyone pays for an opinion about them.
 *
 * Three rules govern the wording here, and all three exist to stop a computed number reading as
 * more authoritative than its inputs deserve.
 *
 * **Scenarios, not instructions.** "Sell 213 shares" is advice. "Under a 20% threshold this
 * implies reducing about 213 shares" is analysis, and it is what the disclaimer says this
 * product produces. The rationale text arrives already phrased that way from the policy layer —
 * this component must not re-summarize it into something shorter and blunter.
 *
 * **Whose number.** A house rule and an advisor's view are rendered differently on purpose. The
 * credit-card paydown is AdvisorOS policy; presenting it under an investor's name would put
 * words in a dead man's mouth.
 *
 * **Nothing is re-derived here.** `holds_up`, `fragile`, `improved` and the summary sentences
 * are computed server-side and rendered as given. Recomputing any of them in TSX would put the
 * rule in two languages, and the copy that drifts would be the one users read.
 */
export function ScenarioPanel({ scenario }: { scenario: PortfolioScenario | null }) {
  if (!scenario) return null;

  const { action_set, counterfactual, sensitivity } = scenario;

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Computed scenario</h2>
        <span className="badge">No API key required</span>
      </div>
      <p className="muted">{scenario.headline}</p>

      {!scenario.has_actions ? (
        <p className="muted small">
          Diagnostics above still apply — this panel only reports what the position-sizing
          thresholds imply, and today they imply nothing.
        </p>
      ) : (
        <>
          {!counterfactual.holds_up && <FailedChecks counterfactual={counterfactual} />}
          <ActionTable actions={action_set.actions} />
          <BeforeAfter counterfactual={counterfactual} />
          {sensitivity && <Robustness sensitivity={sensitivity} />}
        </>
      )}

      <p className="fineprint">
        Educational analysis of scenarios implied by stated thresholds — not personalized
        investment advice, and not an instruction to trade. Every threshold is named on the line
        it drives.
      </p>
    </section>
  );
}

function ActionTable({ actions }: { actions: ProposedAction[] }) {
  const ordered = [...actions].sort((a, b) => a.sequence - b.sequence);
  return (
    <table className="scenario-table">
      <thead>
        <tr>
          <th>Step</th>
          <th>Action</th>
          <th className="num">Size</th>
          <th className="num">Tax to act</th>
        </tr>
      </thead>
      <tbody>
        {ordered.map((action) => (
          <tr key={action.action_id}>
            <td className="num">{action.sequence + 1}</td>
            <td>
              <div className="scenario-action-head">
                <strong>{describeKind(action.kind)}</strong>
                {action.symbol && <span className="scenario-symbol">{action.symbol}</span>}
                {action.proposed_by === "house" && (
                  <span className="badge" title="An AdvisorOS rule, not an advisor's view">
                    House rule
                  </span>
                )}
              </div>
              <p className="small muted">{action.rationale}</p>
            </td>
            <td className="num">{formatSize(action)}</td>
            <td className="num">
              {action.estimated_tax ? (
                formatTax(action.estimated_tax.low_usd, action.estimated_tax.high_usd)
              ) : (
                <span title="No cost basis recorded for this position">unknown</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BeforeAfter({ counterfactual }: { counterfactual: Counterfactual }) {
  const moved = counterfactual.changes.filter((c) => Math.abs(c.after - c.before) > 1e-9);
  if (!moved.length) return null;

  return (
    <div className="scenario-block">
      <h3>If you did this</h3>
      <table className="scenario-table">
        <thead>
          <tr>
            <th>Measure</th>
            <th className="num">Now</th>
            <th className="num">After</th>
          </tr>
        </thead>
        <tbody>
          {moved.map((change) => (
            <tr key={change.label}>
              <td>
                {change.label}
                {/* null means no direction is inherently good — a sale converting a holding to
                    cash is neither better nor worse, and an arrow would claim otherwise. */}
                {change.improved === true && <span className="badge free">better</span>}
                {change.improved === false && <span className="badge risk">worse</span>}
              </td>
              <td className="num">{formatMetric(change.label, change.before)}</td>
              <td className="num">{formatMetric(change.label, change.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {counterfactual.resolved_guardrails.length > 0 && (
        <p className="small">Clears: {counterfactual.resolved_guardrails.join(", ")}</p>
      )}
      {counterfactual.estimated_tax && counterfactual.estimated_tax.high_usd > 0 && (
        <p className="small muted">
          Total estimated tax{" "}
          {formatTax(
            counterfactual.estimated_tax.low_usd,
            counterfactual.estimated_tax.high_usd,
          )}
          . {counterfactual.estimated_tax.assumption}
        </p>
      )}
    </div>
  );
}

function Robustness({ sensitivity }: { sensitivity: Sensitivity }) {
  if (sensitivity.declined) return null;

  return (
    <div className="scenario-block">
      <div className="row-between">
        <h3>How much rests on the threshold</h3>
        <span className={sensitivity.fragile ? "badge risk" : "badge free"}>
          {sensitivity.fragile ? "Fragile" : "Robust"}
        </span>
      </div>

      {sensitivity.summary.map((line) => (
        <p key={line} className="small muted">
          {line}
        </p>
      ))}

      <table className="scenario-table">
        <thead>
          <tr>
            <th>Threshold</th>
            <th>Implication</th>
            <th className="num">Would raise</th>
          </tr>
        </thead>
        <tbody>
          {sensitivity.points.map((point) => (
            <tr
              key={point.cap}
              className={Math.abs(point.cap - sensitivity.baseline) < 1e-9 ? "highlight" : ""}
            >
              <td className="num">{percent(point.cap)}</td>
              <td>{point.acts ? "implies trimming" : "implies holding"}</td>
              <td className="num">{point.acts ? money(point.proceeds_usd) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Shown rather than hidden. A plan that fails its own arithmetic is a bug in a policy, and
 *  quietly dropping it would hide the bug from the only people positioned to report it. */
function FailedChecks({ counterfactual }: { counterfactual: Counterfactual }) {
  return (
    <div className="notice violation">
      <strong>This scenario did not survive its own checks.</strong>
      <ul className="small">
        {!counterfactual.feasible && <li>The steps are not possible against your balances.</li>}
        {counterfactual.introduced_guardrails.length > 0 && (
          <li>It would trigger: {counterfactual.introduced_guardrails.join(", ")}</li>
        )}
        {counterfactual.ineffective_actions.length > 0 && (
          <li>Some steps do not move the number they target.</li>
        )}
        {counterfactual.unapplied.length > 0 && (
          <li>Some steps could not be modelled, so their effect is unknown.</li>
        )}
      </ul>
      <p className="small">Shown for transparency rather than as a candidate to act on.</p>
    </div>
  );
}

const KIND_LABELS: Record<string, string> = {
  trim_position: "Reduce position",
  add_position: "Add to position",
  rebalance_to_target: "Rebalance",
  pay_down_debt: "Pay down debt",
  build_emergency_fund: "Build reserve",
  redirect_cashflow: "Redirect monthly savings",
  hold: "Hold",
};

function describeKind(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

function formatSize(action: ProposedAction): string {
  if (action.shares !== null) return `${action.shares.toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })} sh`;
  if (action.amount_usd !== null) return money(action.amount_usd);
  if (action.target_weight !== null) return percent(action.target_weight);
  return "—";
}

/** Always a range. A single figure would read as computed when the holding period that decides
 *  it was never recorded. */
function formatTax(low: number, high: number): string {
  if (low === high) return money(low);
  return `${money(low)}–${money(high)}`;
}

function money(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function percent(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

/** The change table mixes percentages, dollars and counts under one column, so the unit has to
 *  come from the label rather than from a single formatter. */
function formatMetric(label: string, value: number): string {
  if (label.includes("weight") || label.includes("HHI") || label.includes("rate")) {
    return label.includes("HHI") ? value.toFixed(3) : percent(value);
  }
  if (label.includes("months") || label.includes("positions")) return value.toFixed(1);
  return money(value);
}

/**
 * One page, one decision, start to finish.
 *
 * The old flow asked a question on Analysis and then required a manual jump to Advisors to
 * connect a key, pick a team, choose a depth, run, and read the report. Two navigation items for
 * one task, in an order that reflected how the code is organised rather than what the user is
 * doing. Everything below is the same set of operations arranged as a single downward flow:
 *
 *     ask  ->  free deterministic analysis  ->  proposed committee  ->  preflight  ->  brief
 *
 * The Investor Library is still there, but as somewhere to go and browse — not a station you must
 * pass through to finish a run. `Customize` opens it in a drawer and returns you here.
 *
 * Priority is the other change. The old analysis panel printed eight equal stats and a
 * seven-dimension need vector with raw 0.00-1.00 scores. Here a blocking guardrail outranks a
 * metric, a metric outranks a diagnostic, and the raw scores live behind a disclosure.
 */

import { useState } from "react";
import type {
  AdvisorSummary,
  AnalysisDepth,
  EstimateResponse,
  RunResponse,
  SelectResponse,
} from "../types";
import type { HoldingDraft, ProfileDraft } from "../lib/draft";
import { money, months, percent, years } from "../lib/units";
import { navigate } from "../lib/router";
import { Advanced, Card, EmptyState, InlineAlert, Metric, SectionHeader, StatusBadge } from "../ui";
import { CommitteeSetup } from "../components/CommitteeSetup";
import { ScenarioPanel } from "../components/ScenarioPanel";
import { RunPreflight } from "../components/RunPreflight";
import { ReportView } from "../components/ReportView";

export function DecisionWorkspace({
  profile,
  holdings,
  question,
  depth,
  selection,
  estimate,
  advisors,
  manualIds,
  result,
  running,
  error,
  demo,
  onQuestion,
  onDepth,
  onToggleAdvisor,
  onResetAdvisors,
  onRun,
}: {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  question: string;
  depth: AnalysisDepth;
  selection: SelectResponse | null;
  estimate: EstimateResponse | null;
  advisors: AdvisorSummary[];
  manualIds: Set<string> | null;
  result: RunResponse | null;
  running: boolean;
  error: string | null;
  demo: boolean;
  onQuestion: (q: string) => void;
  onDepth: (d: AnalysisDepth) => void;
  onToggleAdvisor: (id: string) => void;
  onResetAdvisors: () => void;
  onRun: () => void;
}) {
  const analytics = selection?.analytics ?? null;
  const portfolio = selection?.portfolio_analytics ?? null;
  const guardrails = selection?.guardrails ?? [];
  const blocking = guardrails.filter((g) => g.severity === "blocking");
  const cautions = guardrails.filter((g) => g.severity !== "blocking");

  const totalValue = holdings.reduce((sum, h) => sum + (h.market_value ?? 0), 0);
  const largest = [...holdings].sort(
    (a, b) => (b.market_value ?? 0) - (a.market_value ?? 0),
  )[0];
  const nearestGoal = [...profile.goals]
    .filter((g) => g.years_until_needed !== null)
    .sort((a, b) => (a.years_until_needed ?? 0) - (b.years_until_needed ?? 0))[0];
  const worstDebt = [...profile.debts].sort((a, b) => (b.apr ?? 0) - (a.apr ?? 0))[0];

  return (
    <>
      <div className="page-head">
        <h1>What decision are you evaluating?</h1>
        <p className="lede">
          Describe it in your own words. The analysis below updates as you type and costs nothing —
          it never calls a model.
        </p>
      </div>

      <Card>
        <label htmlFor="question">Your question</label>
        <textarea
          id="question"
          rows={3}
          value={question}
          onChange={(e) => onQuestion(e.target.value)}
          placeholder="Should I sell some NVDA to pay off my credit card?"
        />
        {!question.trim() && (
          <p className="small muted" style={{ margin: "10px 0 0" }}>
            Concrete beats broad. "Should I pay off the card before adding to my index funds?"
            routes better than "how am I doing?"
          </p>
        )}
      </Card>

      {/* --- the situation, at a glance ---------------------------------------- */}
      <section>
        <SectionHeader
          title="Your position"
          hint={demo ? "Sample household figures." : undefined}
          action={
            <button className="secondary" onClick={() => navigate("portfolio")}>
              Edit portfolio
            </button>
          }
        />
        <div className="metric-grid">
          <Metric label="Portfolio" value={money(totalValue)} size="large" />
          <Metric
            label="Largest position"
            value={largest?.symbol ?? "—"}
            detail={
              largest && totalValue > 0
                ? percent((largest.market_value ?? 0) / totalValue)
                : "no holdings yet"
            }
            tone={
              largest && totalValue > 0 && (largest.market_value ?? 0) / totalValue > 0.25
                ? "risk"
                : "default"
            }
          />
          <Metric
            label="Cash reserve"
            value={analytics ? months(analytics.emergency_fund_months) : "—"}
            tone={analytics && analytics.emergency_fund_months < 3 ? "risk" : "good"}
          />
          <Metric
            label="Highest-rate debt"
            value={worstDebt ? percent(worstDebt.apr ?? 0, 1) : "None"}
            detail={worstDebt?.name}
            tone={worstDebt && (worstDebt.apr ?? 0) > 0.1 ? "risk" : "good"}
          />
          <Metric
            label="Nearest goal"
            value={nearestGoal?.name ?? "None set"}
            detail={nearestGoal ? years(nearestGoal.years_until_needed) : undefined}
          />
        </div>
      </section>

      {error && <InlineAlert tone="risk" title="Something went wrong">{error}</InlineAlert>}

      {/* --- the free analysis --------------------------------------------------- */}
      {!analytics && (
        <EmptyState title="Ask a question to see the analysis">
          The committee is selected from your actual numbers and what you are asking about, so
          there is nothing to compute until both exist.
        </EmptyState>
      )}

      {analytics && (
        <section>
          <SectionHeader
            title="Analysis"
            hint="Computed deterministically from your figures."
            action={<StatusBadge tone="good">Deterministic · no AI call</StatusBadge>}
          />

          {/* 1. Blocking first. These are the reason the product exists. */}
          {blocking.length > 0 && (
            <div className="stack" style={{ marginBottom: 16 }}>
              {blocking.map((g) => (
                <InlineAlert key={g.code} tone="risk" title={g.message}>
                  {g.detail}
                </InlineAlert>
              ))}
            </div>
          )}

          {/* 2. Then cautions. */}
          {cautions.length > 0 && (
            <div className="stack" style={{ marginBottom: 16 }}>
              {cautions.map((g) => (
                <InlineAlert key={g.code} tone="warn" title={g.message}>
                  {g.detail}
                </InlineAlert>
              ))}
            </div>
          )}

          {blocking.length === 0 && cautions.length === 0 && (
            <InlineAlert tone="good" title="No guardrails triggered">
              Nothing in your situation blocks the decision on financial grounds.
            </InlineAlert>
          )}

          {/* 3. Headline financial state. */}
          <div className="metric-grid" style={{ marginTop: 16 }}>
            <Metric label="Net worth" value={money(analytics.net_worth)} />
            <Metric label="Savings rate" value={percent(analytics.savings_rate)} />
            <Metric
              label="Debt service"
              value={percent(analytics.debt_service_ratio)}
              tone={analytics.debt_service_ratio > 0.36 ? "risk" : "default"}
            />
            <Metric
              label="Life stage"
              value={analytics.life_stage.replace(/_/g, " ")}
              tone="muted"
            />
          </div>

          {/* 4. Decision priorities — named for what they mean, raw scores hidden. */}
          <Advanced label="Decision priorities and portfolio diagnostics">
            <NeedVector analytics={analytics} portfolio={portfolio} />
          </Advanced>
        </section>
      )}

      {/* 5. What those numbers imply — still deterministic, still free. This is the last
             thing computed without a key, and the thing the committee argues about. */}
      <ScenarioPanel scenario={selection?.scenario ?? null} />

      {/* --- the committee, in the same flow -------------------------------------- */}
      {selection && (
        <CommitteeSetup
          selection={selection.selection}
          advisors={advisors}
          manualIds={manualIds}
          onToggle={onToggleAdvisor}
          onReset={onResetAdvisors}
        />
      )}

      {selection && (
        <RunPreflight
          question={question}
          selection={selection.selection}
          advisors={advisors}
          manualIds={manualIds}
          depth={depth}
          estimate={estimate}
          blocking={blocking}
          running={running}
          onDepth={onDepth}
          onRun={onRun}
        />
      )}

      {result && <ReportView report={result.report} usage={result.usage} />}
    </>
  );
}

/**
 * The need vector, renamed and demoted.
 *
 * It is genuinely useful and it is genuinely not a headline: "behavioral_risk 0.73" is a number
 * whose scale nobody outside this codebase knows. Behind a disclosure, described in words, with
 * the raw figure available for anyone who wants it.
 */
function NeedVector({
  analytics,
  portfolio,
}: {
  analytics: NonNullable<SelectResponse["analytics"]>;
  portfolio: SelectResponse["portfolio_analytics"];
}) {
  const needs = Object.entries(analytics.need_vector).sort((a, b) => b[1] - a[1]);
  const [showRaw, setShowRaw] = useState(false);

  return (
    <>
      <p className="small muted">
        Where this profile most needs attention. These weights choose which investor lenses are
        convened; they are relative priorities, not scores out of ten.
      </p>

      <div className="needs" style={{ marginTop: 12 }}>
        {needs.map(([key, value]) => (
          <div className="need" key={key}>
            <span className="need-label">{key.replace(/_/g, " ")}</span>
            <span className="bar">
              <span style={{ width: `${Math.round(value * 100)}%` }} />
            </span>
            <span className="need-score">{showRaw ? value.toFixed(2) : rank(value)}</span>
          </div>
        ))}
      </div>

      <button className="linklike tap small" onClick={() => setShowRaw((s) => !s)}>
        {showRaw ? "Show as priority" : "Show raw weights"}
      </button>

      {portfolio && (
        <>
          <hr className="divider" />
          <h3>Portfolio diagnostics</h3>
          <div className="metric-grid" style={{ marginTop: 10 }}>
            <Metric label="Positions" value={portfolio.holding_count} />
            <Metric
              label="Largest weight"
              value={percent(portfolio.largest_weight)}
              tone={portfolio.largest_weight > 0.25 ? "risk" : "default"}
            />
            <Metric
              label="Effective positions"
              value={portfolio.effective_holdings.toFixed(1)}
              detail="Diversification, adjusted for size"
            />
            <Metric label="Concentration (HHI)" value={portfolio.hhi.toFixed(3)} tone="muted" />
          </div>
        </>
      )}
    </>
  );
}

function rank(value: number): string {
  if (value >= 0.66) return "High";
  if (value >= 0.33) return "Medium";
  return "Low";
}

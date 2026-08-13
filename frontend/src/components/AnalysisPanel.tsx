import type { Guardrail, PortfolioAnalytics, ProfileAnalytics } from "../types";

const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
const money = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 0 });

export function AnalysisPanel({
  analytics,
  portfolio,
  guardrails,
}: {
  analytics: ProfileAnalytics;
  portfolio: PortfolioAnalytics | null;
  guardrails: Guardrail[];
}) {
  const needs = Object.entries(analytics.need_vector).sort((a, b) => b[1] - a[1]);

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Financial analysis</h2>
        <span className="badge free">computed locally · no API key used</span>
      </div>

      <div className="stats">
        <Stat label="Life stage" value={analytics.life_stage.replace(/_/g, " ")} />
        <Stat label="Net worth" value={money(analytics.net_worth)} />
        <Stat label="Savings rate" value={pct(analytics.savings_rate)} />
        <Stat
          label="Emergency fund"
          value={`${analytics.emergency_fund_months.toFixed(1)} mo`}
          warn={analytics.emergency_fund_months < 3}
        />
        <Stat label="Debt / income" value={`${analytics.debt_to_income.toFixed(2)}×`} />
        <Stat
          label="Debt service"
          value={pct(analytics.debt_service_ratio)}
          warn={analytics.debt_service_ratio > 0.36}
        />
        <Stat
          label="High-APR debt"
          value={money(analytics.high_apr_debt_balance)}
          warn={analytics.high_apr_debt_balance > 0}
        />
        <Stat label="Annual interest cost" value={money(analytics.annual_interest_cost)} />
      </div>

      {portfolio && portfolio.holding_count > 0 && (
        <>
          <h3>Portfolio</h3>
          <div className="stats">
            <Stat label="Value" value={money(portfolio.total_value)} />
            <Stat label="Positions" value={String(portfolio.holding_count)} />
            <Stat
              label={`Largest (${portfolio.largest_holding_symbol})`}
              value={pct(portfolio.largest_weight)}
              warn={portfolio.largest_weight > 0.25}
            />
            <Stat label="Effective positions" value={portfolio.effective_holdings.toFixed(1)} />
            <Stat label="Equity share" value={pct(portfolio.equity_share)} />
            <Stat label="In taxable" value={pct(portfolio.taxable_share)} />
          </div>
        </>
      )}

      <h3>Where this profile needs the most help</h3>
      <div className="needs">
        {needs.map(([dim, score]) => (
          <div className="need" key={dim}>
            <span className="need-label">{dim.replace(/_/g, " ")}</span>
            <div className="bar">
              <div className="bar-fill" style={{ width: `${score * 100}%` }} />
            </div>
            <span className="need-score">{score.toFixed(2)}</span>
          </div>
        ))}
      </div>

      {analytics.notable_findings.length > 0 && (
        <>
          <h3>Notable findings</h3>
          <ul>
            {analytics.notable_findings.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </>
      )}

      <h3>Guardrails</h3>
      {guardrails.length === 0 ? (
        <p className="muted">No hard guardrails were triggered.</p>
      ) : (
        <ul className="guardrails">
          {guardrails.map((g) => (
            <li key={g.code} className={`guardrail ${g.severity}`}>
              <span className="severity">{g.severity}</span>
              <div>
                <strong>{g.message}</strong>
                {g.detail && <div className="muted small">{g.detail}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="fineprint">
        Guardrails are enforced by code, not by the model. Advisors are told they may not
        recommend anything contradicting a blocking item, and the final report is re-checked
        against them.
      </p>
    </section>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={`stat${warn ? " warn" : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

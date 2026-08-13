import { useState } from "react";
import type { RunResponse } from "../types";

export function ReportView({ result }: { result: RunResponse }) {
  const { report, usage } = result;
  const [showDetail, setShowDetail] = useState(false);

  return (
    <section className="panel">
      <h2>Committee report</h2>

      {report.guardrail_violations.length > 0 && (
        <div className="violation">
          <strong>Guardrail check flagged this report.</strong>
          <ul>
            {report.guardrail_violations.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="summary">{report.summary}</p>

      <Section title="Recommended actions" items={report.recommended_actions} ordered />
      <Section title="Where the committee agreed" items={report.consensus} />
      <Section title="Where it did not" items={report.disagreements} />
      <Section title="Open questions" items={report.open_questions} />

      {report.risk_challenge && (
        <>
          <h3>Risk challenge</h3>
          {report.risk_challenge.worst_case && (
            <p>
              <strong>Worst case:</strong> {report.risk_challenge.worst_case}
            </p>
          )}
          <Section title="Scenarios" items={report.risk_challenge.scenarios} />
          <Section title="Unaddressed risks" items={report.risk_challenge.unaddressed_risks} />
        </>
      )}

      <button className="secondary" onClick={() => setShowDetail((s) => !s)}>
        {showDetail ? "Hide" : "Show"} individual advisor positions
      </button>

      {showDetail && (
        <div className="advisor-detail">
          {(report.revised_analyses.length ? report.revised_analyses : report.analyses).map((a) => (
            <article key={a.advisor_id}>
              <div className="row-between">
                <h4>{a.display_name}</h4>
                <span className="muted small">confidence {a.confidence.toFixed(2)}</span>
              </div>
              {a.declined ? (
                <p className="muted">
                  Declined to opine: {a.declined_reason || "outside their stated boundaries."}
                </p>
              ) : (
                <>
                  <p>
                    <strong>{a.thesis}</strong>
                  </p>
                  <p className="muted">{a.reasoning}</p>
                  <Section title="Recommendations" items={a.recommendations} />
                  <Section title="Risks flagged" items={a.risks_flagged} />
                </>
              )}
            </article>
          ))}

          {report.critiques.length > 0 && (
            <>
              <h4>Cross-examination</h4>
              <ul>
                {report.critiques
                  .filter((c) => c.disagreement)
                  .map((c, i) => (
                    <li key={i}>
                      <strong>{c.from_advisor_id}</strong> to{" "}
                      <strong>{c.target_advisor_id}</strong>: {c.disagreement}
                    </li>
                  ))}
              </ul>
            </>
          )}
        </div>
      )}

      <CostBreakdown usage={usage} />

      <p className="fineprint">{report.disclaimer}</p>
    </section>
  );
}

function Section({
  title,
  items,
  ordered,
}: {
  title: string;
  items: string[];
  ordered?: boolean;
}) {
  if (!items.length) return null;
  const List = ordered ? "ol" : "ul";
  return (
    <>
      <h3>{title}</h3>
      <List>
        {items.map((i) => (
          <li key={i}>{i}</li>
        ))}
      </List>
    </>
  );
}

function CostBreakdown({ usage }: { usage: RunResponse["usage"] }) {
  const [open, setOpen] = useState(false);
  const cost =
    usage.estimated_cost_usd === null ? "unknown" : `$${usage.estimated_cost_usd.toFixed(4)}`;

  return (
    <div className="cost">
      <h3>What this run cost</h3>
      <div className="stats">
        <div className="stat">
          <div className="stat-label">LLM calls</div>
          <div className="stat-value">{usage.total_calls}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Input tokens</div>
          <div className="stat-value">{usage.total_input_tokens.toLocaleString()}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Output tokens</div>
          <div className="stat-value">{usage.total_output_tokens.toLocaleString()}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Cache reads</div>
          <div className="stat-value">{usage.total_cache_read_tokens.toLocaleString()}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Estimated cost</div>
          <div className="stat-value">{cost}</div>
        </div>
      </div>
      <p className="muted small">
        Paid through your Anthropic API account. This is an estimate computed from token counts
        and published list pricing ({usage.pricing_version}), not Anthropic's billing figure.
        {usage.failed_calls > 0 && ` ${usage.failed_calls} call(s) failed and produced no output.`}
      </p>

      <button className="secondary small" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide" : "Show"} breakdown
      </button>

      {open && (
        <div className="breakdown">
          <h4>By stage</h4>
          <CostTable lines={usage.by_stage} />
          <h4>By advisor</h4>
          <CostTable lines={usage.by_advisor} />
        </div>
      )}
    </div>
  );
}

function CostTable({ lines }: { lines: RunResponse["usage"]["by_stage"] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>label</th>
          <th>calls</th>
          <th>in</th>
          <th>out</th>
          <th>est. cost</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => (
          <tr key={l.label}>
            <td>{l.label.replace(/_/g, " ")}</td>
            <td>{l.calls}</td>
            <td>{l.input_tokens.toLocaleString()}</td>
            <td>{l.output_tokens.toLocaleString()}</td>
            <td>
              {l.estimated_cost_usd === null ? "—" : `$${l.estimated_cost_usd.toFixed(4)}`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

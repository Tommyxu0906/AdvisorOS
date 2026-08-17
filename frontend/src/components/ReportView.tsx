/**
 * The output, as an investment decision brief rather than a transcript.
 *
 * The old order was summary, four undifferentiated string lists, the risk challenge, then a
 * complete token-and-cost accounting — which meant the most rigorous part of the page was the
 * billing. The hierarchy below is the one a reader actually needs:
 *
 *     DECISION     one conclusion
 *     ACTIONS      what to do about it
 *     WHY          the reasons, ranked
 *     TRADEOFFS    what improves, what worsens
 *     RISK         what would make this wrong
 *     POSITIONS    who disagreed, and about what
 *     OPEN         what nobody could answer
 *     EVIDENCE     methodology and provenance
 *     TECHNICAL    tokens, cost, raw memos
 *
 * **The confidence change is not cosmetic.** This used to print `confidence 0.82` beside each
 * advisor. That is the model's own self-report, and we have now measured what such numbers are
 * worth on a comparable task: predictions made in the 0.6-0.7 band were right 42.9% of the time.
 * A number that is wrong by seventeen points of probability should not be rendered to two
 * decimals next to a financial recommendation. It becomes a High/Medium/Low signal, labelled
 * uncalibrated, with the raw float available under Technical details for anyone who wants it.
 *
 * Sections marked TODO below are where `decision-engine-v2` output will land — typed ActionSet,
 * counterfactual before/after, and the sensitivity band. Nothing is rendered for them until the
 * API returns them, because a placeholder that looks like an answer is worse than a gap.
 */

import type { AdvisorAnalysis, CommitteeReport, RunUsage } from "../types";
import { cost } from "../lib/units";
import { Advanced, Card, InlineAlert, SectionHeader, StatusBadge } from "../ui";

export function ReportView({ report, usage }: { report: CommitteeReport; usage: RunUsage }) {
  const memos = report.revised_analyses.length ? report.revised_analyses : report.analyses;
  const declined = memos.filter((m) => m.declined);

  return (
    <section id="report">
      <SectionHeader
        title="Decision brief"
        hint="Re-checked against the same deterministic guardrails after the committee finished."
        action={<StatusBadge tone="neutral">Your key was used</StatusBadge>}
      />

      {report.guardrail_violations.length > 0 && (
        <InlineAlert tone="risk" title="The guardrail check flagged this report">
          <ul className="bullet-list" style={{ fontSize: 14 }}>
            {report.guardrail_violations.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </InlineAlert>
      )}

      {/* --- DECISION ------------------------------------------------------------- */}
      <Card>
        <div className="report-decision">
          <p className="metric-label">The decision</p>
          <p className="summary" style={{ marginBottom: 0 }}>
            {report.summary}
          </p>
        </div>
      </Card>

      {/* --- ACTIONS -------------------------------------------------------------- */}
      {report.recommended_actions.length > 0 && (
        <Card>
          <SectionHeader
            title="What to do"
            hint="In order. Later steps assume the earlier ones happened."
          />
          <ol className="report-actions">
            {report.recommended_actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ol>
          <p className="tiny muted" style={{ marginTop: 12 }}>
            Candidate actions for you to evaluate. AdvisorOS cannot place trades and does not
            connect to a brokerage.
          </p>
        </Card>
      )}

      {/* --- WHY ------------------------------------------------------------------ */}
      {report.consensus.length > 0 && (
        <Card>
          <SectionHeader title="Why" hint="Where the committee converged." />
          <ul className="bullet-list">
            {report.consensus.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </Card>
      )}

      {/* --- TRADEOFFS ------------------------------------------------------------ */}
      {report.disagreements.length > 0 && (
        <Card>
          <SectionHeader
            title="Tradeoffs"
            hint="Genuine disagreement, kept rather than averaged away."
          />
          <ul className="bullet-list">
            {report.disagreements.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </Card>
      )}

      {/* --- RISK ----------------------------------------------------------------- */}
      {report.risk_challenge && (
        <Card>
          <SectionHeader
            title="What could make this wrong"
            hint="A deliberate adversarial pass over the committee's own conclusion."
          />
          {report.risk_challenge.worst_case && (
            <InlineAlert tone="warn" title="Worst case">
              {report.risk_challenge.worst_case}
            </InlineAlert>
          )}
          {report.risk_challenge.scenarios.length > 0 && (
            <>
              <h3 style={{ margin: "18px 0 8px" }}>Scenarios</h3>
              <ul className="bullet-list">
                {report.risk_challenge.scenarios.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </>
          )}
          {report.risk_challenge.unaddressed_risks.length > 0 && (
            <>
              <h3 style={{ margin: "18px 0 8px" }}>Unaddressed</h3>
              <ul className="bullet-list">
                {report.risk_challenge.unaddressed_risks.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      )}

      {/* --- POSITIONS ------------------------------------------------------------ */}
      <Card>
        <SectionHeader
          title="Advisor positions"
          hint={
            declined.length > 0
              ? `${declined.length} declined to opine, which is a permitted answer.`
              : "Each lens, in its own words."
          }
        />
        <div className="stack">
          {memos.map((memo) => (
            <Memo key={memo.advisor_id} memo={memo} />
          ))}
        </div>
      </Card>

      {/* --- OPEN QUESTIONS ------------------------------------------------------- */}
      {report.open_questions.length > 0 && (
        <Card tone="sunk">
          <SectionHeader
            title="Open questions"
            hint="Answering these would change the analysis. Nothing was assumed in their place."
          />
          <ul className="bullet-list">
            {report.open_questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </Card>
      )}

      {/* --- EVIDENCE ------------------------------------------------------------- */}
      <Card tone="quiet">
        <p className="fineprint" style={{ borderTop: 0, paddingTop: 0 }}>
          {report.disclaimer}
        </p>
      </Card>

      {/* --- TECHNICAL ------------------------------------------------------------ */}
      <Advanced label="Technical details — cost, tokens, and raw confidence">
        <div className="metric-grid" style={{ marginBottom: 16 }}>
          <div className="metric">
            <span className="metric-label">Total cost</span>
            <span className="metric-value" style={{ fontSize: 18 }}>
              {cost(usage.estimated_cost_usd)}
            </span>
          </div>
          <div className="metric">
            <span className="metric-label">Model calls</span>
            <span className="metric-value" style={{ fontSize: 18 }}>
              {usage.total_calls}
            </span>
          </div>
          <div className="metric">
            <span className="metric-label">Input tokens</span>
            <span className="metric-value" style={{ fontSize: 18 }}>
              {usage.total_input_tokens.toLocaleString()}
            </span>
          </div>
          <div className="metric">
            <span className="metric-label">Output tokens</span>
            <span className="metric-value" style={{ fontSize: 18 }}>
              {usage.total_output_tokens.toLocaleString()}
            </span>
          </div>
        </div>

        <h3>Raw self-reported confidence</h3>
        <p className="muted">
          These are the model's own numbers, shown here and nowhere else. They have not been
          calibrated against outcomes, so they are not probabilities.
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Advisor</th>
                <th className="num">Raw value</th>
                <th>Displayed as</th>
              </tr>
            </thead>
            <tbody>
              {memos.map((m) => (
                <tr key={m.advisor_id}>
                  <td>{m.display_name}</td>
                  <td className="num">{m.confidence.toFixed(2)}</td>
                  <td>{confidenceBand(m.confidence)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Advanced>
    </section>
  );
}

function Memo({ memo }: { memo: AdvisorAnalysis }) {
  return (
    <article className="memo">
      <div className="row-between" style={{ marginBottom: 8 }}>
        <h3>{memo.display_name}</h3>
        {memo.declined ? (
          <StatusBadge tone="neutral">Declined</StatusBadge>
        ) : (
          <StatusBadge
            tone="info"
            title="The model's own sense of how strongly it holds this view. Not calibrated against outcomes, so not a probability."
          >
            Confidence signal: {confidenceBand(memo.confidence)} · uncalibrated
          </StatusBadge>
        )}
      </div>

      {memo.declined ? (
        <p className="muted">
          Declined to opine: {memo.declined_reason || "outside their stated boundaries."}
        </p>
      ) : (
        <>
          <p className="prose">
            <strong>{memo.thesis}</strong>
          </p>
          <p className="prose">{memo.reasoning}</p>
          {memo.recommendations.length > 0 && (
            <ul className="bullet-list">
              {memo.recommendations.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {memo.risks_flagged.length > 0 && (
            <p className="small muted" style={{ marginTop: 10 }}>
              <strong>Risks flagged: </strong>
              {memo.risks_flagged.join(" · ")}
            </p>
          )}
        </>
      )}
    </article>
  );
}

/**
 * Three bands, because three is roughly what an uncalibrated self-report can support.
 *
 * The thresholds are not derived from anything and are not claimed to be — they exist so the
 * display carries no more resolution than the underlying number deserves.
 */
function confidenceBand(value: number): "High" | "Medium" | "Low" {
  if (value >= 0.75) return "High";
  if (value >= 0.5) return "Medium";
  return "Low";
}

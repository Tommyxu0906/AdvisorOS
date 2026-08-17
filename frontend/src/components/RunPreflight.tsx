/**
 * The last screen before money is spent.
 *
 * One compact review of what is about to happen, in the order someone checks it: the question,
 * who is answering, how thoroughly, what it costs. Token counts are not the visual focus — they
 * are an implementation fact about the estimate, and they live behind the disclosure with the
 * per-stage breakdown.
 *
 * Two honesty rules are enforced here rather than remembered:
 *
 * **Cost is rounded.** `$0.0837` implies a quote; this is a projection from average token counts
 * and the caveat says so. `~$0.08` is the same information without the false precision.
 *
 * **Progress is not invented.** The backend does not stream stage transitions, so the running
 * state describes what the run contains and does not animate a percentage that would be fiction.
 * If streaming is added later, this is where the real stages would land.
 */

import type {
  AdvisorSummary,
  AnalysisDepth,
  CommitteeSelection,
  EstimateResponse,
  Guardrail,
} from "../types";
import { cost } from "../lib/units";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";
import { Advanced, Card, InlineAlert, SectionHeader, StatusBadge } from "../ui";
import { ConnectKeyButton } from "./ConnectKeyButton";

const DEPTHS: { id: AnalysisDepth; label: string; blurb: string }[] = [
  { id: "quick", label: "Quick", blurb: "Independent analysis, then one synthesis." },
  { id: "balanced", label: "Balanced", blurb: "Adds cross-examination and a risk challenge." },
  { id: "deep", label: "Deep", blurb: "Adds revised memos after critique." },
];

export function RunPreflight({
  question,
  selection,
  advisors,
  manualIds,
  depth,
  estimate,
  blocking,
  running,
  onDepth,
  onRun,
}: {
  question: string;
  selection: CommitteeSelection;
  advisors: AdvisorSummary[];
  manualIds: Set<string> | null;
  depth: AnalysisDepth;
  estimate: EstimateResponse | null;
  blocking: Guardrail[];
  running: boolean;
  onDepth: (d: AnalysisDepth) => void;
  onRun: () => void;
}) {
  const { isConnected, model } = useAnthropicConnection();

  const team = manualIds
    ? advisors.filter((a) => manualIds.has(a.advisor_id)).map((a) => a.display_name)
    : selection.selected.map((s) => s.display_name);

  const ready = question.trim() !== "" && team.length > 0;

  return (
    <section>
      <SectionHeader
        title="Run the AI review"
        hint="Everything above was free. This step bills your Anthropic account."
      />

      <Card>
        <div className="metric-grid" style={{ marginBottom: 16 }}>
          <PreflightCell label="Committee" value={`${team.length} advisors`} detail={team.join(", ")} />
          <PreflightCell
            label="Depth"
            value={DEPTHS.find((d) => d.id === depth)?.label ?? depth}
            detail={`${estimate?.expected_llm_calls ?? "—"} model calls`}
          />
          <PreflightCell
            label="Estimated cost"
            value={estimate ? `~${cost(estimate.estimated_cost_usd)}` : "—"}
            detail="Billed to your key"
          />
          <PreflightCell label="Model" value={model} detail="Your choice, in Settings" />
        </div>

        <label>Analysis depth</label>
        <div className="choice-set" style={{ marginBottom: 16 }}>
          {DEPTHS.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`choice${depth === d.id ? " selected" : ""}`}
              aria-pressed={depth === d.id}
              onClick={() => onDepth(d.id)}
            >
              <span className="choice-label">{d.label}</span>
              <span className="choice-hint">{d.blurb}</span>
            </button>
          ))}
        </div>

        {blocking.length > 0 && (
          <InlineAlert tone="warn" title="A guardrail is already triggered">
            The committee is told about this as a hard constraint, and the report is re-checked
            against it afterwards. It will not be argued away.
          </InlineAlert>
        )}

        {!isConnected && (
          <InlineAlert
            tone="info"
            title="AI reasoning requires your Anthropic key"
            action={<ConnectKeyButton />}
          >
            Held in this page's memory for the session only — never written to storage, a
            database, or a saved run.
          </InlineAlert>
        )}

        <div className="row-between" style={{ marginTop: 18 }}>
          <div>
            {running ? (
              <StatusBadge tone="info">Reviewing your decision…</StatusBadge>
            ) : (
              <span className="small muted">
                {ready ? "Ready when you are." : "Ask a question first."}
              </span>
            )}
            {running && (
              <p className="small muted" style={{ margin: "8px 0 0", maxWidth: "48ch" }}>
                This run includes independent analysis, challenge, and synthesis. It usually takes
                under a minute.
              </p>
            )}
          </div>
          <button
            className="primary large"
            disabled={!ready || !isConnected || running}
            onClick={onRun}
          >
            {running ? "Running…" : "Run AI review"}
          </button>
        </div>

        {estimate && (
          <Advanced label="Cost basis and stage breakdown">
            <p>{estimate.basis}</p>
            <p className="muted">{estimate.caveat}</p>
            <div className="table-scroll">
              <table>
                <tbody>
                  <tr>
                    <td className="muted">Stages</td>
                    <td>{estimate.stages.join(" → ")}</td>
                  </tr>
                  <tr>
                    <td className="muted">Expected model calls</td>
                    <td className="num">{estimate.expected_llm_calls}</td>
                  </tr>
                  <tr>
                    <td className="muted">Estimated input tokens</td>
                    <td className="num">{estimate.estimated_input_tokens.toLocaleString()}</td>
                  </tr>
                  <tr>
                    <td className="muted">Estimated output tokens</td>
                    <td className="num">{estimate.estimated_output_tokens.toLocaleString()}</td>
                  </tr>
                  <tr>
                    <td className="muted">Pricing table</td>
                    <td>{estimate.pricing_version}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Advanced>
        )}
      </Card>
    </section>
  );
}

function PreflightCell({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value" style={{ fontSize: 17 }}>
        {value}
      </span>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  );
}

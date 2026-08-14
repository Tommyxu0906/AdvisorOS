import type { AdvisorSummary, AnalysisDepth, CommitteeSelection, EstimateResponse } from "../types";

const DEPTHS: { id: AnalysisDepth; label: string; blurb: string }[] = [
  { id: "quick", label: "Quick", blurb: "Independent analysis, then one synthesis." },
  {
    id: "balanced",
    label: "Balanced",
    blurb: "Adds cross-examination and a risk challenge.",
  },
  {
    id: "deep",
    label: "Deep",
    blurb: "Adds revised memos after critique. Most thorough, most expensive.",
  },
];

export function CommitteePreview({
  selection,
  manualSelection,
  depth,
  estimate,
  onDepth,
  onRun,
  running,
  canRun,
}: {
  selection: CommitteeSelection;
  /** When set, the user hand-picked the team in the Your Team panel — this run uses it instead
   *  of the deterministic selection above. */
  manualSelection: AdvisorSummary[] | null;
  depth: AnalysisDepth;
  estimate: EstimateResponse | null;
  onDepth: (d: AnalysisDepth) => void;
  onRun: () => void;
  running: boolean;
  canRun: boolean;
}) {
  return (
    <section className="panel">
      <div className="row-between">
        <h2>Your committee</h2>
        <span className="badge free">
          {manualSelection ? "hand-picked · no API key used" : "selected deterministically · no API key used"}
        </span>
      </div>

      <div className="depth-picker">
        {DEPTHS.map((d) => (
          <button
            key={d.id}
            className={`depth${depth === d.id ? " active" : ""}`}
            onClick={() => onDepth(d.id)}
          >
            <strong>{d.label}</strong>
            <span className="muted small">{d.blurb}</span>
          </button>
        ))}
      </div>

      {manualSelection ? (
        <ul className="advisors">
          {manualSelection.map((a) => (
            <li key={a.advisor_id}>
              <strong>{a.display_name}</strong>
              <div className="muted small">{a.one_line}</div>
            </li>
          ))}
        </ul>
      ) : (
        <>
          <ul className="advisors">
            {selection.selected.map((a) => (
              <li key={a.advisor_id}>
                <div className="row-between">
                  <strong>{a.display_name}</strong>
                  <span className="muted small">score {a.score.toFixed(2)}</span>
                </div>
                <div className="muted small">{a.rationale}</div>
              </li>
            ))}
          </ul>

          {selection.uncovered_dimensions.length > 0 && (
            <p className="warn-text">
              No available advisor strongly covers{" "}
              {selection.uncovered_dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}. Treat
              conclusions on those dimensions as weakly supported.
            </p>
          )}
          {selection.notes.map((n) => (
            <p className="muted small" key={n}>
              {n}
            </p>
          ))}
        </>
      )}

      {estimate && (
        <div className="estimate">
          <h3>Before you spend anything</h3>
          <p>
            This workflow runs <strong>{estimate.stages.join(" → ")}</strong> across{" "}
            <strong>{(manualSelection ?? selection.selected).length} advisors</strong>, which is{" "}
            <strong>{estimate.expected_llm_calls} LLM calls</strong>.
          </p>
          <p>
            Estimated cost:{" "}
            <strong>
              {estimate.estimated_cost_usd === null
                ? "unknown for this model"
                : `$${estimate.estimated_cost_usd.toFixed(3)}`}
            </strong>{" "}
            <span className="muted small">(pricing {estimate.pricing_version})</span>
          </p>
          <p className="fineprint">{estimate.caveat}</p>
        </div>
      )}

      <button onClick={onRun} disabled={!canRun || running}>
        {running ? "Running committee…" : "Run committee"}
      </button>
      {!canRun && (
        <p className="muted small">Connect an Anthropic API key above to enable this.</p>
      )}
    </section>
  );
}

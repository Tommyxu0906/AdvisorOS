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
        <table>
          <thead>
            <tr>
              <th>Persona</th>
              <th>Approach</th>
            </tr>
          </thead>
          <tbody>
            {manualSelection.map((a) => (
              <tr key={a.advisor_id}>
                <td>
                  <strong>{a.display_name}</strong>
                </td>
                <td>
                  <span className="sub" style={{ marginTop: 0 }}>
                    {a.one_line}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Persona</th>
                <th>Why this advisor</th>
                <th className="num" style={{ width: 80 }}>
                  Score
                </th>
              </tr>
            </thead>
            <tbody>
              {selection.selected.map((a) => (
                <tr key={a.advisor_id}>
                  <td>
                    <strong>{a.display_name}</strong>
                  </td>
                  <td>
                    <span className="sub" style={{ marginTop: 0 }}>
                      {a.rationale}
                    </span>
                  </td>
                  <td className="num">{a.score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>

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
          <div className="stats">
            <div className="stat">
              <div className="stat-label">Advisors</div>
              <div className="stat-value">{(manualSelection ?? selection.selected).length}</div>
            </div>
            <div className="stat">
              <div className="stat-label">LLM calls</div>
              <div className="stat-value">{estimate.expected_llm_calls}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Estimated cost</div>
              <div className="stat-value">
                {estimate.estimated_cost_usd === null
                  ? "—"
                  : `$${estimate.estimated_cost_usd.toFixed(3)}`}
              </div>
            </div>
          </div>
          <p className="small muted">
            Stages: {estimate.stages.join(" → ")}. Billed to your Anthropic account at pricing{" "}
            {estimate.pricing_version}.
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

import { useEffect, useState } from "react";
import { getRun, listRuns } from "../api";
import { useAuth } from "../context/AuthContext";
import type { RunDetail, RunSummary } from "../types";
import { ReportView } from "./ReportView";

/**
 * Saved committee runs for the signed-in user. Nothing here spends an Anthropic key — this is
 * pure identity + storage, reading back what committee.tsx already saved via save_run_best_effort
 * on the backend.
 */
export function HistoryPanel() {
  const { isConfigured, user, session } = useAuth();
  const accessToken = session?.access_token ?? null;

  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) {
      setRuns(null);
      return;
    }
    setLoading(true);
    setError(null);
    listRuns(accessToken)
      .then(setRuns)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load run history."))
      .finally(() => setLoading(false));
  }, [accessToken]);

  if (!isConfigured) return null;

  if (!user) {
    return (
      <section className="panel">
        <h2>History</h2>
        <p className="muted">
          Sign up or log in (top right) to see your saved committee runs here.
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="row-between">
        <h2>Your run history</h2>
        {runs && <span className="badge free">{runs.length} saved</span>}
      </div>

      {loading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error}</p>}

      {runs && runs.length === 0 && !loading && (
        <p className="muted">
          No saved runs yet. Run a committee from the Advisors page and it will show up here.
        </p>
      )}

      {runs && runs.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Question</th>
              <th>Depth</th>
              <th>Status</th>
              <th className="num">Cost</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <RunRow
                key={r.run_id}
                run={r}
                open={openRunId === r.run_id}
                onToggle={() => setOpenRunId((id) => (id === r.run_id ? null : r.run_id))}
                accessToken={accessToken!}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RunRow({
  run,
  open,
  onToggle,
  accessToken,
}: {
  run: RunSummary;
  open: boolean;
  onToggle: () => void;
  accessToken: string;
}) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || detail) return;
    setLoading(true);
    setError(null);
    getRun(accessToken, run.run_id)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load this run."))
      .finally(() => setLoading(false));
  }, [open, detail, accessToken, run.run_id]);

  const cost = run.estimated_cost_usd === null ? "—" : `$${run.estimated_cost_usd.toFixed(4)}`;
  const when = new Date(run.created_at).toLocaleString();

  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }}>
        <td>
          <strong>{run.question}</strong>
          <div className="sub" style={{ marginTop: 0 }}>
            {run.summary}
          </div>
        </td>
        <td>{run.depth}</td>
        <td>{run.status === "failed" ? <span className="badge risk">failed</span> : "ok"}</td>
        <td className="num">{cost}</td>
        <td className="muted small">{when}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5}>
            {loading && <p className="muted">Loading full report…</p>}
            {error && <p className="error">{error}</p>}
            {detail?.error_message && <p className="error">{detail.error_message}</p>}
            {detail?.report && (
              <ReportView
                report={detail.report}
                usage={{
                  run_id: detail.run_id,
                  total_calls: detail.total_calls,
                  // Per-stage/per-advisor/cache breakdown isn't in the projection columns
                  // get_run() selects — only the aggregates below. See db/repositories/runs.py.
                  failed_calls: 0,
                  total_input_tokens: detail.total_input_tokens,
                  total_output_tokens: detail.total_output_tokens,
                  total_cache_read_tokens: 0,
                  total_cache_creation_tokens: 0,
                  estimated_cost_usd: detail.estimated_cost_usd,
                  pricing_version: detail.pricing_version,
                  by_stage: [],
                  by_advisor: [],
                }}
              />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

import { useState } from "react";
import { distillAdvisor } from "../api";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";
import type { AdvisorSummary } from "../types";

const DISTILL_DEPTHS: { id: "quick" | "standard" | "deep"; label: string }[] = [
  { id: "quick", label: "Quick" },
  { id: "standard", label: "Standard" },
  { id: "deep", label: "Deep" },
];

const DEPTH_NOTE: Record<"quick" | "standard" | "deep", string> = {
  quick: "Two research passes plus synthesis — the cheapest usable distillation.",
  standard: "Four research passes plus synthesis. The default.",
  deep: "Seven research passes plus synthesis. Substantially more tokens than a committee run.",
};

const FOCUS_SUGGESTIONS = [
  "valuation",
  "risk management",
  "behavioral psychology",
  "asset allocation",
  "debt strategy",
  "tax efficiency",
];

export function AdvisorsPanel({
  advisors,
  selectedIds,
  onToggle,
  onReset,
  onDistilled,
}: {
  advisors: AdvisorSummary[];
  /** null = deterministic auto-selection; a set = the user is hand-picking the committee. */
  selectedIds: Set<string> | null;
  onToggle: (advisorId: string) => void;
  onReset: () => void;
  onDistilled: (advisor: AdvisorSummary) => void;
}) {
  const { isConnected, model, withKey } = useAnthropicConnection();

  const [subject, setSubject] = useState("");
  const [focusAreas, setFocusAreas] = useState<string[]>([]);
  const [depth, setDepth] = useState<"quick" | "standard" | "deep">("standard");
  const [distilling, setDistilling] = useState(false);
  const [distillError, setDistillError] = useState<string | null>(null);
  const [lastWarnings, setLastWarnings] = useState<string[]>([]);
  const [showDistill, setShowDistill] = useState(false);

  function toggleFocus(f: string) {
    setFocusAreas((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

  async function onDistill(e: React.FormEvent) {
    e.preventDefault();
    setDistilling(true);
    setDistillError(null);
    setLastWarnings([]);
    try {
      const result = await withKey((key) =>
        distillAdvisor(key, subject.trim(), focusAreas, depth, model),
      );
      onDistilled(result.advisor);
      setLastWarnings(result.warnings);
      setSubject("");
      setFocusAreas([]);
      setShowDistill(false);
    } catch (err) {
      setDistillError(err instanceof Error ? err.message : "Distillation failed.");
    } finally {
      setDistilling(false);
    }
  }

  const builtin = advisors.filter((a) => a.origin === "builtin");
  const custom = advisors.filter((a) => a.origin !== "builtin");

  return (
    <>
      <section className="panel">
        <div className="row-between">
          <h2>Committee roster</h2>
          {selectedIds ? (
            <button className="ghost" onClick={onReset}>
              Reset to automatic selection
            </button>
          ) : (
            <span className="badge free">selecting automatically</span>
          )}
        </div>
        <p className="muted">
          Leave every box unchecked and the deterministic selector assembles the team that best
          covers your situation. Check advisors to override it and hand-pick the committee.
        </p>

        <RosterTable
          caption="Built-in"
          advisors={builtin}
          selectedIds={selectedIds}
          onToggle={onToggle}
        />

        {custom.length > 0 && (
          <RosterTable
            caption="Distilled by you"
            advisors={custom}
            selectedIds={selectedIds}
            onToggle={onToggle}
          />
        )}

        {lastWarnings.map((w) => (
          <p className="warn-text" key={w}>
            {w}
          </p>
        ))}
      </section>

      <section className="panel">
        <div className="row-between">
          <h2>Distill a new advisor</h2>
          <button className="secondary" onClick={() => setShowDistill((s) => !s)}>
            {showDistill ? "Cancel" : "New advisor"}
          </button>
        </div>
        <p className="muted">
          Name a real investor, economist, or financial writer. Nuwa plans research questions
          about how they decide and where they fail, runs those passes concurrently, and
          synthesizes a reusable profile — once. Every committee run afterwards reuses it.
        </p>

        {showDistill && (
          <form className="distill-form" onSubmit={onDistill}>
            <div className="distill-grid">
              <div>
                <div className="field">
                  <label htmlFor="subject">Subject</label>
                  <input
                    id="subject"
                    placeholder="e.g. Benjamin Graham"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    required
                  />
                </div>

                <div className="field" style={{ marginTop: 16 }}>
                  <label>Focus areas (optional)</label>
                  <div className="focus-chips">
                    {FOCUS_SUGGESTIONS.map((f) => (
                      <label key={f}>
                        <input
                          type="checkbox"
                          checked={focusAreas.includes(f)}
                          onChange={() => toggleFocus(f)}
                        />
                        {f}
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              <div>
                <div className="field">
                  <label>Distillation depth</label>
                  <div className="radio-set">
                    {DISTILL_DEPTHS.map((d) => (
                      <label key={d.id}>
                        <input
                          type="radio"
                          name="distill-depth"
                          checked={depth === d.id}
                          onChange={() => setDepth(d.id)}
                        />
                        {d.label}
                      </label>
                    ))}
                  </div>
                </div>

                <div className={`notice${depth === "deep" ? " risk" : ""}`}>
                  <p className="small" style={{ margin: 0 }}>
                    {DEPTH_NOTE[depth]}
                  </p>
                </div>

                <p className="fineprint">
                  Billed to your Anthropic key as a one-time cost, separate from — and usually
                  larger than — a single committee run.
                </p>

                <button type="submit" disabled={!isConnected || distilling}>
                  {distilling ? "Distilling…" : "Start distillation"}
                </button>
                {!isConnected && (
                  <p className="muted small">Connect a key above to enable this.</p>
                )}
              </div>
            </div>

            {distillError && <p className="error">{distillError}</p>}
          </form>
        )}
      </section>
    </>
  );
}

function RosterTable({
  caption,
  advisors,
  selectedIds,
  onToggle,
}: {
  caption: string;
  advisors: AdvisorSummary[];
  selectedIds: Set<string> | null;
  onToggle: (advisorId: string) => void;
}) {
  return (
    <>
      <h3>{caption}</h3>
      <table>
        <thead>
          <tr>
            <th style={{ width: 44 }}>Sel</th>
            <th>Persona</th>
            <th>Approach</th>
            <th className="num" style={{ width: 92 }}>
              Profile
            </th>
          </tr>
        </thead>
        <tbody>
          {advisors.map((a) => (
            <tr key={a.advisor_id} className="selectable">
              <td>
                <input
                  type="checkbox"
                  aria-label={`Include ${a.display_name}`}
                  checked={selectedIds?.has(a.advisor_id) ?? false}
                  onChange={() => onToggle(a.advisor_id)}
                />
              </td>
              <td>
                <strong>{a.display_name}</strong>
                {a.topic_affinity.length > 0 && (
                  <span className="sub">
                    {a.topic_affinity.slice(0, 3).map((t) => t.replace(/_/g, " ")).join(" · ")}
                  </span>
                )}
              </td>
              <td>
                <span className="sub" style={{ marginTop: 0 }}>
                  {a.one_line}
                </span>
              </td>
              <td className="num muted">{a.runtime_profile_tokens.toLocaleString()} tok</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

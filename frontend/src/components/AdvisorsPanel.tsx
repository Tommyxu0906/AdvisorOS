import { useState } from "react";
import { distillAdvisor } from "../api";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";
import type { AdvisorSummary } from "../types";

const DISTILL_DEPTHS: { id: "quick" | "standard" | "deep"; label: string; blurb: string }[] = [
  { id: "quick", label: "Quick", blurb: "Cheapest — a single research pass." },
  { id: "standard", label: "Standard", blurb: "A few research passes plus synthesis." },
  { id: "deep", label: "Deep", blurb: "Most thorough. Substantially more tokens than a normal committee run." },
];

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
    <section className="panel">
      <div className="row-between">
        <h2>Your team</h2>
        {selectedIds && (
          <button className="secondary small" onClick={onReset}>
            Reset to automatic selection
          </button>
        )}
      </div>
      <p className="muted">
        Every advisor here is a compressed persona distilled once, ahead of time, from a real
        investor's writing and public track record — not re-researched on every question. By
        default the committee picks whichever 3–4 advisors best cover your situation. Check
        advisors below to hand-pick the team instead; leave everything unchecked to let the
        deterministic selector decide.
      </p>

      <div className="advisor-roster">
        <h3 className="muted small">Built-in</h3>
        <ul className="advisor-checklist">
          {builtin.map((a) => (
            <AdvisorRow
              key={a.advisor_id}
              advisor={a}
              checked={selectedIds?.has(a.advisor_id) ?? false}
              onToggle={onToggle}
            />
          ))}
        </ul>

        {custom.length > 0 && (
          <>
            <h3 className="muted small">Distilled by you</h3>
            <ul className="advisor-checklist">
              {custom.map((a) => (
                <AdvisorRow
                  key={a.advisor_id}
                  advisor={a}
                  checked={selectedIds?.has(a.advisor_id) ?? false}
                  onToggle={onToggle}
                />
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="distill-toggle">
        <button className="secondary" onClick={() => setShowDistill((s) => !s)}>
          {showDistill ? "Cancel" : "+ Distill a new advisor"}
        </button>
      </div>

      {showDistill && (
        <form className="distill-form" onSubmit={onDistill}>
          <p className="muted small">
            Name a real investor, economist, or financial writer. Nuwa runs a short research
            pass on your Anthropic key and produces a reusable advisor persona — done once, then
            available in every future committee run at no extra distillation cost.
          </p>

          <label htmlFor="subject">Who should we distill?</label>
          <input
            id="subject"
            placeholder="e.g. Benjamin Graham, Cathie Wood, Jack Bogle"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
          />

          <label>Focus (optional)</label>
          <div className="focus-chips">
            {FOCUS_SUGGESTIONS.map((f) => (
              <button
                type="button"
                key={f}
                className={`chip${focusAreas.includes(f) ? " active" : ""}`}
                onClick={() => toggleFocus(f)}
              >
                {f}
              </button>
            ))}
          </div>

          <label>Distillation depth</label>
          <div className="depth-picker">
            {DISTILL_DEPTHS.map((d) => (
              <button
                type="button"
                key={d.id}
                className={`depth${depth === d.id ? " active" : ""}`}
                onClick={() => setDepth(d.id)}
              >
                <strong>{d.label}</strong>
                <span className="muted small">{d.blurb}</span>
              </button>
            ))}
          </div>

          <p className="fineprint">
            This is a one-time cost billed to your Anthropic key, separate from — and usually
            larger than — a single committee run.
          </p>

          <button type="submit" disabled={!isConnected || distilling}>
            {distilling ? "Distilling…" : "Start distillation"}
          </button>
          {!isConnected && (
            <p className="muted small">Connect an Anthropic API key above to enable this.</p>
          )}
          {distillError && <p className="error">{distillError}</p>}
        </form>
      )}

      {lastWarnings.map((w) => (
        <p className="warn-text" key={w}>
          {w}
        </p>
      ))}
    </section>
  );
}

function AdvisorRow({
  advisor,
  checked,
  onToggle,
}: {
  advisor: AdvisorSummary;
  checked: boolean;
  onToggle: (advisorId: string) => void;
}) {
  return (
    <li>
      <label className="advisor-checkrow">
        <input
          type="checkbox"
          checked={checked}
          onChange={() => onToggle(advisor.advisor_id)}
        />
        <div>
          <div className="row-between">
            <strong>{advisor.display_name}</strong>
            <span className="muted small">{advisor.origin === "builtin" ? "built-in" : "distilled"}</span>
          </div>
          <div className="muted small">{advisor.one_line}</div>
        </div>
      </label>
    </li>
  );
}

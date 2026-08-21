/**
 * Browse and manage investor lenses. Optional for a normal run.
 *
 * Three changes from the old Advisors page, each removing something that leaked engineering into
 * the product surface:
 *
 * **No token counts in the roster.** `runtime_profile_tokens: 1184` was the fourth column. It is
 * a real and interesting number — it is the cost lever the whole architecture turns on — and it
 * belongs under Technical details, not beside a person's name.
 *
 * **Distillation is a drawer.** It used to occupy a permanent block halfway down the page, so
 * everyone scrolled past a form for a thing most people will never do.
 *
 * **The claim is narrower.** The old copy said each persona was distilled "from a real investor's
 * writing and public track record". Nothing in the pipeline verifies a track record — it reads
 * public writing and available evidence, and that is what it now says.
 */

import { useState } from "react";
import { distillAdvisor } from "../api";
import { useAnthropicConnection } from "../context/AnthropicConnectionContext";
import type { AdvisorSummary } from "../types";
import { EvidenceBadge } from "../components/CommitteeSetup";
import { Advanced, Card, InlineAlert, Overlay, SectionHeader, StatusBadge } from "../ui";
import { percent } from "../lib/units";
import { ConnectKeyButton } from "../components/ConnectKeyButton";

const DISTILL_DEPTHS = [
  { id: "quick", label: "Quick", note: "Two research passes plus synthesis.", cost: "~$0.40" },
  { id: "standard", label: "Standard", note: "Four research passes plus synthesis.", cost: "~$0.90" },
  { id: "deep", label: "Deep", note: "Seven research passes plus synthesis.", cost: "~$1.80" },
] as const;

const FOCUS_SUGGESTIONS = [
  "valuation",
  "risk management",
  "behavioral psychology",
  "asset allocation",
  "debt strategy",
  "tax efficiency",
];

export function InvestorLibraryPage({
  advisors,
  onDistilled,
}: {
  advisors: AdvisorSummary[];
  onDistilled: (advisor: AdvisorSummary) => void;
}) {
  const [distillOpen, setDistillOpen] = useState(false);

  const builtin = advisors.filter((a) => a.origin === "builtin");
  const custom = advisors.filter((a) => a.origin !== "builtin");

  return (
    <>
      <div className="page-head">
        <h1>Investor library</h1>
        <p className="lede">
          Each lens was distilled once from public writings and available evidence, then frozen
          into a reusable profile — mental models, decision rules, and declared blind spots.
          Browsing here is optional: a normal run selects its own committee from your numbers.
        </p>
      </div>

      <section>
        <SectionHeader
          title="Built-in lenses"
          hint="Available to every run, at no cost."
          action={<span className="small muted">{builtin.length} available</span>}
        />
        <div className="grid-2">
          {builtin.map((a) => (
            <AdvisorCard key={a.advisor_id} advisor={a} />
          ))}
        </div>
      </section>

      <section>
        <SectionHeader
          title="Your distilled investors"
          hint="Produced by a distillation run billed to your key."
          action={
            <button className="primary" onClick={() => setDistillOpen(true)}>
              Distill a new investor
            </button>
          }
        />
        {custom.length === 0 ? (
          <Card tone="sunk">
            <p className="small muted" style={{ margin: 0 }}>
              None yet. Distillation is the expensive step and it happens once — after that, the
              resulting lens is reusable in every committee run at no extra research cost.
            </p>
          </Card>
        ) : (
          <div className="grid-2">
            {custom.map((a) => (
              <AdvisorCard key={a.advisor_id} advisor={a} />
            ))}
          </div>
        )}
      </section>

      <Advanced label="Technical details">
        <p>
          A committee run sends a compressed runtime profile rather than the full manifest. That
          split is the cost lever the architecture turns on: distillation is expensive and happens
          once, reuse is cheap and happens on every question.
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Lens</th>
                <th>Origin</th>
                <th className="num">Runtime profile tokens</th>
              </tr>
            </thead>
            <tbody>
              {advisors.map((a) => (
                <tr key={a.advisor_id}>
                  <td>{a.display_name}</td>
                  <td className="muted">{a.origin}</td>
                  <td className="num">{a.runtime_profile_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Advanced>

      <DistillDrawer
        open={distillOpen}
        onClose={() => setDistillOpen(false)}
        onDistilled={(a) => {
          onDistilled(a);
          setDistillOpen(false);
        }}
      />
    </>
  );
}

function AdvisorCard({ advisor }: { advisor: AdvisorSummary }) {
  return (
    <Card as="article" tone="sunk">
      <div className="row-between" style={{ marginBottom: 8 }}>
        <h3>{advisor.display_name}</h3>
        <EvidenceBadge advisor={advisor} />
      </div>
      <p className="small" style={{ color: "var(--ink-soft)" }}>
        {advisor.one_line}
      </p>

      {advisor.topic_affinity.length > 0 && (
        <>
          <p className="metric-label" style={{ marginTop: 12 }}>
            Best for
          </p>
          <div className="focus-chips" style={{ marginTop: 6 }}>
            {advisor.topic_affinity.map((t) => (
              <StatusBadge key={t} tone="neutral">
                {t.replace(/_/g, " ")}
              </StatusBadge>
            ))}
          </div>
        </>
      )}

      {advisor.policy_parameters.length > 0 ? (
        <div className="policy-strip">
          <p className="metric-label" style={{ margin: "12px 0 6px" }}>
            Thresholds it brings to the computation
          </p>
          {advisor.policy_parameters.map((p) => (
            <div key={p.name} className="policy-row">
              <span className="policy-name">{p.name.replace(/_/g, " ")}</span>
              <span className="policy-value">
                {p.value == null
                  ? "—"
                  : p.name.includes("months")
                    ? `${p.value} months`
                    : percent(p.value, 0)}
              </span>
              <StatusBadge tone={p.provenance === "direct" ? "good" : "neutral"}>
                {p.provenance === "derived" ? "read from behaviour" : p.provenance}
              </StatusBadge>
            </div>
          ))}
          <p className="tiny muted" style={{ margin: "6px 0 0" }}>
            These replace the house defaults when this lens drives the scenario, so two lenses
            produce differently sized trims over the same holdings.
          </p>
        </div>
      ) : (
        <p className="tiny muted" style={{ marginTop: 12 }}>
          Carries no thresholds of its own — it contributes reasoning, and the computation runs on
          AdvisorOS house numbers that say so.
        </p>
      )}

      <Advanced label="How it reasons">
        {advisor.mental_models.length > 0 && (
          <>
            <p className="metric-label">Mental models it applies</p>
            <ul className="bullet-list" style={{ fontSize: 14 }}>
              {advisor.mental_models.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </>
        )}
        {advisor.reasoning_rules.length > 0 && (
          <>
            <p className="metric-label" style={{ marginTop: 12 }}>
              Rules that govern its reasoning
            </p>
            <ul className="bullet-list" style={{ fontSize: 14 }}>
              {advisor.reasoning_rules.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </>
        )}
        {advisor.provenance && (
          <>
            <p className="metric-label" style={{ marginTop: 12 }}>
              Where this came from
            </p>
            <p className="small" style={{ margin: 0 }}>
              {advisor.provenance}
            </p>
          </>
        )}
      </Advanced>

      {advisor.honest_boundaries.length > 0 && (
        <Advanced label="Declared limits">
          <p className="metric-label">Will not</p>
          <ul className="bullet-list" style={{ fontSize: 14 }}>
            {advisor.honest_boundaries.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          {advisor.blind_spots.length > 0 && (
            <>
              <p className="metric-label" style={{ marginTop: 12 }}>
                Known blind spots
              </p>
              <ul className="bullet-list" style={{ fontSize: 14 }}>
                {advisor.blind_spots.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </>
          )}
        </Advanced>
      )}
    </Card>
  );
}

/**
 * Distillation, with what it will do and roughly cost stated before the button.
 *
 * The cost figures are order-of-magnitude and labelled as such. They come from the depth's stage
 * count rather than from a quote, and saying "~$0.90" beats saying nothing and beats implying a
 * precision the estimate does not have.
 */
function DistillDrawer({
  open,
  onClose,
  onDistilled,
}: {
  open: boolean;
  onClose: () => void;
  onDistilled: (advisor: AdvisorSummary) => void;
}) {
  const { isConnected, model, withKey } = useAnthropicConnection();
  const [subject, setSubject] = useState("");
  const [focus, setFocus] = useState<string[]>([]);
  const [depth, setDepth] = useState<(typeof DISTILL_DEPTHS)[number]["id"]>("standard");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setRunning(true);
    setError(null);
    try {
      const response = await withKey((key) =>
        distillAdvisor(key, subject.trim(), focus, depth, model),
      );
      onDistilled(response.advisor);
      setSubject("");
      setFocus([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Distillation failed.");
    } finally {
      setRunning(false);
    }
  }

  const chosen = DISTILL_DEPTHS.find((d) => d.id === depth)!;

  return (
    <Overlay open={open} onClose={onClose} title="Distill a new investor" variant="drawer">
      <form onSubmit={submit}>
        <div className="field" style={{ marginBottom: 16 }}>
          <label htmlFor="subject">Subject</label>
          <input
            id="subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Howard Marks"
            required
          />
          <p className="tiny muted" style={{ marginTop: 4 }}>
            An investor or school of thought with substantial public writing.
          </p>
        </div>

        <label>Focus areas — optional</label>
        <div className="focus-chips" style={{ marginBottom: 16 }}>
          {FOCUS_SUGGESTIONS.map((f) => (
            <button
              key={f}
              type="button"
              className={`choice${focus.includes(f) ? " selected" : ""}`}
              style={{ padding: "6px 12px", minHeight: 36 }}
              aria-pressed={focus.includes(f)}
              onClick={() =>
                setFocus((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]))
              }
            >
              <span className="choice-label" style={{ fontSize: 13 }}>
                {f}
              </span>
            </button>
          ))}
        </div>

        <label>Research depth</label>
        <div className="choice-set" style={{ marginBottom: 20 }}>
          {DISTILL_DEPTHS.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`choice${depth === d.id ? " selected" : ""}`}
              aria-pressed={depth === d.id}
              onClick={() => setDepth(d.id)}
            >
              <span className="choice-label">{d.label}</span>
              <span className="choice-hint">{d.note}</span>
            </button>
          ))}
        </div>

        <Card tone="sunk">
          <p className="metric-label">Before you start</p>
          <div className="table-scroll">
            <table>
              <tbody>
                <tr>
                  <td className="muted">Subject</td>
                  <td>{subject.trim() || "—"}</td>
                </tr>
                <tr>
                  <td className="muted">Focus</td>
                  <td>{focus.length ? focus.join(", ") : "General"}</td>
                </tr>
                <tr>
                  <td className="muted">Depth</td>
                  <td>{chosen.label}</td>
                </tr>
                <tr>
                  <td className="muted">Approximate cost</td>
                  <td>{chosen.cost} to your Anthropic key</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="tiny muted" style={{ marginTop: 10 }}>
            Produces a reusable lens: a scored expertise profile, mental models, decision rules,
            and declared blind spots. It is rejected outright if it declares no limits.
          </p>
        </Card>

        {error && (
          <div style={{ marginTop: 16 }}>
            <InlineAlert tone="risk" title="Distillation failed">
              {error}
            </InlineAlert>
          </div>
        )}

        {!isConnected && (
          <div style={{ marginTop: 16 }}>
            <InlineAlert
              tone="info"
              title="Needs your Anthropic key"
              action={<ConnectKeyButton />}
            >
              Distillation is the expensive step, and it bills your account.
            </InlineAlert>
          </div>
        )}

        <div className="row-between" style={{ marginTop: 20 }}>
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary"
            disabled={!isConnected || running || !subject.trim()}
          >
            {running ? "Distilling…" : "Start distillation"}
          </button>
        </div>
      </form>
    </Overlay>
  );
}

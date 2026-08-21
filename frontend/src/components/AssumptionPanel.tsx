/**
 * Test a different assumption, explicitly.
 *
 * This is the control that makes the difference between a chatbot and a decision system
 * visible. Saying "what if I don't need this for ten years" in the chat does *not* change
 * anything — the committee will answer the hypothetical in words, and the numbers underneath
 * will still be the old ones. Changing it here recomputes the scenario first, and only then do
 * the lenses get asked again.
 *
 * That ordering is why this is a form rather than a parsed sentence. A chat turn that silently
 * moved the horizon would mean a user could talk their way into a different recommendation
 * without any figure on screen changing to match.
 *
 * The fields are the ones that actually move the computed scenario: when the money is needed —
 * which is the only thing the house blocks on — and what cash is available to deploy.
 */

import { useState } from "react";
import type { ProfileDraft } from "../lib/draft";
import { money, years } from "../lib/units";
import { Advanced, Card, SectionHeader, StatusBadge } from "../ui";

export interface AssumptionChange {
  label: string;
  from: string;
  to: string;
}

interface Draft {
  horizon: number | null;
  cash: number | null;
}

function readDraft(profile: ProfileDraft): Draft {
  return { horizon: profile.horizon_years, cash: profile.investable_cash };
}

export function AssumptionPanel({
  profile,
  onApply,
}: {
  profile: ProfileDraft;
  onApply: (next: ProfileDraft, changes: AssumptionChange[]) => void;
}) {
  const current = readDraft(profile);
  const [draft, setDraft] = useState<Draft>(current);

  const changes = describeChanges(current, draft);
  const dirty = changes.length > 0;

  function apply() {
    if (!dirty) return;
    onApply(
      { ...profile, horizon_years: draft.horizon, investable_cash: draft.cash },
      changes,
    );
  }

  return (
    <Advanced label="Test a different assumption">
      <Card tone="sunk">
        <SectionHeader
          title="What if something changed?"
          hint="Nothing recomputes until you apply it. Asking the committee never changes a figure."
          action={dirty ? <StatusBadge tone="warn">Not applied yet</StatusBadge> : undefined}
        />

        <div className="assumption-grid">
          <Field
            label="Years until you need this money"
            value={draft.horizon}
            step={1}
            min={0}
            onChange={(v) => setDraft({ ...draft, horizon: v })}
          />
          <Field
            label="Cash available to deploy"
            value={draft.cash}
            step={1000}
            min={0}
            onChange={(v) => setDraft({ ...draft, cash: v })}
          />
        </div>

        {dirty && (
          <div className="assumption-diff">
            {changes.map((c) => (
              <p key={c.label} className="small" style={{ margin: 0 }}>
                <strong>{c.label}</strong> {c.from} → {c.to}
              </p>
            ))}
          </div>
        )}

        <div className="row-between" style={{ marginTop: 14, gap: 10 }}>
          <span className="tiny muted">
            Applying replaces the figures the scenario is computed from, then the engine
            recomputes and the committee can react to the new one.
          </span>
          <span style={{ display: "flex", gap: 8 }}>
            {dirty && (
              <button type="button" className="secondary" onClick={() => setDraft(current)}>
                Reset
              </button>
            )}
            <button type="button" className="primary" disabled={!dirty} onClick={apply}>
              Apply assumption
            </button>
          </span>
        </div>
      </Card>
    </Advanced>
  );
}

function Field({
  label,
  value,
  step,
  min,
  onChange,
}: {
  label: string;
  value: number | null;
  step: number;
  min: number;
  onChange: (v: number | null) => void;
}) {
  const id = `assume-${label.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        step={step}
        min={min}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
    </div>
  );
}

function describeChanges(before: Draft, after: Draft): AssumptionChange[] {
  const out: AssumptionChange[] = [];
  if (before.horizon !== after.horizon) {
    out.push({
      label: "Time horizon",
      from: before.horizon == null ? "—" : years(before.horizon),
      to: after.horizon == null ? "—" : years(after.horizon),
    });
  }
  if (before.cash !== after.cash) {
    out.push({
      label: "Deployable cash",
      from: money(before.cash),
      to: money(after.cash),
    });
  }
  return out;
}

/**
 * Test a different assumption, explicitly.
 *
 * This is the control that makes the difference between a chatbot and a decision system
 * visible. Saying "what if I delay the house by three years" in the chat does *not* change
 * anything — the committee will answer the hypothetical in words, and the numbers underneath
 * will still be the old ones. Changing it here recomputes the scenario first, and only then do
 * the lenses get asked again.
 *
 * That ordering is the whole point, and it is why this is a form rather than a parsed sentence.
 * A chat turn that silently mutated the balance sheet would mean a user could talk their way
 * into a different recommendation without any figure on screen changing to match.
 *
 * The fields are the ones that actually move the computed scenario for this household: the
 * nearest goal's horizon, income, essential expenses, and the highest-rate debt. Everything else
 * lives in Settings, where changing it is a decision about your real situation rather than a
 * hypothetical.
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
  income: number | null;
  expenses: number | null;
  debtBalance: number | null;
}

function readDraft(profile: ProfileDraft): Draft {
  const nearest = nearestGoal(profile);
  const debt = highestAprDebt(profile);
  return {
    horizon: nearest?.years_until_needed ?? null,
    income: profile.income.annual_gross,
    expenses: profile.expenses.monthly_essential,
    debtBalance: debt?.balance ?? null,
  };
}

/** The goal the scenario is most sensitive to: the one that comes due soonest. */
function nearestGoal(profile: ProfileDraft) {
  const dated = profile.goals.filter((g) => g.years_until_needed != null);
  if (dated.length === 0) return null;
  return dated.reduce((a, b) =>
    (a.years_until_needed ?? 0) <= (b.years_until_needed ?? 0) ? a : b,
  );
}

function highestAprDebt(profile: ProfileDraft) {
  const rated = profile.debts.filter((d) => d.apr != null && d.balance != null);
  if (rated.length === 0) return null;
  return rated.reduce((a, b) => ((a.apr ?? 0) >= (b.apr ?? 0) ? a : b));
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

  const goal = nearestGoal(profile);
  const debt = highestAprDebt(profile);

  const changes = describeChanges(current, draft, goal?.name ?? "goal", debt?.name ?? "debt");
  const dirty = changes.length > 0;

  function apply() {
    if (!dirty) return;
    const next: ProfileDraft = structuredClone(profile);

    if (goal && draft.horizon != null) {
      const target = next.goals.find((g) => g.name === goal.name);
      if (target) target.years_until_needed = draft.horizon;
    }
    next.income.annual_gross = draft.income;
    next.expenses.monthly_essential = draft.expenses;
    if (debt && draft.debtBalance != null) {
      const target = next.debts.find((d) => d.name === debt.name);
      if (target) target.balance = draft.debtBalance;
    }

    onApply(next, changes);
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
          {goal && (
            <Field
              label={`${goal.name} — years away`}
              value={draft.horizon}
              step={1}
              min={0}
              onChange={(v) => setDraft({ ...draft, horizon: v })}
            />
          )}
          <Field
            label="Annual income"
            value={draft.income}
            step={5000}
            min={0}
            onChange={(v) => setDraft({ ...draft, income: v })}
          />
          <Field
            label="Monthly essential spending"
            value={draft.expenses}
            step={250}
            min={0}
            onChange={(v) => setDraft({ ...draft, expenses: v })}
          />
          {debt && (
            <Field
              label={`${debt.name} balance`}
              value={draft.debtBalance}
              step={500}
              min={0}
              onChange={(v) => setDraft({ ...draft, debtBalance: v })}
            />
          )}
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
            Applying replaces the figures the scenario is computed from, then the engine recomputes
            and the committee can react to the new one.
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

function describeChanges(
  before: Draft,
  after: Draft,
  goalName: string,
  debtName: string,
): AssumptionChange[] {
  const out: AssumptionChange[] = [];
  if (before.horizon !== after.horizon) {
    out.push({
      label: `${goalName} horizon`,
      from: before.horizon == null ? "—" : years(before.horizon),
      to: after.horizon == null ? "—" : years(after.horizon),
    });
  }
  if (before.income !== after.income) {
    out.push({ label: "Annual income", from: money(before.income), to: money(after.income) });
  }
  if (before.expenses !== after.expenses) {
    out.push({
      label: "Monthly essentials",
      from: money(before.expenses),
      to: money(after.expenses),
    });
  }
  if (before.debtBalance !== after.debtBalance) {
    out.push({
      label: `${debtName} balance`,
      from: money(before.debtBalance),
      to: money(after.debtBalance),
    });
  }
  return out;
}

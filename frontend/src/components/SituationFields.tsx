import type { ProfileDraft } from "../lib/draft";
import { num, str } from "../lib/draft";
import { ACCOUNT_TYPES, Field, GOAL_TYPES, RISK, RowEditor } from "./FormControls";

/**
 * Everything about the person rather than their positions: age, income, expenses, risk
 * appetite, and the debts, assets, and goals behind them.
 *
 * Asked once at intake and edited afterwards on the Settings page, never on Analysis — this
 * changes when someone's life changes, not between two questions about the same portfolio.
 */
export function SituationFields({
  profile,
  onProfile,
}: {
  profile: ProfileDraft;
  onProfile: (p: ProfileDraft) => void;
}) {
  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) =>
    onProfile({ ...profile, [key]: value });

  return (
    <>
      <div className="grid">
        <Field label="Age">
          <input
            type="number"
            placeholder="34"
            min={16}
            max={120}
            value={str(profile.age)}
            onChange={(e) => set("age", num(e.target.value))}
          />
        </Field>
        <Field label="Dependents">
          <input
            type="number"
            placeholder="0"
            min={0}
            value={str(profile.dependents)}
            onChange={(e) => set("dependents", num(e.target.value))}
          />
        </Field>
        <Field label="Annual gross income">
          <input
            type="number"
            placeholder="before tax"
            min={0}
            value={str(profile.income.annual_gross)}
            onChange={(e) =>
              set("income", { ...profile.income, annual_gross: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Employer match (fraction of salary)">
          <input
            type="number"
            step="0.01"
            placeholder="0.04 — blank means none"
            min={0}
            max={1}
            value={str(profile.income.employer_match_pct)}
            onChange={(e) =>
              set("income", { ...profile.income, employer_match_pct: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly essential expenses">
          <input
            type="number"
            placeholder="rent, food, utilities, minimums"
            min={0}
            value={str(profile.expenses.monthly_essential)}
            onChange={(e) =>
              set("expenses", { ...profile.expenses, monthly_essential: num(e.target.value) })
            }
          />
        </Field>
        <Field label="Monthly discretionary">
          <input
            type="number"
            placeholder="everything else — enter 0 if none"
            min={0}
            value={str(profile.expenses.monthly_discretionary)}
            onChange={(e) =>
              set("expenses", {
                ...profile.expenses,
                monthly_discretionary: num(e.target.value),
              })
            }
          />
        </Field>
        <Field label="Risk tolerance">
          <select
            value={profile.risk_tolerance}
            onChange={(e) => set("risk_tolerance", e.target.value)}
          >
            <option value="">— choose —</option>
            {RISK.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Investing experience (0–1)">
          <input
            type="number"
            step="0.05"
            min={0}
            max={1}
            placeholder="0 = novice, 1 = professional"
            value={str(profile.self_reported_experience)}
            onChange={(e) => set("self_reported_experience", num(e.target.value))}
          />
        </Field>
      </div>

      <RowEditor
        title="Debts"
        rows={profile.debts}
        onChange={(rows) => set("debts", rows)}
        blank={{ name: "", balance: null, apr: null, minimum_monthly_payment: null }}
        empty="None — leave empty if you have no debt."
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <input
              type="number"
              placeholder="balance"
              value={str(row.balance)}
              onChange={(e) => update({ ...row, balance: num(e.target.value) })}
            />
            <input
              type="number"
              step="0.001"
              placeholder="APR as 0.229"
              value={str(row.apr)}
              onChange={(e) => update({ ...row, apr: num(e.target.value) })}
            />
            <input
              type="number"
              placeholder="min payment"
              value={str(row.minimum_monthly_payment)}
              onChange={(e) => update({ ...row, minimum_monthly_payment: num(e.target.value) })}
            />
          </>
        )}
      />

      <RowEditor
        title="Assets"
        rows={profile.assets}
        onChange={(rows) => set("assets", rows)}
        blank={{ name: "", value: null, account_type: "cash", is_liquid: true }}
        empty="None yet — cash, savings, and retirement accounts go here."
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <input
              type="number"
              placeholder="value"
              value={str(row.value)}
              onChange={(e) => update({ ...row, value: num(e.target.value) })}
            />
            <select
              value={row.account_type}
              onChange={(e) => update({ ...row, account_type: e.target.value })}
            >
              {ACCOUNT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={row.is_liquid}
                onChange={(e) => update({ ...row, is_liquid: e.target.checked })}
              />
              liquid
            </label>
          </>
        )}
      />

      <RowEditor
        title="Goals"
        rows={profile.goals}
        onChange={(rows) => set("goals", rows)}
        blank={{ name: "", goal_type: "other", years_until_needed: null, priority: null }}
        empty="None yet — a goal and its horizon change which advisors get selected."
        render={(row, update) => (
          <>
            <input
              placeholder="name"
              value={row.name}
              onChange={(e) => update({ ...row, name: e.target.value })}
            />
            <select
              value={row.goal_type}
              onChange={(e) => update({ ...row, goal_type: e.target.value })}
            >
              {GOAL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <input
              type="number"
              step="0.5"
              placeholder="years away"
              value={str(row.years_until_needed)}
              onChange={(e) => update({ ...row, years_until_needed: num(e.target.value) })}
            />
            <input
              type="number"
              min={1}
              max={5}
              placeholder="priority 1–5"
              value={str(row.priority)}
              onChange={(e) => update({ ...row, priority: num(e.target.value) })}
            />
          </>
        )}
      />
    </>
  );
}

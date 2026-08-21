import { EXPERIENCE_LEVELS } from "../lib/units";
import type { ProfileDraft } from "../lib/draft";
import { num, str } from "../lib/draft";
import { Field, RISK } from "./FormControls";

/**
 * Everything about the investor rather than their positions.
 *
 * Five questions, and each one changes a recommendation. This used to ask for income, monthly
 * expenses, dependents, debts, assets, and goals — a household balance sheet collected in order
 * to comment on an equity book, most of which never reached an answer. A platform that advises
 * on investments has no standing to ask about someone's mortgage.
 *
 * Asked once at intake and edited afterwards in Settings, never mid-conversation: this changes
 * when someone's situation changes, not between two questions about the same portfolio.
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

        <Field
          label="Years until you need this money"
          hint="The single most important answer here. It is the only thing that overrides an advisor."
        >
          <input
            type="number"
            placeholder="10"
            min={0}
            max={60}
            step={1}
            value={str(profile.horizon_years)}
            onChange={(e) => set("horizon_years", num(e.target.value))}
          />
        </Field>

        <Field
          label="Cash available to invest"
          hint="What is sitting in the account ready to deploy. Blank means nothing spare."
        >
          <input
            type="number"
            placeholder="0"
            min={0}
            step={500}
            value={str(profile.investable_cash)}
            onChange={(e) => set("investable_cash", num(e.target.value))}
          />
        </Field>

        <Field label="Risk tolerance">
          <select
            value={profile.risk_tolerance}
            onChange={(e) => set("risk_tolerance", e.target.value)}
          >
            <option value="">Select…</option>
            {RISK.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Investing experience">
          <select
            value={str(profile.self_reported_experience)}
            onChange={(e) => set("self_reported_experience", num(e.target.value))}
          >
            <option value="">Select…</option>
            {EXPERIENCE_LEVELS.map((level) => (
              <option key={level.value} value={level.value}>
                {level.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="Anything else worth knowing"
        hint="Optional. Context the numbers do not carry — why a position is held, what you are worried about."
      >
        <textarea
          rows={3}
          placeholder="Most of this is one position I have held for years."
          value={profile.notes}
          onChange={(e) => set("notes", e.target.value)}
        />
      </Field>
    </>
  );
}

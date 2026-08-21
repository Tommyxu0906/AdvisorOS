/**
 * Setup, as six short questions instead of one long form.
 *
 * The old page put age, dependents, income, expenses, employer match, risk tolerance, experience
 * and three repeating tables on one screen. Every field was legitimate; presenting them together
 * made the product look like a database admin panel and made abandoning it the rational choice.
 *
 * Two things matter more than the step count.
 *
 * **Units are human.** Employer match is entered as `4%`, not `0.04`. APR is `22.9%`, not
 * `0.229`. Experience is a named level, not a float. The API still receives the fractions it
 * always did — `lib/units.ts` converts at the edge — because changing a backend schema to make a
 * form nicer is the wrong direction of travel.
 *
 * **Required and optional are visible.** Blank employer match genuinely means "no match", and
 * blank debts genuinely means "no debt". Those are answers. Marking them optional is what lets
 * someone move quickly without wondering whether they have broken something.
 */

import React, { useState } from "react";
import type { HoldingDraft, ProfileDraft } from "../lib/draft";
import { REQUIRED_FIELDS } from "../lib/draft";
import { EXPERIENCE_LEVELS, money, years } from "../lib/units";
import { Card, InlineAlert, SectionHeader, Stepper } from "../ui";
import { HoldingsEditor } from "../components/HoldingsEditor";
import type { QuoteState } from "../lib/useQuotes";

const STEPS = ["About you", "Portfolio", "Review"];

const RISK_LEVELS = [
  { value: "conservative", label: "Conservative", hint: "Protect what I have" },
  { value: "moderate_conservative", label: "Cautious", hint: "Mostly steady, some growth" },
  { value: "moderate", label: "Balanced", hint: "Accept normal market swings" },
  { value: "moderate_aggressive", label: "Growth", hint: "Comfortable with real drops" },
  { value: "aggressive", label: "Aggressive", hint: "Maximum growth, deep drawdowns fine" },
];

// Mirrors backend AccountType exactly. A value this list invents is rejected by the API at the
// first analysis call, which is how the demo household shipped with a "checking" account.


export function OnboardingFlow({
  profile,
  holdings,
  quotes,
  onProfile,
  onHoldings,
  onDone,
}: {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  quotes: QuoteState;
  onProfile: (p: ProfileDraft) => void;
  onHoldings: (h: HoldingDraft[]) => void;
  onDone: () => void;
}) {
  const [step, setStep] = useState(0);

  const patch = (changes: Partial<ProfileDraft>) => onProfile({ ...profile, ...changes });
  const num = (value: string): number | null => (value.trim() === "" ? null : Number(value));

  // Per-step gating, so someone cannot arrive at Review with a hole in step one.
  const blocked = stepBlockers(profile, step);
  const isLast = step === STEPS.length - 1;

  return (
    <div className="page" style={{ maxWidth: 780, margin: "0 auto" }}>
      <div className="page-head">
        <h1>Set up your situation</h1>
        <p className="lede">
          Three short steps, and only what changes an investment recommendation. Nothing is
          filled in for you, because a guessed figure produces a confident answer to the wrong
          question.
        </p>
      </div>

      <Stepper steps={STEPS} current={step} onJump={setStep} />

      <Card>
        {step === 0 && (
          <>
            <SectionHeader
              title="About you"
              hint="Five answers, and each one changes what the engine computes."
            />
            <div className="grid">
              <Field label="Age" required>
                <input
                  type="number"
                  inputMode="numeric"
                  value={profile.age ?? ""}
                  onChange={(e) => patch({ age: num(e.target.value) })}
                />
              </Field>
              <Field label="Years until you need this money" required>
                <input
                  type="number"
                  inputMode="numeric"
                  value={profile.horizon_years ?? ""}
                  onChange={(e) => patch({ horizon_years: num(e.target.value) })}
                  placeholder="10"
                />
              </Field>
              <Field label="Cash available to invest">
                <input
                  type="number"
                  inputMode="numeric"
                  value={profile.investable_cash ?? ""}
                  onChange={(e) => patch({ investable_cash: num(e.target.value) })}
                  placeholder="0"
                />
              </Field>
            </div>

            <Choices
              legend="Risk tolerance"
              required
              options={RISK_LEVELS}
              selected={profile.risk_tolerance}
              onSelect={(value) => patch({ risk_tolerance: String(value) })}
            />

            <Choices
              legend="Investing experience"
              required
              options={EXPERIENCE_LEVELS.map((l) => ({ ...l, value: l.value }))}
              selected={nearestExperience(profile.self_reported_experience)}
              onSelect={(value) => patch({ self_reported_experience: Number(value) })}
            />
          </>
        )}

        {step === 1 && (
          <>
            <SectionHeader
              title="Portfolio"
              hint="Add what you hold, or skip and add it later — the analysis works either way."
            />
            <HoldingsEditor holdings={holdings} quotes={quotes} onHoldings={onHoldings} />
          </>
        )}

        {step === 2 && (
          <>
            <SectionHeader title="Review" hint="Nothing has been sent anywhere yet." />
            <ReviewTable profile={profile} holdings={holdings} onJump={setStep} />
          </>
        )}

        {blocked.length > 0 && step !== 2 && (
          <div style={{ marginTop: 18 }}>
            <InlineAlert tone="warn" title="Still needed on this step">
              {blocked.join(", ")}
            </InlineAlert>
          </div>
        )}

        <div className="row-between" style={{ marginTop: 24 }}>
          <button
            className="secondary"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Back
          </button>
          <button
            className="primary"
            disabled={blocked.length > 0}
            onClick={() => (isLast ? onDone() : setStep((s) => s + 1))}
          >
            {isLast ? "Go to my decision workspace" : "Continue"}
          </button>
        </div>
      </Card>
    </div>
  );
}

// --- pieces ------------------------------------------------------------------------------------

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  const id = `f-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required ? (
          <span className="required-dot" aria-label="required">
            *
          </span>
        ) : (
          <span className="optional"> · optional</span>
        )}
      </label>
      {/* The control is cloned so the label's `for` actually points at it — a wrapper div with a
          label beside it looks right and reads as unlabelled to a screen reader. */}
      {React.isValidElement(children)
        ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id })
        : children}
      {hint && <p className="tiny muted" style={{ margin: "4px 0 0" }}>{hint}</p>}
    </div>
  );
}


function Choices({
  legend,
  options,
  selected,
  onSelect,
  required,
}: {
  legend: string;
  options: readonly { value: string | number; label: string; hint?: string }[];
  selected: string | number | "";
  onSelect: (value: string | number) => void;
  required?: boolean;
}) {
  return (
    <fieldset style={{ border: 0, padding: 0, margin: "20px 0 0" }}>
      <legend style={{ padding: 0 }}>
        <span className="metric-label">
          {legend}
          {required && <span className="required-dot">*</span>}
        </span>
      </legend>
      <div className="choice-set" style={{ marginTop: 8 }}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={`choice${selected === o.value ? " selected" : ""}`}
            aria-pressed={selected === o.value}
            onClick={() => onSelect(o.value)}
          >
            <span className="choice-label">{o.label}</span>
            {o.hint && <span className="choice-hint">{o.hint}</span>}
          </button>
        ))}
      </div>
    </fieldset>
  );
}


function ReviewTable({
  profile,
  holdings,
  onJump,
}: {
  profile: ProfileDraft;
  holdings: HoldingDraft[];
  onJump: (step: number) => void;
}) {
  const rows: { label: string; value: string; step: number }[] = [
    { label: "Age", value: profile.age?.toString() ?? "—", step: 0 },
    {
      label: "Years until needed",
      value: profile.horizon_years == null ? "—" : years(profile.horizon_years),
      step: 0,
    },
    { label: "Cash to invest", value: money(profile.investable_cash), step: 0 },
    {
      label: "Risk tolerance",
      value: RISK_LEVELS.find((r) => r.value === profile.risk_tolerance)?.label ?? "—",
      step: 0,
    },
    {
      label: "Experience",
      value:
        EXPERIENCE_LEVELS.find(
          (l) => l.value === nearestExperience(profile.self_reported_experience),
        )?.label ?? "—",
      step: 0,
    },
    { label: "Holdings", value: `${holdings.length} positions`, step: 1 },
  ];

  return (
    <div className="table-scroll">
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="muted" style={{ width: "45%" }}>
                {r.label}
              </td>
              <td>{r.value}</td>
              <td className="num">
                <button className="linklike small" onClick={() => onJump(r.step)}>
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- gating -------------------------------------------------------------------------------------

const STEP_FIELDS: Record<number, string[]> = {
  0: ["age", "dependents", "risk tolerance", "investing experience"],
  1: ["annual gross income", "monthly essential expenses", "monthly discretionary"],
};

function stepBlockers(profile: ProfileDraft, step: number): string[] {
  const wanted = STEP_FIELDS[step];
  if (!wanted) return [];
  return REQUIRED_FIELDS.filter((f) => wanted.includes(f.label) && !f.filled(profile)).map(
    (f) => f.label,
  );
}

function nearestExperience(value: number | null): number | "" {
  if (value === null) return "";
  return EXPERIENCE_LEVELS.reduce((best, l) =>
    Math.abs(l.value - value) < Math.abs(best.value - value) ? l : best,
  ).value;
}

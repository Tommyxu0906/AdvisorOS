/**
 * The translation layer between how people say things and how the API stores them.
 *
 * The backend keeps employer match as 0.04, APR as 0.229, and investing experience as a 0–1
 * float. Those are reasonable storage choices and terrible input controls: nobody has ever
 * described their credit card as charging "nought point two two nine", and asking someone to
 * rate their own experience as 0.65 produces a number that means nothing to them and is then
 * used as though it meant something.
 *
 * So the conversion happens here, once, at the edge. The draft and the API keep the fractions
 * they already use — no backend schema was changed to make the UI nicer — and every form field
 * reads and writes the human unit.
 */

/** 0.229 -> 22.9. Null passes through: blank is not zero. */
export function toPercent(fraction: number | null): number | null {
  if (fraction === null) return null;
  // Rounded because 0.229 * 100 is 22.900000000000002, which would render in a text input.
  return Math.round(fraction * 1000) / 10;
}

/** 22.9 -> 0.229 */
export function toFraction(percent: number | null): number | null {
  if (percent === null) return null;
  return Math.round(percent * 100) / 10000;
}

/**
 * Investing experience as something a person can actually answer about themselves.
 *
 * The midpoints are deliberate: a four-way choice mapped onto 0 / 0.33 / 0.67 / 1.0 would put
 * two of the four answers at the extremes, and "I have done this a few times" is not the same
 * claim as "I have no idea what a stock is".
 */
export const EXPERIENCE_LEVELS = [
  { value: 0.15, label: "Beginner", hint: "New to investing, or have only held funds" },
  { value: 0.4, label: "Intermediate", hint: "Comfortable with index funds and allocation" },
  { value: 0.7, label: "Advanced", hint: "Pick individual holdings, understand valuation" },
  { value: 0.95, label: "Professional", hint: "Work in markets or manage money for others" },
] as const;

export function experienceLabel(value: number | null): string {
  if (value === null) return "";
  // Nearest, rather than a threshold ladder: a stored 0.65 from an older profile should read as
  // Advanced rather than silently becoming Intermediate because it missed a cutoff.
  return EXPERIENCE_LEVELS.reduce((best, level) =>
    Math.abs(level.value - value) < Math.abs(best.value - value) ? level : best,
  ).label;
}

/**
 * Goal priority. The backend takes 1–5; the labels are what the user chooses between.
 *
 * Only four labels for five slots, because 2 and 3 are not distinguishable to anyone filling in
 * a form. The unused level stays reachable for a profile that already has it.
 */
export const GOAL_PRIORITIES = [
  { value: 1, label: "Essential", hint: "Everything else waits for this" },
  { value: 2, label: "High", hint: "A real commitment with a real date" },
  { value: 3, label: "Medium", hint: "Wanted, but the timing can move" },
  { value: 5, label: "Flexible", hint: "Nice to have, no deadline" },
] as const;

export function priorityLabel(value: number | null): string {
  if (value === null) return "";
  return (
    GOAL_PRIORITIES.reduce((best, p) =>
      Math.abs(p.value - value) < Math.abs(best.value - value) ? p : best,
    ).label
  );
}

/** Asset-class enum values are SCREAMING_SNAKE in the API and unreadable in a table cell. */
export function humanAssetClass(value: string): string {
  const named: Record<string, string> = {
    us_equity: "US equity",
    intl_developed_equity: "International developed",
    emerging_equity: "Emerging markets",
    bonds: "Bonds",
    tips: "TIPS",
    reit: "Real estate",
    commodities: "Commodities",
    crypto: "Crypto",
    cash: "Cash",
    other: "Other",
  };
  return named[value] ?? value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function humanAccountType(value: string): string {
  const named: Record<string, string> = {
    taxable: "Taxable brokerage",
    traditional_401k: "401(k)",
    roth_401k: "Roth 401(k)",
    traditional_ira: "Traditional IRA",
    roth_ira: "Roth IRA",
    hsa: "HSA",
    cash: "Cash / bank",
    other: "Other",
  };
  return named[value] ?? value.replace(/_/g, " ");
}

// --- money and figures -------------------------------------------------------------------------

export function money(value: number | null | undefined, { compact = false } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (compact && Math.abs(value) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function percent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

/**
 * Costs, rounded to something a person can hold in their head.
 *
 * `$0.0837` invites a precision the estimate does not have — it is a projection from average
 * token counts, not a quote. Sub-cent runs say "under a cent" rather than rounding to $0.00,
 * which would read as free.
 */
export function cost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || Number.isNaN(usd)) return "—";
  if (usd > 0 && usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

export function months(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)} months`;
}

export function years(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value === 1 ? "1 year" : `${value.toFixed(1)} years`;
}

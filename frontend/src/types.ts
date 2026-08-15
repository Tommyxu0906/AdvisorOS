/** Mirrors the backend Pydantic models. Kept narrow — only what the UI actually reads. */

export type AnalysisDepth = "quick" | "balanced" | "deep";

export interface Guardrail {
  code: string;
  severity: "info" | "caution" | "blocking";
  message: string;
  detail: string;
}

export interface NeedVector {
  liquidity_risk: number;
  debt_pressure: number;
  concentration_risk: number;
  valuation_sensitivity: number;
  behavioral_risk: number;
  tax_complexity: number;
  longevity_risk: number;
}

export interface ProfileAnalytics {
  life_stage: string;
  net_worth: number;
  savings_rate: number;
  emergency_fund_months: number;
  debt_to_income: number;
  debt_service_ratio: number;
  high_apr_debt_balance: number;
  weighted_avg_apr: number;
  annual_interest_cost: number;
  tax_advantaged_share: number;
  years_of_expenses_covered: number;
  need_vector: NeedVector;
  notable_findings: string[];
}

export interface PortfolioAnalytics {
  total_value: number;
  holding_count: number;
  largest_holding_symbol: string;
  largest_weight: number;
  hhi: number;
  effective_holdings: number;
  equity_share: number;
  defensive_share: number;
  taxable_share: number;
  weights: Record<string, number>;
}

export interface SelectedAdvisor {
  advisor_id: string;
  display_name: string;
  score: number;
  rationale: string;
  covers: string[];
}

export interface CommitteeSelection {
  depth: AnalysisDepth;
  selected: SelectedAdvisor[];
  required_dimensions: string[];
  covered_dimensions: string[];
  uncovered_dimensions: string[];
  mandatory_dimensions: string[];
  notes: string[];
}

/** A dollar range, never a point. The gap between the ends is the holding period nobody
 *  recorded — see TaxRange in backend/app/domain/action.py. */
export interface TaxRange {
  low_usd: number;
  high_usd: number;
  assumption: string;
}

export interface ProposedAction {
  action_id: string;
  kind: string;
  symbol: string | null;
  shares: number | null;
  amount_usd: number | null;
  target_weight: number | null;
  sequence: number;
  /** "house" for AdvisorOS's own rules, otherwise an advisor_id. Rendered — a debt paydown is
   *  not an advisor's opinion and must not be shown as one. */
  proposed_by: string;
  rationale: string;
  estimated_tax: TaxRange | null;
}

export interface MetricChange {
  label: string;
  before: number;
  after: number;
  higher_is_better: boolean | null;
  /** null where no direction is inherently good — net worth after a taxable sale. */
  improved: boolean | null;
}

export interface Counterfactual {
  feasible: boolean;
  changes: MetricChange[];
  estimated_tax: TaxRange | null;
  resolved_guardrails: string[];
  introduced_guardrails: string[];
  ineffective_actions: string[];
  unapplied: string[];
  /** Computed server-side on purpose. Re-deriving "good enough to show" here would put the
   *  definition in two languages and let them drift. */
  holds_up: boolean;
}

export interface SweepPoint {
  cap: number;
  acts: boolean;
  binding: string;
  proceeds_usd: number;
  largest_weight_after: number;
}

export interface Sensitivity {
  baseline: number;
  baseline_acts: boolean;
  binding_at_baseline: string;
  position_count: number;
  points: SweepPoint[];
  flip_at: number | null;
  declined: boolean;
  fragile: boolean;
  /** Authored server-side so the wording is written once. */
  summary: string[];
}

export interface PortfolioScenario {
  action_set: { actions: ProposedAction[] };
  counterfactual: Counterfactual;
  sensitivity: Sensitivity | null;
  policy_owner: string;
  is_house_policy: boolean;
  has_actions: boolean;
  worth_showing: boolean;
  headline: string;
}

export interface SelectResponse {
  selection: CommitteeSelection;
  analytics: ProfileAnalytics;
  portfolio_analytics: PortfolioAnalytics | null;
  guardrails: Guardrail[];
  question_topics: string[];
  /** Deterministic candidate actions. Present on the FREE response — no key required. */
  scenario: PortfolioScenario | null;
}

export interface EstimateResponse {
  depth: AnalysisDepth;
  advisor_count: number;
  stages: string[];
  expected_llm_calls: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_cost_usd: number | null;
  pricing_version: string;
  basis: string;
  caveat: string;
}

export interface AdvisorAnalysis {
  advisor_id: string;
  display_name: string;
  thesis: string;
  reasoning: string;
  recommendations: string[];
  risks_flagged: string[];
  confidence: number;
  declined: boolean;
  declined_reason: string;
}

export interface Critique {
  from_advisor_id: string;
  target_advisor_id: string;
  agreement: string;
  disagreement: string;
  strength: number;
}

export interface RiskChallenge {
  scenarios: string[];
  unaddressed_risks: string[];
  worst_case: string;
}

export interface CommitteeReport {
  run_id: string;
  depth: AnalysisDepth;
  question: string;
  summary: string;
  consensus: string[];
  disagreements: string[];
  recommended_actions: string[];
  open_questions: string[];
  analyses: AdvisorAnalysis[];
  revised_analyses: AdvisorAnalysis[];
  critiques: Critique[];
  risk_challenge: RiskChallenge | null;
  guardrails: Guardrail[];
  guardrail_violations: string[];
  disclaimer: string;
}

export interface CostLine {
  label: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number | null;
}

export interface RunUsage {
  run_id: string;
  total_calls: number;
  failed_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_creation_tokens: number;
  estimated_cost_usd: number | null;
  pricing_version: string;
  by_stage: CostLine[];
  by_advisor: CostLine[];
}

export interface RunResponse {
  report: CommitteeReport;
  selection: CommitteeSelection;
  usage: RunUsage;
  analytics: ProfileAnalytics;
  portfolio_analytics: PortfolioAnalytics | null;
}

export interface AdvisorSummary {
  advisor_id: string;
  display_name: string;
  subject: string;
  origin: string;
  one_line: string;
  topic_affinity: string[];
  blind_spots: string[];
  honest_boundaries: string[];
  runtime_profile_tokens: number;
}

/** The shape the form collects and posts. */
export interface ProfileInput {
  age: number;
  dependents: number;
  income: { annual_gross: number; annual_net?: number | null; stability: number; employer_match_pct: number };
  expenses: { monthly_essential: number; monthly_discretionary: number };
  debts: { name: string; balance: number; apr: number; minimum_monthly_payment: number }[];
  assets: { name: string; value: number; account_type: string; is_liquid: boolean }[];
  goals: { name: string; goal_type: string; years_until_needed: number; priority: number }[];
  risk_tolerance: string;
  self_reported_experience: number;
  notes: string;
}

export interface PortfolioInput {
  holdings: {
    symbol: string;
    asset_class: string;
    /** Optional share count. `market_value` stays authoritative — see draft.ts. */
    quantity?: number | null;
    market_value: number;
    /** Optional. Absent means the tax estimate reports "unknown", not zero. */
    cost_basis?: number | null;
  }[];
}

export interface Quote {
  symbol: string;
  price: number;
  previous_close: number | null;
  change_pct: number | null;
  as_of: string;
  is_delayed: boolean;
  source: string;
}

/** `unpriced` holds the symbols the provider could not resolve — see api.ts:getQuotes. */
export interface QuotesResponse {
  quotes: Quote[];
  unpriced: string[];
}

/**
 * What the server has stored for this account. Both fields are null on a first login, before
 * anything has been saved — see api.ts:getSavedProfile.
 */
export interface SavedProfile {
  profile: ProfileInput | null;
  portfolio: PortfolioInput | null;
}

/** One row of a signed-in user's run history — see api.ts:listRuns. */
export interface RunSummary {
  run_id: string;
  status: string;
  created_at: string;
  depth: AnalysisDepth;
  model: string;
  question: string;
  summary: string;
  advisor_ids: string[];
  guardrail_max_severity: string | null;
  total_calls: number;
  estimated_cost_usd: number | null;
}

/**
 * A saved run, reconstructed from stored JSONB snapshots. `report` is typed as CommitteeReport
 * for convenience, but it is a stored snapshot from whenever the run happened — an old run
 * could in principle predate a field this type now expects.
 */
export interface RunDetail {
  run_id: string;
  status: string;
  created_at: string;
  depth: AnalysisDepth;
  model: string;
  question: string;
  summary: string;
  error_message: string | null;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  estimated_cost_usd: number | null;
  pricing_version: string;
  guardrails: Guardrail[];
  report: CommitteeReport | null;
}

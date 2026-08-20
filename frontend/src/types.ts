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

export interface SelectResponse {
  selection: CommitteeSelection;
  analytics: ProfileAnalytics;
  portfolio_analytics: PortfolioAnalytics | null;
  guardrails: Guardrail[];
  question_topics: string[];
  /** Deterministic, free, and already on the wire — see PortfolioScenario. */
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

/* ---------------------------------------------------------------------------------------
 * The computed scenario.
 *
 * Everything below is produced by the deterministic policy engine on the server and arrives
 * on both the free `/committee/select` response and the paid `/committee/analyze` one. No
 * model touched any of it, which is why the UI can show it before a key is connected.
 *
 * `headline`, `has_actions`, `worth_showing`, `holds_up`, `fragile` and `summary` are computed
 * server-side and rendered as given. Recomputing any of them here would put the same rule in
 * two languages, and the copy that drifted would be the one users read.
 * ------------------------------------------------------------------------------------- */

export type ActionKind =
  | "trim_position"
  | "add_position"
  | "rebalance_to_target"
  | "pay_down_debt"
  | "build_emergency_fund"
  | "redirect_cashflow"
  | "hold";

export type Provenance = "direct" | "derived" | "house_default" | "unknown";
export type Binding = "threshold" | "arithmetic_floor" | "nothing";

export interface TaxRange {
  low_usd: number;
  high_usd: number;
  /** Why it is a range and not a number. Travels with the figure wherever it is shown. */
  assumption: string;
}

export interface ProposedAction {
  action_id: string;
  kind: ActionKind;
  symbol: string | null;
  asset_class: string | null;
  account_type: string | null;
  /** Exactly one of these three is non-null — the server validates that. */
  shares: number | null;
  amount_usd: number | null;
  target_weight: number | null;
  /** Lower runs first. Blocking guardrails resolve before anything optional. */
  sequence: number;
  /** "house" for AdvisorOS policy, otherwise an advisor_id. */
  proposed_by: string;
  rationale: string;
  estimated_tax: TaxRange | null;
}

export interface ActionSet {
  actions: ProposedAction[];
}

export interface MetricChange {
  label: string;
  before: number;
  after: number;
  higher_is_better: boolean | null;
  improved: boolean | null;
}

export interface Infeasibility {
  reason: string;
  action_id: string | null;
  message: string;
}

export interface Counterfactual {
  feasible: boolean;
  infeasibilities: Infeasibility[];
  unapplied: string[];
  changes: MetricChange[];
  estimated_tax: TaxRange | null;
  resolved_guardrails: string[];
  introduced_guardrails: string[];
  ineffective_actions: string[];
  /** Feasible, and it actually moved what it targeted. Computed server-side. */
  holds_up: boolean;
}

export interface Sensitivity {
  parameter: string;
  baseline: number;
  baseline_provenance: Provenance;
  baseline_acts: boolean;
  binding_at_baseline: Binding;
  position_count: number;
  flip_at: number | null;
  declined: boolean;
  /** Would a threshold a reasonable person might pick instead reverse this? */
  fragile: boolean;
  /** Plain sentences, authored server-side so the wording lives in one place. */
  summary: string[];
}

export interface PortfolioScenario {
  action_set: ActionSet;
  counterfactual: Counterfactual;
  sensitivity: Sensitivity | null;
  /** Whose thresholds produced this. Rendered next to every action. */
  policy_owner: string;
  is_house_policy: boolean;
  has_actions: boolean;
  worth_showing: boolean;
  headline: string;
}

/* ---------------------------------------------------------------------------------------
 * Advisory consultation.
 *
 * The lenses rank a choice set the engine computed; the constraint layer overrules any
 * preference the arithmetic forbids, and records that it did. `corrections` and
 * `synthesis.overrides` are shown, never hidden — a preference overruled by arithmetic is
 * information, and a transcript where every lens happens to agree with the engine would give
 * exactly the wrong impression.
 * ------------------------------------------------------------------------------------- */

export type Stance = "endorse" | "oppose" | "mixed" | "abstain";
export type ConfidenceSignal = "low" | "medium" | "high";
export type CandidateKind = "act" | "hold" | "alternative_threshold";

export interface DecisionCandidate {
  candidate_id: string;
  kind: CandidateKind;
  label: string;
  summary: string;
  action_ids: string[];
  feasible: boolean;
  /** Guardrail codes forbidding it. Non-empty means infeasible. */
  blocked_by: string[];
}

export interface AdvisorConsultResponse {
  advisor_id: string;
  display_name: string;
  stance: Stance;
  supported_action_ids: string[];
  opposed_action_ids: string[];
  preferred_candidate_id: string | null;
  rationale: string;
  risks_or_missing_information: string[];
  confidence_signal: ConfidenceSignal;
  declined: boolean;
  declined_reason: string;
  /** What the constraint layer had to change. Rendered, not swallowed. */
  corrections: string[];
  /** Distinct from abstaining: this lens contributed nothing readable at all. */
  parse_failed: boolean;
}

export interface ConsultSynthesis {
  selected_candidate_id: string;
  selected_label: string;
  headline: string;
  endorsing: string[];
  opposing: string[];
  abstaining: string[];
  overrides: string[];
  unresolved_disagreement: boolean;
}

export interface ConsultResponse {
  responses: AdvisorConsultResponse[];
  candidates: DecisionCandidate[];
  synthesis: ConsultSynthesis;
  /** Recomputed server-side from the profile sent, never accepted from the browser. */
  scenario: PortfolioScenario | null;
  guardrails: Guardrail[];
  usage: RunUsage;
}

/** One turn, held in memory only. No persistence in v1. */
export interface ChatTurn {
  role: "user" | "committee";
  text: string;
  advisor_responses: AdvisorConsultResponse[];
  synthesis?: ConsultSynthesis;
}

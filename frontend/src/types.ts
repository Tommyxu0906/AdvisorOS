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
  holdings: { symbol: string; asset_class: string; market_value: number }[];
}

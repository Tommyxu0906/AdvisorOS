/**
 * API client.
 *
 * Note the split: `postFree` never takes a key, `postWithKey` always does. Every function that
 * costs the user money goes through `postWithKey`, which makes the spend surface auditable by
 * reading this one file.
 */

import type {
  ConsultResponse,
  AdvisorSummary,
  AnalysisDepth,
  EstimateResponse,
  PortfolioInput,
  ProfileInput,
  QuotesResponse,
  RunDetail,
  RunResponse,
  RunSummary,
  SavedProfile,
  SelectResponse,
} from "./types";

// In dev, vite.config.ts proxies "/api" to localhost:8000. In production (e.g. frontend on
// Vercel, backend elsewhere), there is no proxy, so the backend origin must be supplied via
// VITE_API_BASE_URL at build time. Falls back to relative "/api" for same-origin deployments.
const API_ORIGIN = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";
const BASE = `${API_ORIGIN}/api`;

async function toResult<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const payload = await res.json();
      const detail = payload?.detail ?? payload;
      message = detail?.message ?? detail?.[0]?.msg ?? message;
    } catch {
      /* keep the status-code message */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return toResult<T>(res);
}

/** Endpoints that require no credentials. */
const postFree = request;

/** Endpoints that spend the user's tokens. The key is added here and nowhere else. */
function postWithKey<T>(path: string, apiKey: string, body: Record<string, unknown>): Promise<T> {
  return request<T>(path, { ...body, anthropic_api_key: apiKey });
}

/**
 * Endpoints that identify the caller instead of spending their tokens — run history. The
 * Supabase access token goes in an Authorization header, never in the body or the URL.
 */
async function getWithSession<T>(path: string, accessToken: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return toResult<T>(res);
}

// --- free -----------------------------------------------------------------------------

/** Server capabilities. `mock_llm` means canned answers — the UI must say so, never hide it. */
export function getHealth(): Promise<{ byok_only: boolean; mock_llm: boolean }> {
  return postFree<{ byok_only: boolean; mock_llm: boolean }>("/health");
}

export function listAdvisors(): Promise<AdvisorSummary[]> {
  return postFree<AdvisorSummary[]>("/advisors");
}

export function selectCommittee(
  profile: ProfileInput,
  portfolio: PortfolioInput | null,
  question: string,
  depth: AnalysisDepth,
): Promise<SelectResponse> {
  return postFree<SelectResponse>("/committee/select", { profile, portfolio, question, depth });
}

export function estimateRun(
  depth: AnalysisDepth,
  advisorCount: number,
  model: string,
): Promise<EstimateResponse> {
  return postFree<EstimateResponse>("/committee/estimate", {
    depth,
    advisor_count: advisorCount,
    model,
  });
}

/**
 * Delayed quotes from a free public feed. Free in both senses that matter here: no Anthropic key
 * and no account. Symbols the provider cannot resolve come back under `unpriced` rather than as
 * an error, so a portfolio of mixed market and non-market assets still gets an answer.
 */
export function getQuotes(symbols: string[]): Promise<QuotesResponse> {
  const query = encodeURIComponent(symbols.join(","));
  return postFree<QuotesResponse>(`/market/quotes?symbols=${query}`);
}

// --- key required ---------------------------------------------------------------------

export async function validateKey(
  apiKey: string,
  model: string,
): Promise<{ valid: boolean; error?: string | null }> {
  return postWithKey<{ valid: boolean; error?: string | null }>("/auth/anthropic/validate", apiKey, {
    model,
  });
}

export function runCommittee(
  apiKey: string,
  profile: ProfileInput,
  portfolio: PortfolioInput | null,
  question: string,
  depth: AnalysisDepth,
  model: string,
  advisorIds: string[] | null,
): Promise<RunResponse> {
  return postWithKey<RunResponse>("/committee/analyze", apiKey, {
    profile,
    portfolio,
    question,
    depth,
    model,
    advisor_ids: advisorIds,
  });
}

// --- run history (session required, no Anthropic key involved) ------------------------

/** One turn of the consultation. The whole history goes up each time — see ConsultRequest. */
export function consultCommittee(
  apiKey: string,
  profile: ProfileInput,
  portfolio: PortfolioInput | null,
  question: string,
  advisorIds: string[],
  history: { role: "user" | "committee"; text: string; advisor_responses: unknown[] }[],
  model: string,
): Promise<ConsultResponse> {
  return postWithKey<ConsultResponse>("/committee/consult", apiKey, {
    profile,
    portfolio,
    question,
    advisor_ids: advisorIds,
    history,
    model,
  });
}

export function listRuns(accessToken: string): Promise<RunSummary[]> {
  return getWithSession<RunSummary[]>("/runs", accessToken);
}

export function getRun(accessToken: string, runId: string): Promise<RunDetail> {
  return getWithSession<RunDetail>(`/runs/${encodeURIComponent(runId)}`, accessToken);
}

// --- saved profile (session required, no Anthropic key involved) -----------------------

export function getSavedProfile(accessToken: string): Promise<SavedProfile> {
  return getWithSession<SavedProfile>("/profile", accessToken);
}

export async function saveProfile(
  accessToken: string,
  profile: ProfileInput,
  portfolio: PortfolioInput | null,
): Promise<SavedProfile> {
  const res = await fetch(`${BASE}/profile`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ profile, portfolio }),
  });
  return toResult<SavedProfile>(res);
}

export function distillAdvisor(
  apiKey: string,
  subject: string,
  focusAreas: string[],
  depth: "quick" | "standard" | "deep",
  model: string,
): Promise<{ advisor: AdvisorSummary; warnings: string[]; usage: RunResponse["usage"] }> {
  return postWithKey("/advisors/distill", apiKey, {
    subject,
    focus_areas: focusAreas,
    depth,
    model,
  });
}

/**
 * API client.
 *
 * Note the split: `postFree` never takes a key, `postWithKey` always does. Every function that
 * costs the user money goes through `postWithKey`, which makes the spend surface auditable by
 * reading this one file.
 */

import type {
  AdvisorSummary,
  AnalysisDepth,
  EstimateResponse,
  PortfolioInput,
  ProfileInput,
  RunResponse,
  SelectResponse,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

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

/** Endpoints that require no credentials. */
const postFree = request;

/** Endpoints that spend the user's tokens. The key is added here and nowhere else. */
function postWithKey<T>(path: string, apiKey: string, body: Record<string, unknown>): Promise<T> {
  return request<T>(path, { ...body, anthropic_api_key: apiKey });
}

// --- free -----------------------------------------------------------------------------

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
): Promise<RunResponse> {
  return postWithKey<RunResponse>("/committee/analyze", apiKey, {
    profile,
    portfolio,
    question,
    depth,
    model,
  });
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

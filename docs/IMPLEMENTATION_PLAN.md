# AIFinancialAdvisor — Implementation Plan (V2, BYOK)

> **Status of this document.** The working directory contained no prior code and no
> `docs/IMPLEMENTATION_PLAN.md` when V2 began, so this file is written fresh rather than edited.
> There was also no pre-existing "Nuwa" system anywhere on this machine — the distillation layer
> described in §9 is built as part of this project, not imported. See "Assumptions" at the end.

## 0. The one-sentence architecture

**The user brings their own Anthropic API key. Deterministic code does all financial math and all
routing decisions. Claude is used only where natural language reasoning is genuinely required, and
every token it costs is billed to the user's own Anthropic account and attributed back to them.**

## 1. Layers

```text
┌──────────────────────────────────────────────────────────┐
│ Browser                                                  │
│   AnthropicConnectionContext  (API key in memory only)   │
│   Profile intake · Portfolio · Question · Depth mode     │
└───────────────────────┬──────────────────────────────────┘
                        │  HTTPS, key in request body
┌───────────────────────▼──────────────────────────────────┐
│ FastAPI backend                                          │
│                                                          │
│  FREE / DETERMINISTIC          PAID / LLM                │
│  ────────────────────          ──────────                │
│  ProfileAnalyzer               AdvisorAnalysis           │
│  PortfolioAnalytics            CrossExamination          │
│  Guardrails                    RiskChallenge             │
│  AdvisorRegistry               Synthesis                 │
│  AdvisorSelector               Nuwa distillation         │
│  CommitteeOptimizer                                      │
│  UsageTracker / CostCalculator                           │
└───────────────────────┬──────────────────────────────────┘
                        │ per-request AnthropicClientFactory.create(user_key)
                        ▼
                  Anthropic API
```

Two things never cross a layer boundary:

1. A `SecretStr` API key never reaches a logger, a store, a response body, or a traceback.
2. An LLM call never happens without an explicit `RunContext` carrying credentials.

## 2. Free vs paid — the load-bearing distinction

Everything in the left column below runs with **no Anthropic key at all**. The endpoints backing
them are usable by anyone, including CI.

| Deterministic (no key)                                   | LLM (user's key)                  |
| -------------------------------------------------------- | --------------------------------- |
| Savings rate, debt ratios, DTI, emergency-fund months     | Question intent extraction        |
| Need vector (7 dimensions), life-stage inference          | Independent advisor analysis      |
| Portfolio weights, HHI, concentration, effective N        | Cross-examination / critique      |
| Return, volatility, max drawdown, correlation matrix      | Risk challenge                    |
| Hard financial guardrails (§4)                            | Final synthesis                   |
| Advisor registry load + runtime-profile compilation       | Custom advisor distillation       |
| Advisor scoring, ranking, committee optimization          |                                   |
| Token usage aggregation and cost estimation               |                                   |

## 3. Domain model (Phase 1)

`backend/app/domain/`

- `profile.py` — `FinancialProfile`, `Income`, `Expenses`, `Debt`, `Asset`, `Goal`,
  `RiskTolerance`, `TimeHorizon`, `LifeStage`.
- `portfolio.py` — `Holding`, `Portfolio`, `AssetClass`, `PriceSeries`.
- `question.py` — `UserQuestion`, `QuestionIntent` (deterministic keyword extraction first;
  LLM extraction only as an optional refinement).
- `advisor.py` — `AdvisorManifest` (full Nuwa artifact) and `AdvisorRuntimeProfile` (compact
  derivative actually sent to Claude).
- `report.py` — `AdvisorAnalysis`, `Critique`, `RiskChallenge`, `CommitteeReport`.

All Pydantic v2 with validation (non-negative amounts, weights that sum, currency consistency).

## 4. Deterministic analytics + guardrails (Phase 2, 8)

`ProfileAnalytics` produces a `NeedVector` — seven scores in `[0,1]`:

`liquidity_risk, debt_pressure, concentration_risk, valuation_sensitivity, behavioral_risk,
tax_complexity, longevity_risk`

`Guardrails` are **hard rules that code enforces and the LLM cannot override**. Each returns a
`Guardrail` with severity and a fixed message, e.g.:

- Emergency fund < 3 months → block "invest the cash" style recommendations.
- Debt at APR > 8% outstanding → prioritize payoff over incremental equity risk.
- Single-position weight > 25% → concentration warning must appear in the final report.
- Time horizon < 3 years for a stated goal → equity allocation caution.

Guardrails are injected into every advisor prompt *and* re-checked against the synthesized report.
Violations are annotated on the report, not silently dropped.

`PortfolioAnalytics` computes weights, HHI, effective N, per-asset-class exposure, and — when a
price series is supplied — annualized return, volatility, max drawdown, and a correlation matrix.
Pure Python/`statistics`; no numpy dependency for the MVP.

## 5. BYOK credential architecture (Phase 5)

```python
class UserLLMCredentials(BaseModel):
    anthropic_api_key: SecretStr
    model_config = ConfigDict(frozen=True)
    def __repr__(self) -> str: return "UserLLMCredentials(anthropic_api_key=<redacted>)"
```

- `AnthropicClientFactory.create(credentials) -> AsyncAnthropic` — a **new client per request**.
  There is no module-level client and no `ANTHROPIC_API_KEY` read anywhere in the request path.
- `RunContext(run_id, credentials, model_config, usage_tracker, depth)` is required by every
  method that can reach the network. This is enforced by signature, not by convention.
- `POST /api/auth/anthropic/validate` does one minimal `max_tokens=1` call and returns
  `{"valid": true}` or `{"valid": false, "error": "<sanitized>"}`. Never echoes the key.

Failure mode when no key is present: `/api/committee/analyze` returns **402-style refusal**
(`503 llm_unavailable` with a machine-readable code), not a silent fallback to a developer key.

## 6. Provider abstraction + usage (Phase 6)

```python
class LLMProvider(Protocol):
    async def generate(self, messages, context, *, system=None, max_tokens=..., ...) -> LLMResponse
```

Implementations: `AnthropicBYOKProvider` (real) and `MockLLMProvider` (deterministic canned
responses, used by every test and by CI). Multi-provider support is explicitly out of scope for V1.

`LLMResponse` carries `text`, `parsed` (when structured output was requested), and `LLMCallUsage`.
`UsageTracker` aggregates into `RunUsage` with `estimated_cost_usd`.

Pricing lives in `config/model_pricing.json`, versioned, with `input/output/cache_read/cache_write`
per million tokens. The UI always labels the number **"Estimated cost"**.

## 7. Advisor registry, runtime profiles, selection (Phases 3–4)

An advisor ships as a directory:

```text
backend/app/advisors/builtin/<advisor_id>/
  manifest.json     # AdvisorManifest — identity, expertise vector, mental models, evidence refs
  SKILL.md          # human/Nuwa-readable long form (traceability; NOT sent at runtime)
```

At load time the registry compiles `manifest.json` into an `AdvisorRuntimeProfile`: identity,
mental models, heuristics, expertise, blind spots, reasoning rules, honest boundaries — bounded to
a target of ~1,200 tokens. `SKILL.md` and any Nuwa research artifacts are **never** sent to Claude
at committee runtime. This is the single largest cost lever in the system.

Selection is deterministic:

1. Score each advisor as `dot(need_vector, advisor.expertise_vector)` plus question-intent affinity
   and guardrail-triggered mandatory-coverage bonuses.
2. Greedily build the smallest team that covers every need dimension above threshold, applying a
   **diversity penalty** so two advisors with near-identical expertise vectors are not both chosen.
3. Cap at 3 (quick/balanced) or 4 (deep). Every selection returns a human-readable rationale per
   advisor — this is what the UI shows before the user spends a token.

## 8. Committee orchestration (Phase 7)

| Mode     | Advisors | Stages                                                        | Calls |
| -------- | -------- | ------------------------------------------------------------- | ----- |
| Quick    | 3        | independent ×3 → synthesis                                     | 4     |
| Balanced | 3        | independent ×3 → cross-exam ×3 → risk challenge → synthesis    | 8     |
| Deep     | 4        | independent ×4 → cross-exam ×4 → revised memo ×4 → risk → synth| 14    |

Independent analyses run concurrently (`asyncio.gather`). Cross-examination gives each advisor the
*other* advisors' theses only, never the full transcript, to bound tokens. Prompt structure is
split stable-first (advisor runtime profile, guardrail text) then dynamic (profile, analytics,
question) so the stable prefix is cacheable.

## 9. Nuwa distillation (Phase 9)

Nuwa is the **advisor production layer**, run once per advisor, paid for by whoever requests it:

```text
subject + focus areas + depth
   → research plan (LLM)
   → N research passes (LLM)
   → synthesis into AdvisorManifest + SKILL.md (LLM, structured output)
   → deterministic validation (schema, expertise vector normalized, boundaries present)
   → registry write (custom/, private to that install)
```

Built-in advisors ship already distilled — users never pay to *use* them, only to *run* them.
Distillation is never re-run when an existing advisor joins a committee.

## 10. API surface (Phase 10)

| Endpoint                          | Key required | Notes                                  |
| --------------------------------- | ------------ | -------------------------------------- |
| `POST /api/auth/anthropic/validate` | yes (in body) | Returns `{valid}` only                |
| `POST /api/profiles/analyze`      | no           | Deterministic analytics + guardrails   |
| `POST /api/portfolio/analyze`     | no           | Deterministic portfolio metrics        |
| `GET  /api/advisors`              | no           | Registry listing                       |
| `POST /api/committee/select`      | no           | Deterministic selection + rationale    |
| `POST /api/committee/estimate`    | no           | Call-count / token estimate per mode   |
| `POST /api/committee/analyze`     | **yes**      | Runs the committee                     |
| `POST /api/advisors/distill`      | **yes**      | Nuwa                                   |

## 11. Frontend (Phase 11)

React + Vite + TypeScript. `AnthropicConnectionContext` holds the key in a `useState` in memory
only — no `localStorage`, no `sessionStorage`, no cookie. Refresh requires re-entry; that is the
intended tradeoff. Flow mirrors §10 top to bottom, ending in the cost breakdown panel.

## 12. Security posture (Phase 13)

`backend/app/core/redaction.py` provides `redact(value)` handling strings, dicts, lists, and
exceptions, keyed on field names (`anthropic_api_key`, `x-api-key`, `authorization`, `api_key`,
`credentials`, `secret`, `token`, `password`) plus a regex for `sk-ant-*` anywhere in free text.
A logging `Filter` applies it to every record. Mandatory tests (all must pass in CI):

`test_api_key_not_persisted`, `test_api_key_not_logged`, `test_api_key_not_returned`,
`test_api_key_redacted_from_exception`, `test_invalid_api_key_handling`,
`test_different_users_use_different_credentials`, `test_no_developer_key_fallback`.

## 13. Evaluation (Phase 12)

Runs against `MockLLMProvider` (correctness, determinism, CI) and optionally against a real key
(cost/quality). Measures: advisor-selection accuracy vs a labelled fixture set, persona
differentiation (lexical overlap between advisor outputs), guardrail coverage, structured-output
reliability, and — per depth mode — LLM calls, tokens, estimated cost, latency, advisor context
size.

## 14. Build order

```text
0  repo scaffold                          ✔ prerequisite
1  domain models
2  profile analytics + guardrails
3  advisor registry + runtime profiles
4  deterministic selection / optimizer
5  BYOK credentials + RunContext
6  provider + usage tracker + pricing
7  committee orchestration
8  portfolio analytics
9  Nuwa distillation (BYOK)
10 FastAPI
11 frontend + BYOK UX
12 evaluation harness
13 security tests + redaction
14 CI + README + demo
```

## Assumptions

1. **No pre-existing Nuwa.** The V2 brief refers to Nuwa as if it exists; nothing by that name was
   present in this repository, the parent directories, or anywhere under `~/Desktop`, `~/Documents`,
   or `~/Developer`. It is therefore built here as `backend/app/nuwa/`. If an external Nuwa was
   intended, the importer in `nuwa/importer.py` is the seam to point at it.
2. **No prior V1 code to refactor.** The instruction to "refactor anything that assumes a global
   application-owned Anthropic API key" found nothing to refactor; BYOK is designed in from the
   first commit instead.
3. **Local git repository.** The project directory sat inside an accidental git repository rooted at
   the user's home folder. `git init` was run here so this project has its own history and does not
   commit into that repository.

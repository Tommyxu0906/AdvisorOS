# AIFinancialAdvisor

A BYOK multi-agent financial decision system. Deterministic code converts an investor profile
into a need vector, selects a small committee of distilled advisor personas, and orchestrates
independent analysis, cross-examination, risk challenge, and synthesis — with per-run token and
cost attribution.

**Three principles govern the design:**

1. **The LLM proposes and reasons.**
2. **Code calculates and decides.**
3. **The user owns the inference credentials and the cost.**

---

## BYOK: what that actually means here

Users bring their own Anthropic API key. Every user-triggered inference call — committee runs and
custom advisor distillation alike — runs on the user's credentials and is billed to their
Anthropic account. The project owner pays nothing for public usage, and the server has no key of
its own to fall back on.

Concretely:

- The application **starts and serves its entire deterministic half with no `ANTHROPIC_API_KEY`
  anywhere in the environment.** CI enforces this: the workflow fails if a credential is present.
- No request-path module reads `ANTHROPIC_API_KEY`. There is a test that greps for it.
- There is **no module-level Anthropic client.** Each request builds its own from its own key via
  `AnthropicClientFactory.create(credentials)`. Two concurrent users never share a client.
- The key is never written to a database, a log, a response body, a run artifact, or
  `localStorage`. It exists in browser memory and request memory, and nowhere else.

### The free / paid split

This is the load-bearing architectural line. Everything on the left runs for free, for anyone.

| Deterministic — no key required                          | LLM — user's key required     |
| -------------------------------------------------------- | ----------------------------- |
| Savings rate, debt ratios, DTI, emergency-fund months     | Advisor analysis              |
| Need vector (7 dimensions), life-stage inference          | Cross-examination             |
| Portfolio weights, HHI, concentration, effective N        | Risk challenge                |
| Return, volatility, max drawdown, correlation matrix      | Final synthesis               |
| Financial guardrails                                      | Custom advisor distillation   |
| Advisor registry, scoring, committee optimization         |                               |
| Question intent classification                            |                               |
| Token usage aggregation and cost estimation               |                               |

A user can enter their whole financial situation, see the analysis, see which advisors would be
selected and why, and see what a run would cost — all before spending a single token.

---

## Quick start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Run the backend (deliberately with no credentials in the environment):

```bash
env -u ANTHROPIC_API_KEY PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --port 8000
```

Run the frontend:

```bash
npm install --prefix frontend && npm run dev --prefix frontend
```

Open http://localhost:5173. The financial analysis and committee selection work immediately.
Paste an Anthropic key into the Connect panel to enable the committee run.

Run the tests and the evaluation harness — neither needs a key or a network:

```bash
PYTHONPATH=backend:. .venv/bin/python -m pytest -q
```

```bash
.venv/bin/python evals/run_eval.py
```

---

## Deploying

This is two separately deployable services — a Python/FastAPI backend and a static Vite/React
frontend — not a single app. **Deploying the repo root to Vercel as-is will 404**: Vercel can't
infer a framework from a monorepo root, and FastAPI's `uvicorn` process doesn't run as a Vercel
serverless function without separate adaptation.

**Frontend on Vercel:**

1. In the Vercel project's *Settings → General*, set **Root Directory** to `frontend`. Vercel
   auto-detects Vite from there; no `vercel.json` is needed.
2. In *Settings → Environment Variables*, add `VITE_API_BASE_URL` pointing at wherever the
   backend ends up (e.g. `https://your-backend.up.railway.app`, no trailing slash). See
   `frontend/.env.example`. Locally this is left unset — `vite.config.ts` proxies `/api` to
   `localhost:8000` instead.
3. Redeploy.

**Backend** — needs a host that runs a long-lived process (`uvicorn`), not a serverless
function: Railway, Render, or Fly.io all work with no code changes. Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

with `PYTHONPATH=backend` and **no `ANTHROPIC_API_KEY` set** — that's the BYOK invariant, not an
oversight. Set `AIFA_CORS_ORIGINS` to the frontend's deployed origin (comma-separated if there's
more than one, e.g. a preview URL and the production domain) so the browser's requests aren't
blocked by CORS.

---

## How a run works

```text
profile + portfolio + question
        │
        ▼  deterministic, free
  ProfileAnalytics ──► NeedVector (7 dims)
  PortfolioAnalytics
  Guardrails ─────────► blocking / caution / info
  QuestionIntent
        │
        ▼  deterministic, free
  CommitteeSelector ──► smallest team covering every salient need,
                        with a written rationale per advisor
        │
        ▼  user sees the team, the stages, and a cost estimate
        │
        ▼  user clicks Run — their key, their tokens
  independent analyses (concurrent)
        └─► cross-examination
              └─► revised memos        (deep only)
                    └─► risk challenge
                          └─► synthesis
        │
        ▼
  Report + guardrail re-check + full cost breakdown
```

### Depth modes

The committee is deliberately small. The architecture could run eight or ten agents; it does not,
because each one costs the user money.

| Mode     | Advisors | Stages                                                          | LLM calls |
| -------- | -------- | --------------------------------------------------------------- | --------- |
| Quick    | 3        | independent → synthesis                                          | 4         |
| Balanced | 3        | independent → cross-exam → risk challenge → synthesis            | 8         |
| Deep     | 4        | independent → cross-exam → revised memo → risk challenge → synth | 14        |

The optimizer may trim a seat when coverage is already complete — but never below three, because
a committee of one has nothing to cross-examine. The pre-run estimate and the actual call count
are asserted equal in the test suite, so the number the user sees before paying is the number
they get.

### Guardrails are enforced by code, not by the model

Deterministic rules — thin emergency fund, high-APR debt, negative cash flow, position
concentration, short-horizon goals — are computed from the user's numbers and injected into every
advisor prompt as binding constraints. After synthesis, the report is **re-checked** against the
blocking guardrails; apparent contradictions are surfaced on the report rather than silently
accepted. A persuasive model cannot talk its way past arithmetic.

### Advisor runtime profiles are the cost lever

Each advisor ships as a full `AdvisorManifest` (mental models, heuristics, blind spots, evidence,
provenance) plus a `SKILL.md`. **Neither is sent to Claude at runtime.** The registry compiles a
compact `AdvisorRuntimeProfile` — currently 535–638 estimated tokens per advisor — and only that
reaches the prompt. Source material stays on disk for traceability. A test asserts that no
manifest's provenance string ever appears in a prompt.

Prompts are also split stable-first: the advisor profile and committee charter go in a cacheable
prefix, the user's numbers follow it.

### Distillation happens once, not per query

Nuwa is the advisor *production* layer, not part of the query path:

```text
subject → research plan → N research passes → synthesis → deterministic validation → registry
```

Six advisors ship pre-distilled (Bogle, Buffett, Munger, Marks, Damodaran, Housel). Users pay
nothing to *use* them — only the runtime inference cost of running them. When a user requests a
new advisor, distillation runs on **their** key, once, and the resulting advisor is reusable
forever. A test asserts that a committee run never invokes a Nuwa stage.

Validation after synthesis is deterministic and strict: a profile with no declared blind spots,
no honest boundaries, an all-zero expertise vector, or no mental models is **rejected**. A
persona with no acknowledged limits is not safe to put in front of someone's finances.

---

## Security posture

`backend/app/core/redaction.py` redacts by field name (`anthropic_api_key`, `x-api-key`,
`authorization`, `credential`, `secret`, …) *and* by value shape (`sk-ant-*` anywhere in free
text), across dicts, lists, exceptions, Pydantic models, and unknown objects. A logging filter
applies it to every record, including exception text.

Logs identify a user by a truncated SHA-256 **fingerprint** (`key-278a92b5137e`), never the key.

The mandatory security tests, all of which run in CI with no credentials present:

| Test                                          | What it proves                                            |
| --------------------------------------------- | ---------------------------------------------------------- |
| `test_api_key_not_persisted`                  | No run artifact or file contains the key                    |
| `test_api_key_not_logged`                     | Keys in messages, args, and tracebacks are redacted         |
| `test_api_key_not_returned`                   | No response body echoes it                                  |
| `test_api_key_redacted_from_exception`        | Exception text and tracebacks are scrubbed                  |
| `test_invalid_api_key_handling`               | Malformed keys fail locally; auth failures return a fixed message |
| `test_different_users_use_different_credentials` | Each request gets its own client                         |
| `test_no_developer_key_fallback`              | Setting `ANTHROPIC_API_KEY` does not enable anything        |

Plus: no module-level Anthropic client exists, no request-path module reads the env var, and the
deterministic endpoints all work with zero credentials.

---

## Evaluation

```bash
.venv/bin/python evals/run_eval.py            # mock provider — deterministic, no key, no network
.venv/bin/python evals/run_eval.py --live     # real calls on YOUR key; costs money
```

Six labelled fixture profiles measure advisor-selection accuracy, guardrail recall, need-vector
recall, persona differentiation (pairwise lexical overlap), structured-output reliability, and —
per depth mode — LLM calls, tokens, cache reads, estimated cost, latency, and advisor context
size. CI gates on the deterministic metrics.

**Measured on the mock provider (deterministic routing, no model involved):**

| Metric                      | Result |
| --------------------------- | ------ |
| Advisor selection accuracy  | 100%   |
| Guardrail recall            | 100%   |
| Need-vector recall          | 86.1%  |
| Guardrail violations        | 0      |

Cost, latency, and persona-overlap figures are **not** reported here as measured results. The
mock provider's canned text characterizes the pipeline's shape, not real model behavior. Run
`--live` against your own key to measure those.

---

## Layout

```text
backend/app/
  domain/       profile, portfolio, needs, question, advisor, report — plain validated data
  analytics/    profile_analytics, portfolio_analytics, guardrails   — deterministic, no LLM
  advisors/     registry, selection, builtin/<id>/{manifest.json,SKILL.md}
  core/         credentials, run_context, redaction                  — the BYOK boundary
  llm/          provider protocol, anthropic_provider, mock_provider, usage, pricing
  committee/    orchestrator, prompts, schemas
  nuwa/         distiller, schemas, importer
  api/          routes/{auth,analysis,committee}, schemas, deps
config/         model_pricing.json — versioned, never hardcoded at call sites
frontend/src/   React + Vite; AnthropicConnectionContext holds the key in memory only
evals/          fixtures + run_eval.py
tests/          unit, integration, security
docs/           IMPLEMENTATION_PLAN.md
```

## API

| Endpoint                            | Key | Purpose                                  |
| ----------------------------------- | --- | ---------------------------------------- |
| `GET  /api/health`                  | no  | Includes `byok_only` — the BYOK invariant |
| `GET  /api/capabilities`            | no  | What is free vs what needs a key          |
| `POST /api/profiles/analyze`        | no  | Analytics + guardrails                    |
| `POST /api/portfolio/analyze`       | no  | Portfolio metrics                         |
| `GET  /api/advisors`                | no  | Registry listing                          |
| `GET  /api/market/quotes`           | no  | Delayed prices for held symbols           |
| `POST /api/committee/select`        | no  | Deterministic selection + rationale       |
| `POST /api/committee/estimate`      | no  | Call count and cost estimate per mode     |
| `POST /api/auth/anthropic/validate` | yes | Returns `{valid}` only                    |
| `POST /api/committee/analyze`       | yes | Runs the committee                        |
| `POST /api/advisors/distill`        | yes | Nuwa distillation                         |

## Market data

Prices come from Yahoo Finance's public chart endpoint: no API key, no signup, no operator cost.
The tradeoff is stated rather than hidden — it is an unofficial endpoint that can change shape
without notice, and its prices are exchange-delayed, not a live tick feed. Daily bars are enough
for everything this product computes from price data (annualized volatility, max drawdown,
correlation); a real-time feed would add cost and a WebSocket ingestion service without changing
a single number the UI reports.

Responses are cached in Postgres (`instruments`, `daily_bars`, `latest_quotes`) with a 15-minute
TTL, so one fetch serves every user holding that symbol. `market_data_fetches` records every
attempt including failures — without it, a provider outage looks identical to "the market did not
move." With no `DATABASE_URL` present, fetches still work; they simply go uncached.

A fetch is fetched only where it earns its latency: the paid `/api/committee/analyze` path
enriches the portfolio with real return series before analysis, while the free deterministic
endpoints (called on every keystroke behind a debounce) stay purely local. Symbols the provider
cannot resolve are left unpriced rather than failing the request — and deliberately are *not*
given a synthetic zero-return series, since fake zeros would understate portfolio volatility.
Cash is the one exception, because cash genuinely does not move.

## Cost model

`config/model_pricing.json` is versioned and loaded at runtime; the version used is recorded on
every run's usage record. Cost is arithmetic over measured token counts, and an unpriced model
returns `null` rather than `0.0` — "unknown" must never be displayed as "free". Every figure the
UI shows is labelled **estimated**, not Anthropic's billing amount.

## Disclaimer

This produces an educational analysis from AI personas. It is not personalized investment advice
from a licensed advisor, and the personas are analytical constructs, not the real people they are
modelled on.

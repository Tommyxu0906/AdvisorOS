# Deployment: two modes, one build

The same code serves both. The only difference is one server-side environment variable, and it
changes exactly one thing: whether the committee's reasoning comes from Anthropic or from canned
placeholders. **Every deterministic computation is real in both modes** — diagnostics,
guardrails, the policy engine, candidate actions, feasibility, the counterfactual, and the
sensitivity sweep never involve a model in either configuration.

| | Production | Public demo |
|---|---|---|
| `AIFA_MOCK_LLM` | `0` (or unset) | `1` |
| Committee reasoning | Anthropic, billed to the visitor's key | Canned placeholders |
| Anthropic key required | Yes, to run a committee | No |
| Deterministic analysis | Real | Real |
| Scenario / candidate actions | Real | Real |
| Cost per visitor | Zero to you (BYOK) | Zero to everyone |

## The flag is server-side, and cannot be reached from a browser

`AIFA_MOCK_LLM` is read in exactly one place, `backend/app/api/deps.py`:

```python
def mock_llm_enabled() -> bool:
    return os.environ.get("AIFA_MOCK_LLM") == "1"
```

No request field, header, query parameter, or client payload feeds into it. `/api/health`
*reports* the flag so the interface can disclose it; the browser learns the value and has no way
to set it. A crafted request cannot talk a production server into serving fabricated model
output — it will get the real provider and be asked for a key.

This is asserted by the gate, not just documented.

## What the demo says about itself

Two disclosures, both unconditional when the flag is on.

A badge on the committee header:

> **Demo answers — no model called**

And a panel above the input:

> **Demo mode: these answers are canned**
> This server was started with `AIFA_MOCK_LLM=1`, so no model is called and no key is spent. The
> stances are fixed placeholders that exist to exercise the interface — they are not analysis,
> and the disagreement below was written into the mock.

Neither can be dismissed, and neither is conditional on anything but the server flag.

Separately, and in **both** modes, the committee header states what the personas are:

> Warren Buffett and Charlie Munger — reasoning frameworks applied to the scenario above, not
> the people themselves.

The prompt charter forbids first-person impersonation, so no output claims to be the real
investors, in either mode.

## Backend (Render)

Two services from the same repository and branch.

**Production**

```
Build    pip install -e ".[dev]"
Start    uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

| Variable | Value |
|---|---|
| `PYTHONPATH` | `backend` |
| `AIFA_CORS_ORIGINS` | production frontend origin |
| `DATABASE_URL` | Supabase connection string |
| `SUPABASE_URL` | Supabase project URL |
| `AIFA_MOCK_LLM` | **unset**, or `0` |
| `ANTHROPIC_API_KEY` | **must not be set** |
| `AIFA_ALLOW_DEV_KEY` | **must not be set** |

**Public demo** — identical, except:

| Variable | Value |
|---|---|
| `AIFA_CORS_ORIGINS` | demo frontend origin |
| `AIFA_MOCK_LLM` | `1` |
| `DATABASE_URL` | omit it |

Omitting `DATABASE_URL` on the demo is deliberate: the app boots and serves everything without
persistence, so a demo visitor cannot create an account or leave anything behind. There is
nothing to clean up and nothing to leak.

## Frontend (Vercel)

Two projects from the same repository, `frontend/` as root.

| Variable | Production | Demo |
|---|---|---|
| `VITE_API_BASE_URL` | production API origin | demo API origin |
| `VITE_SUPABASE_URL` | Supabase project URL | omit |
| `VITE_SUPABASE_ANON_KEY` | anon key | omit |

Omitting the Supabase variables on the demo removes the sign-in path, which matches a backend
that has no database. The anon key is publishable by design and safe in a bundle — it is omitted
here because the demo has nothing to authenticate against, not because it is secret.

## Verifying a deployment

```bash
curl -s https://<api-host>/api/health
```

Production must report:

```json
{ "status": "ok", "byok_only": true, "mock_llm": false }
```

Demo must report `"mock_llm": true`. If a host reports `byok_only: false`, `AIFA_ALLOW_DEV_KEY`
is set and the server holds a key of its own — fix that before sending anyone the URL.

## Standing constraints

- No Anthropic key belongs in any backend environment. CI asserts this.
- Secrets are pasted into the Render and Vercel dashboards directly, never into a file, a commit,
  or a chat window.
- Neither mode connects to a brokerage or places a trade. The read-only connector is not enabled
  in the interface, and the paper-trading harness has no live endpoint in the codebase at all.

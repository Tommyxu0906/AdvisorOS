# Brokerage Connector — Implementation Delta

Branch: `feature/brokerage-connector`. Base: `1dba0f3`.

This is the delta against what exists today, written after reading the auth, database, and
deployment code rather than from the feature description. Where the existing system already
satisfies a requirement, that is said and nothing is built.

---

## Phase A findings — what already exists

### Identity: already sufficient, nothing to add

The requirement is an immutable internal user UUID rather than an email. That exists.

```
auth.users.id (uuid, Supabase Auth)
      │  1:1, mirrored by handle_new_user() trigger
      ▼
public.app_users.id (uuid, PK, FK on delete cascade)
      │  carried through the API boundary as
      ▼
AuthUser.id  (core/supabase_auth.py, verified ES256 against project JWKS)
```

`AuthUser.id` is that UUID as a string, already required on every authenticated route via
`current_user_required`. **This becomes the SnapTrade `userId` directly.** No new authentication
layer, no email-keyed identity, no change to the auth boundary.

One consequence worth stating: brokerage connections make identity non-optional for this
feature, while the rest of the product still works signed-out. The connector routes sit behind
`current_user_required`; everything already built stays reachable without an account.

### The credential invariant this feature changes

`0002_app_users.sql` says, in a comment:

> No credential column exists here or anywhere else in this schema.

That is true today and this feature makes it false. It is the single most important thing in
this plan, so it is handled explicitly rather than by quietly adding a column.

The Anthropic BYOK rule is enforced by CI (a grep for `sk-ant-` keys across the tree, a boot
assertion that no `ANTHROPIC_API_KEY` is present in the backend environment) and by
`tests/security/test_api_key_handling.py`. **None of that weakens.** The key stays in browser
memory, travels per request, and is never written down.

SnapTrade cannot work that way: the `userSecret` is issued once by the provider, is not
re-derivable, and is required for every subsequent call on that user's behalf. Losing it means
the user must re-link every brokerage.

So the system will hold two credential policies that must not be confused, and the schema
comment gets rewritten to say so:

| | Anthropic BYOK | SnapTrade |
|---|---|---|
| Origin | typed by the user | issued by the provider |
| Lifetime | one request | until the integration is deleted |
| Storage | none, ever | encrypted at rest |
| Reaches browser | yes, it is the user's own | **never** |
| On loss | user retypes it | user must re-link every brokerage |

### Database conventions to follow

- Migrations are sequential and prose-commented; next is `0011`.
- `updated_at` maintained by the `touch_updated_at()` trigger, `search_path` pinned to `''`.
- RLS is enabled on every table, written as `(select auth.uid())` so the planner hoists it into
  an InitPlan. **RLS is not the primary control** — FastAPI connects with the service-role key
  and bypasses it; the repository layer's `where user_id = $1` is what actually protects data.
- Money is `numeric(18,2)`; quantities are `double precision`; enum-ish columns use `check`
  constraints matching the Python enums.

New tables follow all of that, with one deliberate departure documented below.

### Analytics the connector must feed, unchanged

`analyze_portfolio` already aggregates by symbol across holdings — that was a duplicate-symbol
corruption bug fixed earlier, and it is exactly the behaviour household view needs. The same
symbol in a taxable account and a Roth already sums correctly for concentration and HHI. The
connector must not re-implement that, and must not lose the account attribution on the way in.

---

## Phase B — provider-neutral domain models

`backend/app/domain/connection.py` (new). No SnapTrade vocabulary anywhere in it.

- `ConnectionStatus` — `active | broken | disabled | pending`. Drives staleness, not value.
- `DataSource` — where a number came from: `provider_reported | provider_computed |
  user_supplied | derived`. Extends the provenance principle from persona parameters to
  portfolio facts.
- `Freshness` — `provider`, `as_of`, `last_successful_sync`, `status`. Carried on every
  connected object, never optional. A stale connection is neither a zero portfolio nor current
  data, and the type must make it impossible to render it as either.
- `TaxLot` — `quantity`, `cost_basis`, `acquired_at`. All optional. Present only when the
  provider actually returns lots.
- `ConnectedAccount`, `ConnectedPosition`, `ConnectedTransaction`, `ConnectedPortfolio` as
  specified. Every provider-optional field stays `| None`; none get defaulted to zero.

The rule that governs this whole module: **absent data is `None`, never `0.0`**. The tax work
already established that unknown and zero are different claims, and a brokerage feed produces
far more unknowns than a hand-typed form does.

## Phase C — `PortfolioConnector` protocol + `MockPortfolioConnector`

`backend/app/connectors/` (new): `base.py` (Protocol), `mock.py`, later `snaptrade.py`.

Mock first, and it is not a stub — it is the fixture CI runs against and the engine behind demo
mode, so it produces a realistic multi-account household including the awkward cases: the same
symbol in two accounts, a position with no cost basis, a position with lots, and a broken
connection returning cached data.

## Phase D — `BrokerCredentialStore`

`backend/app/core/broker_credentials.py` + migration `0011`.

Application-level AES-GCM via `cryptography` (already installed; **must be added to
`pyproject.toml`**, where it is currently an undeclared transitive dependency of `pyjwt[crypto]`).

Encrypting in the application rather than with `pgcrypto` is deliberate: the database never sees
plaintext, so a database dump, a Supabase dashboard session, or a replica does not yield the
secret. The key comes from `AIFA_BROKER_ENCRYPTION_KEY` in the backend environment, alongside the
service-role key — the same trust domain, not a new one.

The departure from RLS convention: `broker_connections` gets a policy that denies `anon` and
`authenticated` **everything**, rather than the usual "own row" grant. Every other table is
readable by its owner because a future direct-from-browser path would be legitimate. For this
table there is no such future — the browser must never read it — so the policy says that outright
instead of granting access nobody should use.

Key rotation is supported by storing a `key_version` column and decrypting against a keyring, so
rotation is a migration of rows rather than a re-link of every brokerage.

## Phase E–G — registration, read-only portal, import

- Register on first connect, store `(user_id, snaptrade_user_id, encrypted_secret)`.
- Connection Portal requested with `connectionType="read"`, asserted by test.
- **HTTP via `httpx`, not the SnapTrade SDK.** The SDK bundles trading services; calling the REST
  API directly means trading code is not merely unused but not present in the dependency tree,
  which makes `test_trading_service_is_not_wired` a real structural guarantee rather than a
  naming convention. `httpx` is already a dependency.
- `BROKERAGE_ACCESS_MODE = "READ_ONLY"` as a module constant, asserted in tests.

## Phase H–I — normalize and feed the existing engine

`ConnectedPortfolio` → existing `Portfolio`/`Holding` via an adapter, so `analyze_portfolio`,
`evaluate_guardrails`, `concentration.propose`, `sensitivity.sweep_concentration`, and
`counterfactual.evaluate` all run unmodified on connected data.

Account provenance is preserved by keeping `ConnectedPortfolio` as the source of record and
treating the flattened `Portfolio` as a *projection* for household analytics. The account view
reads the former; the household view reads the latter. Nothing aggregates destructively.

Tax handling per the existing `TaxRange`: real lots narrow the range, aggregate basis keeps the
current long-term-to-ordinary span, neither present means `None`. Better input data must not be
allowed to reintroduce point estimates.

## Phase J–O — review UI, transactions, webhooks, privacy, goals, security pass

Per the feature description. Webhooks verify HMAC, deduplicate by event id, update state only —
no LLM call is ever triggered by a webhook.

---

## Order of work

Connect → fetch → normalize → analyze, end to end, before any UI polish. Phases B and C first
because they are the contract everything else is written against, and because the mock connector
is what makes the rest testable without a brokerage login.

## What this does not touch

House/persona policy separation, parameter provenance, policy scopes, sanitized runtime
profiles, the no-impersonation rule, `ProposedAction`, `ActionSet` feasibility, `TaxRange`,
sensitivity, counterfactual validation, and Anthropic BYOK all stay exactly as they are. This
feature supplies better facts to them. The 253 existing tests must stay green throughout, and
the manual portfolio entry path must keep working for users who never connect an account.

-- 0011 — read-only brokerage connections, and the first stored secret in this schema.
--
-- 0002 says, in a comment on app_users: "No credential column exists here or anywhere else in
-- this schema." That was true until now, and this migration is the reason it stops being true.
-- The comment is corrected at the bottom of this file rather than left to rot, because a
-- security claim that has quietly become false is worse than one that was never made.
--
-- What has NOT changed: the Anthropic API key is still never stored. It lives in browser memory
-- for the session, travels on the request, and is discarded. CI greps for it and
-- tests/security/test_api_key_handling.py asserts it. The two credentials are different objects
-- and get different treatment:
--
--   Anthropic key  — the user's own, re-enterable at no cost, useful elsewhere. Never stored.
--   SnapTrade      — issued by the provider, exists nowhere else, cannot be re-derived. Losing
--   userSecret       it orphans the user's provider account and forces them to re-link every
--                    brokerage. Stored, encrypted, and never sent to a browser.
--
-- The ciphertext here is produced by the application (AES-256-GCM, see
-- backend/app/core/broker_credentials.py), not by pgcrypto. That is deliberate: pgcrypto would
-- put plaintext through the database on every read and write, which means query logs, EXPLAIN
-- output, replicas, and backups. Postgres stores bytes it cannot read, and a stolen dump is
-- worth nothing without a key that was never in the database.

-- --- provider identity ------------------------------------------------------------------
--
-- One row per (user, provider). The provider is keyed on app_users.id — the immutable internal
-- UUID — and never on email, which changes and is not unique across providers.

create table public.brokerage_provider_users (
  user_id           uuid primary key references public.app_users(id) on delete cascade,
  provider          text not null default 'snaptrade'
                      check (provider in ('snaptrade', 'mock')),

  -- What we send the provider as their user identifier. Stored rather than assumed equal to
  -- user_id so a provider that rejects or transforms our id does not require a schema change.
  provider_user_id  text not null check (length(provider_user_id) between 1 and 256),

  -- Base64 of nonce || AES-GCM(ciphertext || tag). Opaque to Postgres by design.
  secret_ciphertext text not null,
  -- Which key sealed this row. Rotation re-encrypts rows in the background rather than asking
  -- every user to reconnect, and that is only possible if each row says what opened it.
  key_version       integer not null check (key_version > 0),

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table public.brokerage_provider_users is
  'Per-user brokerage provider identity and encrypted provider secret. Ciphertext is produced '
  'by the application, never by the database. Never exposed through any API response.';

comment on column public.brokerage_provider_users.secret_ciphertext is
  'AES-256-GCM, bound to user_id as associated data — a row copied to another user fails to '
  'decrypt rather than authenticating the wrong person to their provider account.';

create trigger brokerage_provider_users_touch
  before update on public.brokerage_provider_users
  for each row execute function public.touch_updated_at();

-- --- connections ------------------------------------------------------------------------
--
-- One row per institution link. A connection covers one or more accounts; accounts themselves
-- are not stored yet — positions are fetched live and normalized in the application, so there
-- is nothing to cache until the sync pipeline lands.

create table public.brokerage_connections (
  id                     uuid primary key default gen_random_uuid(),
  user_id                uuid not null references public.app_users(id) on delete cascade,
  provider               text not null check (provider in ('snaptrade', 'mock')),
  provider_connection_id text not null check (length(provider_connection_id) between 1 and 256),

  institution            text not null default '',

  -- Mirrors app.domain.connection.ConnectionStatus. `disabled` and `broken` both still serve
  -- cached data, which is why status is stored separately from any notion of "has holdings".
  status                 text not null default 'pending'
                           check (status in ('active', 'broken', 'disabled', 'pending')),
  -- Distinct from status: a connection can be broken for reasons re-authentication cannot fix.
  needs_reconnect        boolean not null default false,

  last_successful_sync   timestamptz,

  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),

  -- Reconnecting an institution must update the existing row rather than accumulating
  -- duplicates that would each be counted as a separate portfolio.
  unique (user_id, provider, provider_connection_id)
);

create index brokerage_connections_user_idx on public.brokerage_connections (user_id);

create trigger brokerage_connections_touch
  before update on public.brokerage_connections
  for each row execute function public.touch_updated_at();

-- --- row level security ------------------------------------------------------------------
--
-- Same caveat as 0008: FastAPI connects with the service-role key and bypasses all of this. The
-- repository layer's `where user_id = $1` is the real control. These policies exist so a future
-- direct-from-browser path is safe by construction and a leaked anon key grants nothing.

alter table public.brokerage_provider_users enable row level security;
alter table public.brokerage_connections    enable row level security;

-- Deliberately NO policy on brokerage_provider_users.
--
-- Every other table in this schema grants its owner access, because a future browser-side read
-- of your own data would be legitimate. There is no such future here: the browser must never
-- see a provider secret, not even its own user's. RLS with no permissive policy denies all
-- access to anon and authenticated, which states that intent far more clearly than granting a
-- row-level read nobody is ever supposed to use. The grants below are revoked as well, so the
-- table is unreachable through PostgREST regardless of how policies are edited later.
revoke all on public.brokerage_provider_users from anon, authenticated;

-- Connections carry no secrets and are exactly what a "Connected Accounts" settings page shows,
-- so they follow the normal own-row pattern.
create policy brokerage_connections_own on public.brokerage_connections
  for all using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- --- correct the claim in 0002 ------------------------------------------------------------
--
-- Rewritten rather than deleted: the important half of the original statement is still true and
-- still load-bearing, and someone reading app_users should learn both halves.
comment on table public.app_users is
  'App-level user record, 1:1 with auth.users. Holds identity only — never credentials. '
  'The Anthropic API key is never stored anywhere in this schema. Provider-issued brokerage '
  'secrets ARE stored, encrypted by the application, in brokerage_provider_users (0011).';

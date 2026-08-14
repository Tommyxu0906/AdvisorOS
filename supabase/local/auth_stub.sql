-- LOCAL VALIDATION ONLY — never apply this to Supabase.
--
-- Supabase provides the `auth` schema, `auth.users`, `auth.uid()` and `auth.role()`. A plain
-- Postgres instance does not, so migrations that reference them cannot be applied locally
-- without this stub. It exists so `scripts/validate_migrations.sh` can exercise the real
-- migration files against a real Postgres before they ever touch the project.
--
-- The stub is deliberately minimal: enough columns for the 0002 trigger to compile and fire.

create schema if not exists auth;

-- Supabase's Postgres ships these as first-class roles; a bare instance does not. Needed for
-- any migration that GRANTs/REVOKEs against them (see 0009_security_hardening.sql).
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role;
  end if;
end
$$;

create table if not exists auth.users (
  id                  uuid primary key default gen_random_uuid(),
  email               text,
  raw_user_meta_data  jsonb not null default '{}'
);

-- In Supabase these read the request's JWT claims. Locally they return whatever the test
-- session sets, so RLS policies can be exercised by impersonating a user id.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

create or replace function auth.role()
returns text
language sql
stable
as $$
  select coalesce(nullif(current_setting('request.jwt.claim.role', true), ''), 'anon')
$$;

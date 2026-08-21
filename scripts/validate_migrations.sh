#!/usr/bin/env bash
# Apply every migration in order to a throwaway database, then assert the constraints
# actually reject bad data. Run before pushing a schema change.
#
#   ./scripts/validate_migrations.sh
#
# Requires a local Postgres. Uses supabase/local/auth_stub.sql to stand in for the `auth`
# schema that Supabase provides and a bare Postgres does not.

set -euo pipefail

DB="${1:-advisoros_migration_check}"
# Homebrew's Postgres has no role matching the local username; CI and Supabase both use
# `postgres`. Any standard libpq variable (PGUSER, PGHOST, PGPORT) still overrides.
export PGUSER="${PGUSER:-postgres}"
PSQL=(psql --quiet --no-psqlrc -v ON_ERROR_STOP=1)

cd "$(dirname "$0")/.."

echo "==> recreating $DB"
dropdb --if-exists "$DB"
createdb "$DB"

echo "==> auth stub (local only)"
"${PSQL[@]}" -d "$DB" -f supabase/local/auth_stub.sql >/dev/null

echo "==> migrations"
for f in supabase/migrations/*.sql; do
  printf '    %s\n' "$(basename "$f")"
  "${PSQL[@]}" -d "$DB" -f "$f" >/dev/null
done

echo "==> constraint checks"
# Each case must FAIL. If psql succeeds, the constraint is missing and so is our safety net.
assert_rejects() {
  local label="$1" sql="$2"
  if "${PSQL[@]}" -d "$DB" -c "$sql" >/dev/null 2>&1; then
    echo "    FAIL: $label was accepted but should have been rejected" >&2
    exit 1
  fi
  printf '    ok: rejects %s\n' "$label"
}

# Seed one user + profile to hang the child-row cases off.
"${PSQL[@]}" -d "$DB" >/dev/null <<'SQL'
insert into auth.users (id, email) values ('11111111-1111-1111-1111-111111111111', 'a@example.com');
insert into public.financial_profiles
  (id, user_id, age, horizon_years, investable_cash)
values
  ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111',
   34, 10, 4200);
SQL

assert_rejects "age below 16" \
  "insert into public.financial_profiles (user_id, age, horizon_years, investable_cash)
   values ('11111111-1111-1111-1111-111111111111', 4, 10, 100)"

assert_rejects "horizon beyond 60 years" \
  "insert into public.financial_profiles (user_id, age, horizon_years, investable_cash)
   values ('11111111-1111-1111-1111-111111111111', 34, 99, 100)"

assert_rejects "negative deployable cash" \
  "insert into public.financial_profiles (user_id, age, horizon_years, investable_cash)
   values ('11111111-1111-1111-1111-111111111111', 34, 10, -5)"

assert_rejects "unknown risk tolerance" \
  "insert into public.financial_profiles
     (user_id, age, horizon_years, investable_cash, risk_tolerance)
   values ('11111111-1111-1111-1111-111111111111', 34, 10, 1000, 'yolo')"

assert_rejects "second default profile for one user" \
  "insert into public.financial_profiles
     (user_id, age, income_annual_gross, expenses_monthly_essential, is_default)
   select '11111111-1111-1111-1111-111111111111', 40, 1, 1, true
   from generate_series(1, 2)"

assert_rejects "advisor with no declared blind spots" \
  "insert into public.custom_advisors
     (owner_id, advisor_id, display_name, subject, one_line, mental_models,
      honest_boundaries, exp_debt_pressure)
   values ('11111111-1111-1111-1111-111111111111', 'graham', 'Graham', 'Benjamin Graham',
           'x', array['margin of safety'], array['no'], 0.5)"

assert_rejects "advisor with an all-zero expertise vector" \
  "insert into public.custom_advisors
     (owner_id, advisor_id, display_name, subject, one_line, mental_models,
      blind_spots, honest_boundaries)
   values ('11111111-1111-1111-1111-111111111111', 'graham2', 'Graham', 'Benjamin Graham',
           'x', array['margin of safety'], array['macro'], array['no'])"

assert_rejects "advisor id with uppercase" \
  "insert into public.custom_advisors
     (owner_id, advisor_id, display_name, subject, one_line, mental_models,
      blind_spots, honest_boundaries, exp_debt_pressure)
   values ('11111111-1111-1111-1111-111111111111', 'Graham', 'Graham', 'Benjamin Graham',
           'x', array['m'], array['macro'], array['no'], 0.5)"

assert_rejects "non-positive adj_close" \
  "insert into public.instruments (symbol) values ('TEST');
   insert into public.daily_bars (symbol, trade_date, close, adj_close, source)
   values ('TEST', current_date, 10, 0, 'test')"

assert_rejects "unknown run depth" \
  "insert into public.committee_runs
     (run_id, user_id, question, depth, model, profile_snapshot, analytics,
      selection, pricing_version)
   values ('run_x', '11111111-1111-1111-1111-111111111111', 'q', 'exhaustive', 'm',
           '{}', '{}', '{}', 'v1')"

echo "==> two users may hold the same advisor slug"
"${PSQL[@]}" -d "$DB" >/dev/null <<'SQL'
insert into auth.users (id, email) values ('33333333-3333-3333-3333-333333333333', 'b@example.com');
insert into public.custom_advisors
  (owner_id, advisor_id, display_name, subject, one_line, mental_models,
   blind_spots, honest_boundaries, exp_valuation_sensitivity)
values
  ('11111111-1111-1111-1111-111111111111', 'benjamin_graham', 'Benjamin Graham',
   'Benjamin Graham', 'Margin of safety.', array['margin of safety'],
   array['ignores growth'], array['no market timing'], 0.9),
  ('33333333-3333-3333-3333-333333333333', 'benjamin_graham', 'Benjamin Graham',
   'Benjamin Graham', 'Margin of safety.', array['margin of safety'],
   array['ignores growth'], array['no market timing'], 0.9);
SQL
echo "    ok: (owner_id, advisor_id) is unique per user, not globally"

echo "==> auth.users insert propagates to app_users"
"${PSQL[@]}" -d "$DB" -c \
  "select 1/count(*) from public.app_users
   where id = '11111111-1111-1111-1111-111111111111'" >/dev/null
echo "    ok: handle_new_user trigger fired"

echo "==> updated_at trigger"
"${PSQL[@]}" -d "$DB" >/dev/null <<'SQL'
do $$
declare before timestamptz; after timestamptz;
begin
  select updated_at into before from public.financial_profiles
    where id = '22222222-2222-2222-2222-222222222222';
  perform pg_sleep(0.01);
  update public.financial_profiles set notes = 'touched'
    where id = '22222222-2222-2222-2222-222222222222';
  select updated_at into after from public.financial_profiles
    where id = '22222222-2222-2222-2222-222222222222';
  if after <= before then
    raise exception 'updated_at did not advance';
  end if;
end $$;
SQL
echo "    ok: updated_at advances on update"

echo "==> every table has RLS enabled"
"${PSQL[@]}" -d "$DB" -t -c \
  "select count(*) from pg_tables t
   join pg_class c on c.relname = t.tablename
   where t.schemaname = 'public' and not c.relrowsecurity" \
  | grep -qE '^\s*0\s*$' \
  || { echo "    FAIL: some public table has RLS disabled" >&2; exit 1; }
echo "    ok: RLS on for all public tables"

echo
echo "All migration checks passed."
dropdb "$DB"

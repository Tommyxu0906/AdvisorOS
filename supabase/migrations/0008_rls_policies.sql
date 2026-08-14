-- 0008 — row level security.
--
-- IMPORTANT, and stated plainly: FastAPI connects with the service-role key, which bypasses RLS
-- entirely. These policies are NOT what protects user data today. The repository layer's
-- `where user_id = $1` is. Authorization must be enforced in Python regardless of what is
-- written here.
--
-- The policies exist so that (a) a future direct-from-browser query path is safe by
-- construction rather than by remembering to add a filter, and (b) a leaked anon key grants
-- nothing. Defense in depth and a statement of intent — not the primary control.
--
-- Note the `(select auth.uid())` form rather than a bare `auth.uid()`. Wrapped in a subselect
-- the planner hoists it into an InitPlan and evaluates it once per query; called bare it is
-- re-evaluated per row, which on a few thousand rows is the difference between a sub-millisecond
-- filter and a visibly slow one.

alter table public.app_users            enable row level security;
alter table public.financial_profiles   enable row level security;
alter table public.profile_debts        enable row level security;
alter table public.profile_assets       enable row level security;
alter table public.profile_goals        enable row level security;
alter table public.portfolios           enable row level security;
alter table public.portfolio_holdings   enable row level security;
alter table public.custom_advisors      enable row level security;
alter table public.committee_runs       enable row level security;
alter table public.run_cost_lines       enable row level security;
alter table public.run_llm_calls        enable row level security;
alter table public.instruments          enable row level security;
alter table public.daily_bars           enable row level security;
alter table public.latest_quotes        enable row level security;
alter table public.market_data_fetches  enable row level security;

-- --- owned directly -------------------------------------------------------------------

create policy app_users_own on public.app_users
  for all using (id = (select auth.uid())) with check (id = (select auth.uid()));

create policy financial_profiles_own on public.financial_profiles
  for all using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy portfolios_own on public.portfolios
  for all using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy custom_advisors_own on public.custom_advisors
  for all using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));

create policy committee_runs_own on public.committee_runs
  for all using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- --- owned through a parent -----------------------------------------------------------

create policy profile_debts_own on public.profile_debts
  for all using (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ));

create policy profile_assets_own on public.profile_assets
  for all using (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ));

create policy profile_goals_own on public.profile_goals
  for all using (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.financial_profiles p
    where p.id = profile_id and p.user_id = (select auth.uid())
  ));

create policy portfolio_holdings_own on public.portfolio_holdings
  for all using (exists (
    select 1 from public.portfolios pf
    where pf.id = portfolio_id and pf.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.portfolios pf
    where pf.id = portfolio_id and pf.user_id = (select auth.uid())
  ));

create policy run_cost_lines_own on public.run_cost_lines
  for all using (exists (
    select 1 from public.committee_runs r
    where r.run_id = run_cost_lines.run_id and r.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.committee_runs r
    where r.run_id = run_cost_lines.run_id and r.user_id = (select auth.uid())
  ));

create policy run_llm_calls_own on public.run_llm_calls
  for all using (exists (
    select 1 from public.committee_runs r
    where r.run_id = run_llm_calls.run_id and r.user_id = (select auth.uid())
  ))
  with check (exists (
    select 1 from public.committee_runs r
    where r.run_id = run_llm_calls.run_id and r.user_id = (select auth.uid())
  ));

-- --- shared reference data ------------------------------------------------------------
--
-- Market data belongs to no user. Signed-in clients may read it; only the service role writes,
-- and since the service role bypasses RLS there is deliberately no insert/update policy — the
-- absence of one is what denies writes to everyone else.

create policy instruments_read on public.instruments
  for select using ((select auth.role()) = 'authenticated');

create policy daily_bars_read on public.daily_bars
  for select using ((select auth.role()) = 'authenticated');

create policy latest_quotes_read on public.latest_quotes
  for select using ((select auth.role()) = 'authenticated');

-- market_data_fetches is operational telemetry: no policy at all, so nothing but the
-- service role can see it.

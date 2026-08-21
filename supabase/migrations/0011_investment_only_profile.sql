-- The product narrowed to the investment portfolio, and the schema follows.
--
-- AdvisorOS advises on an investment book. It no longer asks about income, household expenses,
-- mortgages, or credit cards, so it must no longer store them: keeping columns a product has
-- stopped collecting means carrying a duty of care over data nobody is using, and the honest
-- thing is to drop them rather than let them sit stale.
--
-- What replaces them is the pair that actually binds an investment decision — when the money is
-- needed, and what cash is available to deploy.

alter table public.financial_profiles
  add column if not exists horizon_years    double precision not null default 10
    check (horizon_years between 0 and 60),
  add column if not exists investable_cash  numeric(18,2) not null default 0
    check (investable_cash >= 0);

comment on column public.financial_profiles.horizon_years is
  'When this money is needed. Drives the one blocking guardrail the house enforces.';
comment on column public.financial_profiles.investable_cash is
  'Account cash available to deploy. Not a household cash position.';

-- Existing rows: carry over what can be carried, then drop the rest.
-- Liquid assets are the closest thing the old shape had to deployable cash.
update public.financial_profiles p
   set investable_cash = coalesce((
         select sum(a.value) from public.profile_assets a
          where a.profile_id = p.id and a.is_liquid
       ), 0);

-- Nearest dated goal is the closest thing the old shape had to a horizon.
update public.financial_profiles p
   set horizon_years = coalesce((
         select min(g.years_until_needed) from public.profile_goals g
          where g.profile_id = p.id and g.years_until_needed is not null
       ), 10);

alter table public.financial_profiles
  drop column if exists dependents,
  drop column if exists income_annual_gross,
  drop column if exists income_annual_net,
  drop column if exists income_stability,
  drop column if exists employer_match_pct,
  drop column if exists expenses_monthly_essential,
  drop column if exists expenses_monthly_discretionary;

drop table if exists public.profile_debts;
drop table if exists public.profile_goals;
drop table if exists public.profile_assets;

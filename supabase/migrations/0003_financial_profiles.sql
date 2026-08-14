-- 0003 — financial profiles and their editable child rows.
--
-- Mirrors backend/app/domain/profile.py. Income and Expenses are strictly 1:1 with the profile
-- (four and two fields), so they are flattened into columns rather than given their own tables.
--
-- Types: money is numeric(18,2); rates, shares and 0..1 scores are double precision, because
-- they are fractions rather than currency and are compared, not summed.
--
-- Nothing derived is stored here. net_worth, life_stage, the need vector and every ratio are
-- computed by analytics/profile_analytics.py on read. Storing them would create a second source
-- of truth that silently drifts the moment the formulas change.

create table public.financial_profiles (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.app_users(id) on delete cascade,
  name        text not null default 'Main',
  is_default  boolean not null default false,

  age         integer not null check (age between 16 and 120),
  dependents  integer not null default 0 check (dependents >= 0),
  currency    char(3) not null default 'USD',

  income_annual_gross  numeric(18,2) not null check (income_annual_gross >= 0),
  income_annual_net    numeric(18,2) check (income_annual_net >= 0),
  income_stability     double precision not null default 0.8
                         check (income_stability between 0 and 1),
  employer_match_pct   double precision not null default 0
                         check (employer_match_pct between 0 and 1),

  expenses_monthly_essential      numeric(18,2) not null
                                    check (expenses_monthly_essential >= 0),
  expenses_monthly_discretionary  numeric(18,2) not null default 0
                                    check (expenses_monthly_discretionary >= 0),

  risk_tolerance  text not null default 'moderate'
                    check (risk_tolerance in ('conservative', 'moderate_conservative',
                                              'moderate', 'moderate_aggressive', 'aggressive')),
  self_reported_experience  double precision not null default 0.5
                              check (self_reported_experience between 0 and 1),
  notes  text not null default '' check (length(notes) <= 4000),

  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),

  -- Mirrors the model validator on Income: net pay cannot exceed gross.
  constraint net_not_above_gross
    check (income_annual_net is null or income_annual_net <= income_annual_gross)
);

comment on table public.financial_profiles is
  'A user''s financial situation. Multiple named profiles per user support scenario comparison.';

create index financial_profiles_user_idx on public.financial_profiles (user_id);

-- At most one default per user, enforced here rather than in application code so a concurrent
-- double-write cannot produce two defaults.
create unique index financial_profiles_one_default
  on public.financial_profiles (user_id)
  where is_default;

create trigger financial_profiles_touch
  before update on public.financial_profiles
  for each row execute function public.touch_updated_at();

-- --- child rows -----------------------------------------------------------------------
--
-- `position` preserves the order the user arranged rows in; the UI renders these as ordered
-- ledger lines and re-sorting them on every read would be visible churn.

create table public.profile_debts (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid not null references public.financial_profiles(id) on delete cascade,
  position    integer not null default 0,
  name        text not null,
  balance     numeric(18,2) not null check (balance >= 0),
  -- Stored as a fraction (0.229 = 22.9%), matching the domain model.
  apr         double precision not null check (apr between 0 and 1),
  minimum_monthly_payment  numeric(18,2) not null default 0
                             check (minimum_monthly_payment >= 0),
  is_secured  boolean not null default false
);

create index profile_debts_profile_idx on public.profile_debts (profile_id, position);

create table public.profile_assets (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid not null references public.financial_profiles(id) on delete cascade,
  position    integer not null default 0,
  name        text not null,
  value       numeric(18,2) not null check (value >= 0),
  account_type  text not null default 'taxable'
                  check (account_type in ('taxable', 'traditional_401k', 'roth_401k',
                                          'traditional_ira', 'roth_ira', 'hsa', 'cash', 'other')),
  is_liquid   boolean not null default true
);

create index profile_assets_profile_idx on public.profile_assets (profile_id, position);

create table public.profile_goals (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid not null references public.financial_profiles(id) on delete cascade,
  position    integer not null default 0,
  name        text not null,
  goal_type   text not null default 'other'
                check (goal_type in ('retirement', 'home_purchase', 'education',
                                     'emergency_fund', 'wealth_growth', 'income',
                                     'debt_payoff', 'other')),
  target_amount       numeric(18,2) check (target_amount >= 0),
  years_until_needed  double precision not null check (years_until_needed >= 0),
  -- 1 = highest priority.
  priority            integer not null default 3 check (priority between 1 and 5)
);

create index profile_goals_profile_idx on public.profile_goals (profile_id, position);

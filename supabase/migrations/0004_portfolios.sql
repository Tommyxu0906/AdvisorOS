-- 0004 — portfolios and holdings.
--
-- Mirrors backend/app/domain/portfolio.py, with one addition: `quantity`.
--
-- `quantity` does not exist on the Holding domain model today and is nullable here on purpose.
-- market_value stays the authoritative stored figure, so a holding with no market data — a
-- private business, a savings account, a symbol the provider does not cover — keeps working
-- exactly as it does now. When quantity IS present, the price refresher can recompute
-- market_value from the latest close and stamp price_synced_at.

create table public.portfolios (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.app_users(id) on delete cascade,
  -- A portfolio may be attached to a scenario, or stand alone. Deleting the profile keeps
  -- the portfolio rather than destroying holdings the user still cares about.
  profile_id  uuid references public.financial_profiles(id) on delete set null,
  name        text not null default 'Main',
  currency    char(3) not null default 'USD',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index portfolios_user_idx on public.portfolios (user_id);

create trigger portfolios_touch
  before update on public.portfolios
  for each row execute function public.touch_updated_at();

create table public.portfolio_holdings (
  id            uuid primary key default gen_random_uuid(),
  portfolio_id  uuid not null references public.portfolios(id) on delete cascade,
  position      integer not null default 0,
  symbol        text not null check (length(symbol) between 1 and 32),
  name          text not null default '',
  asset_class   text not null default 'other'
                  check (asset_class in ('us_equity', 'intl_developed_equity',
                                         'emerging_equity', 'bonds', 'tips', 'reit',
                                         'commodities', 'crypto', 'cash', 'other')),

  quantity      double precision check (quantity >= 0),
  market_value  numeric(18,2) not null check (market_value >= 0),
  cost_basis    numeric(18,2) check (cost_basis >= 0),

  account_type  text not null default 'taxable'
                  check (account_type in ('taxable', 'traditional_401k', 'roth_401k',
                                          'traditional_ira', 'roth_ira', 'hsa', 'cash', 'other')),
  expense_ratio double precision check (expense_ratio between 0 and 0.1),

  -- Null means market_value is whatever the user typed and has never been price-refreshed.
  price_synced_at timestamptz
);

create index portfolio_holdings_portfolio_idx
  on public.portfolio_holdings (portfolio_id, position);

-- Drives the price fetcher: "every distinct symbol anyone actually holds" is the work queue.
create index portfolio_holdings_symbol_idx on public.portfolio_holdings (symbol);

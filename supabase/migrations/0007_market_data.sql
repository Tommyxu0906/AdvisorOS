-- 0007 — market data cache.
--
-- Shared reference data, not user data: one row per symbol however many users hold it. Operator
-- pays for the feed, so the cache is what keeps a rate-limited free tier viable — the fetcher
-- pulls the union of held symbols once per day, not once per page view.
--
-- Daily bars, not a tick stream. The analytics that consume this (annualized volatility, max
-- drawdown, correlation in analytics/portfolio_analytics.py) are computed from a periodic
-- return series; second-level quotes would add cost and latency without changing any number
-- the product actually reports.

create table public.instruments (
  symbol       text primary key,
  name         text not null default '',
  asset_class  text,
  exchange     text,
  currency     char(3) not null default 'USD',
  -- False for delisted or provider-unsupported symbols, so the fetcher stops retrying them.
  is_active    boolean not null default true,
  last_bar_date      date,
  last_refreshed_at  timestamptz,
  created_at   timestamptz not null default now()
);

create table public.daily_bars (
  symbol      text not null references public.instruments(symbol) on delete cascade,
  trade_date  date not null,
  open   numeric(18,6),
  high   numeric(18,6),
  low    numeric(18,6),
  close  numeric(18,6) not null,
  -- Returns are computed from adj_close, never close: a split or dividend would otherwise
  -- register as a real one-day move and corrupt volatility and drawdown.
  adj_close  numeric(18,6) not null check (adj_close > 0),
  volume     bigint,
  source     text not null,

  primary key (symbol, trade_date)
);

comment on column public.daily_bars.adj_close is
  'Split/dividend adjusted. PriceSeries.returns is derived from this column only.';

-- Descending: every read is "the most recent N bars for this symbol".
create index daily_bars_symbol_date_idx on public.daily_bars (symbol, trade_date desc);

-- Latest known price, for display. Separate from bars because it updates on a different
-- cadence and carries provider metadata that bars do not.
create table public.latest_quotes (
  symbol          text primary key references public.instruments(symbol) on delete cascade,
  price           numeric(18,6) not null,
  previous_close  numeric(18,6),
  change_pct      double precision,
  -- When the price was true, as opposed to when we fetched it.
  as_of           timestamptz not null,
  -- Free tiers are typically 15-minute delayed. Surfaced in the UI rather than implied.
  is_delayed      boolean not null default true,
  source          text not null,
  fetched_at      timestamptz not null default now()
);

-- Every fetch attempt, including failures. Without this a provider outage or a rate-limit wall
-- looks identical to "the market did not move" — stale analytics with no visible cause.
create table public.market_data_fetches (
  id      bigserial primary key,
  symbol  text,
  kind    text not null check (kind in ('bars', 'quote')),
  status  text not null check (status in ('ok', 'rate_limited', 'error', 'not_found')),
  detail  text,
  fetched_at timestamptz not null default now()
);

create index market_data_fetches_recent_idx on public.market_data_fetches (fetched_at desc);
create index market_data_fetches_symbol_idx on public.market_data_fetches (symbol, fetched_at desc);

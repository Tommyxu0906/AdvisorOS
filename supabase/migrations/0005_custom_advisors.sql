-- 0005 — user-distilled advisor personas.
--
-- Only *custom* advisors live here. The six built-ins (bogle, buffett, damodaran, housel,
-- marks, munger) stay on disk as version-controlled manifest.json + SKILL.md artifacts, which
-- is what lets GET /api/advisors answer with no database and no credentials — a property the
-- security suite asserts and this schema must not take away.
--
-- This table replaces the current shared custom/ directory, which is keyed only on advisor_id
-- and therefore lets two users distilling the same subject overwrite each other.

create table public.custom_advisors (
  id          uuid primary key default gen_random_uuid(),
  owner_id    uuid not null references public.app_users(id) on delete cascade,
  -- Slug form of the subject, e.g. 'benjamin_graham'. Matches the domain model's pattern.
  advisor_id  text not null check (advisor_id ~ '^[a-z0-9_]+$'),

  display_name  text not null,
  subject       text not null,
  one_line      text not null,

  -- The 7-dimension expertise vector is stored as columns rather than JSONB because the
  -- deterministic selector scores against these numbers on every committee run, and they are
  -- the natural thing to filter and sort on.
  exp_liquidity_risk         double precision not null default 0
                               check (exp_liquidity_risk between 0 and 1),
  exp_debt_pressure          double precision not null default 0
                               check (exp_debt_pressure between 0 and 1),
  exp_concentration_risk     double precision not null default 0
                               check (exp_concentration_risk between 0 and 1),
  exp_valuation_sensitivity  double precision not null default 0
                               check (exp_valuation_sensitivity between 0 and 1),
  exp_behavioral_risk        double precision not null default 0
                               check (exp_behavioral_risk between 0 and 1),
  exp_tax_complexity         double precision not null default 0
                               check (exp_tax_complexity between 0 and 1),
  exp_longevity_risk         double precision not null default 0
                               check (exp_longevity_risk between 0 and 1),

  topic_affinity     text[] not null default '{}',
  mental_models      text[] not null default '{}',
  heuristics         text[] not null default '{}',
  reasoning_rules    text[] not null default '{}',
  blind_spots        text[] not null default '{}',
  honest_boundaries  text[] not null default '{}',
  -- Holds other advisor_ids. Left as an array rather than a join table: it is advisory
  -- metadata used for prompt framing, not something joined on.
  disagrees_with     text[] not null default '{}',
  -- [{label, source, year, note}, ...]
  evidence           jsonb not null default '[]',

  provenance      text not null default '',
  distilled_at    timestamptz,
  schema_version  integer not null default 1,
  distill_depth   text check (distill_depth in ('quick', 'standard', 'deep')),
  distill_run_id  text,

  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),

  -- Re-distilling the same subject upserts the user's own row. Two users distilling
  -- "Benjamin Graham" get two independent rows instead of clobbering one another.
  constraint custom_advisors_owner_slug unique (owner_id, advisor_id),

  -- The distiller already refuses to emit a persona that fails these. Restating them here
  -- means a bad row cannot enter through a migration, a backfill, or the SQL editor either.
  constraint blind_spots_declared
    check (cardinality(blind_spots) > 0),
  constraint boundaries_declared
    check (cardinality(honest_boundaries) > 0),
  constraint has_reasoning_content
    check (cardinality(mental_models) > 0 or cardinality(heuristics) > 0),
  -- An all-zero vector could never be selected by the router, so it is a failed distillation.
  constraint expertise_not_all_zero
    check (greatest(exp_liquidity_risk, exp_debt_pressure, exp_concentration_risk,
                    exp_valuation_sensitivity, exp_behavioral_risk, exp_tax_complexity,
                    exp_longevity_risk) > 0)
);

comment on table public.custom_advisors is
  'Per-user distilled personas. Built-in advisors are not stored here — they live on disk.';

comment on constraint blind_spots_declared on public.custom_advisors is
  'A persona claiming no limits has no business in front of someone''s finances.';

create index custom_advisors_owner_idx on public.custom_advisors (owner_id);

create trigger custom_advisors_touch
  before update on public.custom_advisors
  for each row execute function public.touch_updated_at();

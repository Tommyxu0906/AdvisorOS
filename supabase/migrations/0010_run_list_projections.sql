-- 0010 — cheap list-view columns for committee_runs.
--
-- list_runs() (app/db/repositories/runs.py) backs a history page: many rows, small fields, no
-- reason to parse the full JSONB snapshot just to render a row. These columns are a projection
-- of data that already exists in `selection` / `guardrails` / `report` — written once at
-- insert time, read many times by the list view.

alter table public.committee_runs
  add column question_topics text[] not null default '{}',
  add column summary text not null default '',
  add column advisor_ids text[] not null default '{}',
  add column guardrail_codes text[] not null default '{}',
  add column guardrail_max_severity text
    check (guardrail_max_severity is null
           or guardrail_max_severity in ('info', 'caution', 'blocking')),
  add column guardrail_violation_count integer not null default 0
    check (guardrail_violation_count >= 0);

create index committee_runs_advisor_ids_idx on public.committee_runs using gin (advisor_ids);

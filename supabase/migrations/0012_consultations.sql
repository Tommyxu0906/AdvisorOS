-- Consultations: the multi-turn committee conversations, saved to the user's history.
--
-- Separate from `committee_runs` rather than squeezed into it. A run is one question producing
-- one report; a consultation is a conversation that grows, keeps its own committee, and has a
-- conclusion that changes as it goes. Forcing them into one table would mean a `question` column
-- that holds the first of several and a `report` column that means something different per row.
--
-- The transcript is stored whole, as sent. It is a snapshot for the same reason run snapshots
-- are: the profile and portfolio it was reasoning about may be edited afterwards, and a history
-- that silently re-renders against today's numbers would be a record of nothing.

create table public.consultations (
  id            uuid primary key default gen_random_uuid(),
  -- Generated in the browser when the conversation starts, so an upsert after every answer
  -- lands on the same row without a round trip to learn its id first.
  client_id     text not null,
  user_id       uuid not null references public.app_users(id) on delete cascade,

  title         text not null default 'New consultation',
  -- Which frameworks were in the room. Stored per consultation because the selection is
  -- per conversation, and a report that could not say who answered would be worth little.
  advisor_ids   text[] not null default '{}',
  model         text not null,
  depth         text not null check (depth in ('quick', 'balanced', 'deep')),

  -- The whole conversation, exactly as it was shown.
  turns         jsonb not null default '[]',
  -- The standing conclusion after the most recent round. Null until one round has completed.
  synthesis     jsonb,
  -- What the engine computed at the last round, so the conclusion can be read against the
  -- options it was choosing between.
  candidates    jsonb not null default '[]',

  question_count integer not null default 0 check (question_count >= 0),

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  unique (user_id, client_id)
);

create index consultations_user_updated_idx
  on public.consultations (user_id, updated_at desc);

create trigger consultations_touch_updated_at
  before update on public.consultations
  for each row execute function public.touch_updated_at();

alter table public.consultations enable row level security;

-- Same posture and the same wording as every other user-owned table: the owner, and nobody else.
create policy consultations_own on public.consultations
  for all using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

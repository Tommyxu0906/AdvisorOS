-- 0001 — extensions and shared helpers.
--
-- Nothing here is app-specific. It exists so later migrations can assume gen_random_uuid()
-- and a single updated_at implementation.

create extension if not exists "pgcrypto";

-- updated_at is maintained by a trigger rather than by application code, so a hand-written
-- fix applied in the Supabase SQL editor cannot silently leave a stale timestamp behind.
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

comment on function public.touch_updated_at() is
  'BEFORE UPDATE trigger: stamps updated_at = now(). Attached to every mutable table.';

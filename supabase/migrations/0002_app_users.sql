-- 0002 — application-level user record.
--
-- Named app_users, not `profiles`: Supabase convention would call this `profiles`, but this
-- application already uses "profile" to mean a *financial* profile. One row per auth.users row.
--
-- No credential column exists here or anywhere else in this schema. The Anthropic API key is
-- held in browser memory for the session and re-entered after a refresh; the server never
-- stores it. See tests/security/test_api_key_handling.py.

create table public.app_users (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text,
  display_name  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

comment on table public.app_users is
  'App-level user record, 1:1 with auth.users. Holds identity only — never credentials.';

create trigger app_users_touch
  before update on public.app_users
  for each row execute function public.touch_updated_at();

-- Mirror new auth.users rows into app_users so the application never has to special-case
-- "signed in but has no app row yet".
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.app_users (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(
      new.raw_user_meta_data ->> 'full_name',
      new.raw_user_meta_data ->> 'name'
    )
  )
  on conflict (id) do nothing;
  return new;
end
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

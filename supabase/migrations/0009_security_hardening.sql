-- 0009 — close two gaps the Supabase security advisor found in the 0001-0008 migrations.
--
-- Both are real: neither is "the linter being pedantic."

-- 1. touch_updated_at() had no search_path pinned. A function with a mutable search_path can
--    be tricked into resolving an unqualified identifier against a schema an attacker
--    controls, if they can create objects earlier in the caller's search_path. The function
--    body here has no unqualified references, but pinning the path costs nothing and closes
--    the class of bug outright rather than relying on "there's nothing to exploit today."
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

-- 2. handle_new_user() is SECURITY DEFINER, which is required — it inserts into app_users as
--    the function owner regardless of who triggers auth.users, and the inserting principal
--    during signup has no rows in app_users yet to authorize against. But every function in
--    the public schema is exposed by PostgREST as POST /rest/v1/rpc/<name> unless revoked, so
--    without this, anon and authenticated could call it directly — bypassing the trigger,
--    passing an arbitrary NEW record shape is not possible via RPC (it takes no arguments and
--    reads only the trigger's NEW), but there is no reason to leave a trigger-only function
--    reachable as a public endpoint at all.
revoke execute on function public.handle_new_user() from public, anon, authenticated;
-- The trigger still fires: PostgreSQL invokes trigger functions as the function owner,
-- independent of REST-API-level EXECUTE grants.

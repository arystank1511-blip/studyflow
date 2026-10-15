-- StudyFlow: let a signed-in student delete their own account. Run once in the Supabase SQL Editor.
-- Deleting the auth user removes their tasks, study sessions and calendar link through
-- "on delete cascade". The function can only ever delete the caller (auth.uid()).
begin;
create or replace function public.studyflow_delete_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '28000';
  end if;
  delete from auth.users where id = auth.uid();
end
$$;
revoke all on function public.studyflow_delete_account() from public, anon;
grant execute on function public.studyflow_delete_account() to authenticated;
commit;

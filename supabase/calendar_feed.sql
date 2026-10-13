-- StudyFlow calendar subscription. Run once in the Supabase SQL Editor after schema.sql.
-- Each student may have one secret feed link. Only its SHA-256 hash is stored; the link itself
-- is shown to the student once. Calendar apps fetch the feed without signing in, so a narrow
-- SECURITY DEFINER function returns unfinished tasks for a matching hash and nothing else.
begin;
create table public.studyflow_calendar_feeds (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now()
);
alter table public.studyflow_calendar_feeds enable row level security;
alter table public.studyflow_calendar_feeds force row level security;
revoke all on public.studyflow_calendar_feeds from anon, authenticated;
grant select, delete on public.studyflow_calendar_feeds to authenticated;
grant insert (user_id, token_hash) on public.studyflow_calendar_feeds to authenticated;
create policy "Read own calendar feed" on public.studyflow_calendar_feeds for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Create own calendar feed" on public.studyflow_calendar_feeds for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Delete own calendar feed" on public.studyflow_calendar_feeds for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Returns null for an unknown link, otherwise a JSON array (possibly empty) of unfinished tasks.
create function public.studyflow_calendar_feed(feed_hash text)
returns json
language sql
stable
security definer
set search_path = ''
as $$
  select case
    when not exists (select 1 from public.studyflow_calendar_feeds f where f.token_hash = feed_hash) then null
    else coalesce((
      select json_agg(json_build_object('id', t.id, 'title', t.title, 'course', t.course, 'due_date', t.due_date,
                                        'priority', t.priority, 'notes', t.notes) order by t.due_date, t.id)
      from public.studyflow_tasks t
      join public.studyflow_calendar_feeds f on f.user_id = t.user_id
      where f.token_hash = feed_hash and t.status <> 'Done' and t.due_date >= current_date - 30
    ), '[]'::json)
  end
$$;
revoke all on function public.studyflow_calendar_feed(text) from public, anon, authenticated;
grant execute on function public.studyflow_calendar_feed(text) to anon, authenticated;
commit;

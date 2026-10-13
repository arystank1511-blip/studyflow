-- StudyFlow study timer (YPT-style). Run once in the Supabase SQL Editor after schema.sql.
-- Each row is one finished stopwatch session. Rows are private to their owner (RLS).
begin;
create table public.studyflow_study_sessions (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  course text not null check (char_length(trim(course)) between 1 and 80),
  minutes integer not null check (minutes between 1 and 720),
  day date not null,
  started_at timestamptz,
  created_at timestamptz not null default now()
);
create index studyflow_study_sessions_owner_day on public.studyflow_study_sessions(user_id, day);
alter table public.studyflow_study_sessions enable row level security;
alter table public.studyflow_study_sessions force row level security;
revoke all on public.studyflow_study_sessions from anon, authenticated;
grant select, delete on public.studyflow_study_sessions to authenticated;
grant insert (user_id, course, minutes, day, started_at) on public.studyflow_study_sessions to authenticated;
grant usage on sequence public.studyflow_study_sessions_id_seq to authenticated;
create policy "Read own study sessions" on public.studyflow_study_sessions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Log own study sessions" on public.studyflow_study_sessions for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Delete own study sessions" on public.studyflow_study_sessions for delete to authenticated
  using ((select auth.uid()) = user_id);
commit;

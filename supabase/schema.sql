-- Run once in a NEW Supabase project's SQL Editor before deploying.
-- No personal/local tasks are imported. Existing tables are never dropped.
begin;
create table public.studyflow_tasks (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  title text not null check (char_length(trim(title)) between 1 and 100),
  course text not null check (char_length(trim(course)) between 1 and 80),
  due_date date not null,
  priority text not null default 'Medium' check (priority in ('Low', 'Medium', 'High')),
  status text not null default 'To do' check (status in ('To do', 'In progress', 'Done')),
  notes text not null default '' check (char_length(notes) <= 300),
  created_at timestamptz not null default now()
);
create index studyflow_tasks_owner_due on public.studyflow_tasks(user_id, due_date);
alter table public.studyflow_tasks enable row level security;
alter table public.studyflow_tasks force row level security;
revoke all on public.studyflow_tasks from anon, authenticated;
grant select, delete on public.studyflow_tasks to authenticated;
grant insert (user_id, title, course, due_date, priority, status, notes) on public.studyflow_tasks to authenticated;
grant update (title, course, due_date, priority, status, notes) on public.studyflow_tasks to authenticated;
grant usage on sequence public.studyflow_tasks_id_seq to authenticated;
create policy "Read own tasks" on public.studyflow_tasks for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Create own tasks" on public.studyflow_tasks for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Edit own tasks" on public.studyflow_tasks for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Delete own tasks" on public.studyflow_tasks for delete to authenticated
  using ((select auth.uid()) = user_id);
commit;

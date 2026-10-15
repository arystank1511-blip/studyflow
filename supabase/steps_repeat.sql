-- StudyFlow: checklists inside tasks and repeating tasks. Run once in the Supabase SQL Editor
-- BEFORE deploying the code that sends them. Safe to run twice.
begin;
alter table public.studyflow_tasks add column if not exists repeat text not null default ''
  check (repeat in ('', 'daily', 'weekly', 'monthly'));
alter table public.studyflow_tasks add column if not exists steps jsonb not null default '[]'::jsonb
  check (jsonb_typeof(steps) = 'array' and jsonb_array_length(steps) <= 20 and octet_length(steps::text) <= 4000);
grant insert (repeat, steps), update (repeat, steps) on public.studyflow_tasks to authenticated;
commit;

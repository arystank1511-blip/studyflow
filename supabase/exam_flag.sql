-- StudyFlow: an explicit "exam" mark on tasks. Run once in the Supabase SQL Editor
-- BEFORE deploying the code that sends it. Safe to run twice.
begin;
alter table public.studyflow_tasks add column if not exists is_exam boolean not null default false;
grant insert (is_exam), update (is_exam) on public.studyflow_tasks to authenticated;
-- Existing tasks keep the countdown they had: mark the ones whose title or course looked like an exam.
update public.studyflow_tasks set is_exam = true
where not is_exam
  and lower(title || ' ' || course) ~ '(экзамен|емтихан|exam|коллоквиум|сессия|midterm|final|рубеж|тест|test|quiz|аралық)';
commit;

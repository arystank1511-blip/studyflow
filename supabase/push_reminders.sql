-- StudyFlow: morning reminders on the phone (Web Push). Run once in the Supabase SQL Editor.
-- Replace CRON_SECRET_SHA256 below with the SHA-256 (hex) of the CRON_SECRET set on Vercel.
-- Students manage only their own subscriptions (RLS). The daily sender has no user session: it
-- calls studyflow_push_digest with the cron secret and gets, per device, only what the
-- notification shows (counts and the nearest exam), never the task list.
begin;
create table public.studyflow_push_subscriptions (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  endpoint text not null check (char_length(endpoint) <= 1000 and endpoint ~ '^https://(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|web\.push\.apple\.com|[a-z0-9.-]+\.notify\.windows\.com)/'),
  p256dh text not null check (p256dh ~ '^[A-Za-z0-9_-]{80,100}$'),
  auth text not null check (auth ~ '^[A-Za-z0-9_-]{16,32}$'),
  language text not null default 'ru' check (language in ('ru', 'kk', 'en')),
  timezone text not null default 'Asia/Almaty' check (char_length(timezone) <= 64),
  created_at timestamptz not null default now(),
  unique (user_id, endpoint)
);
create index studyflow_push_subscriptions_user on public.studyflow_push_subscriptions(user_id);

-- An unknown timezone would break the morning query for everyone, so it falls back to Almaty.
create function public.studyflow_push_check_timezone()
returns trigger language plpgsql set search_path = '' as $$
begin
  if not exists (select 1 from pg_catalog.pg_timezone_names where name = new.timezone) then
    new.timezone := 'Asia/Almaty';
  end if;
  return new;
end
$$;
create trigger studyflow_push_timezone before insert or update on public.studyflow_push_subscriptions
  for each row execute function public.studyflow_push_check_timezone();

alter table public.studyflow_push_subscriptions enable row level security;
alter table public.studyflow_push_subscriptions force row level security;
revoke all on public.studyflow_push_subscriptions from anon, authenticated;
grant select, delete on public.studyflow_push_subscriptions to authenticated;
grant insert (user_id, endpoint, p256dh, auth, language, timezone) on public.studyflow_push_subscriptions to authenticated;
grant usage on sequence public.studyflow_push_subscriptions_id_seq to authenticated;
create policy "Read own push subscriptions" on public.studyflow_push_subscriptions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Add own push subscriptions" on public.studyflow_push_subscriptions for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Remove own push subscriptions" on public.studyflow_push_subscriptions for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Server-only settings: no grants at all, read only inside security-definer functions.
create table if not exists public.studyflow_private_settings (key text primary key, value text not null);
alter table public.studyflow_private_settings enable row level security;
alter table public.studyflow_private_settings force row level security;
revoke all on public.studyflow_private_settings from anon, authenticated;
insert into public.studyflow_private_settings (key, value) values ('cron_secret_sha256', 'CRON_SECRET_SHA256')
  on conflict (key) do update set value = excluded.value;

create function public.studyflow_cron_allowed(cron_secret text)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.studyflow_private_settings
    where key = 'cron_secret_sha256'
      and value = encode(sha256(convert_to(coalesce(cron_secret, ''), 'UTF8')), 'hex'))
$$;

create function public.studyflow_push_digest(cron_secret text)
returns json language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.studyflow_cron_allowed(cron_secret) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  return coalesce((
    select json_agg(json_build_object(
      'endpoint', r.endpoint, 'p256dh', r.p256dh, 'auth', r.auth, 'language', r.language,
      'today', r.today_count, 'overdue', r.overdue_count, 'exam_title', r.exam_title, 'exam_days', r.exam_days))
    from (
      select s.endpoint, s.p256dh, s.auth, s.language,
        (select count(*) from public.studyflow_tasks t
          where t.user_id = s.user_id and t.status <> 'Done' and t.due_date = l.today) as today_count,
        (select count(*) from public.studyflow_tasks t
          where t.user_id = s.user_id and t.status <> 'Done' and t.due_date < l.today) as overdue_count,
        e.title as exam_title,
        e.due_date - l.today as exam_days
      from public.studyflow_push_subscriptions s
      cross join lateral (select (now() at time zone s.timezone)::date as today) l
      left join lateral (
        select t.title, t.due_date from public.studyflow_tasks t
        where t.user_id = s.user_id and t.is_exam and t.status <> 'Done' and t.due_date between l.today and l.today + 7
        order by t.due_date, t.id limit 1
      ) e on true
    ) r), '[]'::json);
end
$$;

-- The sender removes devices the push service says are gone (uninstalled app, revoked permission).
create function public.studyflow_push_forget(cron_secret text, dead_endpoint text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.studyflow_cron_allowed(cron_secret) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  delete from public.studyflow_push_subscriptions where endpoint = dead_endpoint;
end
$$;

revoke all on function public.studyflow_cron_allowed(text), public.studyflow_push_digest(text),
  public.studyflow_push_forget(text, text) from public, anon, authenticated;
grant execute on function public.studyflow_push_digest(text), public.studyflow_push_forget(text, text) to anon, authenticated;
commit;

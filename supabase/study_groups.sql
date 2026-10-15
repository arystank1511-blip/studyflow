-- StudyFlow study groups (YPT-style ranking). Run once in the Supabase SQL Editor after study_sessions.sql.
-- Members see each other's nickname and study minutes only: never emails, tasks or courses.
-- The tables have no direct grants; every read and write goes through the functions below,
-- which act only for the signed-in caller (auth.uid()).
begin;
create table public.studyflow_groups (
  id bigint generated always as identity primary key,
  name text not null check (char_length(trim(name)) between 1 and 40),
  invite_code text not null unique check (invite_code ~ '^[A-HJ-NP-Z2-9]{8}$'),
  owner uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
create table public.studyflow_group_members (
  group_id bigint not null references public.studyflow_groups(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  nickname text not null check (char_length(trim(nickname)) between 1 and 30),
  joined_at timestamptz not null default now(),
  primary key (group_id, user_id)
);
create index studyflow_group_members_user on public.studyflow_group_members(user_id);
alter table public.studyflow_groups enable row level security;
alter table public.studyflow_groups force row level security;
alter table public.studyflow_group_members enable row level security;
alter table public.studyflow_group_members force row level security;
revoke all on public.studyflow_groups, public.studyflow_group_members from anon, authenticated;

-- The invite code comes from the app (Python's secrets module) so it is unguessable.
create function public.studyflow_create_group(group_name text, nick text, code text)
returns json language plpgsql security definer set search_path = '' as $$
declare
  uid uuid := auth.uid();
  new_id bigint;
begin
  if uid is null then raise exception 'not signed in' using errcode = '28000'; end if;
  if (select count(*) from public.studyflow_group_members where user_id = uid) >= 10 then
    raise exception 'too many groups' using errcode = 'P0001';
  end if;
  insert into public.studyflow_groups (name, invite_code, owner) values (trim(group_name), code, uid) returning id into new_id;
  insert into public.studyflow_group_members (group_id, user_id, nickname) values (new_id, uid, trim(nick));
  return json_build_object('id', new_id, 'invite_code', code);
end
$$;

-- Returns null for an unknown code.
create function public.studyflow_join_group(code text, nick text)
returns json language plpgsql security definer set search_path = '' as $$
declare
  uid uuid := auth.uid();
  gid bigint;
begin
  if uid is null then raise exception 'not signed in' using errcode = '28000'; end if;
  select id into gid from public.studyflow_groups where invite_code = upper(trim(code));
  if gid is null then return null; end if;
  if not exists (select 1 from public.studyflow_group_members where group_id = gid and user_id = uid) then
    if (select count(*) from public.studyflow_group_members where user_id = uid) >= 10 then
      raise exception 'too many groups' using errcode = 'P0001';
    end if;
    if (select count(*) from public.studyflow_group_members where group_id = gid) >= 50 then
      raise exception 'group is full' using errcode = 'P0002';
    end if;
  end if;
  insert into public.studyflow_group_members (group_id, user_id, nickname) values (gid, uid, trim(nick))
  on conflict (group_id, user_id) do update set nickname = excluded.nickname;
  return json_build_object('id', gid);
end
$$;

-- The last member to leave removes the group.
create function public.studyflow_leave_group(gid bigint)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '28000'; end if;
  delete from public.studyflow_group_members where group_id = gid and user_id = auth.uid();
  delete from public.studyflow_groups g
  where g.id = gid and not exists (select 1 from public.studyflow_group_members m where m.group_id = gid);
end
$$;

create function public.studyflow_my_groups()
returns json language sql stable security definer set search_path = '' as $$
  select coalesce(json_agg(json_build_object(
           'id', g.id, 'name', g.name, 'invite_code', g.invite_code, 'nickname', m.nickname,
           'members', (select count(*) from public.studyflow_group_members c where c.group_id = g.id))
         order by m.joined_at), '[]'::json)
  from public.studyflow_group_members m
  join public.studyflow_groups g on g.id = m.group_id
  where m.user_id = auth.uid()
$$;

-- Weekly ranking for a group the caller belongs to; null otherwise.
create function public.studyflow_group_board(gid bigint, week_start date, today date)
returns json language sql stable security definer set search_path = '' as $$
  select case
    when not exists (select 1 from public.studyflow_group_members where group_id = gid and user_id = auth.uid())
      or today < week_start or today - week_start > 6 then null
    else coalesce((
      select json_agg(r order by r.week_minutes desc, r.nickname)
      from (
        select m.nickname, m.user_id = auth.uid() as me,
               coalesce(sum(s.minutes) filter (where s.day = today), 0) as today_minutes,
               coalesce(sum(s.minutes), 0) as week_minutes
        from public.studyflow_group_members m
        left join public.studyflow_study_sessions s on s.user_id = m.user_id and s.day between week_start and today
        where m.group_id = gid
        group by m.user_id, m.nickname
      ) r), '[]'::json)
  end
$$;

revoke all on function public.studyflow_create_group(text, text, text), public.studyflow_join_group(text, text),
  public.studyflow_leave_group(bigint), public.studyflow_my_groups(), public.studyflow_group_board(bigint, date, date)
  from public, anon;
grant execute on function public.studyflow_create_group(text, text, text), public.studyflow_join_group(text, text),
  public.studyflow_leave_group(bigint), public.studyflow_my_groups(), public.studyflow_group_board(bigint, date, date)
  to authenticated;
commit;

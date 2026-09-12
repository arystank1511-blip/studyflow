# StudyFlow product review — 13 September 2026

This is a focused comparison of published product features, not a market-share or user-research study. No claims about conversion, productivity gains, or demand have been measured.

| Reference | Relevant pattern | StudyFlow decision |
| --- | --- | --- |
| [Todoist: Upcoming view](https://www.todoist.com/help/todoist/get-started/plan-your-week-with-the-upcoming-view-OKOg1mR8) | Review scheduled work and reschedule deadlines | Add today/week/overdue views and editable deadlines |
| [MyStudyLife: feature tour](https://mystudylife.com/tour/) | Connect coursework to subjects and deadlines; group tasks by day | Make course filters and a grouped agenda central |
| [TickTick](https://ticktick.com/) | Calendar views, focus tools, and task filters | Add a seven-day deadline overview rather than a full calendar integration in this iteration |

## What changed

- Replaced oversized promotional headings and passive metric cards with a compact daily desk and an actionable next task.
- The suggestion uses unfinished tasks only, earliest deadline first, then priority; an in-progress task wins otherwise equal ties. It does not use AI or estimate task effort.
- Added six quick views, exact course filters, and day links. Filter state is kept in the URL and survives task actions.
- Added editing while preserving task identity and completion status.
- The seven-day strip includes today and the following six days. Completed work is excluded; overdue tasks have a separate review link. Three or more deadlines are highlighted as a visual count threshold, not a measure of hours or difficulty.
- Completed tasks are collapsed in the agenda. Progress pages now link to remaining and overdue coursework.
- Removed the always-full total-task progress bar. Overall completion remains a real ratio, explicitly labelled as task counts rather than grades or study time.

## Deliberately deferred

Notifications, timetable integrations, accounts, recurring tasks, and cloud sync require additional product and infrastructure work. The local app does not send reminders while closed. No subscription, external account, or deployment was added.

## Verification

The regression suite uses temporary databases. It covers task workflows, editing, validation, CSRF, escaped content, deadline boundaries, prioritisation, weekly counts, filters, and course attention ordering. Browser checks cover the main views and narrow-screen layout.

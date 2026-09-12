# StudyFlow — Student Task & Progress Tracker

StudyFlow is a lightweight web application that helps students organise assignments, keep track of deadlines, and see their academic progress at a glance.

## Why I built it

An educational portfolio project by Arystan Kabdesh, developed with AI coding assistance. It explores server-side routing, task workflows, relational data storage, input validation, and responsive UI design through a practical student planner.

## Features

- Create tasks with course, deadline, priority, and optional notes
- Change a task's status: **To do**, **In progress**, or **Done**
- Search tasks and filter by status
- Track total, completed, in-progress, and overdue tasks
- Unified Tasks & plan workspace: calendar and task list in one place; `/planner` remains a compatible URL
- Select a date to view its tasks; use its separate plus button to create a task with that deadline
- Active, overdue, completed and all filters retain the selected date; counts reflect the selected date, course and search
- Explicit Apply filters and Save status buttons; no automatic status submission
- Edit titles, courses, deadlines, priorities, and notes without losing completion status
- Filter by course; jump from course progress straight to outstanding work
- Successful creation or rescheduling reveals the task on its deadline, even if a previous filter would hide it
- Course progress: completed tasks divided by the total for each course
- Delete tasks with an in-app confirmation dialog and cancellation
- Responsive interface for desktop and mobile
- Dedicated phone layout: bottom navigation, large touch targets, full-screen task form, and scrollable week strip
- Russian / English switch, remembered for one year; first visit follows the browser language
- Localized dates, task statuses, validation messages, and deletion confirmation; user-entered task text stays unchanged
- Keyboard-accessible task dialog, inline validation, and protected POST forms
- Cloud mode: Supabase PostgreSQL, email/password registration and sign-in, account-owned tasks, and logout
- Defense in depth: owner filters plus database RLS, CSRF checks, secure production cookies, private response caching, and a restrictive script policy

## Tech stack

- Python 3.11 or newer
- Flask
- SQLite for private local use; Supabase PostgreSQL + Auth for cloud use
- HTML, CSS, and vanilla JavaScript

## Run locally

On Windows, the easiest option is to double-click `run.bat`. It creates a virtual environment, installs Flask, and opens the project in your browser.

Or use the commands below:

```bash
git clone https://github.com/arystank1511-blip/studyflow.git
cd studyflow
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000` in your browser.

The SQLite database is created on first launch. Your tasks stay on your computer and are excluded from Git. The Golos Text font is loaded from Google Fonts; an offline browser uses a sans-serif fallback.

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, then `.venv/bin/python app.py`.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest -v
```

Tests use temporary databases and cover task creation, status updates, deletion, validation, search, filtering, deadline ordering, course progress, CSRF rejection, and HTML escaping.

## Scope and configuration

StudyFlow has two deliberately separate modes. Local mode keeps the existing SQLite workspace on loopback only. Cloud mode requires Supabase Auth and PostgreSQL with the supplied RLS policies. On Vercel, cloud mode is mandatory and missing configuration fails closed. Publishing the source to GitHub does not host the backend. See [DEPLOYMENT.md](DEPLOYMENT.md) for setup and required live checks; see [SECURITY.md](SECURITY.md) for tested protections and limits.

Copy `.env.example` to a private `.env` for local cloud configuration. Never commit real secrets. Cloud registration/login uses email and password through Supabase Auth. Email confirmation is intentionally disabled at the owner's request: addresses are unverified identifiers, not proof of mailbox ownership. No confirmation or password-reset emails are offered. Use your own address and save your password in a password manager. New passwords require at least 15 characters and no more than 72 UTF-8 bytes.

- `STUDYFLOW_DATABASE`: optional SQLite path (default: `studyflow.db` beside `app.py`).
- `STUDYFLOW_SECRET_KEY`: optional stable session secret, supplied through the environment. Otherwise a fresh secret is generated at startup and open forms must be reloaded after a restart.
- `STUDYFLOW_DEBUG=1`: opt in to the local debugger and reloader. Disabled by default.
- `STUDYFLOW_MODE=cloud`: enable Supabase authentication and cloud storage.
- `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`: Supabase project connection (never a service-role key).
- `STUDYFLOW_ALLOWED_HOSTS`: exact production hostnames; Vercel deployment hostnames are added automatically.

## Future improvements

- Calendar view and reminders
- Account export/deletion, CAPTCHA integration, and operational monitoring

## Author

Arystan Kabdesh — Computer Science Student at Astana IT University

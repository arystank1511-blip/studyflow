# StudyFlow — Student Task & Progress Tracker

StudyFlow is a lightweight web application that helps students organise assignments, keep track of deadlines, and see their academic progress at a glance.

## Why I built it

An educational portfolio project by Arystan Kabdesh, developed with AI coding assistance. It explores server-side routing, task workflows, relational data storage, input validation, and responsive UI design through a practical student planner.

## Features

- Create tasks with course, deadline, priority, and optional notes
- Change a task's status: **To do**, **In progress**, or **Done**
- Search tasks and filter by status
- Track total, completed, in-progress, and overdue tasks
- Academic planner: unfinished tasks first, ordered by deadline
- Course progress: completed tasks divided by the total for each course
- Delete tasks with confirmation
- Responsive interface for desktop and mobile
- Keyboard-accessible task dialog, inline validation, and protected POST forms

## Tech stack

- Python 3.11 or newer
- Flask
- SQLite
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

StudyFlow is a local, single-user application. It has no login or account isolation yet; publishing the source to GitHub does not host the website. Add authentication and a production server before exposing it to the Internet. GitHub Pages cannot run this Flask backend.

- `STUDYFLOW_DATABASE`: optional SQLite path (default: `studyflow.db` beside `app.py`).
- `STUDYFLOW_SECRET_KEY`: optional stable session secret, supplied through the environment. Otherwise a fresh secret is generated at startup and open forms must be reloaded after a restart.
- `STUDYFLOW_DEBUG=1`: opt in to the local debugger and reloader. Disabled by default.

## Future improvements

- User authentication
- Calendar view and reminders
- Edit existing tasks
- Cloud deployment with PostgreSQL

## Author

Arystan Kabdesh — Computer Science Student at Astana IT University

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import zlib
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, abort, send_from_directory, flash, g, has_request_context, redirect, render_template, request, session, url_for
from i18n import current_language, t, task_count
from quick_add import parse as parse_quick_add
import schedule_scan
from dotenv import load_dotenv
from cloud import (FeedStorageMissing, StudyStorageMissing, cloud_feed, cloud_sessions, cloud_tasks, feed_tasks,
                   register_auth, valid_config)
from werkzeug.exceptions import SecurityError

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "studyflow.db"
load_dotenv(BASE_DIR / ".env", override=False)
ON_VERCEL = os.environ.get("VERCEL") == "1"
CLOUD_MODE = ON_VERCEL or os.environ.get("STUDYFLOW_MODE", "local") == "cloud"
SECRET_KEY = os.environ.get("STUDYFLOW_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
# Vercel servers run in UTC; without a browser timezone this decides what "today" means.
DEFAULT_TIMEZONE = os.environ.get("STUDYFLOW_TIMEZONE", "")
allowed_hosts = [host.strip() for host in os.environ.get("STUDYFLOW_ALLOWED_HOSTS", "").split(",") if host.strip()]
allowed_hosts += [os.environ[key] for key in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL") if os.environ.get(key)]

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32),
    DATABASE=os.environ.get("STUDYFLOW_DATABASE", str(DATABASE)),
    MAX_CONTENT_LENGTH=32 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=ON_VERCEL or os.environ.get("STUDYFLOW_HTTPS") == "1",
    SESSION_COOKIE_NAME="studyflow_session",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    TRUSTED_HOSTS=allowed_hosts if ON_VERCEL else ["localhost", "127.0.0.1", *allowed_hosts],
    CLOUD_MODE=CLOUD_MODE,
    CLOUD_READY=valid_config(SUPABASE_URL, SUPABASE_KEY) and len(SECRET_KEY) >= 32 and (not ON_VERCEL or bool(allowed_hosts)),
    SUPABASE_URL=SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY=SUPABASE_KEY,
    # Turn on only after the Google provider and redirect URL are configured in Supabase.
    GOOGLE_LOGIN=os.environ.get("STUDYFLOW_GOOGLE_LOGIN") == "1",
    # Cloudflare Turnstile site key (public). Set it only after captcha protection with the
    # matching secret is on in Supabase, which then checks every token itself.
    TURNSTILE_SITE_KEY=os.environ.get("TURNSTILE_SITE_KEY", "").strip(),
)


LANGUAGES = {"ru", "kk", "en"}


@app.before_request
def allow_photo_upload():
    # Forms stay capped at 32 KB; only the timetable photo may be larger.
    if request.endpoint == "schedule_scan_photo":
        request.max_content_length = schedule_scan.MAX_IMAGE_BYTES + 64 * 1024


@app.before_request
def choose_language():
    saved = request.cookies.get("studyflow_language")
    g.language = saved if saved in LANGUAGES else (request.accept_languages.best_match(["en", "ru", "kk"]) or "en")


@app.before_request
def protect_forms():
    if request.method == "POST":
        token = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token.encode(), submitted.encode()):
            abort(400, description="This form has expired. Reload the page and try again.")


def shown_undo():
    undo = session.pop("undo", None)
    if undo:
        session["undo_ready"] = undo
    return undo


def offer_undo(task_id, field, previous, message):
    """Remember the previous value so the next page can offer to restore it."""
    session["undo"] = {"id": task_id, "field": field, "value": previous, "message": message}


def current_task(task_id):
    if app.config["CLOUD_MODE"]:
        return cloud_tasks(task_id=task_id)[0]
    task = get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        abort(404)
    return task


def set_task_field(task_id, field, value):
    if app.config["CLOUD_MODE"]:
        cloud_tasks("PATCH", task_id, {field: value})
    else:
        db = get_db()
        # Field names come from a fixed allowlist, never from the request.
        if not db.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, task_id)).rowcount:
            abort(404)
        db.commit()


@app.context_processor
def form_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {
        "t": t, "task_count": task_count, "lang": g.get("language", "en"), "course_color": course_color,
        "csrf_token": session["csrf_token"],
        "task_form": session.pop("task_draft", {}),
        "task_errors": session.pop("task_errors", {}),
        "undo": shown_undo(),
    }


register_auth(app)


@app.post("/language")
def change_language():
    language = request.form.get("language", "")
    if language not in LANGUAGES:
        abort(400, description="Choose a supported language.")
    destination = request.form.get("return_to", "/")
    try:
        parsed = urlsplit(destination)
        unsafe = parsed.scheme or parsed.netloc
    except ValueError:
        unsafe = True
    if unsafe or not destination.startswith("/") or destination.startswith("//") or "\\" in destination or any(ord(char) < 32 for char in destination):
        destination = url_for("index")
    response = redirect(destination)
    response.set_cookie("studyflow_language", language, max_age=365 * 24 * 60 * 60, httponly=True, secure=app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


def return_to_workspace():
    page = request.form.get("next", "dashboard")
    if page in {"planner", "progress", "focus", "study", "schedule"}:
        return redirect(url_for(page))
    return redirect(url_for("index", **workspace_filters(), _anchor="task-workspace"))


def workspace_filters():
    return {key: request.args[key] for key in ("status", "q", "view", "course", "day") if key in request.args}


def user_now() -> datetime:
    """Now in the student's timezone (browser cookie, then STUDYFLOW_TIMEZONE, then server)."""
    candidates = [unquote(request.cookies.get("studyflow_tz", ""))] if has_request_context() else []
    for name in [*candidates, DEFAULT_TIMEZONE]:
        if not name or len(name) > 64 or not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*", name):
            continue
        try:
            return datetime.now(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return datetime.now()


def user_today() -> date:
    return user_now().date()


def greeting() -> str:
    hour = user_now().hour
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    if 18 <= hour < 23:
        return "Good evening"
    return "Good night"


COURSE_COLORS = ("#2E5BFF", "#D97706", "#12A06B", "#E5484D", "#7C5CFF", "#0B7F8C", "#D6408B", "#64748B")


def course_color(name: str) -> str:
    """A stable colour per course name, so the same subject always looks the same."""
    return COURSE_COLORS[zlib.crc32(name.strip().casefold().encode("utf-8")) % len(COURSE_COLORS)]


def urgency_key(task):
    return (task["status"] == "Done", task["due_date"], {"High": 0, "Medium": 1, "Low": 2}.get(task["priority"], 1), task["status"] != "In progress", task["id"])


def deadline_label(task, today):
    days = (date.fromisoformat(task["due_date"]) - today).days
    if days < 0:
        return t("{days} days overdue", days=abs(days))
    if days == 0:
        return t("Due today")
    if days == 1:
        return t("Due tomorrow")
    return t("Due in {days} days", days=days)


def weekly_load(tasks, today):
    return [{"date": (today + timedelta(days=offset)).isoformat(),
             "label": "Today" if offset == 0 else (today + timedelta(days=offset)).strftime("%a"),
             "number": (today + timedelta(days=offset)).day,
             "total": sum(task["due_date"] == (today + timedelta(days=offset)).isoformat() for task in tasks),
             "count": sum(task["status"] != "Done" and task["due_date"] == (today + timedelta(days=offset)).isoformat() for task in tasks)}
            for offset in range(7)]


def get_db() -> sqlite3.Connection:
    if app.config["CLOUD_MODE"]:
        raise RuntimeError("SQLite is disabled in cloud mode")
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: object | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    if app.config["CLOUD_MODE"]:
        return
    with closing(sqlite3.connect(app.config["DATABASE"])) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                course TEXT NOT NULL,
                due_date TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'Medium',
                status TEXT NOT NULL DEFAULT 'To do',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course TEXT NOT NULL,
                minutes INTEGER NOT NULL,
                day TEXT NOT NULL,
                started_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS study_sessions_day ON study_sessions(day);
            CREATE TABLE IF NOT EXISTS calendar_feed (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        if "is_exam" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}:
            db.execute("ALTER TABLE tasks ADD COLUMN is_exam INTEGER NOT NULL DEFAULT 0")
            for task_id, title, course in db.execute("SELECT id, title, course FROM tasks").fetchall():
                if looks_like_exam(title, course):
                    db.execute("UPDATE tasks SET is_exam = 1 WHERE id = ?", (task_id,))
        db.commit()


def all_tasks():
    return cloud_tasks() if app.config["CLOUD_MODE"] else get_db().execute("SELECT * FROM tasks").fetchall()


def task_metrics(tasks: list[sqlite3.Row]) -> dict[str, int]:
    today = user_today().isoformat()
    total = len(tasks)
    completed = sum(task["status"] == "Done" for task in tasks)
    in_progress = sum(task["status"] == "In progress" for task in tasks)
    overdue = sum(task["status"] != "Done" and task["due_date"] < today for task in tasks)
    percentage = lambda value: round((value / total) * 100) if total else 0
    return {
        "total": total,
        "completed": completed,
        "in_progress": in_progress,
        "overdue": overdue,
        "total_percent": 100 if total else 0,
        "completed_percent": percentage(completed),
        "in_progress_percent": percentage(in_progress),
        "overdue_percent": percentage(overdue),
    }


def course_progress(tasks: list[sqlite3.Row]) -> list[dict[str, int | str]]:
    courses: dict[str, dict[str, int | str]] = {}
    today = user_today().isoformat()
    for task in tasks:
        course = task["course"]
        if course not in courses:
            courses[course] = {"name": course, "total": 0, "done": 0, "overdue": 0}
        courses[course]["total"] += 1
        courses[course]["done"] += task["status"] == "Done"
        courses[course]["overdue"] += task["status"] != "Done" and task["due_date"] < today
    summary = []
    for course in courses.values():
        course["percent"] = round((course["done"] / course["total"]) * 100) if course["total"] else 0
        summary.append(course)
    return sorted(summary, key=lambda item: (-item["overdue"], item["percent"], item["name"]))


@app.route("/")
def index():
    status = request.args.get("status", "All")
    if status not in {"All", "To do", "In progress", "Done"}:
        status = "All"
    search = request.args.get("q", "").strip()
    today = user_today()
    all_items = sorted(all_tasks(), key=urgency_key)
    view = request.args.get("view", "all" if status != "All" else "active")
    if view not in {"active", "today", "week", "overdue", "done", "all"}:
        view = "active"
    course = request.args.get("course", "")
    day = request.args.get("day", "")
    try:
        day = date.fromisoformat(day).isoformat() if day else ""
    except ValueError:
        day = ""
    def matches(task):
        done = task["status"] == "Done"
        return ((view != "active" or not done)
                and (view != "today" or (not done and task["due_date"] == today.isoformat()))
                and (view != "week" or (not done and today.isoformat() <= task["due_date"] <= (today + timedelta(days=6)).isoformat()))
                and (view != "overdue" or (not done and task["due_date"] < today.isoformat()))
                and (view != "done" or done)
                and (status == "All" or task["status"] == status)
                and (not course or task["course"] == course)
                and (not day or task["due_date"] == day)
                and (not search or search.casefold() in (task["title"] + " " + task["course"]).casefold()))
    tasks = [task for task in all_items if matches(task)]
    scope = [task for task in all_items if (not day or task["due_date"] == day)
             and (not course or task["course"] == course)
             and (not search or search.casefold() in (task["title"] + " " + task["course"]).casefold())]
    filter_counts = {"all": len(scope), "active": sum(task["status"] != "Done" for task in scope),
                     "done": sum(task["status"] == "Done" for task in scope),
                     "overdue": sum(task["status"] != "Done" and task["due_date"] < today.isoformat() for task in scope)}
    active = [task for task in all_items if task["status"] != "Done"]
    next_task = active[0] if active else None
    metrics = task_metrics(all_items)
    counts = {"active": len(active), "today": sum(task["due_date"] == today.isoformat() for task in active),
              "week": sum(today.isoformat() <= task["due_date"] <= (today + timedelta(days=6)).isoformat() for task in active),
              "overdue": metrics["overdue"], "done": metrics["completed"], "all": metrics["total"]}
    return render_template("index.html", tasks=tasks, metrics=metrics, status=status, search=search,
                           today=today.isoformat(), page="dashboard", view=view, course=course, day=day,
                           courses=sorted({task["course"] for task in all_items}), counts=counts,
                           next_task=next_task, next_label=deadline_label(next_task, today) if next_task else "",
                           week=weekly_load(all_items, today), filters=workspace_filters(), filter_counts=filter_counts,
                           greeting=greeting(), today_total=sum(task["due_date"] == today.isoformat() for task in all_items),
                           today_done=sum(task["due_date"] == today.isoformat() and task["status"] == "Done" for task in all_items))


@app.route("/planner")
def planner():
    # Backward-compatible URL, but only one workspace template and navigation entry.
    return index()


@app.route("/progress")
def progress():
    tasks = sorted(all_tasks(), key=lambda task: task["due_date"])
    metrics = task_metrics(tasks)
    feed_url = session.pop("feed_url", None)  # The secret link is shown once, right after it is made.
    return render_template("progress.html", metrics=metrics, courses=course_progress(tasks), page="progress",
                           feed=calendar_feed_state(), feed_url=feed_url,
                           google_feed_url=feed_url and "https://calendar.google.com/calendar/render?" + urlencode({"cid": re.sub(r"^https?://", "webcal://", feed_url)}),
                           webcal_url=feed_url and re.sub(r"^https?://", "webcal://", feed_url))


def calendar_feed_state():
    if app.config["CLOUD_MODE"]:
        try:
            rows = cloud_feed()
        except FeedStorageMissing:
            return {"ready": False, "on": False}
        return {"ready": True, "on": bool(rows), "since": str(rows[0]["created_at"])[:10] if rows else None}
    row = get_db().execute("SELECT created_at FROM calendar_feed WHERE id = 1").fetchone()
    return {"ready": True, "on": row is not None, "since": row["created_at"][:10] if row else None}


@app.post("/calendar/subscription")
def calendar_subscription():
    action = request.form.get("action")
    if action not in {"create", "off"}:
        abort(400)
    token = secrets.token_urlsafe(32) if action == "create" else None
    digest = hashlib.sha256(token.encode()).hexdigest() if token else None
    if app.config["CLOUD_MODE"]:
        try:
            cloud_feed("POST" if token else "DELETE", digest)
        except FeedStorageMissing:
            flash("Calendar subscription is not connected yet: the site owner needs to run supabase/calendar_feed.sql in Supabase.", "error")
            return redirect(url_for("progress"))
    else:
        db = get_db()
        db.execute("DELETE FROM calendar_feed")
        if token:
            db.execute("INSERT INTO calendar_feed (id, token_hash, created_at) VALUES (1, ?, ?)", (digest, datetime.now(timezone.utc).isoformat()))
        db.commit()
    if token:
        scheme = "https" if app.config["SESSION_COOKIE_SECURE"] else request.scheme
        session["feed_url"] = url_for("calendar_feed", token=token, _external=True, _scheme=scheme)
        flash("Your calendar link is ready.", "success")
    else:
        flash("Calendar subscription turned off. The old link no longer works.", "success")
    return redirect(url_for("progress", _anchor="calendar-feed"))


@app.get("/calendar/feed/<token>.ics")
def calendar_feed(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        abort(404)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if app.config["CLOUD_MODE"]:
        tasks = feed_tasks(digest)
    else:
        row = get_db().execute("SELECT token_hash FROM calendar_feed WHERE id = 1").fetchone()
        matches = row is not None and secrets.compare_digest(row["token_hash"], digest)
        tasks = [task for task in get_db().execute("SELECT * FROM tasks").fetchall() if task["status"] != "Done"] if matches else None
    if tasks is None:
        abort(404)
    response = Response(ics_calendar(tasks, refresh=True), mimetype="text/calendar")
    response.headers["Content-Disposition"] = 'inline; filename="studyflow.ics"'
    return response


def ical_text(value: str) -> str:
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def ical_fold(line: str) -> str:
    # RFC 5545: lines longer than 75 octets continue on the next line after a space.
    parts, current = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def download(body: str, mimetype: str, filename: str) -> Response:
    response = Response(body, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.get("/sw.js")
def service_worker():
    # Served from the root so it may control every page; it caches only public static files.
    # The shared no-store header makes browsers pick up a new worker on every visit.
    return send_from_directory(BASE_DIR / "static", "sw.js", mimetype="application/javascript", max_age=0)


@app.get("/calendar.ics")
def calendar_export():
    return download(ics_calendar([task for task in all_tasks() if task["status"] != "Done"]), "text/calendar", "studyflow.ics")


def ics_calendar(tasks, refresh=False) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//StudyFlow//Deadlines//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:StudyFlow"]
    if refresh:
        # Subscribed calendars: ask apps to check back hourly (many still choose their own interval).
        lines += ["REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for task in sorted(tasks, key=lambda item: (str(item["due_date"]), item["id"])):
        start = date.fromisoformat(str(task["due_date"]))
        lines += ["BEGIN:VEVENT", f"UID:studyflow-task-{task['id']}@studyflow", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{start + timedelta(days=1):%Y%m%d}",
                  "SUMMARY:" + ical_text(f"{task['title']} · {task['course']}"),
                  "DESCRIPTION:" + ical_text(task["notes"] or ""), "PRIORITY:" + {"High": "1", "Medium": "5", "Low": "9"}.get(task["priority"], "5"),
                  # Reminder at 09:00 the day before an all-day deadline.
                  "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + ical_text(task["title"]), "TRIGGER:-PT15H", "END:VALARM",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(ical_fold(line) for line in lines) + "\r\n"


@app.get("/export.json")
def data_export():
    fields = ("id", "title", "course", "due_date", "priority", "status", "notes", "created_at")
    tasks = [{**{key: (str(task[key]) if key in ("due_date", "created_at") else task[key]) for key in fields}, "is_exam": is_exam(task)}
             for task in sorted(all_tasks(), key=lambda item: item["id"])]
    body = json.dumps({"app": "StudyFlow", "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tasks": tasks},
                      ensure_ascii=False, indent=2)
    return download(body, "application/json", "studyflow-export.json")


EXAM_WORDS = ("экзамен", "емтихан", "exam", "коллоквиум", "сессия", "сессиясы", "midterm", "final", "рубеж", "тест", "test", "quiz", "аралық")


def looks_like_exam(title, course=""):
    """A guess for quick add and old clients; the task form's checkbox always wins."""
    text = (title + " " + course).casefold()
    return any(word in text for word in EXAM_WORDS)


def is_exam(task):
    try:
        return bool(task["is_exam"])
    except (KeyError, IndexError):  # Rows from before the column existed.
        return False


def validated_task():
    title = request.form.get("title", "").strip()
    course = request.form.get("course", "").strip()
    due_date = request.form.get("due_date", "")
    priority = request.form.get("priority", "Medium")
    notes = request.form.get("notes", "").strip()
    errors = {}
    if not title or len(title) > 100:
        errors["title"] = "Enter a task title between 1 and 100 characters."
    if not course or len(course) > 80:
        errors["course"] = "Enter a course between 1 and 80 characters."
    if len(notes) > 300:
        errors["notes"] = "Keep notes within 300 characters."
    try:
        if date.fromisoformat(due_date).isoformat() != due_date:
            raise ValueError
    except ValueError:
        errors["due_date"] = "Choose a valid due date."
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"
    exam = request.form.get("is_exam") == "1" if request.form.get("exam_field") else looks_like_exam(title, course)
    return {"title": title[:100], "course": course[:80], "due_date": due_date[:10], "notes": notes[:300], "priority": priority,
            "is_exam": exam}, errors


def insert_task(values):
    if app.config["CLOUD_MODE"]:
        cloud_tasks("POST", data=values)
    else:
        get_db().execute(
            "INSERT INTO tasks (title, course, due_date, priority, notes, is_exam, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (values["title"], values["course"], values["due_date"], values["priority"], values["notes"], int(bool(values.get("is_exam"))),
             datetime.now().isoformat(timespec="seconds")),
        )
        get_db().commit()


def quick_values(text):
    parsed = parse_quick_add(text, user_today(), {"ru": "Общее", "kk": "Жалпы"}.get(current_language(), "General"))
    errors = {}
    if not parsed["title"] or len(parsed["title"]) > 100:
        errors["title"] = "Enter a task title between 1 and 100 characters."
    if len(parsed["course"]) > 80:
        errors["course"] = "Enter a course between 1 and 80 characters."
    values = {key: parsed[key] for key in ("title", "course", "due_date", "priority", "notes")}
    values["is_exam"] = looks_like_exam(values["title"], values["course"])
    return values, parsed["found"], errors


@app.get("/tasks/quick/preview")
def quick_add_preview():
    values, found, errors = quick_values(request.args.get("text", "")[:300])
    priorities = {"High": t("High"), "Medium": t("Medium"), "Low": t("Low")}
    return {"title": values["title"], "course": values["course"], "course_color": course_color(values["course"]),
            "due_date": values["due_date"], "priority": priorities[values["priority"]], "priority_key": values["priority"],
            "time": found.get("time", ""), "found": sorted(found), "valid": not errors}


@app.post("/tasks/quick")
def quick_add():
    values, _, errors = quick_values(request.form.get("text", ""))
    if errors:
        flash(next(iter(errors.values())), "error")
        return redirect(url_for("index", _anchor="quick-add"))
    insert_task(values)
    flash("Task added successfully.", "success")
    return redirect(url_for("index", view="all", day=values["due_date"], _anchor="task-workspace"))


@app.post("/tasks/reschedule-overdue")
def reschedule_overdue():
    today = user_today().isoformat()
    overdue = [task for task in all_tasks() if task["status"] != "Done" and task["due_date"] < today]
    for task in overdue:
        if app.config["CLOUD_MODE"]:
            cloud_tasks("PATCH", task["id"], {"due_date": today})
        else:
            get_db().execute("UPDATE tasks SET due_date = ? WHERE id = ?", (today, task["id"]))
    if not app.config["CLOUD_MODE"]:
        get_db().commit()
    flash(t("Moved {n} to today.", n=task_count(len(overdue))) if overdue else "Nothing is overdue.", "success")
    return redirect(url_for("index", _anchor="task-workspace"))


KIND_LABELS = {"lecture": "Lecture", "practice": "Practice", "lab": "Lab", "seminar": "Seminar", "other": "Class"}


@app.get("/schedule")
def schedule():
    return render_template("schedule.html", page="schedule", scan_ready=schedule_scan.available(), filters={})


@app.post("/schedule/scan")
def schedule_scan_photo():
    photo = request.files.get("photo")
    if not photo:
        flash("Choose a photo of your timetable.", "error")
        return redirect(url_for("schedule"))
    try:
        result = schedule_scan.scan(photo.read(schedule_scan.MAX_IMAGE_BYTES + 1))
    except schedule_scan.ScanError as error:
        flash(str(error), "error")
        return redirect(url_for("schedule"))
    if not result["classes"]:
        flash("No classes were found in this photo. Try a clearer, straight photo of the timetable.", "error")
        return redirect(url_for("schedule"))
    return render_template("schedule_review.html", page="schedule", classes=result["classes"], warnings=result["warnings"],
                           kinds=KIND_LABELS, has_parity=any(row["parity"] != "every" for row in result["classes"]), filters={})


def schedule_rows():
    """Read the reviewed rows back from the form, trusting nothing the browser sent."""
    try:
        count = min(int(request.form.get("count", "0")), schedule_scan.MAX_CLASSES)
    except ValueError:
        count = 0
    rows = []
    for index in range(max(count, 0)):
        if request.form.get(f"include-{index}") != "1":
            continue
        row = schedule_scan.clean({"classes": [{
            "weekday": int(request.form.get(f"weekday-{index}", "-1")) if request.form.get(f"weekday-{index}", "").isdigit() else -1,
            "start": request.form.get(f"start-{index}", ""), "end": request.form.get(f"end-{index}", ""),
            "course": request.form.get(f"course-{index}", ""), "kind": request.form.get(f"kind-{index}", ""),
            "room": request.form.get(f"room-{index}", ""), "teacher": request.form.get(f"teacher-{index}", ""),
            "parity": request.form.get(f"parity-{index}", "every"),
        }]})["classes"]
        rows.extend(row)
    return rows


@app.post("/schedule/import")
def schedule_import():
    rows = schedule_rows()
    weeks = request.form.get("weeks", "2")
    weeks = int(weeks) if weeks in {"1", "2", "4", "8"} else 2
    this_week = request.form.get("this_week", "odd")
    this_week = this_week if this_week in {"odd", "even"} else "odd"
    today = user_today()
    monday = today - timedelta(days=today.weekday())
    existing = {(task["title"], task["course"], str(task["due_date"])) for task in all_tasks()}
    added = 0
    for week in range(weeks):
        parity = this_week if week % 2 == 0 else ("even" if this_week == "odd" else "odd")
        for row in rows:
            if row["parity"] not in ("every", parity):
                continue
            day = monday + timedelta(days=7 * week + row["weekday"])
            if day < today:
                continue
            title = (t(KIND_LABELS[row["kind"]]) + (" " + row["start"] if row["start"] else ""))[:100]
            notes = " · ".join(part for part in (
                f"{row['start']}–{row['end']}" if row["start"] and row["end"] else row["start"],
                (t("Room") + " " + row["room"]) if row["room"] else "", row["teacher"]) if part)[:300]
            key = (title, row["course"], day.isoformat())
            if key in existing or added >= 300:
                continue
            insert_task({"title": title, "course": row["course"], "due_date": day.isoformat(), "priority": "Low", "notes": notes})
            existing.add(key)
            added += 1
    flash(t("Added {n} classes to your plan.", n=added) if added else "Nothing new to add.", "success")
    return redirect(url_for("index", view="week", _anchor="task-workspace"))




def study_sessions_since(day):
    if app.config["CLOUD_MODE"]:
        return cloud_sessions(since=day)
    return [dict(row) for row in get_db().execute(
        "SELECT course, minutes, day FROM study_sessions WHERE day >= ? ORDER BY day", (day,)).fetchall()]


def study_level(minutes):
    return 0 if minutes <= 0 else 1 if minutes < 30 else 2 if minutes < 60 else 3 if minutes < 120 else 4


@app.get("/study")
def study():
    today = user_today()
    start = today - timedelta(days=today.weekday() + 7 * 11)
    try:
        sessions = study_sessions_since(start.isoformat())
        storage_ready = True
    except StudyStorageMissing:
        sessions, storage_ready = [], False
    by_day, today_courses, week_courses = {}, {}, {}
    week_start = (today - timedelta(days=6)).isoformat()
    for row in sessions:
        day, minutes, course = str(row["day"]), int(row["minutes"]), row["course"]
        by_day[day] = by_day.get(day, 0) + minutes
        if day == today.isoformat():
            today_courses[course] = today_courses.get(course, 0) + minutes
        if day >= week_start:
            week_courses[course] = week_courses.get(course, 0) + minutes
    tasks = all_tasks()
    active = [task for task in tasks if task["status"] != "Done"]
    names = {task["course"] for task in active} | set(today_courses) | set(week_courses)
    other = t("Other")
    subjects = sorted(names, key=lambda name: (-today_courses.get(name, 0), -week_courses.get(name, 0), name.casefold()))[:12]
    if other not in subjects:
        subjects.append(other)
    heatmap = []
    for week in range(12):
        column = []
        for weekday in range(7):
            day = start + timedelta(days=7 * week + weekday)
            minutes = by_day.get(day.isoformat(), 0)
            column.append({"date": day.isoformat(), "minutes": minutes, "level": study_level(minutes), "future": day > today})
        heatmap.append(column)
    exams = sorted((task for task in active if str(task["due_date"]) >= today.isoformat() and is_exam(task)),
                   key=lambda task: str(task["due_date"]))[:3]
    ddays = [{"title": task["title"], "course": task["course"],
              "days": (date.fromisoformat(str(task["due_date"])) - today).days} for task in exams]
    streak = 0
    cursor = today if by_day.get(today.isoformat()) else today - timedelta(days=1)
    while by_day.get(cursor.isoformat()):
        streak += 1
        cursor -= timedelta(days=1)
    week_total = sum(week_courses.values())
    return render_template("study.html", page="study", filters={}, storage_ready=storage_ready,
                           today_total=sum(today_courses.values()), today_courses=today_courses, subjects=subjects,
                           week_courses=sorted(week_courses.items(), key=lambda item: -item[1]), week_total=week_total,
                           heatmap=heatmap, ddays=ddays, streak=streak, today=today.isoformat())


@app.post("/study/sessions")
def save_study_session():
    course = " ".join(request.form.get("course", "").split())[:80]
    try:
        seconds = int(request.form.get("seconds", "0"))
    except ValueError:
        seconds = 0
    if not course or not 60 <= seconds <= 12 * 3600:
        return {"saved": 0}, 400 if not course or seconds > 12 * 3600 else 200
    minutes = round(seconds / 60)
    started = request.form.get("started_at", "")[:40]
    started = started if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", started) else None
    row = {"course": course, "minutes": minutes, "day": user_today().isoformat(), "started_at": started}
    try:
        if app.config["CLOUD_MODE"]:
            cloud_sessions("POST", row)
        else:
            get_db().execute("INSERT INTO study_sessions (course, minutes, day, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
                             (course, minutes, row["day"], started, datetime.now().isoformat(timespec="seconds")))
            get_db().commit()
    except StudyStorageMissing:
        return {"saved": 0, "error": "storage"}, 503
    return {"saved": minutes}


@app.get("/focus")
def focus():
    active = sorted((task for task in all_tasks() if task["status"] != "Done"), key=urgency_key)
    selected = request.args.get("task", type=int)
    current = next((task for task in active if task["id"] == selected), active[0] if active else None)
    return render_template("focus.html", page="focus", tasks=active, current=current, filters={})


@app.post("/tasks")
def create_task():
    values, errors = validated_task()
    if errors:
        session["task_draft"], session["task_errors"] = values, errors
        return return_to_workspace()
    insert_task(values)
    flash("Task added successfully.", "success")
    if request.form.get("workspace") == "unified":
        return redirect(url_for("index", view="all", day=values["due_date"], _anchor="task-workspace"))
    return return_to_workspace()


@app.route("/tasks/<int(min=1,max=9223372036854775807):task_id>/edit", methods=["GET", "POST"])
def edit_task(task_id):
    task = cloud_tasks(task_id=task_id)[0] if app.config["CLOUD_MODE"] else get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        abort(404)
    values, errors = (validated_task() if request.method == "POST" else (dict(task), {}))
    destination = request.values.get("next", "dashboard")
    if destination not in {"dashboard", "planner", "progress"}:
        destination = "dashboard"
    if request.method == "POST" and not errors:
        if app.config["CLOUD_MODE"]:
            cloud_tasks("PATCH", task_id, values)
        else:
            get_db().execute("UPDATE tasks SET title = ?, course = ?, due_date = ?, priority = ?, notes = ?, is_exam = ? WHERE id = ?",
                             (values["title"], values["course"], values["due_date"], values["priority"], values["notes"], int(values["is_exam"]), task_id))
            get_db().commit()
        flash("Task updated. Your plan and progress are up to date.", "success")
        if request.form.get("workspace") == "unified":
            return redirect(url_for("index", view="all", day=values["due_date"], _anchor="task-workspace"))
        return return_to_workspace()
    back_url = url_for(destination) if destination in {"planner", "progress"} else url_for("index", **workspace_filters())
    return render_template("edit.html", task=task, values=values, errors=errors, page="dashboard" if destination == "planner" else destination,
                           back_url=back_url, filters=workspace_filters()), 400 if errors else 200


@app.post("/tasks/<int(min=1,max=9223372036854775807):task_id>/status")
def update_status(task_id: int):
    status = request.form.get("status", "To do")
    if status not in {"To do", "In progress", "Done"}:
        flash("Invalid task status.", "error")
        return return_to_workspace()
    previous = current_task(task_id)["status"]
    set_task_field(task_id, "status", status)
    if previous != status:
        offer_undo(task_id, "status", previous, "Task marked as done." if status == "Done" else "Task status updated.")
    else:
        flash("Task status updated.", "success")
    return return_to_workspace()


@app.post("/tasks/<int(min=1,max=9223372036854775807):task_id>/undo")
def undo_change(task_id: int):
    undo = session.pop("undo_ready", None)
    if not undo or undo.get("id") != task_id or undo.get("field") not in {"status", "due_date"}:
        flash("Nothing to undo.", "error")
        return return_to_workspace()
    value = undo.get("value")
    valid = value in {"To do", "In progress", "Done"} if undo["field"] == "status" else bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)))
    if not valid:
        abort(400)
    set_task_field(task_id, undo["field"], value)
    flash("Change undone.", "success")
    return return_to_workspace()


@app.post("/tasks/<int(min=1,max=9223372036854775807):task_id>/snooze")
def snooze_task(task_id: int):
    tomorrow = (user_today() + timedelta(days=1)).isoformat()
    previous = str(current_task(task_id)["due_date"])
    set_task_field(task_id, "due_date", tomorrow)
    offer_undo(task_id, "due_date", previous, "Moved to tomorrow.")
    return return_to_workspace()


@app.post("/tasks/<int(min=1,max=9223372036854775807):task_id>/delete")
def delete_task(task_id: int):
    if app.config["CLOUD_MODE"]:
        cloud_tasks("DELETE", task_id)
    else:
        db = get_db()
        result = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if not result.rowcount:
            abort(404)
        db.commit()
    flash("Task deleted.", "success")
    return return_to_workspace()


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(500)
@app.errorhandler(503)
def friendly_error(error):
    if isinstance(error, SecurityError):
        return "Bad request", 400
    descriptions = {404: "The requested page or task could not be found.", 413: "The form is too large. Shorten your notes and try again.",
                    401: "Please sign in to continue.", 403: "Access is not allowed.",
                    500: "Something went wrong. Please try again later.",
                    503: "The cloud workspace is unavailable. Please try again later."}
    description = descriptions.get(error.code, error.description if error.description in {"This form has expired. Reload the page and try again.", "Choose a supported language."} else "Please check the form and try again.")
    return render_template("error.html", error=error, error_description=description), error.code


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", debug=not CLOUD_MODE and os.environ.get("STUDYFLOW_DEBUG") == "1")

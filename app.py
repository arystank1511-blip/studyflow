from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from i18n import t, task_count
from dotenv import load_dotenv
from cloud import cloud_tasks, register_auth, valid_config
from werkzeug.exceptions import SecurityError

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "studyflow.db"
load_dotenv(BASE_DIR / ".env", override=False)
ON_VERCEL = os.environ.get("VERCEL") == "1"
CLOUD_MODE = ON_VERCEL or os.environ.get("STUDYFLOW_MODE", "local") == "cloud"
SECRET_KEY = os.environ.get("STUDYFLOW_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
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
)


@app.before_request
def choose_language():
    saved = request.cookies.get("studyflow_language")
    g.language = saved if saved in {"ru", "en"} else (request.accept_languages.best_match(["en", "ru"]) or "en")


@app.before_request
def protect_forms():
    if request.method == "POST":
        token = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token.encode(), submitted.encode()):
            abort(400, description="This form has expired. Reload the page and try again.")


@app.context_processor
def form_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {
        "t": t, "task_count": task_count, "lang": g.get("language", "en"),
        "csrf_token": session["csrf_token"],
        "task_form": session.pop("task_draft", {}),
        "task_errors": session.pop("task_errors", {}),
    }


register_auth(app)


@app.post("/language")
def change_language():
    language = request.form.get("language", "")
    if language not in {"ru", "en"}:
        abort(400, description="Choose English or Russian.")
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
    if page in {"planner", "progress"}:
        return redirect(url_for(page))
    return redirect(url_for("index", **workspace_filters()))


def workspace_filters():
    return {key: request.args[key] for key in ("status", "q", "view", "course", "day") if key in request.args}


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
            """
        )
        db.commit()


def all_tasks():
    return cloud_tasks() if app.config["CLOUD_MODE"] else get_db().execute("SELECT * FROM tasks").fetchall()


def task_metrics(tasks: list[sqlite3.Row]) -> dict[str, int]:
    today = date.today().isoformat()
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
    for task in tasks:
        course = task["course"]
        if course not in courses:
            courses[course] = {"name": course, "total": 0, "done": 0, "overdue": 0}
        courses[course]["total"] += 1
        courses[course]["done"] += task["status"] == "Done"
        courses[course]["overdue"] += task["status"] != "Done" and task["due_date"] < date.today().isoformat()
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
    today = date.today()
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
                           week=weekly_load(all_items, today), filters=workspace_filters(), filter_counts=filter_counts)


@app.route("/planner")
def planner():
    # Backward-compatible URL, but only one workspace template and navigation entry.
    return index()


@app.route("/progress")
def progress():
    tasks = sorted(all_tasks(), key=lambda task: task["due_date"])
    metrics = task_metrics(tasks)
    return render_template("progress.html", metrics=metrics, courses=course_progress(tasks), page="progress")


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
    return {"title": title[:100], "course": course[:80], "due_date": due_date[:10], "notes": notes[:300], "priority": priority}, errors


@app.post("/tasks")
def create_task():
    values, errors = validated_task()
    if errors:
        session["task_draft"], session["task_errors"] = values, errors
        return return_to_workspace()
    if app.config["CLOUD_MODE"]:
        cloud_tasks("POST", data=values)
    else:
        get_db().execute(
            "INSERT INTO tasks (title, course, due_date, priority, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (values["title"], values["course"], values["due_date"], values["priority"], values["notes"], datetime.now().isoformat(timespec="seconds")),
        )
        get_db().commit()
    flash("Task added successfully.", "success")
    if request.form.get("workspace") == "unified":
        return redirect(url_for("index", view="all", day=values["due_date"], _anchor="task-workspace"))
    return return_to_workspace()


@app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
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
            get_db().execute("UPDATE tasks SET title = ?, course = ?, due_date = ?, priority = ?, notes = ? WHERE id = ?",
                             (values["title"], values["course"], values["due_date"], values["priority"], values["notes"], task_id))
            get_db().commit()
        flash("Task updated. Your plan and progress are up to date.", "success")
        if request.form.get("workspace") == "unified":
            return redirect(url_for("index", view="all", day=values["due_date"], _anchor="task-workspace"))
        return return_to_workspace()
    back_url = url_for(destination) if destination in {"planner", "progress"} else url_for("index", **workspace_filters())
    return render_template("edit.html", task=task, values=values, errors=errors, page="dashboard" if destination == "planner" else destination,
                           back_url=back_url, filters=workspace_filters()), 400 if errors else 200


@app.post("/tasks/<int:task_id>/status")
def update_status(task_id: int):
    status = request.form.get("status", "To do")
    if status not in {"To do", "In progress", "Done"}:
        flash("Invalid task status.", "error")
        return return_to_workspace()
    if app.config["CLOUD_MODE"]:
        cloud_tasks("PATCH", task_id, {"status": status})
    else:
        db = get_db()
        result = db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        if not result.rowcount:
            abort(404)
        db.commit()
    flash("Task status updated.", "success")
    return return_to_workspace()


@app.post("/tasks/<int:task_id>/delete")
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
    description = descriptions.get(error.code, error.description if error.description in {"This form has expired. Reload the page and try again.", "Choose English or Russian."} else "Please check the form and try again.")
    return render_template("error.html", error=error, error_description=description), error.code


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", debug=not CLOUD_MODE and os.environ.get("STUDYFLOW_DEBUG") == "1")

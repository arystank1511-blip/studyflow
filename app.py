from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "studyflow.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("STUDYFLOW_SECRET_KEY") or secrets.token_hex(32),
    DATABASE=os.environ.get("STUDYFLOW_DATABASE", str(DATABASE)),
    MAX_CONTENT_LENGTH=32 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


@app.before_request
def protect_forms():
    if request.method == "POST":
        token = session.get("csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token, submitted):
            abort(400, description="This form has expired. Reload the page and try again.")


@app.context_processor
def form_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {
        "csrf_token": session["csrf_token"],
        "task_form": session.pop("task_draft", {}),
        "task_errors": session.pop("task_errors", {}),
    }


def return_to_workspace():
    page = request.form.get("next", "dashboard")
    if page in {"planner", "progress"}:
        return redirect(url_for(page))
    return redirect(url_for("index", status=request.args.get("status", "All"), q=request.args.get("q", "")))


def get_db() -> sqlite3.Connection:
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
            courses[course] = {"name": course, "total": 0, "done": 0}
        courses[course]["total"] += 1
        courses[course]["done"] += task["status"] == "Done"
    summary = []
    for course in courses.values():
        course["percent"] = round((course["done"] / course["total"]) * 100) if course["total"] else 0
        summary.append(course)
    return sorted(summary, key=lambda item: (item["percent"], item["name"]))


@app.route("/")
def index():
    status = request.args.get("status", "All")
    if status not in {"All", "To do", "In progress", "Done"}:
        status = "All"
    search = request.args.get("q", "").strip()
    db = get_db()
    query = "SELECT * FROM tasks WHERE 1 = 1"
    params: list[str] = []
    if status in {"To do", "In progress", "Done"}:
        query += " AND status = ?"
        params.append(status)
    if search:
        query += " AND (title LIKE ? OR course LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term])
    query += " ORDER BY CASE status WHEN 'Done' THEN 1 ELSE 0 END, due_date ASC, id DESC"
    tasks = db.execute(query, params).fetchall()
    all_tasks = db.execute("SELECT * FROM tasks").fetchall()
    return render_template("index.html", tasks=tasks, metrics=task_metrics(all_tasks), status=status, search=search, today=date.today().isoformat(), page="dashboard")


@app.route("/planner")
def planner():
    tasks = get_db().execute(
        "SELECT * FROM tasks ORDER BY CASE status WHEN 'Done' THEN 1 ELSE 0 END, due_date ASC, id DESC"
    ).fetchall()
    return render_template("planner.html", tasks=tasks, metrics=task_metrics(tasks), today=date.today().isoformat(), page="planner")


@app.route("/progress")
def progress():
    tasks = get_db().execute("SELECT * FROM tasks ORDER BY due_date ASC").fetchall()
    metrics = task_metrics(tasks)
    return render_template("progress.html", metrics=metrics, courses=course_progress(tasks), page="progress")


@app.post("/tasks")
def create_task():
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
    if errors:
        session["task_draft"] = {"title": title[:100], "course": course[:80], "due_date": due_date[:10], "notes": notes[:300], "priority": priority[:6]}
        session["task_errors"] = errors
        return return_to_workspace()
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"
    get_db().execute(
        "INSERT INTO tasks (title, course, due_date, priority, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (title, course, due_date, priority, notes, datetime.now().isoformat(timespec="seconds")),
    )
    get_db().commit()
    flash("Task added successfully.", "success")
    return return_to_workspace()


@app.post("/tasks/<int:task_id>/status")
def update_status(task_id: int):
    status = request.form.get("status", "To do")
    if status not in {"To do", "In progress", "Done"}:
        flash("Invalid task status.", "error")
        return return_to_workspace()
    db = get_db()
    result = db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    if not result.rowcount:
        abort(404)
    db.commit()
    return return_to_workspace()


@app.post("/tasks/<int:task_id>/delete")
def delete_task(task_id: int):
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
def friendly_error(error):
    return render_template("error.html", error=error), error.code


if __name__ == "__main__":
    init_db()
    app.run(debug=os.environ.get("STUDYFLOW_DEBUG") == "1")

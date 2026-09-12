"""Regression checks use a temporary SQLite database, never the personal workspace."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app, course_progress, get_db, init_db, task_metrics, urgency_key, weekly_load


class StudyFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db = app.config["DATABASE"]
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "test.db"))
        init_db()
        self.client = app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.token = session["csrf_token"]

    def tearDown(self):
        app.config["DATABASE"] = self.original_db
        self.temp.cleanup()

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": self.token, **data})

    def create(self, **changes):
        data = dict(title="Database assignment", course="Computer Science", due_date=date.today().isoformat())
        data.update(changes)
        return self.post("/tasks", **data)

    def tasks(self):
        with app.app_context():
            return get_db().execute("SELECT * FROM tasks").fetchall()

    def test_pages_and_empty_states(self):
        for path in ("/", "/planner", "/progress", "/?status=unknown"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'name="csrf_token"', response.data)
                self.assertIn(b'aria-current="page"', response.data)
        self.assertEqual(self.client.get("/missing").status_code, 404)

    def test_create_status_progress_and_delete(self):
        self.assertEqual(self.create(next="planner").location, "/planner")
        task_id = self.tasks()[0]["id"]
        response = self.post(f"/tasks/{task_id}/status", status="Done", next="planner")
        self.assertEqual(response.location, "/planner")
        self.assertEqual(task_metrics(self.tasks())["completed_percent"], 100)
        self.assertIn(b'aria-valuenow="100"', self.client.get("/progress").data)
        self.assertEqual(self.post(f"/tasks/{task_id}/delete", next="planner").location, "/planner")
        self.assertEqual(len(self.tasks()), 0)
        self.assertIn(b"Task deleted.", self.client.get("/planner").data)

    def test_create_stays_on_progress(self):
        self.assertEqual(self.create(next="progress").location, "/progress")
        self.assertIn(b"Task added successfully.", self.client.get("/progress").data)

    def test_invalid_fields_preserve_draft(self):
        for changes in ({"due_date": "not-a-date"}, {"due_date": "2026-02-30"}, {"title": " "}, {"course": "x" * 81}, {"notes": "n" * 301}):
            with self.subTest(changes=changes):
                response = self.create(next="planner", **changes)
                self.assertEqual(response.location, "/planner")
                page = self.client.get("/planner").data
                self.assertIn(b'data-reopen="true"', page)
                self.assertIn(b'aria-invalid="true"', page)
                self.assertEqual(len(self.tasks()), 0)

    def test_csrf_blocks_all_mutations(self):
        self.create()
        for path in ("/tasks", "/tasks/1/status", "/tasks/1/delete"):
            self.assertEqual(self.client.post(path, data={"status": "Done"}).status_code, 400)
        self.assertEqual(self.tasks()[0]["status"], "To do")

    def test_filter_and_search(self):
        self.create(title="Algorithms")
        self.create(title="Essay", course="Writing")
        self.post("/tasks/1/status", status="Done")
        page = self.client.get("/?q=Writing&status=To+do").data
        self.assertIn(b"Essay", page)
        self.assertNotIn(b"Algorithms", page)
        self.assertIn(b"No matching tasks", self.client.get("/?q=missing").data)
        self.assertIn(b"Clear filters", self.client.get("/?q=missing").data)

    def test_metrics_and_deadline_order(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.create(title="Overdue", due_date=yesterday)
        self.create(title="Completed")
        self.create(title="Active")
        self.post("/tasks/2/status", status="Done")
        self.post("/tasks/3/status", status="In progress")
        metrics = task_metrics(self.tasks())
        self.assertEqual((metrics["overdue"], metrics["completed"], metrics["in_progress"]), (1, 1, 1))
        self.assertEqual(course_progress(self.tasks())[0]["percent"], 33)
        self.assertEqual(task_metrics([])["total_percent"], 0)
        page = self.client.get("/planner?view=all").data
        self.assertLess(page.index(b"Overdue</h3>"), page.index(b"Active</h3>"))
        self.assertLess(page.index(b"Active</h3>"), page.index(b"Completed</h3>"))

    def test_escaped_content_and_safe_redirect(self):
        self.create(title='<script>alert("x")</script>', next="https://example.com")
        page = self.client.get("/").data
        self.assertNotIn(b'<script>alert("x")</script>', page)
        self.assertIn(b"&lt;script&gt;", page)
        self.assertTrue(self.create(next="https://example.com").location.startswith("/"))

    def test_missing_tasks_and_bad_status(self):
        self.assertEqual(self.post("/tasks/999/status", status="Done").status_code, 404)
        self.assertEqual(self.post("/tasks/999/delete").status_code, 404)
        self.create()
        self.post("/tasks/1/status", status="bogus", next="planner")
        self.assertEqual(self.tasks()[0]["status"], "To do")
        self.assertIn(b"Invalid task status", self.client.get("/planner").data)

    def test_edit_keeps_status_and_updates_course_progress(self):
        self.create()
        self.post("/tasks/1/status", status="Done")
        response = self.post("/tasks/1/edit?view=done&course=Math", title="New title", course="Math", due_date="2030-01-01", priority="High", notes="Bring notes", next="dashboard")
        self.assertIn("view=done", response.location)
        self.assertIn("course=Math", response.location)
        task = self.tasks()[0]
        self.assertEqual((task["title"], task["course"], task["status"], task["priority"]), ("New title", "Math", "Done", "High"))
        self.assertEqual(course_progress(self.tasks())[0]["percent"], 100)

    def test_invalid_edit_does_not_write(self):
        self.create()
        response = self.post("/tasks/1/edit", title="Changed", course="Math", due_date="bad", next="planner")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'aria-invalid="true"', response.data)
        self.assertIn(b'value="Changed"', response.data)
        self.assertEqual(self.tasks()[0]["title"], "Database assignment")
        self.assertEqual(self.client.get("/tasks/999/edit").status_code, 404)
        self.assertEqual(self.client.post("/tasks/1/edit").status_code, 400)

    def test_date_views_exclude_done_and_respect_boundaries(self):
        today = date.today()
        for title, offset in [("Late", -1), ("Today task", 0), ("Week end", 6), ("Later task", 7)]:
            self.create(title=title, due_date=(today + timedelta(days=offset)).isoformat())
        from unittest.mock import patch
        import app as module
        original = module.render_template
        contexts = []
        def capture(template, **context):
            contexts.append(context)
            return original(template, **context)
        with patch.object(module, "render_template", side_effect=capture):
            self.client.get("/?view=week")
            self.assertEqual([t["title"] for t in contexts[-1]["tasks"]], ["Today task", "Week end"])
            self.client.get("/?view=overdue")
            self.assertEqual([t["title"] for t in contexts[-1]["tasks"]], ["Late"])
            self.post("/tasks/2/status", status="Done")
            self.client.get("/?view=today")
            self.assertEqual(contexts[-1]["tasks"], [])
            self.client.get("/?day=not-a-date")
            self.assertEqual(contexts[-1]["day"], "")

    def test_urgency_and_weekly_load(self):
        self.create(title="Low priority", priority="Low")
        self.create(title="High priority", priority="High")
        self.assertEqual(sorted(self.tasks(), key=urgency_key)[0]["title"], "High priority")
        self.assertEqual(weekly_load(self.tasks(), date.today())[0]["count"], 2)
        self.post("/tasks/2/status", status="Done")
        self.assertEqual(weekly_load(self.tasks(), date.today())[0]["count"], 1)

    def test_calendar_dates_select_tasks_and_plus_opens_creation(self):
        page = self.client.get("/planner").get_data(as_text=True)
        for offset in range(7):
            day = (date.today() + timedelta(days=offset)).isoformat()
            self.assertIn(f'data-open-modal data-task-date="{day}"', page)
            self.assertIn(f'aria-label="Add a task for {day}"', page)
            self.assertIn(f'href="/?view=all&amp;day={day}#task-workspace"', page)
            self.assertIn(f'aria-label="Show tasks for {day}"', page)
        self.assertEqual(page.count('class="day-add-task"'), 7)
        self.client.set_cookie("studyflow_language", "ru")
        self.assertIn("Дата открывает список задач.", self.client.get("/planner").get_data(as_text=True))

    def test_day_view_defaults_new_task_date_but_preserves_validation_draft(self):
        target = (date.today() + timedelta(days=2)).isoformat()
        other = (date.today() + timedelta(days=4)).isoformat()
        page = self.client.get(f"/?day={target}").get_data(as_text=True)
        self.assertIn(f'name="due_date" value="{target}"', page)
        self.post(f"/tasks?day={target}", title="", course="CS", due_date=other)
        page = self.client.get(f"/?day={target}").get_data(as_text=True)
        self.assertIn(f'name="due_date" value="{other}"', page)
        page = self.client.get("/?day=not-a-date").get_data(as_text=True)
        self.assertIn('name="due_date" value=""', page)

    def test_calendar_task_returns_to_planner_with_selected_deadline(self):
        target = (date.today() + timedelta(days=6)).isoformat()
        self.assertEqual(self.create(due_date=target, next="planner").location, "/planner")
        self.assertEqual(self.tasks()[0]["due_date"], target)
        self.assertEqual(weekly_load(self.tasks(), date.today())[6]["count"], 1)

    def test_filters_survive_task_actions(self):
        self.create()
        response = self.post("/tasks/1/status?view=today&course=Computer+Science&q=Database", status="In progress", next="dashboard")
        self.assertIn("view=today", response.location)
        self.assertIn("course=Computer+Science", response.location)
        self.assertIn("q=Database", response.location)
        self.assertEqual(self.client.get("/tasks/1/edit?next=planner").status_code, 200)

    def test_course_attention_uses_overdue_work(self):
        self.create(course="A course")
        self.create(course="Z course", due_date=(date.today() - timedelta(days=1)).isoformat())
        self.assertEqual(course_progress(self.tasks())[0]["name"], "Z course")
        self.assertEqual(course_progress(self.tasks())[0]["overdue"], 1)

    def test_language_switch_preserves_page_and_filters(self):
        response = self.post("/language", language="ru", return_to="/?view=week&course=Math")
        self.assertEqual(response.location, "/?view=week&course=Math")
        self.assertIn("Max-Age=31536000", response.headers["Set-Cookie"])
        for path in ("/", "/planner", "/progress"):
            text = self.client.get(path).get_data(as_text=True)
            self.assertIn('<html lang="ru">', text)
            self.assertIn("Новая задача", text)
        self.post("/language", language="en", return_to="/planner")
        self.assertIn(b'<html lang="en">', self.client.get("/planner").data)
        self.assertIn(b"Tasks &amp; plan", self.client.get("/planner").data)

    def test_unified_creation_reveals_task_even_from_other_date_or_filter(self):
        target = (date.today() + timedelta(days=3)).isoformat()
        response = self.post("/tasks?day=2020-01-01&view=done&q=missing", title="Visible new task", course="CS", due_date=target, workspace="unified")
        self.assertEqual(response.location, f"/?view=all&day={target}#task-workspace")
        self.assertIn(b"Visible new task", self.client.get(response.location).data)

    def test_unified_tabs_preserve_date_and_counts_match_visible_scope(self):
        target = date.today().isoformat()
        self.create(title="Pending", course="Math")
        self.create(title="Finished", course="Math")
        self.create(title="Other course", course="CS")
        self.post("/tasks/2/status", status="Done")
        from unittest.mock import patch
        import app as module
        original = module.render_template
        contexts = []
        def capture(template, **context):
            contexts.append(context)
            return original(template, **context)
        with patch.object(module, "render_template", side_effect=capture):
            response = self.client.get(f"/?day={target}&course=Math&view=done")
        self.assertEqual(contexts[-1]["filter_counts"], {"all": 2, "active": 1, "done": 1, "overdue": 0})
        self.assertEqual([task["title"] for task in contexts[-1]["tasks"]], ["Finished"])
        self.assertIn(f'view=active&amp;day={target}&amp;q=&amp;course=Math#task-workspace'.encode(), response.data)
        self.assertIn(b'aria-current="date"', response.data)
        self.assertIn(b'Save status for Finished', response.data)
        self.assertNotIn(b'data-auto-submit', response.data)

    def test_legacy_planner_uses_unified_navigation_and_calendar(self):
        for path in ("/", "/planner"):
            page = self.client.get(path).get_data(as_text=True)
            nav = page.split('<nav class="side-nav">')[1].split('</nav>')[0]
            self.assertEqual(nav.count('<a '), 2)
            self.assertNotIn('href="/planner"', nav)
            self.assertIn('id="task-workspace"', page)
            self.assertIn('class="day-add-task"', page)

    def test_unified_reschedule_reveals_updated_task_and_keeps_status(self):
        self.create()
        self.post("/tasks/1/status", status="Done")
        response = self.post("/tasks/1/edit?day=2020-01-01&view=active", workspace="unified", title="Moved task", course="New course", due_date="2030-01-02")
        self.assertEqual(response.location, "/?view=all&day=2030-01-02#task-workspace")
        self.assertIn(b"Moved task", self.client.get(response.location).data)
        self.assertEqual(self.tasks()[0]["status"], "Done")

    def test_delete_has_accessible_in_app_confirmation(self):
        self.create()
        page = self.client.get("/").data
        self.assertIn(b'<dialog id="delete-dialog"', page)
        self.assertIn(b'aria-labelledby="delete-heading"', page)
        self.assertIn(b'data-cancel-delete', page)
        self.assertIn(b'data-confirm-delete', page)
        self.assertIn(b'data-confirm=', page)

    def test_language_detection_and_cookie_precedence(self):
        response = self.client.get("/", headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"})
        self.assertIn(b'<html lang="ru">', response.data)
        self.client.set_cookie("studyflow_language", "en")
        response = self.client.get("/", headers={"Accept-Language": "ru"})
        self.assertIn(b'<html lang="en">', response.data)

    def test_russian_forms_keep_database_enums_and_user_text(self):
        self.client.set_cookie("studyflow_language", "ru")
        self.create(title="Prepare essay", course="History", priority="High")
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Prepare essay", page)
        self.assertIn("History", page)
        self.assertIn('value="In progress"', page)
        self.assertIn("В работе", page)
        self.assertIn("Задача добавлена.", page)
        self.post("/tasks/1/status", status="In progress")
        self.assertEqual(self.tasks()[0]["status"], "In progress")
        self.create(due_date="wrong")
        self.assertIn("Выберите корректную дату.", self.client.get("/").get_data(as_text=True))
        self.assertIn("Страница не найдена", self.client.get("/missing").get_data(as_text=True))

    def test_language_endpoint_validates_redirect_and_csrf(self):
        self.assertEqual(self.client.post("/language", data={"language": "ru"}).status_code, 400)
        self.assertEqual(self.post("/language", language="de").status_code, 400)
        for destination in ("https://example.com", "//example.com", "//[", "/\\example.com"):
            self.assertEqual(self.post("/language", language="ru", return_to=destination).location, "/")

    def test_russian_task_plurals(self):
        from flask import g
        from i18n import task_count
        with app.test_request_context():
            g.language = "ru"
            self.assertEqual([task_count(n) for n in (0, 1, 2, 5, 11, 21, 24)],
                             ["0 задач", "1 задача", "2 задачи", "5 задач", "11 задач", "21 задача", "24 задачи"])


if __name__ == "__main__":
    unittest.main()

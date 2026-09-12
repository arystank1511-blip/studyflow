"""Regression checks use a temporary SQLite database, never the personal workspace."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import app, course_progress, get_db, init_db, task_metrics


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
        page = self.client.get("/planner").data
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


if __name__ == "__main__":
    unittest.main()

"""Timetable photo import and the YPT-style study stopwatch. Claude is never called here."""
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import schedule_scan
from app import app, get_db, init_db

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class ScheduleAndStudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = dict(app.config)
        app.config.update(TESTING=True, CLOUD_MODE=False, SESSION_COOKIE_SECURE=False,
                          TRUSTED_HOSTS=["localhost", "127.0.0.1"], DATABASE=str(Path(self.temp.name) / "t.db"))
        init_db()
        self.client = app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as state:
            self.csrf = state["csrf_token"]

    def tearDown(self):
        app.config.clear()
        app.config.update(self.original)
        self.temp.cleanup()

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": self.csrf, **data})

    def tasks(self):
        with app.app_context():
            return [dict(row) for row in get_db().execute("SELECT * FROM tasks ORDER BY due_date, id").fetchall()]

    # ---------- parsing and validation ----------
    def test_clean_rejects_bad_rows_and_normalises(self):
        result = schedule_scan.clean({"classes": [
            {"weekday": 0, "start": "9.00", "end": "10:20", "course": "  Матанализ  ", "kind": "lecture", "room": "305", "teacher": "", "parity": "every"},
            {"weekday": 9, "start": "", "end": "", "course": "Bad day", "kind": "lab", "room": "", "teacher": "", "parity": "every"},
            {"weekday": 2, "start": "25:00", "end": "", "course": "", "kind": "x", "room": "", "teacher": "", "parity": "odd"},
            {"weekday": 1, "start": "8:00", "end": "", "course": "Физика", "kind": "unknown", "room": "", "teacher": "", "parity": "weird"},
        ], "warnings": "row 3 blurry"})
        self.assertEqual([row["course"] for row in result["classes"]], ["Матанализ", "Физика"])
        self.assertEqual(result["classes"][0]["start"], "09:00")
        self.assertEqual((result["classes"][1]["kind"], result["classes"][1]["parity"]), ("other", "every"))

    def test_scan_checks_file_type_size_and_key(self):
        with self.assertRaises(schedule_scan.ScanError):
            schedule_scan.scan(b"%PDF-1.4 not an image")
        with self.assertRaises(schedule_scan.ScanError):
            schedule_scan.scan(JPEG + b"0" * schedule_scan.MAX_IMAGE_BYTES)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(schedule_scan.ScanError):
                schedule_scan.scan(JPEG)

    # ---------- pages and import ----------
    def test_schedule_page_hides_upload_without_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            page = self.client.get("/schedule").data
        self.assertNotIn(b'enctype="multipart/form-data"', page)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            self.assertIn(b'enctype="multipart/form-data"', self.client.get("/schedule").data)

    def test_photo_upload_shows_review_and_limits_size(self):
        found = {"classes": [{"weekday": 0, "start": "09:00", "end": "10:20", "course": "Матанализ", "kind": "lecture",
                              "room": "305", "teacher": "Иванов", "parity": "every"}], "warnings": ""}
        with patch("schedule_scan.scan", return_value=found) as scan:
            response = self.client.post("/schedule/scan", data={"csrf_token": self.csrf, "photo": (io.BytesIO(JPEG), "t.jpg")},
                                        content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Матанализ".encode(), response.data)
        self.assertEqual(scan.call_args.args[0], JPEG)
        # Ordinary forms keep the 32 KB cap; only the photo route may be larger.
        self.assertEqual(self.post("/tasks", title="x" * 40000, course="c", due_date=date.today().isoformat()).status_code, 413)
        with patch("schedule_scan.scan", side_effect=schedule_scan.ScanError("Upload a photo in JPEG, PNG or WebP format.")):
            response = self.client.post("/schedule/scan", data={"csrf_token": self.csrf, "photo": (io.BytesIO(b"nope"), "t.txt")},
                                        content_type="multipart/form-data")
        self.assertEqual(response.location, "/schedule")

    def test_import_creates_tasks_per_week_with_parity_and_no_duplicates(self):
        today = date.today()
        weekday = today.weekday()
        form = {"count": "3", "weeks": "2", "this_week": "odd",
                "include-0": "1", "weekday-0": str(weekday), "start-0": "09:00", "end-0": "10:20", "course-0": "Матанализ",
                "kind-0": "lecture", "room-0": "305", "teacher-0": "Иванов", "parity-0": "every",
                "include-1": "1", "weekday-1": str(weekday), "start-1": "11:00", "end-1": "12:20", "course-1": "Физика",
                "kind-1": "lab", "room-1": "", "teacher-1": "", "parity-1": "even",
                "weekday-2": str(weekday), "course-2": "Unticked", "kind-2": "lecture", "parity-2": "every"}
        response = self.post("/schedule/import", **form)
        self.assertIn("view=week", response.location)
        tasks = self.tasks()
        this_week = today.isoformat()
        next_week = (today + timedelta(days=7)).isoformat()
        self.assertEqual([(t["course"], t["due_date"]) for t in tasks],
                         [("Матанализ", this_week), ("Матанализ", next_week), ("Физика", next_week)])
        self.assertEqual(tasks[0]["title"], "Lecture 09:00")
        self.assertIn("305", tasks[0]["notes"])
        self.assertEqual(tasks[0]["priority"], "Low")
        self.post("/schedule/import", **form)  # Importing the same photo twice adds nothing.
        self.assertEqual(len(self.tasks()), 3)

    def test_import_ignores_tampered_rows(self):
        self.post("/schedule/import", count="999", weeks="52", **{
            "include-0": "1", "weekday-0": "7", "course-0": "Bad", "kind-0": "lecture", "parity-0": "every",
            "include-1": "1", "weekday-1": "0", "course-1": "", "kind-1": "lecture", "parity-1": "every"})
        self.assertEqual(self.tasks(), [])

    # ---------- study stopwatch ----------
    def test_study_sessions_are_validated_and_aggregated(self):
        self.post("/tasks", title="Экзамен по физике", course="Физика", due_date=(date.today() + timedelta(days=5)).isoformat())
        self.assertEqual(json.loads(self.post("/study/sessions", course="Физика", seconds="30").data)["saved"], 0)
        self.assertEqual(self.post("/study/sessions", course="", seconds="600").status_code, 400)
        self.assertEqual(self.post("/study/sessions", course="Физика", seconds=str(13 * 3600)).status_code, 400)
        self.assertEqual(json.loads(self.post("/study/sessions", course="Физика", seconds="1500", started_at="2026-10-13T09:00:00.000Z").data)["saved"], 25)
        self.post("/study/sessions", course="Матанализ", seconds="3600")
        page = self.client.get("/study").get_data(as_text=True)
        self.assertIn('data-base="5100"', page)  # 85 minutes today.
        self.assertIn("D-5", page)
        self.assertIn('data-subject="Физика"', page)
        self.assertIn("heat level-3", page)  # 85 minutes is the "60–120" level.
        self.assertIn("1 days in a row", page)

    # ---------- calendar subscription ----------
    def test_calendar_feed_link_is_secret_shown_once_and_revocable(self):
        self.post("/tasks", title="Курсовая", course="Право", due_date=(date.today() + timedelta(days=3)).isoformat())
        self.post("/tasks", title="Сдано", course="Право", due_date=date.today().isoformat())
        with app.app_context():
            get_db().execute("UPDATE tasks SET status = 'Done' WHERE title = 'Сдано'")
            get_db().commit()
        self.assertIn(b'value="create"', self.client.get("/progress").data)
        self.assertEqual(self.post("/calendar/subscription", action="other").status_code, 400)
        self.post("/calendar/subscription", action="create")
        page = self.client.get("/progress").get_data(as_text=True)
        link = page.split('id="feed-url" value="')[1].split('"')[0]
        self.assertRegex(link, r"^http://localhost/calendar/feed/[A-Za-z0-9_-]{43}\.ics$")
        self.assertIn("calendar.google.com/calendar/render?cid=webcal", page)
        self.assertNotIn(link, self.client.get("/progress").get_data(as_text=True))  # Shown only once.
        with app.app_context():
            stored = get_db().execute("SELECT token_hash FROM calendar_feed").fetchone()["token_hash"]
        self.assertNotIn(stored, link)  # Only the hash is kept.
        feed = app.test_client().get(link.removeprefix("http://localhost"))  # No cookies, like a calendar app.
        self.assertEqual(feed.mimetype, "text/calendar")
        self.assertIn("Курсовая".encode(), feed.data)
        self.assertNotIn("Сдано".encode(), feed.data)
        self.assertIn(b"REFRESH-INTERVAL", feed.data)
        self.assertEqual(app.test_client().get("/calendar/feed/" + "A" * 43 + ".ics").status_code, 404)
        self.post("/calendar/subscription", action="off")
        self.assertEqual(app.test_client().get(link.removeprefix("http://localhost")).status_code, 404)
        self.assertEqual(self.client.post("/calendar/subscription", data={"action": "create"}).status_code, 400)  # CSRF.

    def test_exam_mark_drives_the_countdown(self):
        soon = (date.today() + timedelta(days=4)).isoformat()
        # The form's checkbox wins over the title: an exam without the word, and a "test" that is not one.
        self.post("/tasks", title="Анатомия", course="Био", due_date=soon, exam_field="1", is_exam="1")
        self.post("/tasks", title="Тест драйв", course="Хобби", due_date=soon, exam_field="1")
        self.post("/tasks/quick", text="Экзамен по истории послезавтра")  # Quick add guesses from the words.
        rows = {row["title"]: row["is_exam"] for row in self.tasks()}
        self.assertEqual(rows["Анатомия"], 1)
        self.assertEqual(rows["Тест драйв"], 0)
        self.assertEqual(rows["Экзамен по истории"], 1)
        page = self.client.get("/study").get_data(as_text=True)
        self.assertIn("Анатомия", page)
        self.assertNotIn("Тест драйв", page)
        self.assertIn("priority exam", self.client.get("/?view=all").get_data(as_text=True))
        task_id = next(row["id"] for row in self.tasks() if row["title"] == "Анатомия")
        self.post(f"/tasks/{task_id}/edit", title="Анатомия", course="Био", due_date=soon, priority="Medium", exam_field="1")
        self.assertEqual(next(row["is_exam"] for row in self.tasks() if row["id"] == task_id), 0)

    # ---------- checklists and repeating tasks ----------
    def test_checklist_keeps_ticks_and_validates(self):
        due = date.today().isoformat()
        self.post("/tasks", title="Курсовая", course="Право", due_date=due, steps_field="1", repeat="", steps="План\nВведение\n\nГлава 1")
        task = self.tasks()[0]
        self.assertEqual([step["text"] for step in json.loads(task["steps"])], ["План", "Введение", "Глава 1"])
        self.post(f"/tasks/{task['id']}/steps/1")
        self.assertEqual([step["done"] for step in json.loads(self.tasks()[0]["steps"])], [False, True, False])
        self.assertEqual(self.post(f"/tasks/{task['id']}/steps/7").status_code, 404)
        # Editing keeps the tick of a step whose text did not change.
        self.post(f"/tasks/{task['id']}/edit", title="Курсовая", course="Право", due_date=due, priority="Medium",
                  steps_field="1", repeat="", steps="Введение\nГлава 2")
        self.assertEqual(json.loads(self.tasks()[0]["steps"]), [{"text": "Введение", "done": True}, {"text": "Глава 2", "done": False}])
        response = self.post(f"/tasks/{task['id']}/edit", title="Курсовая", course="Право", due_date=due, priority="Medium",
                             steps_field="1", repeat="", steps="\n".join(["шаг"] * 21))
        self.assertEqual(response.status_code, 400)
        page = self.client.get("/?view=all").get_data(as_text=True)
        self.assertIn("1/2", page)
        self.assertIn("step-toggle is-done", page)
        # A form without the checklist fields (quick add, older pages) leaves them alone.
        self.post(f"/tasks/{task['id']}/edit", title="Курсовая!", course="Право", due_date=due, priority="Medium")
        self.assertEqual(len(json.loads(self.tasks()[0]["steps"])), 2)

    def test_repeating_task_moves_on_and_undo_takes_it_back(self):
        due = "2026-01-31"
        self.post("/tasks", title="Отчёт", course="Работа", due_date=due, steps_field="1", repeat="monthly", steps="Собрать данные")
        task = self.tasks()[0]
        self.post(f"/tasks/{task['id']}/steps/0")
        self.post(f"/tasks/{task['id']}/status", status="Done")
        rows = self.tasks()
        self.assertEqual([(row["due_date"], row["status"], row["repeat"]) for row in rows],
                         [("2026-01-31", "Done", ""), ("2026-02-28", "To do", "monthly")])
        self.assertEqual(json.loads(rows[1]["steps"]), [{"text": "Собрать данные", "done": False}])
        self.client.get("/")  # The undo toast is shown on the next page.
        self.post(f"/tasks/{task['id']}/undo")
        rows = self.tasks()
        self.assertEqual([(row["due_date"], row["status"], row["repeat"]) for row in rows], [("2026-01-31", "To do", "monthly")])
        # Re-opening a finished task does not create another copy.
        self.post(f"/tasks/{task['id']}/status", status="Done")
        self.post(f"/tasks/{task['id']}/status", status="To do")
        self.post(f"/tasks/{task['id']}/status", status="Done")
        self.assertEqual(len(self.tasks()), 2)

    def test_next_due_dates(self):
        from app import next_due
        self.assertEqual(next_due("2026-12-31", "daily"), "2027-01-01")
        self.assertEqual(next_due("2026-10-13", "weekly"), "2026-10-20")
        self.assertEqual(next_due("2028-01-31", "monthly"), "2028-02-29")
        self.assertEqual(next_due("2026-12-15", "monthly"), "2027-01-15")

    def test_study_requires_csrf(self):
        self.assertEqual(self.client.post("/study/sessions", data={"course": "x", "seconds": "600"}).status_code, 400)
        self.assertEqual(self.client.post("/schedule/import", data={"count": "0"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

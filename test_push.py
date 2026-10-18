"""Morning reminders: subscriptions, the daily job and the notification text. No real push is sent."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import push
from app import app, get_db, init_db
from cloud import CloudError

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
P256DH = "B" + "a" * 86
AUTH = "c" * 22
KEYS = {"VAPID_PUBLIC_KEY": "BPublicKey", "VAPID_PRIVATE_KEY": "private", "CRON_SECRET": "cron-secret-value"}


class PushTextTests(unittest.TestCase):
    def test_messages_in_three_languages(self):
        self.assertEqual(push.message("ru", 3, 1, "Экзамен по матанализу", 2),
                         "Сегодня 3 задачи · просрочено: 1 · Экзамен по матанализу через 2 дн.")
        self.assertEqual(push.message("ru", 5, 0, None, None), "Сегодня 5 задач")
        self.assertEqual(push.message("ru", 21, 0, "Физика", 1), "Сегодня 21 задача · Физика завтра")
        self.assertEqual(push.message("kk", 2, 0, "Физика", 0), "Бүгін 2 тапсырма · Физика — бүгін")
        self.assertEqual(push.message("en", 1, 2, "Calculus", 4), "Today: 1 task · 2 overdue · Calculus in 4 days")
        self.assertIsNone(push.message("ru", 0, 0, None, None))

    def test_only_known_push_services_are_accepted(self):
        self.assertTrue(push.valid_subscription(FCM, P256DH, AUTH))
        self.assertTrue(push.valid_subscription("https://web.push.apple.com/QX", P256DH, AUTH))
        for endpoint in ("http://fcm.googleapis.com/x", "https://evil.example/x", "https://fcm.googleapis.com.evil.io/x",
                         "https://169.254.169.254/latest", "https://localhost/x"):
            self.assertFalse(push.valid_subscription(endpoint, P256DH, AUTH), endpoint)
        self.assertFalse(push.valid_subscription(FCM, "short", AUTH))


class LocalPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = dict(app.config)
        app.config.update(TESTING=True, CLOUD_MODE=False, SESSION_COOKIE_SECURE=False,
                          TRUSTED_HOSTS=["localhost", "127.0.0.1"], DATABASE=str(Path(self.temp.name) / "t.db"))
        init_db()
        self.env = patch.dict(os.environ, KEYS)
        self.env.start()
        self.client = app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as state:
            self.csrf = state["csrf_token"]

    def tearDown(self):
        self.env.stop()
        app.config.clear()
        app.config.update(self.original)
        self.temp.cleanup()

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": self.csrf, **data})

    def devices(self):
        with app.app_context():
            return [dict(row) for row in get_db().execute("SELECT * FROM push_subscriptions").fetchall()]

    def test_progress_shows_the_switch_only_with_keys(self):
        self.assertIn(b'data-key="BPublicKey"', self.client.get("/progress").data)
        with patch.dict(os.environ, {"VAPID_PUBLIC_KEY": ""}):
            self.assertNotIn(b"data-push", self.client.get("/progress").data)
            self.assertEqual(self.post("/push/subscribe", endpoint=FCM, p256dh=P256DH, auth=AUTH).status_code, 404)

    def test_subscribe_validates_and_replaces_the_same_device(self):
        self.client.set_cookie("studyflow_tz", "Asia/Almaty")
        self.client.set_cookie("studyflow_language", "kk")
        self.assertEqual(self.post("/push/subscribe", endpoint="https://evil.example/x", p256dh=P256DH, auth=AUTH).status_code, 400)
        self.assertEqual(self.client.post("/push/subscribe", data={"endpoint": FCM, "p256dh": P256DH, "auth": AUTH}).status_code, 400)
        self.assertEqual(self.devices(), [])
        self.assertTrue(self.post("/push/subscribe", endpoint=FCM, p256dh=P256DH, auth=AUTH).json["ok"])
        self.post("/push/subscribe", endpoint=FCM, p256dh=P256DH, auth=AUTH)
        devices = self.devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual((devices[0]["language"], devices[0]["timezone"]), ("kk", "Asia/Almaty"))
        self.post("/push/unsubscribe", endpoint=FCM)
        self.assertEqual(self.devices(), [])

    def test_morning_job_needs_the_secret_and_forgets_dead_devices(self):
        today = date.today().isoformat()
        self.post("/tasks", title="Эссе", course="Философия", due_date=today)
        self.post("/tasks", title="Экзамен по физике", course="Физика", due_date=(date.today() + timedelta(days=3)).isoformat())
        self.post("/push/subscribe", endpoint=FCM, p256dh=P256DH, auth=AUTH)
        self.post("/push/subscribe", endpoint=FCM + "dead", p256dh=P256DH, auth=AUTH)
        self.assertEqual(self.client.get("/cron/reminders").status_code, 404)
        self.assertEqual(self.client.get("/cron/reminders", headers={"Authorization": "Bearer wrong"}).status_code, 404)
        sent = []
        def fake_send(row, body):
            sent.append(body)
            return "gone" if row["endpoint"].endswith("dead") else "sent"
        with patch("push.send", side_effect=fake_send):
            result = self.client.get("/cron/reminders", headers={"Authorization": "Bearer cron-secret-value"}).json
        self.assertEqual((result["sent"], result["gone"]), (1, 1))
        self.assertIn("Экзамен по физике", sent[0])
        self.assertEqual([device["endpoint"] for device in self.devices()], [FCM])


class CloudPushTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(app.config)
        app.config.update(TESTING=True, CLOUD_MODE=True, CLOUD_READY=True, SUPABASE_URL="https://test.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test", SESSION_COOKIE_SECURE=False,
                          TRUSTED_HOSTS=["localhost", "127.0.0.1"])
        self.env = patch.dict(os.environ, KEYS)
        self.env.start()
        self.client = app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as state:
            self.csrf = state["csrf_token"]

    def tearDown(self):
        self.env.stop()
        app.config.clear()
        app.config.update(self.original)

    def test_subscribe_is_scoped_to_the_signed_in_student(self):
        self.client.set_cookie("sf_access", "alice-token")
        calls = []
        def provider(method, path, **kwargs):
            if path == "/auth/v1/user":
                return {"id": "11111111-1111-4111-8111-111111111111", "email": "a@example.com", "email_confirmed_at": "x"}
            calls.append((method, kwargs.get("token"), kwargs.get("params"), kwargs.get("data")))
            return [] if method == "GET" else None
        with patch("cloud.api", side_effect=provider):
            response = self.client.post("/push/subscribe", data={"csrf_token": self.csrf, "endpoint": FCM, "p256dh": P256DH, "auth": AUTH})
        self.assertTrue(response.json["ok"])
        self.assertEqual([call[0] for call in calls], ["DELETE", "GET", "POST"])
        self.assertTrue(all(call[1] == "alice-token" for call in calls))
        self.assertEqual(calls[2][3]["user_id"], "11111111-1111-4111-8111-111111111111")

    def test_morning_job_uses_the_digest_function_without_a_session(self):
        rows = [{"endpoint": FCM, "p256dh": P256DH, "auth": AUTH, "language": "ru", "today": 2, "overdue": 0,
                 "exam_title": None, "exam_days": None},
                {"endpoint": FCM + "x", "p256dh": P256DH, "auth": AUTH, "language": "ru", "today": 0, "overdue": 0,
                 "exam_title": None, "exam_days": None}]
        with patch("cloud.api", return_value=rows) as remote, patch("push.send", return_value="sent") as send:
            result = self.client.get("/cron/reminders", headers={"Authorization": "Bearer cron-secret-value"}).json
        self.assertEqual(remote.call_args.args, ("POST", "/rest/v1/rpc/studyflow_push_digest"))
        self.assertEqual(remote.call_args.kwargs["data"], {"cron_secret": "cron-secret-value"})
        self.assertIsNone(remote.call_args.kwargs.get("token"))
        self.assertEqual((result["sent"], result["quiet"]), (1, 1))
        self.assertEqual(send.call_args.args[1], "Сегодня 2 задачи")
        with patch("cloud.api", side_effect=CloudError(404)):
            self.assertEqual(self.client.get("/cron/reminders", headers={"Authorization": "Bearer cron-secret-value"}).status_code, 503)


if __name__ == "__main__":
    unittest.main()

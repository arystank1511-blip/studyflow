"""Offline security regression tests. Real Supabase RLS needs the separate SQL test."""
import time
import unittest
from unittest.mock import patch

import httpx
from flask import g

from app import app, get_db
from cloud import CloudError, api, cloud_tasks, valid_config

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


class CloudSecurityTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(app.config)
        app.config.update(TESTING=True, CLOUD_MODE=True, CLOUD_READY=True,
                          SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="sb_publishable_test",
                          SESSION_COOKIE_SECURE=False, TRUSTED_HOSTS=["localhost", "127.0.0.1"])
        self.client = app.test_client()
        self.client.get("/login")
        with self.client.session_transaction() as state:
            self.csrf = state["csrf_token"]

    def tearDown(self):
        app.config.clear()
        app.config.update(self.original)

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": self.csrf, **data})

    def sign_in(self, client=None, token="alice-token"):
        (client or self.client).set_cookie("sf_access", token)

    def provider(self, method, path, *, token=None, data=None, params=None, headers=None):
        if path == "/auth/v1/user":
            if token not in {"alice-token", "bob-token"}:
                raise CloudError(401)
            return {"id": ALICE if token == "alice-token" else BOB, "email": "student@example.com", "email_confirmed_at": "2026-01-01"}
        if path == "/rest/v1/studyflow_tasks":
            expected = ALICE if token == "alice-token" else BOB
            self.assertEqual(params["user_id"], "eq." + expected)
            if method == "POST":
                self.assertEqual(data["user_id"], expected)
                self.assertNotIn("id", data)
                self.assertNotIn("created_at", data)
                return [{"id": 2, **data}]
            if token == "alice-token":
                return [{"id": 1, "user_id": ALICE, "title": "Alice private note", "course": "Algorithms",
                         "due_date": "2026-12-31", "priority": "Medium", "status": "To do", "notes": "<script>alert(1)</script>"}]
            return []
        raise AssertionError((method, path))

    def test_anonymous_cannot_read_or_write(self):
        with patch("cloud.api") as remote:
            for path in ("/", "/planner", "/progress", "/tasks/1/edit"):
                self.assertEqual(self.client.get(path).location, "/login")
            for path in ("/tasks", "/tasks/1/delete", "/tasks/1/status", "/tasks/1/edit"):
                self.client.get("/login")
                with self.client.session_transaction() as state:
                    self.csrf = state["csrf_token"]
                self.assertEqual(self.post(path).location, "/login")
            remote.assert_not_called()

    def test_all_post_forms_require_csrf(self):
        for path in ("/login", "/login/verify", "/logout", "/language", "/tasks", "/tasks/1/edit", "/tasks/1/status", "/tasks/1/delete"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 400)
                self.assertEqual(self.client.post(path, data={"csrf_token": "подделка"}).status_code, 400)

    def test_tenant_filters_and_foreign_task_ids(self):
        self.sign_in()
        with patch("cloud.api", side_effect=self.provider):
            for path in ("/", "/planner", "/progress", "/tasks/1/edit"):
                self.assertEqual(self.client.get(path).status_code, 200)
            page = self.client.get("/").data
            self.assertIn(b"Alice private note", page)
            self.assertNotIn(b"<script>alert(1)</script>", page)
            self.assertIn(b"&lt;script&gt;", page)
            self.post("/tasks", title="Test", course="CS", due_date="2026-12-31", user_id=BOB, id="1", created_at="spoofed")
            self.sign_in(token="bob-token")
            for path in ("/", "/planner", "/progress"):
                self.assertNotIn(b"Alice private note", self.client.get(path).data)
            self.assertEqual(self.client.get("/tasks/1/edit").status_code, 404)
            for path in ("/tasks/1/edit", "/tasks/1/delete", "/tasks/1/status"):
                response = self.post(path, title="Hijack", course="CS", due_date="2026-12-31", status="Done")
                self.assertEqual(response.status_code, 404)

    def test_unverified_and_forged_identity_rejected(self):
        self.sign_in(token="forged")
        with patch("cloud.api", side_effect=self.provider):
            self.assertEqual(self.client.get("/").location, "/login")
        self.sign_in()
        with patch("cloud.api", return_value={"id": ALICE, "email": "unverified@example.com"}):
            self.assertEqual(self.client.get("/").location, "/login")

    def test_login_verify_rotates_csrf_and_hides_tokens(self):
        with patch("cloud.api", return_value={}):
            self.assertEqual(self.post("/login", email="student@example.com").location, "/login/verify")
        with self.client.session_transaction() as state:
            state["task_draft"] = {"title": "Old account data"}
        app.config["SESSION_COOKIE_SECURE"] = True
        with patch("cloud.api", side_effect=[{"access_token": "alice-token", "refresh_token": "private-refresh"},
                                             {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "yes"}]):
            response = self.post("/login/verify", code="123456")
        self.assertEqual(response.location, "/")
        self.assertNotIn(b"private-refresh", response.data)
        auth_cookies = [item for item in response.headers.getlist("Set-Cookie") if item.startswith("sf_")]
        self.assertEqual(len(auth_cookies), 2)
        for cookie in auth_cookies:
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=Lax", cookie)
        with self.client.session_transaction() as state:
            self.assertNotIn("task_draft", state)
            self.assertNotIn("otp_email", state)
            self.assertNotEqual(state["csrf_token"], self.csrf)

    def test_otp_validation_cooldown_and_generic_error(self):
        with patch("cloud.api") as remote:
            self.assertEqual(self.post("/login", email="invalid").status_code, 400)
            remote.assert_not_called()
        with patch("cloud.api", return_value={}):
            self.post("/login", email="student@example.com")
        with patch("cloud.api") as remote:
            self.assertEqual(self.post("/login", email="another@example.com").status_code, 400)
            self.assertEqual(self.post("/login/verify", code="abc").status_code, 400)
            remote.assert_not_called()
        with patch("cloud.api", side_effect=CloudError(422)):
            self.assertIn(b"invalid or expired", self.post("/login/verify", code="123456").data)
        with self.client.session_transaction() as state:
            state["otp_sent_at"] = time.time() - 601
        self.assertEqual(self.client.get("/login/verify").location, "/login")

    def test_refresh_and_logout(self):
        self.sign_in(token="expired")
        self.client.set_cookie("sf_refresh", "old-refresh")
        user = {"id": ALICE, "email_confirmed_at": "yes"}
        with patch("cloud.api", side_effect=[CloudError(401), {"access_token": "fresh", "refresh_token": "rotated"}, user, []]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("sf_refresh=rotated" in c for c in response.headers.getlist("Set-Cookie")))
        with patch("cloud.api", side_effect=[user, None]) as remote:
            response = self.post("/logout")
            self.assertEqual(remote.call_args.args[:2], ("POST", "/auth/v1/logout"))
        self.assertEqual(response.location, "/login")
        self.assertTrue(any("sf_refresh=" in c and "Max-Age=0" in c for c in response.headers.getlist("Set-Cookie")))

    def test_outage_fails_closed_and_no_sqlite_fallback(self):
        self.sign_in()
        with patch("cloud.api", side_effect=CloudError(503)), patch("app.get_db") as local:
            self.assertEqual(self.client.get("/").status_code, 503)
            local.assert_not_called()
        app.config["CLOUD_READY"] = False
        self.assertEqual(self.client.get("/").status_code, 503)
        self.assertEqual(self.client.get("/health").status_code, 503)
        self.assertEqual(self.post("/login", email="student@example.com").status_code, 503)
        with app.app_context():
            with self.assertRaises(RuntimeError):
                get_db()

    def test_headers_host_and_sensitive_paths(self):
        response = self.client.get("/login")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("script-src-attr 'none'", response.headers["Content-Security-Policy"])
        for path in ("/.env", "/studyflow.db", "/.git/config", "/static/../.env", "/supabase/schema.sql"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/", headers={"Host": "attacker.example"}).status_code, 400)

    def test_cloud_config_rejects_secret_keys_and_ssrf_urls(self):
        self.assertTrue(valid_config("https://test.supabase.co", "sb_publishable_example"))
        for key in ("sb_secret_dangerous", "", "service_role"):
            self.assertFalse(valid_config("https://test.supabase.co", key))
        for url in ("http://test.supabase.co", "https://evil.com", "http://127.0.0.1", "https://test.supabase.co@evil.com", "https://test.supabase.co/path"):
            self.assertFalse(valid_config(url, "sb_publishable_example"))

    def test_http_transport_no_redirects_and_no_error_body_leak(self):
        response = httpx.Response(400, text="SECRET_TOKEN database internals", request=httpx.Request("GET", "https://test.supabase.co"))
        with app.app_context(), patch("cloud.httpx.request", return_value=response) as transport:
            with self.assertRaises(CloudError) as caught:
                api("GET", "/auth/v1/user", token="private-token")
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertFalse(transport.call_args.kwargs["follow_redirects"])
            self.assertEqual(transport.call_args.kwargs["headers"]["Authorization"], "Bearer private-token")

    def test_local_mode_rejects_external_requests(self):
        app.config.update(CLOUD_MODE=False, TESTING=False)
        self.assertEqual(self.client.get("/", environ_overrides={"REMOTE_ADDR": "198.51.100.5"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()

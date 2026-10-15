"""Offline security regression tests. Real Supabase RLS needs the separate SQL test."""
import base64
import json
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
        if path == "/rest/v1/studyflow_calendar_feeds" and method == "GET":
            return []
        raise AssertionError((method, path))

    def test_account_deletion_needs_the_email_and_signs_out(self):
        with patch("cloud.api") as remote:
            self.assertEqual(self.client.get("/account/delete").location, "/login")
            remote.assert_not_called()
        self.sign_in()
        calls = []
        def provider(method, path, **kwargs):
            if path == "/auth/v1/user":
                return self.provider(method, path, **kwargs)
            calls.append((method, path, kwargs.get("token")))
            return None
        with patch("cloud.api", side_effect=provider):
            page = self.client.get("/account/delete")
            self.assertIn(b"student@example.com", page.data)
            self.assertEqual(self.post("/account/delete", email="someone@else.com").status_code, 400)
            self.assertEqual(calls, [])
            self.assertEqual(self.client.post("/account/delete", data={"email": "student@example.com"}).status_code, 400)  # CSRF.
            with self.client.session_transaction() as state:
                self.csrf = state["csrf_token"]
            response = self.post("/account/delete", email=" Student@Example.com ")
        self.assertEqual(calls, [("POST", "/rest/v1/rpc/studyflow_delete_account", "alice-token")])
        self.assertEqual(response.location, "/login")
        self.assertIn("sf_access=;", response.headers.get("Set-Cookie", "") + ";".join(response.headers.getlist("Set-Cookie")))
        self.sign_in()
        with patch("cloud.api", side_effect=lambda method, path, **kw: self.provider(method, path, **kw) if path == "/auth/v1/user" else (_ for _ in ()).throw(CloudError(404))):
            with self.client.session_transaction() as state:
                state["csrf_token"] = self.csrf
            response = self.post("/account/delete", email="student@example.com")
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"delete_account.sql", response.data)

    def test_study_groups_use_caller_scoped_functions(self):
        self.sign_in()
        calls = []
        groups = [{"id": 7, "name": "IT-21", "invite_code": "ABCD2345", "nickname": "alice", "members": 2}]
        board = [{"nickname": "<b>bob</b>", "me": False, "today_minutes": 30, "week_minutes": 300},
                 {"nickname": "alice", "me": True, "today_minutes": 0, "week_minutes": 120}]
        def provider(method, path, **kwargs):
            if path == "/auth/v1/user":
                return self.provider(method, path, **kwargs)
            calls.append((path.rsplit("/", 1)[1], kwargs.get("data"), kwargs.get("token")))
            name = path.rsplit("/", 1)[1]
            return {"studyflow_my_groups": groups, "studyflow_group_board": board, "studyflow_join_group": None,
                    "studyflow_create_group": {"id": 9, "invite_code": "X"}}.get(name)
        with patch("cloud.api", side_effect=provider):
            page = self.client.get("/study/groups").get_data(as_text=True)
            self.assertIn("&lt;b&gt;bob&lt;/b&gt;", page)  # Nicknames are escaped.
            self.assertIn("ABCD2345", page)
            self.assertIn("is-me", page)
            self.assertNotIn("student@example.com</span>" + "x", page)
            board_call = next(call for call in calls if call[0] == "studyflow_group_board")
            self.assertEqual(board_call[1]["gid"], 7)
            self.assertEqual(board_call[2], "alice-token")
            # Unknown codes, bad input and CSRF never reach a group change.
            self.assertIn("/study/groups", self.post("/study/groups/join", code="ZZZZ9999", nickname="al").location)
            calls.clear()
            self.post("/study/groups/join", code="bad", nickname="al")
            self.post("/study/groups/create", name="  ", nickname="al")
            self.client.post("/study/groups/create", data={"name": "X", "nickname": "al"})
            self.assertEqual(calls, [])
            response = self.post("/study/groups/create", name="  Physics   club ", nickname="al")
            self.assertEqual(response.location, "/study/groups?group=9")
            created = calls[-1][1]
            self.assertEqual(created["group_name"], "Physics club")
            self.assertRegex(created["code"], r"^[A-HJ-NP-Z2-9]{8}$")
        with patch("cloud.api", side_effect=lambda method, path, **kw: self.provider(method, path, **kw) if path == "/auth/v1/user" else (_ for _ in ()).throw(CloudError(404))):
            self.assertIn(b"study_groups.sql", self.client.get("/study/groups").data)

    def test_welcome_page_and_robots_are_public(self):
        with patch("cloud.api") as remote:
            page = self.client.get("/welcome", headers={"Accept-Language": "ru"})
            self.assertEqual(page.status_code, 200)
            text = page.get_data(as_text=True)
            self.assertIn("Начать бесплатно", text)
            self.assertIn('href="/register"', text)
            self.assertIn('property="og:image"', text)
            robots = self.client.get("/robots.txt").get_data(as_text=True)
            self.assertIn("Allow: /welcome", robots)
            self.assertIn("Disallow: /", robots)
            remote.assert_not_called()
        # An expired session still goes to sign-in, not to the welcome page.
        self.client.set_cookie("sf_refresh", "old")
        with patch("cloud.api", side_effect=CloudError(401)):
            self.assertEqual(self.client.get("/").location, "/login")

    def test_confirmed_token_is_not_rechecked_on_every_page(self):
        def token(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return "h." + payload + ".s"
        fresh = token(int(time.time()) + 3600)
        user = {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "now"}
        self.client.set_cookie("sf_access", fresh)
        with patch("cloud.api", return_value=user) as remote, patch("app.cloud_tasks", return_value=[]):
            self.client.get("/focus")
            self.client.get("/focus")
            self.assertEqual([c.args for c in remote.call_args_list], [("GET", "/auth/v1/user")])
            # A different token is never served from the remembered check.
            self.client.set_cookie("sf_access", token(int(time.time()) + 3601))
            self.client.get("/focus")
            self.assertEqual(remote.call_count, 2)
            # After the recheck interval Supabase is asked again.
            with patch("cloud.time.time", return_value=time.time() + 301):
                self.client.get("/focus")
            self.assertEqual(remote.call_count, 3)
        # A token about to expire is not trusted from memory; it goes to Supabase (and refresh) instead.
        self.client.set_cookie("sf_access", token(int(time.time()) + 30))
        with patch("cloud.api", side_effect=CloudError(401)):
            self.assertEqual(self.client.get("/focus").location, "/login")

    def test_calendar_feed_uses_hash_and_anonymous_rpc(self):
        token = "a" * 43
        digest = __import__("hashlib").sha256(token.encode()).hexdigest()
        row = {"id": 5, "title": "Essay", "course": "Law", "due_date": "2026-12-01", "priority": "High", "notes": ""}
        with patch("cloud.api", return_value=[row]) as remote:
            response = app.test_client().get(f"/calendar/feed/{token}.ics")
        self.assertIn(b"SUMMARY:Essay", response.data)
        self.assertEqual(remote.call_args.args, ("POST", "/rest/v1/rpc/studyflow_calendar_feed"))
        self.assertEqual(remote.call_args.kwargs["data"], {"feed_hash": digest})
        self.assertIsNone(remote.call_args.kwargs.get("token"))
        with patch("cloud.api", return_value=None):
            self.assertEqual(app.test_client().get(f"/calendar/feed/{token}.ics").status_code, 404)
        self.sign_in()
        calls = []
        def provider(method, path, **kwargs):
            if path == "/auth/v1/user":
                return self.provider(method, path, **kwargs)
            calls.append((method, path, kwargs.get("params"), kwargs.get("data")))
            return [] if method == "GET" else None
        with patch("cloud.api", side_effect=provider), patch("app.cloud_tasks", return_value=[]):
            self.post("/calendar/subscription", action="create")
        self.assertEqual([c[0] for c in calls], ["DELETE", "POST"])
        self.assertEqual(calls[0][2], {"user_id": "eq." + ALICE})
        self.assertEqual(calls[1][3]["user_id"], ALICE)
        self.assertRegex(calls[1][3]["token_hash"], r"^[0-9a-f]{64}$")

    def test_anonymous_cannot_read_or_write(self):
        with patch("cloud.api") as remote:
            self.assertEqual(self.client.get("/").location, "/welcome")  # First-time visitors see what StudyFlow is.
            for path in ("/planner", "/progress", "/focus", "/study", "/schedule", "/tasks/quick/preview", "/calendar.ics", "/export.json", "/tasks/1/edit"):
                self.assertEqual(self.client.get(path).location, "/login")
            for path in ("/tasks", "/tasks/quick", "/schedule/scan", "/schedule/import", "/study/sessions", "/tasks/reschedule-overdue", "/tasks/1/delete", "/tasks/1/status", "/tasks/1/edit"):
                self.client.get("/login")
                with self.client.session_transaction() as state:
                    self.csrf = state["csrf_token"]
                self.assertEqual(self.post(path).location, "/login")
            remote.assert_not_called()

    def test_all_post_forms_require_csrf(self):
        for path in ("/login", "/register", "/login/code", "/login/verify", "/password/forgot", "/password/reset", "/logout",
                     "/language", "/tasks", "/tasks/quick", "/schedule/scan", "/schedule/import", "/study/sessions", "/tasks/reschedule-overdue", "/tasks/1/edit", "/tasks/1/status", "/tasks/1/delete"):
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

    def test_password_login_rotates_csrf_and_hides_secrets(self):
        with self.client.session_transaction() as state:
            state["task_draft"] = {"title": "Old account data"}
        app.config["SESSION_COOKIE_SECURE"] = True
        with patch("cloud.api", side_effect=[{"access_token": "alice-token", "refresh_token": "private-refresh"},
                                             {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "yes"}]) as remote:
            response = self.post("/login", email="Student@example.com", password="long-test-password")
            self.assertEqual(remote.call_args_list[0].args, ("POST", "/auth/v1/token"))
            self.assertEqual(remote.call_args_list[0].kwargs["params"], {"grant_type": "password"})
        self.assertEqual(response.location, "/")
        self.assertNotIn(b"private-refresh", response.data)
        self.assertNotIn(b"long-test-password", response.data)
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
            self.assertNotIn("long-test-password", str(dict(state)))

    def test_password_validation_and_generic_errors(self):
        with patch("cloud.api") as remote:
            self.assertEqual(self.post("/login", email="invalid").status_code, 400)
            for password in ("", "short", "a" * 73, "я" * 37):
                self.assertEqual(self.post("/register", email="student@example.com", password=password, password_confirm=password).status_code, 400)
            self.assertEqual(self.post("/register", email="student@example.com", password="long-test-password", password_confirm="not-the-same-password").status_code, 400)
            remote.assert_not_called()
        with patch("cloud.api", side_effect=CloudError(422)):
            response = self.post("/login", email="student@example.com", password="secret-test-value")
            self.assertIn(b"Email or password is incorrect", response.data)
            self.assertNotIn(b"secret-test-value", response.data)
        with patch("cloud.api", side_effect=CloudError(429)):
            self.assertEqual(self.post("/login", email="student@example.com", password="secret-test-value").status_code, 429)
        with patch("cloud.api", side_effect=CloudError(503)):
            self.assertEqual(self.post("/register", email="student@example.com", password="secret-test-value", password_confirm="secret-test-value").status_code, 503)

    def test_signup_signs_in_directly_when_provider_returns_a_session(self):
        with patch("cloud.api", side_effect=[{"access_token": "alice-token", "refresh_token": "private-refresh"},
                                             {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "auto-confirmed"}]) as remote:
            response = self.post("/register", email=" Student@example.com ", password="test phrase with spaces", password_confirm="test phrase with spaces", user_id=BOB)
        self.assertEqual(response.location, "/")
        self.assertEqual(remote.call_args_list[0].args, ("POST", "/auth/v1/signup"))
        self.assertEqual(remote.call_args_list[0].kwargs["data"], {"email": "student@example.com", "password": "test phrase with spaces"})
        self.assertEqual(len(remote.call_args_list), 2)
        with self.client.session_transaction() as state:
            self.assertNotIn("test phrase with spaces", str(dict(state)))

    def test_signup_with_confirmation_asks_for_code_without_revealing_accounts(self):
        def no_auth_cookies(response):
            return not any(c.startswith("sf_") for c in response.headers.getlist("Set-Cookie"))
        # Confirmation on: Supabase answers with a user and no session, for new and existing addresses alike.
        for result in ({"id": ALICE, "email": "student@example.com"}, {}, None):
            with patch("cloud.api", return_value=result):
                response = self.post("/register", email="student@example.com", password="secret-test-value", password_confirm="secret-test-value")
            self.assertEqual(response.location, "/login/verify")
            self.assertTrue(no_auth_cookies(response))
            with self.client.session_transaction() as state:
                self.assertEqual(state["otp_email"], "student@example.com")
                self.assertNotIn("secret-test-value", str(dict(state)))
        with patch("cloud.api", return_value={"access_token": "alice-token"}):
            response = self.post("/register", email="student@example.com", password="secret-test-value", password_confirm="secret-test-value")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(no_auth_cookies(response))
        with patch("cloud.api", side_effect=CloudError(422)):
            self.assertEqual(self.post("/register", email="student@example.com", password="secret-test-value", password_confirm="secret-test-value").status_code, 400)

    def test_auth_pages_offer_codes_and_recovery(self):
        for route in ("/login", "/register"):
            page = self.client.get(route).data
            self.assertIn(b'type="password"', page)
            self.assertIn(b'href="/login/code"', page)
            self.assertNotIn(b"Email ownership is not verified", page)
            self.assertNotIn(b"/auth/google", page)
            self.assertIn("Пароль".encode(), self.client.get(route, headers={"Accept-Language": "ru"}).data)
        self.assertIn(b'href="/password/forgot"', self.client.get("/login").data)
        app.config["GOOGLE_LOGIN"] = True
        self.assertIn(b'href="/auth/google"', self.client.get("/login").data)
        with patch("cloud.api") as remote:
            self.assertEqual(self.client.get("/login/verify").location, "/login/code")
            self.assertEqual(self.client.get("/password/reset").location, "/password/forgot")
            remote.assert_not_called()

    def test_turnstile_token_is_required_and_forwarded(self):
        self.assertNotIn(b"challenges.cloudflare.com", self.client.get("/login").headers["Content-Security-Policy"].encode())
        app.config["TURNSTILE_SITE_KEY"] = "0x4AAA-site"
        response = self.client.get("/login")
        self.assertIn(b'data-sitekey="0x4AAA-site"', response.data)
        self.assertIn("https://challenges.cloudflare.com", response.headers["Content-Security-Policy"])
        with patch("cloud.api") as remote:
            for path, form in (("/login", {"password": "whatever-password"}), ("/login/code", {}),
                               ("/password/forgot", {}),
                               ("/register", {"password": "new-long-password!", "password_confirm": "new-long-password!"})):
                response = self.post(path, email="student@example.com", **form)
                self.assertEqual(response.status_code, 400, path)
                self.assertIn(b"not a robot", response.data)
            remote.assert_not_called()
        with patch("cloud.api", return_value={}) as remote:
            self.post("/login/code", email="student@example.com", **{"cf-turnstile-response": "tok"})
            self.assertEqual(remote.call_args.kwargs["data"]["gotrue_meta_security"], {"captcha_token": "tok"})

    def test_email_code_sign_in(self):
        with patch("cloud.api", return_value={}) as remote:
            self.assertEqual(self.post("/login/code", email=" Student@Example.com ").location, "/login/verify")
            self.assertEqual(remote.call_args.args, ("POST", "/auth/v1/otp"))
            self.assertEqual(remote.call_args.kwargs["data"], {"email": "student@example.com", "create_user": True})
            # A second request within a minute is refused before reaching the provider.
            self.assertEqual(self.post("/login/code", email="student@example.com").status_code, 429)
            self.assertEqual(remote.call_count, 1)
        self.assertIn(b"student@example.com", self.client.get("/login/verify").data)
        with patch("cloud.api") as remote:
            for code in ("", "12345", "abcdef", "1" * 11):
                self.assertEqual(self.post("/login/verify", code=code).status_code, 400)
            remote.assert_not_called()
        with patch("cloud.api", side_effect=CloudError(403)):
            self.assertIn(b"invalid or expired", self.post("/login/verify", code="123456").data)
        with patch("cloud.api", side_effect=[{"access_token": "alice-token", "refresh_token": "r"},
                                             {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "now"}]) as remote:
            response = self.post("/login/verify", code="123 456")
            self.assertEqual(remote.call_args_list[0].kwargs["data"], {"type": "email", "email": "student@example.com", "token": "123456"})
        self.assertEqual(response.location, "/")
        with self.client.session_transaction() as state:
            self.assertNotIn("otp_email", state)
            self.assertNotEqual(state["csrf_token"], self.csrf)

    def test_password_reset_is_generic_and_needs_the_code(self):
        for failure in (None, CloudError(404), CloudError(422)):
            with patch("cloud.api", side_effect=failure, return_value=None):
                response = self.post("/password/forgot", email="unknown@example.com")
            self.assertEqual(response.location, "/password/reset")
        with patch("cloud.api", side_effect=CloudError(429)):
            self.assertEqual(self.post("/password/forgot", email="unknown@example.com").status_code, 429)
        with patch("cloud.api") as remote:
            self.assertEqual(self.post("/password/reset", code="123456", password="short", password_confirm="short").status_code, 400)
            self.assertEqual(self.post("/password/reset", code="12", password="new-long-password!", password_confirm="new-long-password!").status_code, 400)
            remote.assert_not_called()
        with patch("cloud.api", side_effect=CloudError(403)):
            self.assertEqual(self.post("/password/reset", code="123456", password="new-long-password!", password_confirm="new-long-password!").status_code, 400)
        calls = [{"access_token": "alice-token", "refresh_token": "r"}, {"id": ALICE},
                 {"id": ALICE, "email": "x@example.com", "email_confirmed_at": "now"}]
        with patch("cloud.api", side_effect=calls) as remote:
            response = self.post("/password/reset", code="654321", password="new-long-password!", password_confirm="new-long-password!")
            self.assertEqual(remote.call_args_list[0].kwargs["data"], {"type": "recovery", "email": "unknown@example.com", "token": "654321"})
            self.assertEqual(remote.call_args_list[1].args, ("PUT", "/auth/v1/user"))
            self.assertEqual(remote.call_args_list[1].kwargs["token"], "alice-token")
        self.assertEqual(response.location, "/")
        self.assertNotIn(b"new-long-password!", response.data)

    def test_google_sign_in_uses_pkce_bound_to_the_session(self):
        self.assertEqual(self.client.get("/auth/google").status_code, 404)
        app.config["GOOGLE_LOGIN"] = True
        response = self.client.get("/auth/google")
        self.assertTrue(response.location.startswith("https://test.supabase.co/auth/v1/authorize?"))
        self.assertIn("provider=google", response.location)
        self.assertIn("code_challenge_method=s256", response.location)
        with self.client.session_transaction() as state:
            verifier = state["pkce_verifier"]
        self.assertNotIn(verifier, response.location)
        with patch("cloud.api") as remote:
            self.assertEqual(app.test_client().get("/auth/callback?code=stolen").location, "/login")
            remote.assert_not_called()
        with patch("cloud.api", side_effect=[{"access_token": "alice-token", "refresh_token": "r"},
                                             {"id": ALICE, "email": "student@example.com", "email_confirmed_at": "now"}]) as remote:
            response = self.client.get("/auth/callback?code=abc")
            self.assertEqual(remote.call_args_list[0].kwargs["params"], {"grant_type": "pkce"})
            self.assertEqual(remote.call_args_list[0].kwargs["data"], {"auth_code": "abc", "code_verifier": verifier})
        self.assertEqual(response.location, "/")
        with patch("cloud.api") as remote:
            self.assertEqual(self.client.get("/auth/callback?code=abc").location, "/login")  # The verifier is single-use.
            remote.assert_not_called()

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

    def test_malformed_cloud_configuration_fails_closed(self):
        url = "https://test.supabase.co"
        for payload in ([], None, 1, "anon", {"role": "service_role"}):
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            with self.subTest(payload=payload):
                self.assertFalse(valid_config(url, f"header.{encoded}.signature"))
        for key in (None, 123, [], "sb_publishable_", "header.e30.signature", "header.%.signature"):
            with self.subTest(key=key):
                self.assertFalse(valid_config(url, key))
        for value in (None, 123, [], "https://.supabase.co"):
            with self.subTest(url=value):
                self.assertFalse(valid_config(value, "sb_publishable_test"))
        encoded = base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("=")
        self.assertTrue(valid_config(url, f"header.{encoded}.signature"))

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

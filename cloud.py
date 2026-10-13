"""Supabase Auth + PostgREST. Never use a service-role key for user requests."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for


class CloudError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__("Cloud request failed")  # No response bodies / credentials in logs.


def valid_config(url, key):
    if not isinstance(url, str) or not isinstance(key, str):
        return False
    try:
        parsed = urlsplit(url)
        valid_url = (parsed.scheme == "https" and parsed.hostname and
                     re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname) and not parsed.username and
                     not parsed.password and parsed.port in (None, 443) and parsed.path in ("", "/") and
                     not parsed.query and not parsed.fragment)
        if key.startswith("sb_publishable_"):
            valid_key = len(key) > len("sb_publishable_")
        else:
            parts = key.split(".")
            if len(parts) != 3 or not all(parts):
                return False
            payload = parts[1]
            decoded = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
            # This only checks the configured key type; Supabase validates the key.
            valid_key = isinstance(decoded, dict) and decoded.get("role") == "anon"
        return bool(valid_url and valid_key)
    except (ValueError, IndexError, TypeError, UnicodeDecodeError):
        return False


def api(method, path, *, token=None, data=None, params=None, headers=None):
    config = current_app.config
    if not config["CLOUD_READY"]:
        raise CloudError()
    request_headers = {"apikey": config["SUPABASE_PUBLISHABLE_KEY"], **(headers or {})}
    if token:
        request_headers["Authorization"] = "Bearer " + token
    try:
        response = httpx.request(method, config["SUPABASE_URL"].rstrip("/") + path,
                                 headers=request_headers, json=data, params=params,
                                 timeout=httpx.Timeout(12, connect=5), follow_redirects=False)
    except httpx.RequestError:
        raise CloudError() from None
    if not 200 <= response.status_code < 300:
        raise CloudError(response.status_code)
    try:
        return response.json() if response.content else None
    except ValueError:
        raise CloudError() from None


def authenticated_user(token):
    user = api("GET", "/auth/v1/user", token=token)
    # Supabase sets this once the mailbox owner has entered a code or followed a link.
    if not isinstance(user, dict) or not user.get("email_confirmed_at"):
        raise CloudError(401)
    try:
        UUID(user["id"])
    except (KeyError, TypeError, ValueError):
        raise CloudError(401) from None
    return {"id": user["id"], "email": user.get("email", "")}


# How long a confirmed access token is trusted without asking Supabase again. Every database
# request still sends the token to Supabase, which checks its signature and expiry under RLS.
RECHECK_SECONDS = 300


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_expiry(token):
    """The exp claim of a token Supabase has already accepted; 0 when it cannot be read."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError, UnicodeDecodeError):
        return 0


def remember_user(token, user):
    expires = token_expiry(token)
    if expires:
        # The Flask session is signed, so a visitor cannot plant a user here.
        session["auth_check"] = {"token": token_digest(token), "user": user, "at": int(time.time()), "exp": expires}


def remembered_user(token):
    entry = session.get("auth_check")
    now = time.time()
    if (not isinstance(entry, dict) or not isinstance(entry.get("user"), dict)
            or not secrets.compare_digest(str(entry.get("token", "")), token_digest(token))):
        return None
    if now - entry.get("at", 0) >= RECHECK_SECONDS or entry.get("exp", 0) - now < 60:
        return None  # Re-check, or refresh shortly before the token expires.
    return entry["user"]


def accept_tokens(result):
    if not isinstance(result, dict) or not result.get("access_token") or not result.get("refresh_token"):
        raise CloudError(401)
    g.access_token = result["access_token"]
    g.user = authenticated_user(g.access_token)
    g.new_tokens = (result["access_token"], result["refresh_token"])
    remember_user(g.access_token, g.user)


def load_identity():
    g.user = None
    if not current_app.config["CLOUD_MODE"]:
        # An old local workspace must never become a public, unauthenticated database.
        if not current_app.testing and (request.remote_addr not in {"127.0.0.1", "::1"} or
                                        request.host.split(":")[0] not in {"127.0.0.1", "localhost"}):
            abort(403)
        return
    public = {"static", "login", "register", "login_code", "verify_login", "forgot_password", "reset_password",
              "google_login", "auth_callback", "change_language", "health", "service_worker"}
    if request.endpoint in public or request.endpoint is None:
        return
    if not current_app.config["CLOUD_READY"]:
        abort(503)
    token = request.cookies.get("sf_access")
    refresh = request.cookies.get("sf_refresh")
    try:
        if not token:
            raise CloudError(401)
        g.user = remembered_user(token)
        if g.user is None:
            g.user = authenticated_user(token)
            remember_user(token, g.user)
        g.access_token = token
    except CloudError as error:
        if error.status not in (400, 401, 403):
            abort(503)
        if refresh:
            try:
                result = api("POST", "/auth/v1/token", params={"grant_type": "refresh_token"},
                             data={"refresh_token": refresh})
                accept_tokens(result)
                return
            except CloudError as refresh_error:
                if refresh_error.status not in (400, 401, 403, 422):
                    abort(503)
        g.clear_auth = True
        session.clear()
        return redirect(url_for("login"))


def secure_response(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    captcha = " https://challenges.cloudflare.com" if current_app.config.get("TURNSTILE_SITE_KEY") else ""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        f"form-action 'self'; script-src 'self'{captcha}; script-src-attr 'none'; frame-src 'self'{captcha}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'{captcha}"
    )
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Vercel-CDN-Cache-Control"] = "no-store"
        response.headers["CDN-Cache-Control"] = "no-store"
        response.vary.add("Cookie")
    secure = current_app.config["SESSION_COOKIE_SECURE"]
    if secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    for name, value in zip(("sf_access", "sf_refresh"), getattr(g, "new_tokens", ())):
        response.set_cookie(name, value, max_age=7 * 86400, httponly=True, secure=secure, samesite="Lax", path="/")
    if getattr(g, "clear_auth", False):
        for name in ("sf_access", "sf_refresh"):
            response.delete_cookie(name, secure=secure, httponly=True, samesite="Lax", path="/")
    return response


def valid_email(email):
    return len(email) <= 254 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) is not None


def password_problem(password, confirm):
    if not password or len(password) < 15 or len(password.encode("utf-8")) > 72:
        return "Use at least 15 characters and at most 72 UTF-8 bytes."
    if not secrets.compare_digest(password.encode("utf-8"), confirm.encode("utf-8")):
        return "Passwords do not match."
    return None


def auth_failure(error, rejected):
    """Map a provider error to a generic message and status; never echo provider details."""
    if error.status == 429:
        return "Too many attempts. Please try again later.", 429
    if error.status in (400, 401, 403, 404, 422):
        return rejected, 400
    return "Sign-in is temporarily unavailable. Please try again later.", 503


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def register_auth(app):
    app.before_request(load_identity)
    app.after_request(secure_response)

    def ready():
        return app.config["CLOUD_MODE"] and app.config["CLOUD_READY"]

    def page(mode, status=200, **context):
        context.setdefault("email", "")
        return render_template("auth.html", mode=mode, ready=ready(), google=app.config.get("GOOGLE_LOGIN", False),
                               captcha_key=app.config.get("TURNSTILE_SITE_KEY", ""),
                               error=context.pop("error", None), **context), status

    def with_captcha(data):
        """Attach the Turnstile token for Supabase to verify; None when it is required but missing."""
        if not app.config.get("TURNSTILE_SITE_KEY"):
            return data
        token = request.form.get("cf-turnstile-response", "").strip()
        if not token or len(token) > 2048:
            return None
        return {**data, "gotrue_meta_security": {"captcha_token": token}}

    def start_session(result):
        accept_tokens(result)  # Missing or unverified session => CloudError, fail closed.
        session.clear()
        session["csrf_token"] = secrets.token_hex(32)
        remember_user(g.access_token, g.user)
        flash("You are signed in.", "success")
        return redirect(url_for("index"))

    def remember_code_request(email, purpose):
        session["otp_email"], session["otp_purpose"], session["otp_sent_at"] = email, purpose, int(time.time())

    def require_ready():
        if not ready():
            abort(503)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return page("login")
        require_ready()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not valid_email(email):
            return page("login", 400, email=email, error="Enter a valid email address.")
        if not password or len(password.encode("utf-8")) > 72:
            return page("login", 400, email=email, error="Email or password is incorrect.")
        data = with_captcha({"email": email, "password": password})
        if data is None:
            return page("login", 400, email=email, error="Complete the check that you are not a robot.")
        try:
            # Passwords go only to Supabase over HTTPS, never into our
            # database, session, logs, templates, or task metadata.
            return start_session(api("POST", "/auth/v1/token", params={"grant_type": "password"}, data=data))
        except CloudError as error:
            message, status = auth_failure(error, "Email or password is incorrect.")
            return page("login", status, email=email, error=message)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return page("register")
        require_ready()
        email = request.form.get("email", "").strip().lower()
        if not valid_email(email):
            return page("register", 400, email=email, error="Enter a valid email address.")
        problem = password_problem(request.form.get("password", ""), request.form.get("password_confirm", ""))
        if problem:
            return page("register", 400, email=email, error=problem)
        data = with_captcha({"email": email, "password": request.form["password"]})
        if data is None:
            return page("register", 400, email=email, error="Complete the check that you are not a robot.")
        try:
            result = api("POST", "/auth/v1/signup", data=data)
            if isinstance(result, dict) and result.get("access_token"):
                return start_session(result)  # Only when the provider has confirmation turned off.
        except CloudError as error:
            message, status = auth_failure(error, "Could not create an account. Try signing in or use another email.")
            return page("register", status, email=email, error=message)
        # Confirmation is on. The provider answers the same way for an address that already
        # exists, so the next screen never reveals whether someone is registered.
        remember_code_request(email, "signup")
        flash("We sent a confirmation code to your email.", "success")
        return redirect(url_for("verify_login"))

    @app.route("/login/code", methods=["GET", "POST"])
    def login_code():
        if request.method == "GET":
            return page("code", email=session.get("otp_email", ""))
        require_ready()
        email = request.form.get("email", "").strip().lower()
        if not valid_email(email):
            return page("code", 400, email=email, error="Enter a valid email address.")
        if session.get("otp_email") == email and time.time() - session.get("otp_sent_at", 0) < 60:
            return page("code", 429, email=email, error="Wait a minute before requesting another code.")
        data = with_captcha({"email": email, "create_user": True})
        if data is None:
            return page("code", 400, email=email, error="Complete the check that you are not a robot.")
        try:
            api("POST", "/auth/v1/otp", data=data)
        except CloudError as error:
            message, status = auth_failure(error, "Could not send a code. Please try again later.")
            return page("code", status, email=email, error=message)
        remember_code_request(email, "login")
        flash("We sent a sign-in code to your email.", "success")
        return redirect(url_for("verify_login"))

    @app.route("/login/verify", methods=["GET", "POST"])
    def verify_login():
        email = session.get("otp_email")
        if not email:
            return redirect(url_for("login_code"))
        if request.method == "GET":
            return page("verify", email=email)
        require_ready()
        code = re.sub(r"\s", "", request.form.get("code", ""))
        if not re.fullmatch(r"\d{6,10}", code):
            return page("verify", 400, email=email, error="The code is invalid or expired. Request a new code.")
        try:
            return start_session(api("POST", "/auth/v1/verify", data={"type": "email", "email": email, "token": code}))
        except CloudError as error:
            message, status = auth_failure(error, "The code is invalid or expired. Request a new code.")
            return page("verify", status, email=email, error=message)

    @app.route("/password/forgot", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "GET":
            return page("forgot")
        require_ready()
        email = request.form.get("email", "").strip().lower()
        if not valid_email(email):
            return page("forgot", 400, email=email, error="Enter a valid email address.")
        data = with_captcha({"email": email})
        if data is None:
            return page("forgot", 400, email=email, error="Complete the check that you are not a robot.")
        try:
            api("POST", "/auth/v1/recover", data=data)
        except CloudError as error:
            if error.status == 429 or error.status >= 500:
                message, status = auth_failure(error, "")
                return page("forgot", status, email=email, error=message)
            # Any other refusal looks like success, so the form cannot test which emails exist.
        session["reset_email"], session["reset_sent_at"] = email, int(time.time())
        flash("If this email has an account, we sent a reset code.", "success")
        return redirect(url_for("reset_password"))

    @app.route("/password/reset", methods=["GET", "POST"])
    def reset_password():
        email = session.get("reset_email")
        if not email:
            return redirect(url_for("forgot_password"))
        if request.method == "GET":
            return page("reset", email=email)
        require_ready()
        code = re.sub(r"\s", "", request.form.get("code", ""))
        problem = password_problem(request.form.get("password", ""), request.form.get("password_confirm", ""))
        if not re.fullmatch(r"\d{6,10}", code):
            problem = "The code is invalid or expired. Request a new code."
        if problem:
            return page("reset", 400, email=email, error=problem)
        try:
            result = api("POST", "/auth/v1/verify", data={"type": "recovery", "email": email, "token": code})
            if not isinstance(result, dict) or not result.get("access_token"):
                raise CloudError(401)
            api("PUT", "/auth/v1/user", token=result["access_token"], data={"password": request.form["password"]})
            response = start_session(result)
        except CloudError as error:
            message, status = auth_failure(error, "The code is invalid or expired. Request a new code.")
            return page("reset", status, email=email, error=message)
        flash("Your password has been changed.", "success")
        return response

    @app.get("/auth/google")
    def google_login():
        if not ready() or not app.config.get("GOOGLE_LOGIN"):
            abort(404)
        verifier = secrets.token_urlsafe(64)
        session["pkce_verifier"] = verifier
        scheme = "https" if app.config["SESSION_COOKIE_SECURE"] else request.scheme
        query = urlencode({"provider": "google", "redirect_to": url_for("auth_callback", _external=True, _scheme=scheme),
                           "code_challenge": pkce_challenge(verifier), "code_challenge_method": "s256"})
        return redirect(app.config["SUPABASE_URL"].rstrip("/") + "/auth/v1/authorize?" + query)

    @app.get("/auth/callback")
    def auth_callback():
        # The verifier lives only in this browser's session, so a code started elsewhere cannot sign anyone in.
        verifier = session.pop("pkce_verifier", None)
        code = request.args.get("code", "")
        if not ready() or not verifier or not code or len(code) > 512:
            flash("Google sign-in did not finish. Please try again.", "error")
            return redirect(url_for("login"))
        try:
            return start_session(api("POST", "/auth/v1/token", params={"grant_type": "pkce"},
                                     data={"auth_code": code, "code_verifier": verifier}))
        except CloudError as error:
            message, _ = auth_failure(error, "Google sign-in did not finish. Please try again.")
            flash(message, "error")
            return redirect(url_for("login"))

    @app.post("/logout")
    def logout():
        try:
            if g.get("access_token"):
                api("POST", "/auth/v1/logout", token=g.access_token, params={"scope": "local"})
        except CloudError:
            # Do not report logout success while a refresh token remains valid remotely.
            abort(503)
        session.clear()
        g.clear_auth = True
        return redirect(url_for("login"))

    @app.get("/health")
    def health():
        ready_now = not app.config["CLOUD_MODE"] or app.config["CLOUD_READY"]
        return {"status": "ok" if ready_now else "unavailable"}, 200 if ready_now else 503


class StudyStorageMissing(Exception):
    """The study-session table has not been created in Supabase yet."""


def cloud_sessions(method="GET", data=None, since=None):
    if not g.get("user") or not g.get("access_token"):
        abort(401)
    path = "/rest/v1/studyflow_study_sessions"
    try:
        if method == "POST":
            row = {key: value for key, value in data.items() if key in {"course", "minutes", "day", "started_at"}}
            row["user_id"] = g.user["id"]
            return api("POST", path, token=g.access_token, data=row, headers={"Prefer": "return=minimal"})
        return api("GET", path, token=g.access_token, params={
            "user_id": "eq." + g.user["id"], "day": "gte." + since, "select": "course,minutes,day",
            "order": "day.asc", "limit": 5000})
    except CloudError as error:
        if error.status == 404:
            raise StudyStorageMissing() from None
        abort(503)


def cloud_tasks(method="GET", task_id=None, data=None):
    # Never accept ownership or raw query expressions from a form.
    if not g.get("user") or not g.get("access_token"):
        abort(401)
    params = {"user_id": "eq." + g.user["id"]}
    if task_id is not None:
        params["id"] = "eq." + str(int(task_id))
    if data is not None:
        data = {key: value for key, value in data.items() if key in {"title", "course", "due_date", "priority", "notes", "status"}}
        if method == "POST":
            data["user_id"] = g.user["id"]
    try:
        if method == "GET" and task_id is None:
            tasks = []
            for offset in range(0, 10000, 500):
                batch = api("GET", "/rest/v1/studyflow_tasks", token=g.access_token,
                            params={**params, "select": "*", "order": "id.asc", "offset": offset, "limit": 500})
                tasks.extend(batch)
                if len(batch) < 500:
                    return tasks
            abort(503)  # Never silently present partial totals.
        rows = api(method, "/rest/v1/studyflow_tasks", token=g.access_token, params=params,
                   data=data, headers={"Prefer": "return=representation"})
        if task_id is not None and not rows:
            abort(404)  # Same answer for nonexistent and another user's task.
        return rows
    except CloudError:
        abort(503)

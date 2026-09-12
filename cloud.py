"""Supabase Auth + PostgREST. Never use a service-role key for user requests."""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for


class CloudError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__("Cloud request failed")  # No response bodies / credentials in logs.


def valid_config(url, key):
    try:
        parsed = urlsplit(url)
        valid_url = (parsed.scheme == "https" and parsed.hostname and
                     parsed.hostname.endswith(".supabase.co") and not parsed.username and
                     not parsed.password and parsed.port in (None, 443) and parsed.path in ("", "/") and
                     not parsed.query and not parsed.fragment)
        if key.startswith("sb_publishable_"):
            valid_key = True
        else:
            payload = key.split(".")[1]
            valid_key = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("role") == "anon"
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
    if not isinstance(user, dict) or not user.get("email_confirmed_at"):
        raise CloudError(401)
    try:
        UUID(user["id"])
    except (KeyError, TypeError, ValueError):
        raise CloudError(401) from None
    return {"id": user["id"], "email": user.get("email", "")}


def accept_tokens(result):
    if not isinstance(result, dict) or not result.get("access_token") or not result.get("refresh_token"):
        raise CloudError(401)
    g.access_token = result["access_token"]
    g.user = authenticated_user(g.access_token)
    g.new_tokens = (result["access_token"], result["refresh_token"])


def load_identity():
    g.user = None
    if not current_app.config["CLOUD_MODE"]:
        # An old local workspace must never become a public, unauthenticated database.
        if not current_app.testing and (request.remote_addr not in {"127.0.0.1", "::1"} or
                                        request.host.split(":")[0] not in {"127.0.0.1", "localhost"}):
            abort(403)
        return
    public = {"static", "login", "verify_login", "change_language", "health"}
    if request.endpoint in public or request.endpoint is None:
        return
    if not current_app.config["CLOUD_READY"]:
        abort(503)
    token = request.cookies.get("sf_access")
    refresh = request.cookies.get("sf_refresh")
    try:
        if not token:
            raise CloudError(401)
        g.user = authenticated_user(token)
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
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'"
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


def register_auth(app):
    app.before_request(load_identity)
    app.after_request(secure_response)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        email = ""
        ready = app.config["CLOUD_MODE"] and app.config["CLOUD_READY"]
        if request.method == "POST":
            if not ready:
                abort(503)
            email = request.form.get("email", "").strip().lower()
            if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                error = "Enter a valid email address."
            elif time.time() - session.get("otp_sent_at", 0) < 60:
                error = "Wait a minute before requesting another code."
            else:
                try:
                    # Provider enforces email quotas/rate limits; no local-only security limiter.
                    api("POST", "/auth/v1/otp", data={"email": email, "create_user": True})
                    session["otp_email"] = email
                    session["otp_sent_at"] = time.time()
                    return redirect(url_for("verify_login"))
                except CloudError as exc:
                    error = "Too many attempts. Please try again later." if exc.status == 429 else "Could not send a code. Please try again later."
        return render_template("auth.html", step="email", error=error, email=email, ready=ready), 400 if error else 200

    @app.route("/login/verify", methods=["GET", "POST"])
    def verify_login():
        email = session.get("otp_email")
        if not email or time.time() - session.get("otp_sent_at", 0) > 600:
            session.pop("otp_email", None)
            return redirect(url_for("login"))
        error = None
        if request.method == "POST":
            code = request.form.get("code", "").strip()
            if not re.fullmatch(r"[0-9]{6,10}", code):
                error = "The code is invalid or expired. Request a new code."
            else:
                try:
                    result = api("POST", "/auth/v1/verify", data={"email": email, "token": code, "type": "email"})
                    accept_tokens(result)
                    session.clear()  # Rotate CSRF and remove drafts from any previous account.
                    session["csrf_token"] = secrets.token_hex(32)
                    flash("You are signed in.", "success")
                    return redirect(url_for("index"))
                except CloudError as exc:
                    error = ("Too many attempts. Please try again later." if exc.status == 429 else
                             "The code is invalid or expired. Request a new code." if exc.status in (400, 401, 403, 422) else
                             "Sign-in is temporarily unavailable. Please try again later.")
        return render_template("auth.html", step="code", error=error, email=email, ready=True), 400 if error else 200

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
        ready = not app.config["CLOUD_MODE"] or app.config["CLOUD_READY"]
        return {"status": "ok" if ready else "unavailable"}, 200 if ready else 503


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

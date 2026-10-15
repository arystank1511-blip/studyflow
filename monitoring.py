"""Optional error monitoring with Sentry. Off unless SENTRY_DSN is set.

Students' tasks, emails and tokens must never leave for a third party, so every event is
stripped down to what is needed to fix a bug: the exception, the stack trace without local
variables, the route and the HTTP method.
"""
import os

KEEP_REQUEST = ("method", "url")


def scrub(event, hint=None):
    request = event.get("request")
    if isinstance(request, dict):
        url = str(request.get("url", "")).split("?", 1)[0]
        event["request"] = {key: request[key] for key in KEEP_REQUEST if key in request}
        if "url" in event["request"]:
            event["request"]["url"] = url
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    for name in ("extra", "contexts"):
        if isinstance(event.get(name), dict):
            event[name] = {key: value for key, value in event[name].items() if key in ("runtime", "os", "trace")}
    return event


def init():
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        environment=os.environ.get("VERCEL_ENV", "local"),
        release=os.environ.get("VERCEL_GIT_COMMIT_SHA") or None,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        traces_sample_rate=0,
        before_send=scrub,
    )
    return True

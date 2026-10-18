"""Morning reminders on the phone (Web Push). Off unless the VAPID keys are set.

The browser hands us an endpoint at a push service (Google, Mozilla, Apple, Microsoft) and two
keys; the payload is encrypted for that device, so the push service never sees the text.
"""
from __future__ import annotations

import json
import os
import re

PUSH_HOSTS = re.compile(r"^https://(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com|web\.push\.apple\.com|"
                        r"[a-z0-9.-]+\.notify\.windows\.com)/")
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{80,100}$")
AUTH_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
SITE_URL = "https://studyflow-nine-chi.vercel.app"


def public_key() -> str:
    return os.environ.get("VAPID_PUBLIC_KEY", "").strip()


def enabled() -> bool:
    return bool(public_key() and os.environ.get("VAPID_PRIVATE_KEY", "").strip())


def valid_subscription(endpoint: str, p256dh: str, auth: str) -> bool:
    return (len(endpoint) <= 1000 and bool(PUSH_HOSTS.match(endpoint)) and bool(KEY_PATTERN.match(p256dh))
            and bool(AUTH_PATTERN.match(auth)))


def _ru_tasks(n: int) -> str:
    if 11 <= n % 100 <= 14:
        word = "задач"
    elif n % 10 == 1:
        word = "задача"
    elif n % 10 in (2, 3, 4):
        word = "задачи"
    else:
        word = "задач"
    return f"{n} {word}"


def _ru_days(n: int) -> str:
    return "дн."


def message(language: str, today: int, overdue: int, exam_title: str | None, exam_days: int | None) -> str | None:
    """The notification body, or None when there is nothing worth waking the phone for."""
    parts = []
    if language == "kk":
        if today:
            parts.append(f"Бүгін {today} тапсырма")
        if overdue:
            parts.append(f"мерзімі өткен: {overdue}")
        if exam_title and exam_days is not None:
            when = "бүгін" if exam_days == 0 else "ертең" if exam_days == 1 else f"{exam_days} күннен кейін"
            parts.append(f"{exam_title} — {when}")
    elif language == "en":
        if today:
            parts.append(f"Today: {today} task{'' if today == 1 else 's'}")
        if overdue:
            parts.append(f"{overdue} overdue")
        if exam_title and exam_days is not None:
            when = "today" if exam_days == 0 else "tomorrow" if exam_days == 1 else f"in {exam_days} days"
            parts.append(f"{exam_title} {when}")
    else:
        if today:
            parts.append(f"Сегодня {_ru_tasks(today)}")
        if overdue:
            parts.append(f"просрочено: {overdue}")
        if exam_title and exam_days is not None:
            when = "сегодня" if exam_days == 0 else "завтра" if exam_days == 1 else f"через {exam_days} {_ru_days(exam_days)}"
            parts.append(f"{exam_title} {when}")
    return " · ".join(parts) if parts else None


def send(subscription: dict, body: str) -> str:
    """Deliver one notification. Returns "sent", "gone" (forget this device) or "failed"."""
    if not valid_subscription(subscription.get("endpoint", ""), subscription.get("p256dh", ""), subscription.get("auth", "")):
        return "gone"
    # Imported here: cryptography and requests are only needed by the morning job.
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": subscription["endpoint"],
                               "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
            data=json.dumps({"title": "StudyFlow", "body": body, "url": "/"}, ensure_ascii=False),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"].strip(),
            vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "").strip() or SITE_URL},
            ttl=12 * 3600,
            timeout=10,
        )
        return "sent"
    except WebPushException as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        return "gone" if status in (404, 410) else "failed"
    except Exception:  # Network trouble with one device must not stop the others.
        return "failed"

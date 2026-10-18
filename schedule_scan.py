"""Read a photo of a university or school timetable with Google Gemini and return the classes.

The model only extracts what it sees; the student reviews every row before any task
is created. Needs GEMINI_API_KEY on the server; without it the feature stays hidden.
"""
from __future__ import annotations

import base64
import json
import os
import re

import httpx

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = "gemini-3.8-flash"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CLASSES = 80
KINDS = ("lecture", "practice", "lab", "seminar", "other")
PARITIES = ("every", "odd", "even")

SYSTEM_PROMPT = """You read photos of class timetables from universities and schools in Kazakhstan and elsewhere.
The text may be in Kazakh, Russian or English, printed or handwritten, as a table or a list.

Extract every class you can read. For each one give:
- weekday: 0 = Monday … 6 = Sunday (Дүйсенбі/Понедельник = 0, … Жексенбі/Воскресенье = 6)
- start and end: 24-hour "HH:MM". If only a lesson number is shown (e.g. "2 пара", "3-сабақ"), leave the time empty.
- course: the subject name exactly as written, without the room or teacher
- kind: lecture (лекция, дәріс), practice (практика, практикалық), lab (лабораторная, зертханалық), seminar, or other
- room and teacher when shown, otherwise empty
- parity: "odd" for числитель / upper week / алымы, "even" for знаменатель / lower week / бөлімі, otherwise "every"

Only report what is visible. Skip cells you cannot read rather than guessing, and mention them in warnings.
If the photo is not a timetable, return no classes and say so in warnings."""

SCHEMA = {
    "type": "object",
    "properties": {
        "classes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "weekday": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "course": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "room": {"type": "string"},
                    "teacher": {"type": "string"},
                    "parity": {"type": "string", "enum": list(PARITIES)},
                },
                "required": ["weekday", "start", "end", "course", "kind", "room", "teacher", "parity"],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "string"},
    },
    "required": ["classes", "warnings"],
    "additionalProperties": False,
}

IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
}


class ScanError(Exception):
    """A user-facing reason the photo could not be read (message is a translation key)."""


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def image_type(data: bytes) -> str | None:
    for signature, media_type in IMAGE_SIGNATURES.items():
        if data.startswith(signature) and (media_type != "image/webp" or data[8:12] == b"WEBP"):
            return media_type
    return None


def _time(value) -> str:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(value or ""))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return ""
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def clean(raw: dict) -> dict:
    """Validate the model's JSON again on our side and trim every field."""
    classes = []
    for item in (raw.get("classes") or [])[:MAX_CLASSES]:
        if not isinstance(item, dict):
            continue
        course = " ".join(str(item.get("course", "")).split())[:80]
        weekday = item.get("weekday")
        if not course or weekday not in range(7):
            continue
        classes.append({
            "weekday": weekday,
            "start": _time(item.get("start")),
            "end": _time(item.get("end")),
            "course": course,
            "kind": item.get("kind") if item.get("kind") in KINDS else "other",
            "room": " ".join(str(item.get("room", "")).split())[:30],
            "teacher": " ".join(str(item.get("teacher", "")).split())[:60],
            "parity": item.get("parity") if item.get("parity") in PARITIES else "every",
        })
    classes.sort(key=lambda row: (row["weekday"], row["start"] or "99:99"))
    return {"classes": classes, "warnings": str(raw.get("warnings", ""))[:400]}


def scan(data: bytes) -> dict:
    media_type = image_type(data)
    if not media_type:
        raise ScanError("Upload a photo in JPEG, PNG or WebP format.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ScanError("The photo is too large. Use one under 5 MB.")
    if not available():
        raise ScanError("Timetable scanning is not connected yet.")

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": media_type, "data": base64.standard_b64encode(data).decode("ascii")}},
            {"text": "Extract the timetable from this photo."},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                             "temperature": 0, "maxOutputTokens": 16000},
    }
    model = os.environ.get("GEMINI_MODEL", "").strip() or MODEL
    try:
        response = httpx.post(API_URL.format(model=model), json=body, timeout=httpx.Timeout(50, connect=10),
                              headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()})
    except httpx.HTTPError:
        raise ScanError("The photo could not be read right now. Try again later.") from None
    if response.status_code == 429:
        raise ScanError("Too many photos at once. Try again in a minute.")
    if response.status_code != 200:
        raise ScanError("The photo could not be read right now. Try again later.")

    try:
        candidate = (response.json().get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    except (ValueError, AttributeError, TypeError):
        text, candidate = "", {}
    if candidate.get("finishReason") not in (None, "STOP") or not text:
        raise ScanError("The photo could not be read. Try a clearer, straight photo of the timetable.")
    try:
        return clean(json.loads(text))
    except (ValueError, TypeError, AttributeError):
        raise ScanError("The photo could not be read. Try a clearer, straight photo of the timetable.") from None

"""Turn one line such as "Эссе пт 18:00 !высокий #философия" into task fields.

Recognised tokens (Russian, Kazakh or English), anywhere in the line:
  #course          course name; underscores become spaces ("#data_bases")
  !high / !высокий / !жоғары / !1, !medium / !средний / !орташа / !2, !low / !низкий / !төмен / !3, or !!! / !! / !
  today, tomorrow, сегодня, завтра, послезавтра, бүгін, ертең, бүрсігүні
  weekday names or short forms (пн … вс, mon … sun, "в пятницу")
  dd.mm, dd.mm.yyyy, yyyy-mm-dd
  "через 3 дня", "in 3 days", "3 күннен кейін"
  hh:mm            kept in the notes, since tasks have a date and no time
Everything else is the title.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

PRIORITIES = {
    "high": "High", "высокий": "High", "срочно": "High", "жоғары": "High", "маңызды": "High", "шұғыл": "High", "1": "High", "!!": "High",
    "medium": "Medium", "средний": "Medium", "орташа": "Medium", "2": "Medium", "!": "Medium",
    "low": "Low", "низкий": "Low", "төмен": "Low", "3": "Low", "": "Low",
}
WEEKDAYS = {
    0: ("пн", "понедельник", "mon", "monday", "дс", "дүйсенбі"),
    1: ("вт", "вторник", "tue", "tuesday", "сс", "сейсенбі"),
    2: ("ср", "среда", "среду", "wed", "wednesday", "сәрсенбі"),
    3: ("чт", "четверг", "thu", "thursday", "бс", "бейсенбі"),
    4: ("пт", "пятница", "пятницу", "fri", "friday", "жм", "жұма"),
    5: ("сб", "суббота", "субботу", "sat", "saturday", "сенбі"),
    6: ("вс", "воскресенье", "sun", "sunday", "жс", "жексенбі"),
}
WEEKDAY_WORDS = {word: day for day, words in WEEKDAYS.items() for word in words}
RELATIVE = {"сегодня": 0, "today": 0, "бүгін": 0, "завтра": 1, "tomorrow": 1, "ертең": 1, "послезавтра": 2, "бүрсігүні": 2}

TOKEN = re.compile(
    r"(?P<course>#[\w-]+)"
    r"|(?<!\S)(?P<priority>!(?:!{0,2}|[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+|[123]))(?=\s|$)"
    r"|(?P<in_days>(?:(?:через|in)\s+(?P<n>\d{1,3})\s+(?:дн[еяй]+|день|days?))|(?:(?P<kk_n>\d{1,3})\s+күннен\s+кейін))"
    r"|(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<dotted>\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)"
    r"|(?P<time>\b\d{1,2}:\d{2}\b)"
    r"|(?P<word>(?:в\s+|во\s+|on\s+)?[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+)",
    re.IGNORECASE,
)


def _dotted(value: str, today: date) -> date | None:
    parts = [int(part) for part in value.split(".")]
    year = parts[2] if len(parts) == 3 else today.year
    if year < 100:
        year += 2000
    try:
        result = date(year, parts[1], parts[0])
    except ValueError:
        return None
    # "05.01" written in December means next January.
    if len(parts) == 2 and result < today - timedelta(days=60):
        result = result.replace(year=year + 1)
    return result


def parse(text: str, today: date, default_course: str) -> dict:
    """Return title/course/due_date/priority/notes plus which parts were recognised."""
    text = " ".join(text.split())[:300]
    found: dict[str, str] = {}
    due: date | None = None
    course = priority = time = None
    keep: list[str] = []
    position = 0

    for match in TOKEN.finditer(text):
        keep.append(text[position:match.start()])
        position = match.end()
        raw = match.group(0)
        kind = match.lastgroup if match.lastgroup not in ("n", "kk_n") else "in_days"
        if match.group("course") and course is None:
            course = match.group("course")[1:].replace("_", " ")
            continue
        if match.group("priority") and priority is None:
            key = match.group("priority")[1:].lower()
            if key in PRIORITIES:
                priority = PRIORITIES[key]
                continue
        if due is None:
            candidate = None
            if match.group("in_days"):
                candidate = today + timedelta(days=int(match.group("n") or match.group("kk_n")))
            elif match.group("iso"):
                try:
                    candidate = date.fromisoformat(match.group("iso"))
                except ValueError:
                    candidate = None
            elif match.group("dotted"):
                candidate = _dotted(match.group("dotted"), today)
            elif kind == "word":
                word = re.sub(r"^(?:в|во|on)\s+", "", raw, flags=re.IGNORECASE).lower()
                if word in RELATIVE:
                    candidate = today + timedelta(days=RELATIVE[word])
                elif word in WEEKDAY_WORDS:
                    candidate = today + timedelta(days=(WEEKDAY_WORDS[word] - today.weekday()) % 7)
            if candidate is not None:
                due = candidate
                continue
        if match.group("time") and time is None:
            hours, minutes = (int(part) for part in match.group("time").split(":"))
            if hours < 24 and minutes < 60:
                time = f"{hours:02d}:{minutes:02d}"
                continue
        keep.append(raw)
    keep.append(text[position:])

    title = " ".join("".join(keep).split()).strip(" ,.-—")
    if due:
        found["due_date"] = due.isoformat()
    if course:
        found["course"] = course
    if priority:
        found["priority"] = priority
    if time:
        found["time"] = time
    return {
        "title": title,
        "course": course or default_course,
        "due_date": (due or today).isoformat(),
        "priority": priority or "Medium",
        "notes": time or "",
        "found": found,
    }

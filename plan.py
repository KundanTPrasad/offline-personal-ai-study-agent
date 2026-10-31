import re
from datetime import date, timedelta
from typing import Literal, Optional

import db
import ollama
from pydantic import BaseModel

MODEL = "qwen3.5:4b"


class Task(BaseModel):
    title: str
    target: Optional[int] = None
    type: Literal["dsa", "learning", "event", "other"]


class Plan(BaseModel):
    when: Literal["today", "tomorrow", "day_after_tomorrow", "specific_date"]
    day: Optional[int] = None
    month: Optional[int] = None
    tasks: list[Task]


SYSTEM = (
    "You extract a student's plan from their message. "
    "The message may be in English, Hindi, or Hinglish. "
    "Return the tasks as JSON. Write task titles in short English. "
    "Keep each title close to the user's own words; do not add words like "
    "'planning' or 'preparation' (for example 'shaadi hai' becomes 'Wedding'). "
    "Use type 'dsa' for coding practice questions, 'learning' for lectures or study, "
    "'event' for events, weddings, parties or meetings, otherwise 'other'. "
    "If the message gives a number of items (like '4 question' or '2 DSA questions'), "
    "put that number in target, else null. "
    "For 'when': 'tomorrow' if the user says kal/tomorrow, 'day_after_tomorrow' for parso, "
    "'specific_date' if they name a date like '2 Nov' or '15 December' "
    "(then set day=2, month=11 as numbers), otherwise 'today'."
)

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({MONTH_RE})\b", re.I)
MONTH_FIRST = re.compile(rf"\b({MONTH_RE})\s*(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")  # dd/mm


def find_date_in_text(text):
    """Return (day, month) if the text names a calendar date, else None."""
    m = DAY_FIRST.search(text)
    if m:
        return int(m.group(1)), MONTHS[m.group(2).lower()]
    m = MONTH_FIRST.search(text)
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    m = SLASH.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def build_date(day, month):
    today = date.today()
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d < today:
        d = date(today.year + 1, month, day)
    return d


def resolve_date(text, plan):
    # the date is worked out by Python, not by the model
    today = date.today()

    found = find_date_in_text(text)
    if found:
        d = build_date(*found)
        if d:
            return d

    lower = text.lower()
    if re.search(r"\bparso\b|day after tomorrow", lower):
        return today + timedelta(days=2)
    if re.search(r"\bkal\b|\btomorrow\b", lower):
        return today + timedelta(days=1)

    # fallback: what the model said
    if plan.when == "tomorrow":
        return today + timedelta(days=1)
    if plan.when == "day_after_tomorrow":
        return today + timedelta(days=2)
    if plan.when == "specific_date" and plan.day and plan.month:
        d = build_date(plan.day, plan.month)
        if d:
            return d
    return today


def extract_plan(text):
    r = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        format=Plan.model_json_schema(),
        think=False,
        options={"temperature": 0},
    )
    plan = Plan.model_validate_json(r["message"]["content"])
    return resolve_date(text, plan), plan.tasks


if __name__ == "__main__":
    db.init_db()
    text = input("Tell me your plan: ")
    plan_date, tasks = extract_plan(text)
    db.save_tasks(plan_date, tasks)

    print("Saved! Tasks for:", plan_date)
    for row in db.get_tasks(plan_date):
        print(f"{row['id']}. {row['title']} | target: {row['target']} | status: {row['status']}")
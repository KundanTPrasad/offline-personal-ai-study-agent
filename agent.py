import difflib
import re
from datetime import date, timedelta
from typing import Literal, Optional

import db
import ollama
import plan
import streak
from pydantic import BaseModel

MODEL = plan.MODEL


class Intent(BaseModel):
    intent: Literal["add_plan", "show_plan", "update_status", "general_chat"]


class Update(BaseModel):
    title: str
    type: Literal["dsa", "learning", "event", "other"]
    status: Literal["done", "partly", "not_done"]
    count: Optional[int] = None


class Updates(BaseModel):
    updates: list[Update]


ROUTER_SYSTEM = (
    "Classify the user's message (English, Hindi or Hinglish) into exactly one intent.\n"
    "add_plan: the user is telling tasks they WANT or WILL do, or events they will attend. "
    "Examples: 'kal 4 DSA question karne hain', 'I will attend the event on 5 Nov', "
    "'10 nov ko shaadi hai', 'today I want to revise one topic'.\n"
    "show_plan: the user asks what their plan or schedule is. "
    "Examples: 'aaj ka kya plan hai', 'what is my plan for tomorrow', 'kal ka plan batao', "
    "'10 nov ko kya hai', 'show upcoming plans', 'future mein kya plan hai'.\n"
    "update_status: the user says what they ALREADY finished or did not finish today. "
    "Examples: 'aaj 3 DSA question ho gaye', 'I finished the lecture', "
    "'lecture adhura raha', 'DSA ke 2 question hi kar paya'.\n"
    "general_chat: anything else, like greetings, questions, explanations or advice. "
    "Examples: 'hii', 'what is a queue?', 'give me a study tip'."
)

UPDATES_SYSTEM = (
    "The student says what they did today. The message may be English, Hindi or Hinglish. "
    "For each task they mention, return: title (short English, close to their words), "
    "type ('dsa' for coding practice questions, 'learning' for lectures or study, "
    "'event' for events or meetings, otherwise 'other'), "
    "status ('done' if fully finished, 'partly' if only some or half, 'not_done' if they did not do it), "
    "and count (the number of items finished, like 3 in '3 DSA questions done', else null)."
)

# ---------- language ----------
HINGLISH_WORDS = {
    "hai", "hain", "ka", "ki", "ke", "ko", "mein", "kal", "aaj", "parso", "karne", "karna",
    "kya", "batao", "ho", "gaya", "gaye", "gayi", "kiya", "nahi", "mujhe", "mera", "meri",
    "aur", "phir", "se", "par", "tha", "thi", "kar", "raha", "rahi", "abhi", "bhi",
}


def is_hinglish(text):
    if re.search(r"[\u0900-\u097F]", text):
        return True
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return any(w in HINGLISH_WORDS for w in words)


T = {
    "en": {
        "saved": "Saved for {date}:",
        "already": "Already in your plan: {items}",
        "no_tasks": "I couldn't find any tasks in that. Try something like: 'kal 3 DSA question karne hain'.",
        "no_plan": "No plan for {date}.",
        "plan_for": "Plan for {date}:",
        "no_upcoming": "You have no upcoming plans.",
        "upcoming": "Your plans from today onwards:",
        "no_plan_today": "You have no plan for today yet. Tell me what you want to do today first.",
        "updated": "Updated today's progress:",
        "no_match": "I couldn't find these in today's plan: {items}",
        "pending_left": "{n} task(s) still pending today.",
        "score": "Today's score: {score}/{total}",
        "no_updates": "I couldn't tell which task you finished. Try: 'aaj 3 DSA question ho gaye'.",
    },
    "hi": {
        "saved": "{date} ke liye save ho gaya:",
        "already": "Ye pehle se plan mein hai: {items}",
        "no_tasks": "Mujhe isme koi task nahi mila. Aise likho: 'kal 3 DSA question karne hain'.",
        "no_plan": "{date} ke liye koi plan nahi hai.",
        "plan_for": "{date} ka plan:",
        "no_upcoming": "Aage ka koi plan nahi hai.",
        "upcoming": "Aaj se aage ke plans:",
        "no_plan_today": "Aaj ka abhi koi plan nahi hai. Pehle batao aaj kya karna hai.",
        "updated": "Aaj ki progress update kar di:",
        "no_match": "Ye aaj ke plan mein nahi mile: {items}",
        "pending_left": "Aaj {n} task abhi baaki hain.",
        "score": "Aaj ka score: {score}/{total}",
        "no_updates": "Samajh nahi aaya kaunsa task hua. Aise likho: 'aaj 3 DSA question ho gaye'.",
    },
}

STATUS = {
    "en": {"done": "done", "partly": "partly done", "not_done": "not done", "pending": "pending"},
    "hi": {"done": "ho gaya", "partly": "aadha hua", "not_done": "nahi hua", "pending": "baaki"},
}

UPCOMING = re.compile(r"upcoming|future|aane wale|aage|all plans|next days|sab plan", re.I)


def classify(text):
    try:
        r = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": text},
            ],
            format=Intent.model_json_schema(),
            think=False,
            options={"temperature": 0},
        )
        return Intent.model_validate_json(r["message"]["content"]).intent
    except Exception:
        return "general_chat"


# ---------- show plan ----------
def date_from_text(text):
    """Which day is the user asking about? Worked out by Python, not the model."""
    today = date.today()
    found = plan.find_date_in_text(text)
    if found:
        day, month = found
        try:
            return date(today.year, month, day)
        except ValueError:
            pass
    lower = text.lower()
    if re.search(r"\bparso\b|day after tomorrow", lower):
        return today + timedelta(days=2)
    if re.search(r"\byesterday\b|\bbeeta kal\b|\bbeete kal\b", lower):
        return today - timedelta(days=1)
    if re.search(r"\bkal\b|\btomorrow\b", lower):
        return today + timedelta(days=1)
    return today


def format_day(d, lang):
    rows = db.get_tasks(d)
    label = d.strftime("%d %b %Y")
    if not rows:
        return T[lang]["no_plan"].format(date=label)
    lines = [T[lang]["plan_for"].format(date=label)]
    for r in rows:
        target = f" (target: {r['target']})" if r["target"] else ""
        lines.append(f"- {r['title']}{target}  [{STATUS[lang].get(r['status'], r['status'])}]")
    return "\n".join(lines)


def format_upcoming(lang):
    rows = db.get_upcoming(date.today())
    if not rows:
        return T[lang]["no_upcoming"]
    lines = [T[lang]["upcoming"]]
    current = None
    for r in rows:
        if r["plan_date"] != current:
            current = r["plan_date"]
            lines.append(f"\n{current}:")
        lines.append(f"- {r['title']}  [{STATUS[lang].get(r['status'], r['status'])}]")
    return "\n".join(lines)


def do_show_plan(text, lang):
    if UPCOMING.search(text):
        return format_upcoming(lang)
    return format_day(date_from_text(text), lang)


# ---------- add plan ----------
def do_add_plan(text, lang):
    plan_date, tasks = plan.extract_plan(text)
    if not tasks:
        return T[lang]["no_tasks"]
    added = db.save_tasks(plan_date, tasks)
    label = plan_date.strftime("%d %b %Y")
    lines = []
    if added:
        lines.append(T[lang]["saved"].format(date=label))
        for t in added:
            target = f" (target: {t.target})" if t.target else ""
            lines.append(f"- {t.title}{target}")
    skipped = [t.title for t in tasks if t not in added]
    if skipped:
        lines.append(T[lang]["already"].format(items=", ".join(skipped)))
    return "\n".join(lines)


# ---------- update status ----------
def match_task(update, rows, used):
    best, best_score = None, 0.0
    for r in rows:
        if r["id"] in used:
            continue
        score = difflib.SequenceMatcher(None, update.title.lower(), r["title"].lower()).ratio()
        if r["type"] == update.type:
            score += 0.5
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.5 else None


def final_status(update, row):
    # numbers are judged by Python, not the model
    if update.count is not None and row["target"]:
        if update.count == 0:
            return "not_done"
        return "done" if update.count >= row["target"] else "partly"
    return update.status


def do_update_status(text, lang):
    today = date.today()
    rows = db.get_tasks(today)
    if not rows:
        return T[lang]["no_plan_today"]
    try:
        r = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": UPDATES_SYSTEM},
                {"role": "user", "content": text},
            ],
            format=Updates.model_json_schema(),
            think=False,
            options={"temperature": 0},
        )
        updates = Updates.model_validate_json(r["message"]["content"]).updates
    except Exception:
        updates = []
    if not updates:
        return T[lang]["no_updates"]

    used, changed, missed = set(), [], []
    for u in updates:
        row = match_task(u, rows, used)
        if row is None:
            missed.append(u.title)
            continue
        used.add(row["id"])
        status = final_status(u, row)
        db.update_status(row["id"], status)
        changed.append(f"- {row['title']}: {STATUS[lang][status]}")

    lines = []
    if changed:
        lines.append(T[lang]["updated"])
        lines += changed
    if missed:
        lines.append(T[lang]["no_match"].format(items=", ".join(missed)))

    pending = sum(1 for x in db.get_tasks(today) if x["status"] == "pending")
    if pending:
        lines.append(T[lang]["pending_left"].format(n=pending))
    elif changed:
        s = streak.day_summary(today)
        lines.append(T[lang]["score"].format(score=s["score"], total=s["total"]))
        lines.append(streak.motivation(streak.current_streak(), s))
    return "\n".join(lines)


# ---------- general chat ----------
def do_chat(text, history, lang):
    language = (
        "Reply in simple Hinglish (Hindi written in English letters)."
        if lang == "hi"
        else "Reply in English."
    )
    system = (
        "You are a friendly study assistant running offline on the student's laptop. "
        f"Today's date is {date.today().strftime('%A, %d %B %Y')}. "
        f"{language} "
        "Use plain text only: no markdown, no asterisks, no headings. "
        "Keep answers short, at most 6 lines. "
        "You cannot browse the internet; if you are not sure about something, say so."
    )
    messages = [{"role": "system", "content": system}] + history[-8:]
    messages.append({"role": "user", "content": text})
    r = ollama.chat(model=MODEL, messages=messages, think=False)
    return r["message"]["content"].strip()


def handle(text, history):
    lang = "hi" if is_hinglish(text) else "en"
    intent = classify(text)
    if intent == "add_plan":
        reply = do_add_plan(text, lang)
    elif intent == "show_plan":
        reply = do_show_plan(text, lang)
    elif intent == "update_status":
        reply = do_update_status(text, lang)
    else:
        reply = do_chat(text, history, lang)
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    del history[:-12]
    return reply, intent


if __name__ == "__main__":
    db.init_db()
    history = []
    print("Chat started. Type 'exit' to quit.\n")
    while True:
        text = input("You: ").strip()
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        reply, intent = handle(text, history)
        print(f"[intent: {intent}]")
        print("Agent:", reply, "\n")
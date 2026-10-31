import difflib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from typing import Literal, Optional

import db
import fitz  # PyMuPDF for offline PDF extraction
import ollama
import plan
import streak
from pydantic import BaseModel

MODEL = plan.MODEL

# Context storage for the last uploaded document
LAST_DOCUMENT_CONTEXT = {
    "name": "",
    "text": "",
    "is_image": False,
    "image_path": "",
}


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
    "update_status: the user says what they ALREADY finished or did not finish today or on a previous day. "
    "Examples: 'aaj 3 DSA question ho gaye', 'I finished the lecture', 'kal DSA ke 2 question ho gaye the', "
    "'yesterday lecture was done', 'DSA ke 2 question hi kar paya'.\n"
    "general_chat: anything else, like greetings, questions, explanations or advice. "
    "Examples: 'hii', 'kaise ho', 'what is a queue?', 'give me a study tip'."
)

UPDATES_SYSTEM = (
    "The student says what tasks they completed or did not finish. The message may be English, Hindi or Hinglish. "
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
    "aur", "phir", "se", "par", "tha", "thi", "kar", "raha", "rahi", "abhi", "bhi", "kaise",
    "theek", "accha", "badhiya", "kaisa", "kaisi", "sab", "kaam", "banao", "poocho", "kardo",
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
        "updated_past": "Updated progress for {date}:",
        "no_match": "I couldn't find these in your pending plan: {items}",
        "pending_left": "{n} task(s) still pending today.",
        "score": "Today's score: {score}/{total}",
        "no_updates": "I couldn't tell which task you finished. Try: 'aaj 3 DSA question ho gaye'.",
        "streak_update": "Streak updated: {streak}-day streak!",
        "all_done": "Fantastic! Marked all today's tasks as completed:",
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
        "updated_past": "{date} ki progress update kar di:",
        "no_match": "Ye aapke pending tasks mein nahi mile: {items}",
        "pending_left": "Aaj {n} task abhi baaki hain.",
        "score": "Aaj ka score: {score}/{total}",
        "no_updates": "Samajh nahi aaya kaunsa task hua. Aise likho: 'aaj 3 DSA question ho gaye'.",
        "streak_update": "Streak update ho gayi: {streak} din!",
        "all_done": "Bahut badiya! Aaj ke saare tasks complete mark kar diye:",
    },
}

STATUS = {
    "en": {"done": "done", "partly": "partly done", "not_done": "not done", "pending": "pending"},
    "hi": {"done": "ho gaya", "partly": "aadha hua", "not_done": "nahi hua", "pending": "baaki"},
}

UPCOMING = re.compile(r"upcoming|future|aane wale|aage|all plans|next days|sab plan|भविष्य|आगे|आने वाले", re.I)

CHECKIN_RE = re.compile(
    r"\b(checkin|check-in|check in|day end|day-end|wrap up|wrapup|shutdown check|sone ja raha|band kar raha|aaj ka checkin)\b|चेक\s*इन|दिन खत्म",
    re.I,
)

ALL_DONE_RE = re.compile(
    r"\b(completed\s+(all(\s+of)?|my\s+todays?|everything|the\s+work)|all\s+(work\s+done|tasks?\s+done|work\s+completed|finished|done)|sab\s+(kaam\s+)?(ho\s+gaya|khatam|done)|finished\s+(all|everything|todays?\s+work))\b|सब\s*(काम\s*)?हो\s*गया|पूरा\s*हो\s*गया",
    re.I,
)

PRACTICE_QUESTIONS_RE = re.compile(
    r"\b(generate\s+(practice\s+)?questions?|practice\s+questions?|quiz(\s+banao)?|test\s+me|questions?\s+banao|question\s+poocho|practice\s+karni\s+hai)\b|प्रश्न\s*पूछो|सवाल\s*पूछो|क्विज|प्रैक्टिस",
    re.I,
)

CANCEL_RE = re.compile(
    r"\b(cancel|delete|hata\s+do|hatao|remove|drop|nahi\s+karna)\b|कैंसिल|रद्द|हटा\s*दो|हटाओ|डिलीट",
    re.I,
)


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


# ---------- clean table formatter ----------
def format_tasks_table(rows, lang="en"):
    """Render task rows in a clean, universal ASCII table."""
    if not rows:
        return ""
    col_task = "Task"
    col_target = "Target"
    col_status = "Status"

    max_task = max(len(col_task), max((len(r["title"]) for r in rows), default=4))
    max_target = max(len(col_target), max((len(str(r["target"] or "-")) for r in rows), default=6))
    max_status = max(len(col_status), max((len(STATUS[lang].get(r["status"], r["status"])) for r in rows), default=6))

    max_task = min(max(max_task, 16), 26)
    max_target = max(max_target, 6)
    max_status = max(max_status, 11)

    sep = f"+{'-' * (max_task + 2)}+{'-' * (max_target + 2)}+{'-' * (max_status + 2)}+"
    head = f"| {col_task:<{max_task}} | {col_target:^{max_target}} | {col_status:<{max_status}} |"
    lines = [sep, head, sep]

    for r in rows:
        title = r["title"]
        if len(title) > max_task:
            title = title[: max_task - 3] + "..."
        target = str(r["target"]) if r["target"] else "-"
        status = STATUS[lang].get(r["status"], r["status"])
        lines.append(f"| {title:<{max_task}} | {target:^{max_target}} | {status:<{max_status}} |")

    lines.append(sep)
    return "\n".join(lines)


# ---------- cancel & delete plan ----------
def do_cancel_plan(text, lang="en"):
    lower = text.lower()
    today = date.today()

    # 1. All future plans ("cancel all future plan", "sabhi future plan cancel kardo")
    if ("future" in lower or "aage" in lower or "aane" in lower or "भविष्य" in lower):
        count = db.delete_future_tasks(today)
        if count > 0:
            return (
                f"All future plans cancelled ({count} task(s) removed)."
                if lang == "en"
                else f"Aage ke saare future plans cancel kar diye ({count} task delete hue)."
            )
        return (
            "You have no future plans scheduled."
            if lang == "en"
            else "Aage ke liye koi future plan nahi tha."
        )

    # 2. Tomorrow's plan ("cancel all tomorrow plan", "kal ka plan cancel kardo")
    if re.search(r"\b(tomorrow|tommorow|tomorow|kal|कल)\b", lower) or "कल" in lower:
        tomorrow = today + timedelta(days=1)
        count = db.delete_tasks_by_date(tomorrow)
        if count > 0:
            return (
                f"Tomorrow's plan ({tomorrow.strftime('%d %b %Y')}) cancelled ({count} task(s) removed)."
                if lang == "en"
                else f"Kal ({tomorrow.strftime('%d %b %Y')}) ka plan cancel ho gaya ({count} task delete hue)."
            )
        return (
            "No plans found for tomorrow."
            if lang == "en"
            else "Kal ke liye koi plan nahi mila."
        )

    # 3. Today's plan ("cancel today's plan", "aaj ka plan cancel karo")
    if re.search(r"\b(today|aaj|आज)\b", lower) or "आज" in lower:
        count = db.delete_tasks_by_date(today)
        if count > 0:
            return (
                f"Today's plan ({today.strftime('%d %b %Y')}) cancelled ({count} task(s) removed)."
                if lang == "en"
                else f"Aaj ({today.strftime('%d %b %Y')}) ka plan cancel ho gaya ({count} task delete hue)."
            )
        return (
            "No plans found for today."
            if lang == "en"
            else "Aaj ke liye koi plan nahi mila."
        )

    # 4. Specific calendar date ("cancel 5 Nov plan")
    found = plan.find_date_in_text(text)
    if found:
        day, month = found
        try:
            d = date(today.year, month, day)
            count = db.delete_tasks_by_date(d)
            if count > 0:
                return (
                    f"Plans for {d.strftime('%d %b %Y')} cancelled ({count} task(s) removed)."
                    if lang == "en"
                    else f"{d.strftime('%d %b %Y')} ka plan cancel ho gaya ({count} task delete hue)."
                )
            return f"No plans found for {d.strftime('%d %b %Y')}."
        except ValueError:
            pass

    # 5. Specific task by title (e.g. "party cancel kardo" or "cancel college jaana")
    upcoming = db.get_upcoming(today)
    if upcoming:
        clean_text = re.sub(
            r"\b(cancel|delete|hata\s+do|hatao|remove|kardo|karna|plan|tasks?|the|all|my|sabhi|sab)\b",
            "",
            lower,
            flags=re.I,
        ).strip()
        best, best_score = None, 0.0
        for task in upcoming:
            score = difflib.SequenceMatcher(None, clean_text, task["title"].lower()).ratio()
            if clean_text and (clean_text in task["title"].lower() or task["title"].lower() in clean_text):
                score += 0.5
            if score > best_score:
                best, best_score = task, score

        if best and best_score >= 0.4:
            db.delete_task_by_id(best["id"])
            return (
                f"Cancelled task: {best['title']} ({best['plan_date']})."
                if lang == "en"
                else f"Task cancel ho gaya: {best['title']} ({best['plan_date']})."
            )

    return (
        "Couldn't find the plan to cancel. You can say 'cancel tomorrow plan', 'cancel all future plan', or name the task."
        if lang == "en"
        else "Cancel karne ke liye koi plan nahi mila. Aap 'kal ka plan cancel kardo' ya 'cancel all future plan' bol sakte hain."
    )


# ---------- check-in ----------
def do_checkin(lang="en"):
    today = date.today()
    rows = db.get_tasks(today)
    if not rows:
        return (
            "No tasks were planned for today. What are your plans for tomorrow?"
            if lang == "en"
            else "Aaj ke liye koi plan nahi tha. Kal kya karne ka plan hai?"
        )
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        s = streak.day_summary(today)
        return (
            "All tasks completed for today! Fantastic job.\n"
            f"Today's score: {s['score']}/{s['total']}\n"
            + streak.motivation(streak.current_streak(), s)
        ) if lang == "en" else (
            "Aaj ke saare tasks complete ho chuke hain! Shandar kaam.\n"
            f"Aaj ka score: {s['score']}/{s['total']}\n"
            + streak.motivation(streak.current_streak(), s)
        )

    label = today.strftime("%d %b %Y")
    lines = [
        f"Day-end Check-in ({label})! You have {len(pending)} pending task(s):\n"
        if lang == "en"
        else f"Day-end Check-in ({label})! Aaj {len(pending)} task abhi baaki hain:\n"
    ]
    lines.append(format_tasks_table(pending, lang))
    lines.append(
        "\nWhat did you finish before shutting down? (e.g. 'DSA done, lecture not finished')"
        if lang == "en"
        else "\nShut down karne se pehle batao kya-kya hua? (e.g. 'DSA ho gaya, lecture nahi ho paya')"
    )
    return "\n".join(lines)


# ---------- complete all today's tasks ----------
def do_complete_all_today(lang="en"):
    today = date.today()
    rows = db.get_tasks(today)
    if not rows:
        return T[lang]["no_plan_today"]

    changed = []
    for r in rows:
        if r["status"] != "done":
            db.update_status(r["id"], "done")
            changed.append(f"- {r['title']}: {STATUS[lang]['done']}")

    lines = []
    if changed:
        lines.append(T[lang]["all_done"])
        lines += changed
    else:
        lines.append(T[lang]["all_done"])
        for r in rows:
            lines.append(f"- {r['title']}: {STATUS[lang]['done']}")

    s = streak.day_summary(today)
    lines.append(f"\n{T[lang]['score'].format(score=s['score'], total=s['total'])}")
    lines.append(streak.motivation(streak.current_streak(), s))
    return "\n".join(lines)


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
    lines = [T[lang]["plan_for"].format(date=label) + "\n"]
    lines.append(format_tasks_table(rows, lang))
    return "\n".join(lines)


def format_upcoming(lang):
    rows = db.get_upcoming(date.today())
    if not rows:
        return T[lang]["no_upcoming"]
    lines = [T[lang]["upcoming"]]
    current = None
    group = []
    for r in rows:
        if r["plan_date"] != current:
            if current and group:
                lines.append(format_tasks_table(group, lang))
                lines.append("")
            current = r["plan_date"]
            lines.append(f"{current}:")
            group = [r]
        else:
            group.append(r)
    if group:
        lines.append(format_tasks_table(group, lang))
    return "\n".join(lines)


def get_startup_message(lang="en"):
    today = date.today()
    rows = db.get_tasks(today)
    label = today.strftime("%d %b %Y")
    lines = []

    # Today's plan in tabular format
    if rows:
        lines.append(f"Hi! Here is your plan for today ({label}):\n" if lang == "en" else f"Hi! Aaj ka plan ({label}):\n")
        lines.append(format_tasks_table(rows, lang))
    else:
        if lang == "en":
            lines.append(
                f"Hi! You have no tasks planned for today ({label}) yet.\n"
                "What would you like to achieve today? Tell me your plan (e.g. '4 DSA questions, 1 lecture')."
            )
        else:
            lines.append(
                f"Hi! Aaj ({label}) ke liye koi plan nahi hai.\n"
                "Aaj kya-kya karna hai batao (e.g. 'aaj 4 DSA question karne hain')."
            )

    # Missed check-in: earlier days with pending tasks in table format
    past_pending = db.get_past_pending(today)
    if past_pending:
        lines.append("\n" + ("You have pending tasks from earlier:\n" if lang == "en" else "Pehle ke kuch tasks abhi pending hain:\n"))
        lines.append(format_tasks_table(past_pending, lang))
        prompt = (
            "\nDid you finish any of these? (e.g. 'yesterday DSA done' or 'kal DSA ho gaya tha')"
            if lang == "en"
            else "\nKya inme se kuch complete hua tha? Bata sakte ho (e.g. 'kal DSA ho gaya tha')."
        )
        lines.append(prompt)
    elif rows:
        ending = "\nAll the best! Let me know as you make progress." if lang == "en" else "\nAll the best! Jab bhi koi task ho, mujhe batana."
        lines.append(ending)

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
def update_date_from_text(text):
    """Detect if an update refers to yesterday or a specific past date."""
    today = date.today()
    found = plan.find_date_in_text(text)
    if found:
        day, month = found
        try:
            d = date(today.year, month, day)
            if d <= today:
                return d
        except ValueError:
            pass
    lower = text.lower()
    if (
        re.search(r"\byesterday\b|\bbeeta kal\b|\bbeete kal\b", lower)
        or re.search(r"\bkal\b.*(?:\btha\b|\bthi\b|\bthe\b)", lower)
        or re.search(r"(?:\btha\b|\bthi\b|\bthe\b).*\bkal\b", lower)
    ):
        return today - timedelta(days=1)
    return None


def match_task(update, rows, used):
    best, best_score = None, 0.0
    for r in rows:
        if r["id"] in used:
            continue
        score = difflib.SequenceMatcher(None, update.title.lower(), r["title"].lower()).ratio()
        if r["type"] == update.type:
            score += 0.5
        # slight preference for today's tasks if match score is similar
        if str(r["plan_date"]) == str(date.today()):
            score += 0.1
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
    past_date = update_date_from_text(text)

    if past_date:
        rows = db.get_tasks(past_date)
    else:
        # candidate pool: today's tasks + past pending tasks
        rows = list(db.get_tasks(today)) + list(db.get_past_pending(today))

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

    used, changed_today, changed_past, missed = set(), [], {}, []
    for u in updates:
        row = match_task(u, rows, used)
        if row is None:
            missed.append(u.title)
            continue
        used.add(row["id"])
        status = final_status(u, row)
        db.update_status(row["id"], status)
        row_date = row["plan_date"]
        status_text = STATUS[lang][status]
        if row_date == str(today):
            changed_today.append(f"- {row['title']}: {status_text}")
        else:
            if row_date not in changed_past:
                changed_past[row_date] = []
            changed_past[row_date].append(f"- {row['title']}: {status_text}")

    lines = []
    if changed_today:
        lines.append(T[lang]["updated"])
        lines += changed_today
    for p_date, items in changed_past.items():
        lines.append(T[lang]["updated_past"].format(date=p_date))
        lines += items
    if missed:
        lines.append(T[lang]["no_match"].format(items=", ".join(missed)))

    pending = sum(1 for x in db.get_tasks(today) if x["status"] == "pending")
    if pending:
        lines.append(T[lang]["pending_left"].format(n=pending))
    elif changed_today:
        s = streak.day_summary(today)
        lines.append(T[lang]["score"].format(score=s["score"], total=s["total"]))
        lines.append(streak.motivation(streak.current_streak(), s))
    elif changed_past:
        cur_streak = streak.current_streak()
        if cur_streak > 0:
            lines.append(T[lang]["streak_update"].format(streak=cur_streak))

    return "\n".join(lines)


# ---------- universal offline document & media extractor ----------
def extract_text_from_file(file_path):
    p = Path(file_path)
    suffix = p.suffix.lower()

    # Plain text / Code files
    if suffix in (".txt", ".md", ".py", ".c", ".cpp", ".java", ".js", ".html", ".json"):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    # CSV Data
    if suffix == ".csv":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [f.readline() for _ in range(100)]
            return "".join(lines)

    # Word .docx
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(file_path) as z:
                xml_content = z.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                texts = [node.text for node in tree.iter() if node.text]
                return " ".join(texts)
        except Exception as e:
            return f"Error reading Word document: {e}"

    # PowerPoint .pptx
    if suffix == ".pptx":
        try:
            with zipfile.ZipFile(file_path) as z:
                slide_files = sorted(
                    [f for f in z.namelist() if f.startswith("ppt/slides/slide") and f.endswith(".xml")],
                    key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0,
                )
                slides_text = []
                for idx, sf in enumerate(slide_files, 1):
                    xml_content = z.read(sf)
                    tree = ET.fromstring(xml_content)
                    slide_text = " ".join(node.text for node in tree.iter() if node.text)
                    if slide_text.strip():
                        slides_text.append(f"[Slide {idx}]: {slide_text}")
                return "\n\n".join(slides_text)
        except Exception as e:
            return f"Error reading PowerPoint presentation: {e}"

    # Excel .xlsx
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(file_path) as z:
                if "xl/sharedStrings.xml" in z.namelist():
                    tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    texts = [node.text for node in tree.iter() if node.text]
                    return " | ".join(texts[:300])
                return "Excel sheet loaded."
        except Exception as e:
            return f"Error reading Excel sheet: {e}"

    # PDF
    if suffix == ".pdf":
        try:
            doc = fitz.open(file_path)
            extracted = ""
            for i in range(min(8, len(doc))):
                extracted += doc[i].get_text() + "\n"
            if len(extracted.strip()) > 80:
                return extracted
            else:
                # Scanned PDF: Render first page as image
                pix = doc[0].get_pixmap(dpi=150)
                temp_img = p.with_suffix(".scanned_page.png")
                pix.save(str(temp_img))
                return ("IMAGE", str(temp_img))
        except Exception as e:
            return f"Error reading PDF: {e}"

    # Images
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        return ("IMAGE", file_path)

    return f"Unsupported file type: {suffix}"


def analyze_study_image(image_path):
    """Analyze an uploaded note/study image offline using qwen3.5:4b vision (Summary only)."""
    prompt = (
        "You are an offline personal AI study assistant. The user uploaded an image/document.\n"
        "Provide a clear, helpful summary and key highlights of what is in this document.\n"
        "Reply in clean, elegant plain text. DO NOT use markdown asterisks (no '**'), no backticks, no hashtags.\n"
        "DO NOT generate practice questions or quizzes unless explicitly asked.\n\n"
        "Format exactly like this:\n\n"
        "DOCUMENT SUMMARY\n"
        "- Point 1 with key details\n"
        "- Point 2 with key details\n"
        "- Point 3 with key details"
    )
    try:
        r = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "user", "content": prompt, "images": [image_path]},
            ],
            think=False,
            options={"temperature": 0.2},
        )
        return r["message"]["content"].strip()
    except Exception as e:
        return f"Failed to analyze image offline: {e}"


def analyze_study_document(file_path):
    """Universal offline study analyzer for PDF, Word, PPT, Excel, Text, and Images."""
    p = Path(file_path)
    res = extract_text_from_file(file_path)

    LAST_DOCUMENT_CONTEXT["name"] = p.name

    # If it's an image (or scanned PDF page)
    if isinstance(res, tuple) and res[0] == "IMAGE":
        img_path = res[1]
        LAST_DOCUMENT_CONTEXT["is_image"] = True
        LAST_DOCUMENT_CONTEXT["image_path"] = img_path
        LAST_DOCUMENT_CONTEXT["text"] = ""
        out = analyze_study_image(img_path)
        hint = "\n\nTip: Want practice questions? Just type 'generate practice questions'!"
        return out + hint

    if isinstance(res, str) and (res.startswith("Error") or res.startswith("Unsupported")):
        return res

    LAST_DOCUMENT_CONTEXT["is_image"] = False
    LAST_DOCUMENT_CONTEXT["image_path"] = ""
    LAST_DOCUMENT_CONTEXT["text"] = res[:5000]

    doc_type = p.suffix.upper()[1:]
    prompt = (
        f"You are an offline personal AI study assistant. The user uploaded a {doc_type} document ({p.name}).\n"
        "Provide a clean, structured summary of the content and main takeaways.\n"
        "Reply in clean, elegant plain text. DO NOT use markdown asterisks (no '**'), no backticks, no hashtags.\n"
        "DO NOT generate practice questions or quizzes unless explicitly asked.\n\n"
        "Format exactly like this:\n\n"
        "DOCUMENT SUMMARY\n"
        "- Point 1 with key details\n"
        "- Point 2 with key details\n"
        "- Point 3 with key details\n\n"
        f"CONTENT EXCERPT:\n{res[:4500]}"
    )
    try:
        r = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            options={"temperature": 0.2},
        )
        hint = "\n\nTip: Want practice questions? Just type 'generate practice questions'!"
        return r["message"]["content"].strip() + hint
    except Exception as e:
        return f"Failed to analyze {doc_type} document offline: {e}"


def do_generate_practice_questions(lang="en"):
    """Generate 3 practice questions on-demand from the last uploaded document."""
    if not LAST_DOCUMENT_CONTEXT["name"]:
        return (
            "No document is currently attached. Please upload a document or note first using the Attachment button!"
            if lang == "en"
            else "Abhi koi document attached nahi hai. Pehle 'Attachment' button se document ya note upload karein!"
        )

    doc_name = LAST_DOCUMENT_CONTEXT["name"]

    if LAST_DOCUMENT_CONTEXT["is_image"]:
        prompt = (
            f"Based on the previously uploaded study image ({doc_name}), generate exactly 3 practice questions "
            "to test the student's understanding. Provide a concise answer for each question.\n"
            "Format in clean plain text. DO NOT use markdown asterisks (no '**'), no backticks, no hashtags.\n\n"
            "Format:\n"
            "PRACTICE QUESTIONS\n"
            "1. Question 1?\n"
            "   Answer: Concise answer.\n\n"
            "2. Question 2?\n"
            "   Answer: Concise answer.\n\n"
            "3. Question 3?\n"
            "   Answer: Concise answer."
        )
        try:
            r = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt, "images": [LAST_DOCUMENT_CONTEXT["image_path"]]}],
                think=False,
                options={"temperature": 0.2},
            )
            return r["message"]["content"].strip()
        except Exception as e:
            return f"Error generating questions: {e}"
    else:
        text_excerpt = LAST_DOCUMENT_CONTEXT["text"]
        prompt = (
            f"Based on the uploaded document ({doc_name}), generate exactly 3 practice questions "
            "to test the student's understanding. Provide a concise answer for each question.\n"
            "Format in clean plain text. DO NOT use markdown asterisks (no '**'), no backticks, no hashtags.\n\n"
            "Format:\n"
            "PRACTICE QUESTIONS\n"
            "1. Question 1?\n"
            "   Answer: Concise answer.\n\n"
            "2. Question 2?\n"
            "   Answer: Concise answer.\n\n"
            "3. Question 3?\n"
            "   Answer: Concise answer.\n\n"
            f"DOCUMENT CONTENT:\n{text_excerpt[:4000]}"
        )
        try:
            r = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                think=False,
                options={"temperature": 0.2},
            )
            return r["message"]["content"].strip()
        except Exception as e:
            return f"Error generating questions: {e}"


# ---------- general chat ----------
def do_chat(text, history, lang):
    language = (
        "Reply in simple, natural Hinglish (Hindi written in English letters)."
        if lang == "hi"
        else "Reply in English."
    )
    system = (
        "You are a friendly personal AI assistant running offline on the student's laptop. "
        f"Today's date is {date.today().strftime('%A, %d %B %Y')}. "
        f"{language}\n"
        "RULES FOR BREVITY & STYLE:\n"
        "1. For greetings and casual conversation (like 'kaise ho', 'hi', 'kya haal hai', 'hello', 'aur batao'), "
        "reply in strictly 1 short sentence! Do not lecture or write long paragraphs. "
        "Example: 'Main badhiya hoon! Aaj ka plan kaisa chal raha hai?'\n"
        "2. For general questions or study topics, give a clear, direct answer in at most 2-3 short lines.\n"
        "3. Plain text only: never use raw markdown asterisks '**', backticks or hashtags."
    )
    messages = [{"role": "system", "content": system}] + history[-6:]
    messages.append({"role": "user", "content": text})
    r = ollama.chat(model=MODEL, messages=messages, think=False, options={"temperature": 0.2})
    return r["message"]["content"].strip()


def handle(text, history):
    lang = "hi" if is_hinglish(text) else "en"

    # Fast-path 1: Cancel / Delete plan (MUST be before update_status and router!)
    if CANCEL_RE.search(text.strip().lower()):
        reply = do_cancel_plan(text, lang)
        intent = "cancel_plan"
    # Fast-path 2: On-demand practice questions for last uploaded document
    elif PRACTICE_QUESTIONS_RE.search(text.strip().lower()):
        reply = do_generate_practice_questions(lang)
        intent = "practice_questions"
    # Fast-path 3: "completed all work today" / "sab kaam ho gaya"
    elif ALL_DONE_RE.search(text.strip().lower()):
        reply = do_complete_all_today(lang)
        intent = "update_status"
    # Fast-path 4: day-end checkin shortcut
    elif CHECKIN_RE.search(text.strip().lower()):
        reply = do_checkin(lang)
        intent = "checkin"
    else:
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

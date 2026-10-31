from datetime import date, timedelta

import db

SCORE = {"done": 1.0, "partly": 0.5, "not_done": 0.0}


def day_summary(d):
    rows = db.get_tasks(d)
    if not rows:
        return None
    return {
        "total": len(rows),
        "score": sum(SCORE.get(r["status"], 0) for r in rows),
        "pending": any(r["status"] == "pending" for r in rows),
    }


def current_streak():
    d = date.today()
    s = day_summary(d)
    if s is None or s["pending"]:
        d -= timedelta(days=1)  # today isn't finished yet, so start counting from yesterday
    streak = 0
    while True:
        s = day_summary(d)
        if s is None or s["pending"] or s["score"] / s["total"] < 0.5:
            break
        streak += 1
        d -= timedelta(days=1)
    return streak


def motivation(days, s):
    pct = s["score"] / s["total"]
    if pct == 1:
        msg = "Perfect day! Fantastic work."
    elif pct >= 0.5:
        msg = "Good job today. Try to finish the remaining work tomorrow."
    else:
        msg = "A slower day, and that's okay. Pick one small task tomorrow and start again."

    if days >= 7:
        msg += f" {days}-day streak, this is becoming a habit!"
    elif days >= 3:
        msg += f" {days} days in a row, don't break it now."
    elif days >= 1:
        msg += f" Your streak has started: {days} day."
    return msg
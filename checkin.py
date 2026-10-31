from datetime import date

import db
import streak


STATUS_MAP = {"d": "done", "p": "partly", "n": "not_done"}


def show_today():
    rows = db.get_tasks(date.today())
    if not rows:
        print("Aaj ke liye koi plan nahi hai.")
        return
    print("Aaj ka plan:")
    for row in rows:
        target = f" (target: {row['target']})" if row["target"] else ""
        print(f"- {row['title']}{target} [{row['status']}]")

def show_upcoming():
    rows = db.get_upcoming(date.today())
    if not rows:
        print("Aane wale dino ke liye koi plan nahi hai.")
        return
    current = None
    for row in rows:
        if row["plan_date"] != current:
            current = row["plan_date"]
            print(f"\n{current}:")
        print(f"- {row['title']} [{row['status']}]")
    


def day_end_checkin():
    rows = [r for r in db.get_tasks(date.today()) if r["status"] == "pending"]
    if not rows:
        print("Check-in karne ke liye koi pending task nahi hai.")
        return
    print("Day End check-in. Har task ke liye likho: d = done, p = partly, n = not done")
    for row in rows:
        while True:
            ans = input(f"{row['title']}? (d/p/n): ").strip().lower()
            if ans in STATUS_MAP:
                break
            print("Sirf d, p ya n likho.")
        db.update_status(row["id"], STATUS_MAP[ans])

    s = streak.day_summary(date.today())
    n = streak.current_streak()
    print(f"\nAaj ka score: {s['score']}/{s['total']} (done = 1, partly = 0.5)")
    print(f"Streak: {n} din")
    print(streak.motivation(n, s))


if __name__ == "__main__":
    db.init_db()
    choice = input("1 = aaj ka plan, 2 = Day End check-in, 3 = aane wale dino ka plan: ").strip()
    if choice == "1":
        show_today()
    elif choice == "2":
        day_end_checkin()
    elif choice == "3":
        show_upcoming()
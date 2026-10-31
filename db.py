from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).resolve().parent / "agent.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_date TEXT NOT NULL,
                title TEXT NOT NULL,
                target INTEGER,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )


def save_tasks(plan_date, tasks):
    """Saves new tasks, skips ones already in that day's plan. Returns the tasks actually added."""
    added = []
    with get_conn() as conn:
        for t in tasks:
            exists = conn.execute(
                "SELECT 1 FROM tasks WHERE plan_date = ? AND lower(title) = lower(?) "
                "AND COALESCE(target, -1) = COALESCE(?, -1)",
                (str(plan_date), t.title, t.target),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO tasks (plan_date, title, target, type) VALUES (?, ?, ?, ?)",
                    (str(plan_date), t.title, t.target, t.type),
                )
                added.append(t)
    return added


def get_tasks(plan_date):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE plan_date = ?", (str(plan_date),)
        ).fetchall()


def update_status(task_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))


def get_upcoming(from_date):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE plan_date >= ? ORDER BY plan_date, id",
            (str(from_date),),
        ).fetchall()


def get_past_pending(before_date):
    """Returns pending tasks from days prior to before_date."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE plan_date < ? AND status = 'pending' ORDER BY plan_date, id",
            (str(before_date),),
        ).fetchall()


def get_plan_dates():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT plan_date FROM tasks ORDER BY plan_date"
        ).fetchall()
    return [r["plan_date"] for r in rows]

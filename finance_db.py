import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "svgtracker.db"

EMPTY_TRAINING_STATE = {
    "goals": [],
    "plan": {},
    "planOverrides": {},
    "history": [],
    "attendance": {},
    "planMeta": {"effectiveFrom": None, "dayUpdatedAt": {}},
    "activeWorkout": None,
    "sync": {"revision": 0, "updatedAt": None, "resetAt": None, "activeWorkoutClearedAt": None, "deviceId": None, "tombstones": {"goals": {}, "goalEntries": {}, "planOverrides": {}}},
}


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                photo_url TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS finance(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                monthly_income REAL DEFAULT 0,
                month TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS mandatory_expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT, category TEXT, amount REAL, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT, category TEXT, amount REAL, date TEXT
            );
            CREATE TABLE IF NOT EXISTS debts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                person TEXT, amount REAL, debt_type TEXT, status TEXT, date TEXT
            );
            CREATE TABLE IF NOT EXISTS training_state(
                user_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def get_or_create_user(data):
    telegram_id = str(data["id"])
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if row:
            db.execute(
                "UPDATE users SET username=?, first_name=?, last_name=?, photo_url=? WHERE id=?",
                (
                    data.get("username"),
                    data.get("first_name"),
                    data.get("last_name"),
                    data.get("photo_url"),
                    row["id"],
                ),
            )
            return row["id"]
        cur = db.execute(
            "INSERT INTO users(telegram_id,username,first_name,last_name,photo_url,created_at) VALUES(?,?,?,?,?,?)",
            (
                telegram_id,
                data.get("username"),
                data.get("first_name"),
                data.get("last_name"),
                data.get("photo_url"),
                now,
            ),
        )
        return cur.lastrowid


def get_training_state(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT state_json, updated_at FROM training_state WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return None
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            state = dict(EMPTY_TRAINING_STATE)
        return {"state": state, "updated_at": row["updated_at"]}


def save_training_state(user_id, state):
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with connect() as db:
        db.execute(
            """
            INSERT INTO training_state(user_id, state_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (user_id, payload, now),
        )
    return now


def reset_user_data(telegram_id):
    """Clear mutable data for one Telegram user without deleting the user identity row."""
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE telegram_id=?", (str(telegram_id),)
        ).fetchone()
        if not row:
            return False

        user_id = row["id"]
        for table in ("finance", "mandatory_expenses", "expenses", "debts"):
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        reset_state = dict(EMPTY_TRAINING_STATE)
        reset_state["sync"] = {
            "revision": 0,
            "updatedAt": now,
            "resetAt": now,
            "activeWorkoutClearedAt": now,
            "deviceId": None,
            "tombstones": {"goals": {}, "goalEntries": {}, "planOverrides": {}},
        }
        empty_payload = json.dumps(reset_state, ensure_ascii=False, separators=(",", ":"))
        db.execute(
            """
            INSERT INTO training_state(user_id, state_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (user_id, empty_payload, now),
        )
        return True

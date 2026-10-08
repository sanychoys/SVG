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

EMPTY_FINANCE_STATE = {
    "version": 4,
    "monthlyIncome": 0,
    "monthlyBudgets": {},
    "categories": [],
    "mandatoryExpenses": [],
    "expenses": [],
    "debts": [],
    "sync": {
        "updatedAt": None,
        "resetAt": None,
        "budgetUpdatedAt": None,
        "tombstones": {"expenses": {}, "mandatoryExpenses": {}, "debts": {}, "categories": {}},
    },
}



def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
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
            CREATE TABLE IF NOT EXISTS finance_state(
                user_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_settings(
                user_id INTEGER PRIMARY KEY,
                bot_notifications INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS friend_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_user_id INTEGER NOT NULL,
                receiver_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(sender_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(receiver_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_friend_requests_receiver
                ON friend_requests(receiver_user_id, status);
            CREATE INDEX IF NOT EXISTS idx_friend_requests_sender
                ON friend_requests(sender_user_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_friend_requests_pending_unique
                ON friend_requests(sender_user_id, receiver_user_id) WHERE status='pending';
            CREATE TABLE IF NOT EXISTS friendships(
                user_id INTEGER NOT NULL,
                friend_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, friend_user_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(friend_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_blocks(
                blocker_user_id INTEGER NOT NULL,
                blocked_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(blocker_user_id, blocked_user_id),
                FOREIGN KEY(blocker_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(blocked_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_user_blocks_blocked
                ON user_blocks(blocked_user_id);
            """
        )


def get_or_create_user(data):
    telegram_id = str(data["id"])
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        now = utc_now()
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
            db.execute(
                "INSERT OR IGNORE INTO user_settings(user_id, bot_notifications, updated_at) VALUES(?,1,?)",
                (row["id"], now),
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
        user_id = cur.lastrowid
        db.execute(
            "INSERT INTO user_settings(user_id, bot_notifications, updated_at) VALUES(?,1,?)",
            (user_id, now),
        )
        return user_id


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
    now = utc_now()
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


def get_finance_state(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT state_json, updated_at FROM finance_state WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return None
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            state = {}
        return {"state": state, "updated_at": row["updated_at"]}


def save_finance_state(user_id, state):
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    now = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO finance_state(user_id, state_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (user_id, payload, now),
        )
    return now


def user_public_dict(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "photo_url": row["photo_url"],
    }


def get_user_settings(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT bot_notifications, updated_at FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            now = utc_now()
            db.execute(
                "INSERT INTO user_settings(user_id, bot_notifications, updated_at) VALUES(?,1,?)",
                (user_id, now),
            )
            return {"bot_notifications": True, "updated_at": now}
        return {
            "bot_notifications": bool(row["bot_notifications"]),
            "updated_at": row["updated_at"],
        }


def set_bot_notifications(user_id, enabled):
    now = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO user_settings(user_id, bot_notifications, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                bot_notifications=excluded.bot_notifications,
                updated_at=excluded.updated_at
            """,
            (user_id, 1 if enabled else 0, now),
        )
    return {"bot_notifications": bool(enabled), "updated_at": now}


def get_user_telegram_id(user_id):
    with connect() as db:
        row = db.execute("SELECT telegram_id FROM users WHERE id=?", (user_id,)).fetchone()
        return row["telegram_id"] if row else None


def get_profile_data(user_id):
    settings = get_user_settings(user_id)
    with connect() as db:
        friends = db.execute(
            """
            SELECT u.id, u.username, u.first_name, u.last_name, u.photo_url
            FROM friendships f
            JOIN users u ON u.id=f.friend_user_id
            WHERE f.user_id=?
            ORDER BY COALESCE(NULLIF(u.first_name,''), NULLIF(u.username,''), u.telegram_id) COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
        incoming = db.execute(
            """
            SELECT r.id AS request_id, r.created_at, u.id, u.username, u.first_name, u.last_name, u.photo_url
            FROM friend_requests r
            JOIN users u ON u.id=r.sender_user_id
            WHERE r.receiver_user_id=? AND r.status='pending'
            ORDER BY r.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        outgoing = db.execute(
            """
            SELECT r.id AS request_id, r.created_at, u.id, u.username, u.first_name, u.last_name, u.photo_url
            FROM friend_requests r
            JOIN users u ON u.id=r.receiver_user_id
            WHERE r.sender_user_id=? AND r.status='pending'
            ORDER BY r.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        blocked = db.execute(
            """
            SELECT u.id, u.username, u.first_name, u.last_name, u.photo_url, b.created_at
            FROM user_blocks b
            JOIN users u ON u.id=b.blocked_user_id
            WHERE b.blocker_user_id=?
            ORDER BY b.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return {
        # The setting currently controls one concrete notification class: friend requests.
        "notifications_enabled": settings["bot_notifications"],
        "friends": [user_public_dict(row) for row in friends],
        "incoming": [dict(user_public_dict(row), request_id=row["request_id"], created_at=row["created_at"]) for row in incoming],
        "outgoing": [dict(user_public_dict(row), request_id=row["request_id"], created_at=row["created_at"]) for row in outgoing],
        "blocked": [dict(user_public_dict(row), blocked_at=row["created_at"]) for row in blocked],
    }


def users_are_blocked(db, first_user_id, second_user_id):
    return db.execute(
        """
        SELECT 1 FROM user_blocks
        WHERE (blocker_user_id=? AND blocked_user_id=?)
           OR (blocker_user_id=? AND blocked_user_id=?)
        LIMIT 1
        """,
        (first_user_id, second_user_id, second_user_id, first_user_id),
    ).fetchone() is not None


def block_user(user_id, target_user_id):
    if int(user_id) == int(target_user_id):
        return False
    now = utc_now()
    with connect() as db:
        target = db.execute("SELECT id FROM users WHERE id=?", (target_user_id,)).fetchone()
        if not target:
            return False
        db.execute(
            "INSERT OR IGNORE INTO user_blocks(blocker_user_id, blocked_user_id, created_at) VALUES(?,?,?)",
            (user_id, target_user_id, now),
        )
        # Blocking is definitive: remove friendship and any pending requests in either direction.
        db.execute(
            "DELETE FROM friendships WHERE (user_id=? AND friend_user_id=?) OR (user_id=? AND friend_user_id=?)",
            (user_id, target_user_id, target_user_id, user_id),
        )
        db.execute(
            """
            UPDATE friend_requests SET status='rejected', updated_at=?
            WHERE status='pending' AND ((sender_user_id=? AND receiver_user_id=?) OR (sender_user_id=? AND receiver_user_id=?))
            """,
            (now, user_id, target_user_id, target_user_id, user_id),
        )
        return True


def unblock_user(user_id, target_user_id):
    with connect() as db:
        cur = db.execute(
            "DELETE FROM user_blocks WHERE blocker_user_id=? AND blocked_user_id=?",
            (user_id, target_user_id),
        )
        return cur.rowcount > 0


def create_friend_request(user_id, username):
    clean = str(username or "").strip().lstrip("@").lower()
    if not clean:
        return {"status": "invalid"}
    with connect() as db:
        target = db.execute(
            "SELECT id, telegram_id, username, first_name, last_name, photo_url FROM users WHERE lower(username)=?",
            (clean,),
        ).fetchone()
        if not target:
            return {"status": "not_found"}
        target_id = target["id"]
        if target_id == user_id:
            return {"status": "self"}
        if users_are_blocked(db, user_id, target_id):
            # Do not reveal which side created the block.
            return {"status": "blocked"}
        existing_friend = db.execute(
            "SELECT 1 FROM friendships WHERE user_id=? AND friend_user_id=?",
            (user_id, target_id),
        ).fetchone()
        if existing_friend:
            return {"status": "already_friends", "target": user_public_dict(target)}

        reverse = db.execute(
            """
            SELECT id FROM friend_requests
            WHERE sender_user_id=? AND receiver_user_id=? AND status='pending'
            ORDER BY id DESC LIMIT 1
            """,
            (target_id, user_id),
        ).fetchone()
        now = utc_now()
        if reverse:
            request_id = reverse["id"]
            db.execute("UPDATE friend_requests SET status='accepted', updated_at=? WHERE id=?", (now, request_id))
            db.execute("INSERT OR IGNORE INTO friendships(user_id, friend_user_id, created_at) VALUES(?,?,?)", (user_id, target_id, now))
            db.execute("INSERT OR IGNORE INTO friendships(user_id, friend_user_id, created_at) VALUES(?,?,?)", (target_id, user_id, now))
            return {"status": "accepted", "target": user_public_dict(target), "target_telegram_id": target["telegram_id"]}

        pending = db.execute(
            """
            SELECT id FROM friend_requests
            WHERE sender_user_id=? AND receiver_user_id=? AND status='pending'
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, target_id),
        ).fetchone()
        if pending:
            return {"status": "pending", "target": user_public_dict(target)}

        cur = db.execute(
            "INSERT INTO friend_requests(sender_user_id,receiver_user_id,status,created_at,updated_at) VALUES(?,?,'pending',?,?)",
            (user_id, target_id, now, now),
        )
        return {
            "status": "created",
            "request_id": cur.lastrowid,
            "target": user_public_dict(target),
            "target_user_id": target_id,
            "target_telegram_id": target["telegram_id"],
        }


def resolve_friend_request(user_id, request_id, accept):
    now = utc_now()
    with connect() as db:
        row = db.execute(
            "SELECT id,sender_user_id,receiver_user_id,status FROM friend_requests WHERE id=?",
            (request_id,),
        ).fetchone()
        if not row or row["receiver_user_id"] != user_id or row["status"] != "pending":
            return False
        sender_id = row["sender_user_id"]
        if accept and users_are_blocked(db, user_id, sender_id):
            db.execute("UPDATE friend_requests SET status='rejected', updated_at=? WHERE id=?", (now, request_id))
            return False
        status = "accepted" if accept else "rejected"
        db.execute("UPDATE friend_requests SET status=?, updated_at=? WHERE id=?", (status, now, request_id))
        if accept:
            db.execute("INSERT OR IGNORE INTO friendships(user_id, friend_user_id, created_at) VALUES(?,?,?)", (user_id, sender_id, now))
            db.execute("INSERT OR IGNORE INTO friendships(user_id, friend_user_id, created_at) VALUES(?,?,?)", (sender_id, user_id, now))
        return True


def remove_friend(user_id, friend_user_id):
    with connect() as db:
        exists = db.execute(
            "SELECT 1 FROM friendships WHERE user_id=? AND friend_user_id=?",
            (user_id, friend_user_id),
        ).fetchone()
        if not exists:
            return False
        db.execute(
            "DELETE FROM friendships WHERE (user_id=? AND friend_user_id=?) OR (user_id=? AND friend_user_id=?)",
            (user_id, friend_user_id, friend_user_id, user_id),
        )
        return True


def reset_user_data(telegram_id):
    """Clear mutable test data for one Telegram user without deleting identity or social settings."""
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE telegram_id=?", (str(telegram_id),)
        ).fetchone()
        if not row:
            return False

        user_id = row["id"]
        for table in ("finance", "mandatory_expenses", "expenses", "debts"):
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        now = utc_now()
        reset_finance = dict(EMPTY_FINANCE_STATE)
        reset_finance["sync"] = {
            "updatedAt": now,
            "resetAt": now,
            "budgetUpdatedAt": None,
            "tombstones": {"expenses": {}, "mandatoryExpenses": {}, "debts": {}, "categories": {}},
        }
        empty_finance_payload = json.dumps(reset_finance, ensure_ascii=False, separators=(",", ":"))
        db.execute(
            """
            INSERT INTO finance_state(user_id, state_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (user_id, empty_finance_payload, now),
        )

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

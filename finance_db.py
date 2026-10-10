import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
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
    "incomes": [],
    "debts": [],
    "sync": {
        "updatedAt": None,
        "resetAt": None,
        "budgetUpdatedAt": None,
        "tombstones": {"expenses": {}, "incomes": {}, "mandatoryExpenses": {}, "debts": {}, "categories": {}},
    },
}



def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _ClosingConnection(sqlite3.Connection):
    """Close SQLite handles on context exit, not just commit/rollback.

    sqlite3.Connection.__exit__ alone does NOT close a connection. This fixes
    connection descriptor leaks on common API paths which use `with connect()`.
    """
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30, factory=_ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
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
                friend_request_notifications INTEGER NOT NULL DEFAULT 1,
                timezone_name TEXT,
                timezone_offset_minutes INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shortcut_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shortcut_tokens_user
                ON shortcut_tokens(user_id, revoked_at);
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
            CREATE TABLE IF NOT EXISTS web_auth_requests(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                approved_at TEXT,
                consumed_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_web_auth_requests_expiry
                ON web_auth_requests(expires_at);
            CREATE TABLE IF NOT EXISTS web_sessions(
                session_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_web_sessions_user
                ON web_sessions(user_id, revoked_at);
            """
        )
        db.execute("PRAGMA journal_mode = WAL")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(user_settings)").fetchall()}
        if "friend_request_notifications" not in columns:
            db.execute(
                "ALTER TABLE user_settings ADD COLUMN friend_request_notifications INTEGER NOT NULL DEFAULT 1"
            )
            db.execute(
                "UPDATE user_settings SET friend_request_notifications=bot_notifications"
            )
        if "timezone_name" not in columns:
            db.execute("ALTER TABLE user_settings ADD COLUMN timezone_name TEXT")
        if "timezone_offset_minutes" not in columns:
            db.execute("ALTER TABLE user_settings ADD COLUMN timezone_offset_minutes INTEGER NOT NULL DEFAULT 0")


def get_or_create_user(data):
    telegram_id = str(data["id"])
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        now = utc_now()
        if row:
            db.execute(
                """
                UPDATE users SET
                    username=COALESCE(?, username),
                    first_name=COALESCE(?, first_name),
                    last_name=COALESCE(?, last_name),
                    photo_url=COALESCE(?, photo_url)
                WHERE id=?
                """,
                (
                    data.get("username"),
                    data.get("first_name"),
                    data.get("last_name"),
                    data.get("photo_url"),
                    row["id"],
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO user_settings(user_id, bot_notifications, friend_request_notifications, updated_at) VALUES(?,1,1,?)",
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
            "INSERT INTO user_settings(user_id, bot_notifications, friend_request_notifications, updated_at) VALUES(?,1,1,?)",
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


def create_shortcut_token(user_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        # Keep one active Action Button token per user. Issuing a new one revokes the old one.
        db.execute(
            "UPDATE shortcut_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (now, user_id),
        )
        db.execute(
            "INSERT INTO shortcut_tokens(user_id, token_hash, created_at) VALUES(?,?,?)",
            (user_id, token_hash, now),
        )
    return token


def revoke_shortcut_tokens(user_id):
    now = utc_now()
    with connect() as db:
        cur = db.execute(
            "UPDATE shortcut_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (now, user_id),
        )
    return cur.rowcount


def get_user_by_shortcut_token(token):
    clean = str(token or "").strip()
    if not clean or len(clean) > 200:
        return None
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        row = db.execute(
            """
            SELECT st.user_id, u.telegram_id, u.username, u.first_name, u.last_name
            FROM shortcut_tokens st
            JOIN users u ON u.id=st.user_id
            WHERE st.token_hash=? AND st.revoked_at IS NULL
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE shortcut_tokens SET last_used_at=? WHERE token_hash=?",
            (now, token_hash),
        )
        return dict(row)



def _iso_after(seconds=0, days=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds, days=days)).isoformat().replace("+00:00", "Z")


def create_web_auth_request(ttl_seconds=600):
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    expires_at = _iso_after(seconds=max(60, min(int(ttl_seconds), 1800)))
    with connect() as db:
        db.execute("DELETE FROM web_auth_requests WHERE expires_at < ? OR consumed_at IS NOT NULL", (now,))
        db.execute(
            "INSERT INTO web_auth_requests(token_hash, created_at, expires_at) VALUES(?,?,?)",
            (token_hash, now, expires_at),
        )
    return {"token": token, "expires_at": expires_at}


def approve_web_auth_request(token, user_id):
    clean = str(token or "").strip()
    if not clean or len(clean) > 160:
        return False
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        row = db.execute(
            "SELECT expires_at, consumed_at FROM web_auth_requests WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row or row["consumed_at"] is not None or row["expires_at"] < now:
            return False
        cur = db.execute(
            "UPDATE web_auth_requests SET user_id=?, approved_at=? WHERE token_hash=? AND consumed_at IS NULL",
            (user_id, now, token_hash),
        )
        return cur.rowcount > 0


def consume_web_auth_request(token):
    clean = str(token or "").strip()
    if not clean or len(clean) > 160:
        return {"status": "invalid"}
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        row = db.execute(
            """
            SELECT r.user_id, r.expires_at, r.approved_at, r.consumed_at,
                   u.telegram_id, u.username, u.first_name, u.last_name, u.photo_url
            FROM web_auth_requests r
            LEFT JOIN users u ON u.id=r.user_id
            WHERE r.token_hash=?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return {"status": "invalid"}
        if row["expires_at"] < now:
            return {"status": "expired"}
        if row["consumed_at"] is not None:
            return {"status": "consumed"}
        if not row["user_id"] or not row["approved_at"]:
            return {"status": "pending", "expires_at": row["expires_at"]}
        db.execute(
            "UPDATE web_auth_requests SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
            (now, token_hash),
        )
        return {
            "status": "approved",
            "user_id": row["user_id"],
            "telegram_id": row["telegram_id"],
            "username": row["username"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "photo_url": row["photo_url"],
        }


def create_web_session(user_id, ttl_days=30):
    token = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    expires_at = _iso_after(days=max(1, min(int(ttl_days), 90)))
    with connect() as db:
        db.execute("DELETE FROM web_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL", (now,))
        db.execute(
            "INSERT INTO web_sessions(session_hash, user_id, created_at, expires_at, last_seen_at) VALUES(?,?,?,?,?)",
            (token_hash, user_id, now, expires_at, now),
        )
    return {"token": token, "expires_at": expires_at}


def get_user_by_web_session(token):
    clean = str(token or "").strip()
    if not clean or len(clean) > 220:
        return None
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        row = db.execute(
            """
            SELECT s.user_id, s.expires_at, s.last_seen_at,
                   u.telegram_id, u.username, u.first_name, u.last_name, u.photo_url
            FROM web_sessions s
            JOIN users u ON u.id=s.user_id
            WHERE s.session_hash=? AND s.revoked_at IS NULL
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        if not row or row["expires_at"] < now:
            return None
        # Avoid a write on every API request; refresh the activity stamp at most hourly.
        last_seen = row["last_seen_at"] or ""
        if not last_seen or last_seen < _iso_after(seconds=-3600):
            db.execute("UPDATE web_sessions SET last_seen_at=? WHERE session_hash=?", (now, token_hash))
        return dict(row)


def revoke_web_session(token):
    clean = str(token or "").strip()
    if not clean or len(clean) > 220:
        return False
    token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        cur = db.execute(
            "UPDATE web_sessions SET revoked_at=? WHERE session_hash=? AND revoked_at IS NULL",
            (now, token_hash),
        )
        return cur.rowcount > 0


def list_user_web_sessions(user_id, current_token=''):
    """Public, privacy-minimised web session inventory for its owner."""
    current_hash = hashlib.sha256(str(current_token).encode('utf-8')).hexdigest() if current_token else None
    now = utc_now()
    with connect() as db:
        rows = db.execute(
            """SELECT session_hash, created_at, last_seen_at, expires_at
               FROM web_sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
               ORDER BY COALESCE(last_seen_at,created_at) DESC LIMIT 100""",
            (user_id, now),
        ).fetchall()
    return [{"id": row['session_hash'][:20], "created_at":row['created_at'],
             "last_seen_at":row['last_seen_at'],"expires_at":row['expires_at'],
             "current":row['session_hash']==current_hash} for row in rows]


def revoke_user_web_sessions(user_id, mode='others', session_id=None, current_token=''):
    """Revoke only the caller's sessions. One-session IDs are a hash prefix, not tokens."""
    current_hash = hashlib.sha256(str(current_token).encode('utf-8')).hexdigest() if current_token else None
    if mode not in {'others', 'one'}:
        raise ValueError('Invalid revocation mode')
    if mode == 'one' and (not isinstance(session_id,str) or len(session_id)!=20 or any(c not in '0123456789abcdef' for c in session_id)):
        raise ValueError('Invalid session identifier')
    now=utc_now()
    with connect() as db:
        if mode == 'others':
            if current_hash:
                result=db.execute("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND expires_at>? AND session_hash!=?",(now,user_id,now,current_hash))
            else:
                result=db.execute("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND expires_at>?",(now,user_id,now))
        else:
            if current_hash and session_id==current_hash[:20]:
                raise ValueError('Current session must use logout')
            result=db.execute("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND expires_at>? AND substr(session_hash,1,20)=?",(now,user_id,now,session_id))
        return result.rowcount


def get_user_settings(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT bot_notifications, friend_request_notifications, timezone_name, timezone_offset_minutes, updated_at FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            now = utc_now()
            db.execute(
                "INSERT INTO user_settings(user_id, bot_notifications, friend_request_notifications, updated_at) VALUES(?,1,1,?)",
                (user_id, now),
            )
            return {
                "bot_notifications": True,
                "friend_request_notifications": True,
                "timezone_name": None,
                "timezone_offset_minutes": 0,
                "updated_at": now,
            }
        return {
            "bot_notifications": bool(row["bot_notifications"]),
            "friend_request_notifications": bool(row["friend_request_notifications"]),
            "timezone_name": row["timezone_name"],
            "timezone_offset_minutes": int(row["timezone_offset_minutes"] or 0),
            "updated_at": row["updated_at"],
        }


def set_bot_notifications(user_id, enabled, kind="bot"):
    now = utc_now()
    column = "friend_request_notifications" if kind == "friends" else "bot_notifications"
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO user_settings(user_id, bot_notifications, friend_request_notifications, updated_at) VALUES(?,1,1,?)",
            (user_id, now),
        )
        db.execute(
            f"UPDATE user_settings SET {column}=?, updated_at=? WHERE user_id=?",
            (1 if enabled else 0, now, user_id),
        )
    settings = get_user_settings(user_id)
    settings["updated_at"] = now
    return settings



def set_user_timezone(user_id, timezone_name, offset_minutes):
    now = utc_now()
    name = str(timezone_name or "").strip()[:80] or None
    try:
        offset = int(offset_minutes)
    except (TypeError, ValueError):
        offset = 0
    offset = max(-14 * 60, min(14 * 60, offset))
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO user_settings(user_id, bot_notifications, friend_request_notifications, timezone_name, timezone_offset_minutes, updated_at) VALUES(?,1,1,?,?,?)",
            (user_id, name, offset, now),
        )
        db.execute(
            "UPDATE user_settings SET timezone_name=?, timezone_offset_minutes=?, updated_at=? WHERE user_id=?",
            (name, offset, now, user_id),
        )
    return get_user_settings(user_id)


def get_user_settings_by_telegram_id(telegram_id):
    with connect() as db:
        row = db.execute(
            """
            SELECT s.bot_notifications, s.friend_request_notifications, s.timezone_name,
                   s.timezone_offset_minutes, s.updated_at
            FROM users u
            JOIN user_settings s ON s.user_id=u.id
            WHERE u.telegram_id=?
            """,
            (str(telegram_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "bot_notifications": bool(row["bot_notifications"]),
            "friend_request_notifications": bool(row["friend_request_notifications"]),
            "timezone_name": row["timezone_name"],
            "timezone_offset_minutes": int(row["timezone_offset_minutes"] or 0),
            "updated_at": row["updated_at"],
        }

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
        # Keep notifications_enabled for older clients; new clients use explicit settings.
        "notifications_enabled": settings["bot_notifications"],
        "bot_notifications_enabled": settings["bot_notifications"],
        "friend_request_notifications_enabled": settings["friend_request_notifications"],
        "timezone_name": settings.get("timezone_name"),
        "timezone_offset_minutes": settings.get("timezone_offset_minutes", 0),
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
        # Shared schedule is a friendship feature. If the normalized schedule
        # tables are already installed, blocking also revokes direct shared
        # events owned by either side. The guard keeps finance_db compatible
        # with databases created before the schedule module is initialized.
        has_schedule = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_event_members'"
        ).fetchone()
        if has_schedule:
            db.execute(
                """
                DELETE FROM schedule_event_members
                WHERE user_id=? AND event_id IN (SELECT id FROM schedule_events WHERE owner_user_id=?)
                """,
                (target_user_id, user_id),
            )
            db.execute(
                """
                DELETE FROM schedule_event_members
                WHERE user_id=? AND event_id IN (SELECT id FROM schedule_events WHERE owner_user_id=?)
                """,
                (user_id, target_user_id),
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



def cancel_friend_request(user_id, request_id):
    """Cancel one pending outgoing friend request owned by the sender."""
    now = utc_now()
    with connect() as db:
        cur = db.execute(
            """
            UPDATE friend_requests
            SET status='cancelled', updated_at=?
            WHERE id=? AND sender_user_id=? AND status='pending'
            """,
            (now, request_id, user_id),
        )
        return cur.rowcount > 0

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
            "tombstones": {"expenses": {}, "incomes": {}, "mandatoryExpenses": {}, "debts": {}, "categories": {}},
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


def get_pending_friend_request_sender(receiver_id, request_id):
    """Return sender only when the target really received a pending request."""
    with connect() as db:
        row = db.execute("""SELECT u.id, u.telegram_id FROM friend_requests r
                JOIN users u ON u.id=r.sender_user_id
                WHERE r.id=? AND r.receiver_user_id=? AND r.status='pending'""",(request_id,receiver_id)).fetchone()
        return dict(row) if row else None

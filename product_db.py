import calendar
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finance_db import connect, utc_now

ALLOWED_RECURRENCE = {"none", "daily", "weekly", "monthly"}


def init_product_db():
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedule_events(
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                all_day INTEGER NOT NULL DEFAULT 0,
                recurrence TEXT NOT NULL DEFAULT 'none',
                recurrence_until TEXT,
                reminder_minutes INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_schedule_events_owner_start
                ON schedule_events(owner_user_id, starts_at);
            CREATE TABLE IF NOT EXISTS schedule_event_members(
                event_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'editor',
                created_at TEXT NOT NULL,
                PRIMARY KEY(event_id, user_id),
                FOREIGN KEY(event_id) REFERENCES schedule_events(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_schedule_members_user
                ON schedule_event_members(user_id, event_id);
            CREATE TABLE IF NOT EXISTS notes(
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                reminder_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notes_user_updated
                ON notes(user_id, archived, pinned, updated_at);
            CREATE TABLE IF NOT EXISTS notification_preferences(
                user_id INTEGER PRIMARY KEY,
                preferences_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS bot_reminder_log(
                user_id INTEGER NOT NULL,
                reminder_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(user_id, reminder_key),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )


def _user_public(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "photo_url": row["photo_url"],
    }


def _friend_ids(db, user_id):
    return {
        int(row["friend_user_id"])
        for row in db.execute("SELECT friend_user_id FROM friendships WHERE user_id=?", (user_id,)).fetchall()
    }


def _blocked_pair(db, a, b):
    return db.execute(
        """
        SELECT 1 FROM user_blocks
        WHERE (blocker_user_id=? AND blocked_user_id=?) OR (blocker_user_id=? AND blocked_user_id=?)
        LIMIT 1
        """,
        (a, b, b, a),
    ).fetchone() is not None


def _event_participants(db, event_id):
    rows = db.execute(
        """
        SELECT u.id,u.username,u.first_name,u.last_name,u.photo_url,m.role
        FROM schedule_event_members m
        JOIN users u ON u.id=m.user_id
        WHERE m.event_id=?
        ORDER BY CASE WHEN m.role='owner' THEN 0 ELSE 1 END,
                 COALESCE(NULLIF(u.first_name,''), NULLIF(u.username,''), u.telegram_id) COLLATE NOCASE
        """,
        (event_id,),
    ).fetchall()
    return [dict(_user_public(row), role=row["role"]) for row in rows]


def _event_dict(db, row, viewer_user_id):
    participants = _event_participants(db, row["id"])
    role_row = db.execute(
        "SELECT role FROM schedule_event_members WHERE event_id=? AND user_id=?",
        (row["id"], viewer_user_id),
    ).fetchone()
    role = role_row["role"] if role_row else None
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "title": row["title"],
        "details": row["details"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "all_day": bool(row["all_day"]),
        "recurrence": row["recurrence"],
        "recurrence_until": row["recurrence_until"],
        "reminder_minutes": row["reminder_minutes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "participants": participants,
        "role": role,
        "is_owner": int(row["owner_user_id"]) == int(viewer_user_id),
        "can_edit": role in {"owner", "editor"},
    }


def get_schedule_event(user_id, event_id):
    with connect() as db:
        row = db.execute("SELECT * FROM schedule_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return None
        member = db.execute(
            "SELECT role FROM schedule_event_members WHERE event_id=? AND user_id=?",
            (event_id, user_id),
        ).fetchone()
        if not member:
            return None
        if int(row["owner_user_id"]) != int(user_id) and _blocked_pair(db, row["owner_user_id"], user_id):
            return None
        return _event_dict(db, row, user_id)


def list_schedule_events(user_id):
    with connect() as db:
        rows = db.execute(
            """
            SELECT e.* FROM schedule_events e
            JOIN schedule_event_members m ON m.event_id=e.id
            WHERE m.user_id=?
            ORDER BY e.starts_at ASC, e.created_at ASC
            """,
            (user_id,),
        ).fetchall()
        visible = []
        for row in rows:
            if int(row["owner_user_id"]) != int(user_id) and _blocked_pair(db, row["owner_user_id"], user_id):
                continue
            visible.append(_event_dict(db, row, user_id))
        return visible


def create_schedule_event(user_id, event_id, payload):
    now = utc_now()
    participants = payload.get("participant_ids") or []
    clean_participants = []
    with connect() as db:
        friends = _friend_ids(db, user_id)
        for raw in participants:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid == int(user_id) or pid not in friends or _blocked_pair(db, user_id, pid):
                continue
            if pid not in clean_participants:
                clean_participants.append(pid)
        db.execute(
            """
            INSERT INTO schedule_events(
                id,owner_user_id,title,details,starts_at,ends_at,all_day,recurrence,recurrence_until,reminder_minutes,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                user_id,
                payload["title"],
                payload.get("details", ""),
                payload["starts_at"],
                payload.get("ends_at"),
                1 if payload.get("all_day") else 0,
                payload.get("recurrence", "none"),
                payload.get("recurrence_until"),
                payload.get("reminder_minutes"),
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO schedule_event_members(event_id,user_id,role,created_at) VALUES(?,?, 'owner', ?)",
            (event_id, user_id, now),
        )
        for pid in clean_participants:
            db.execute(
                "INSERT OR IGNORE INTO schedule_event_members(event_id,user_id,role,created_at) VALUES(?,?, 'editor', ?)",
                (event_id, pid, now),
            )
        row = db.execute("SELECT * FROM schedule_events WHERE id=?", (event_id,)).fetchone()
        return _event_dict(db, row, user_id)


def update_schedule_event(user_id, event_id, payload):
    now = utc_now()
    with connect() as db:
        event = db.execute("SELECT * FROM schedule_events WHERE id=?", (event_id,)).fetchone()
        if not event:
            return None, "not_found"
        membership = db.execute(
            "SELECT role FROM schedule_event_members WHERE event_id=? AND user_id=?",
            (event_id, user_id),
        ).fetchone()
        if not membership or membership["role"] not in {"owner", "editor"}:
            return None, "forbidden"
        if int(event["owner_user_id"]) != int(user_id) and _blocked_pair(db, event["owner_user_id"], user_id):
            return None, "forbidden"
        db.execute(
            """
            UPDATE schedule_events SET title=?,details=?,starts_at=?,ends_at=?,all_day=?,recurrence=?,recurrence_until=?,reminder_minutes=?,updated_at=?
            WHERE id=?
            """,
            (
                payload["title"], payload.get("details", ""), payload["starts_at"], payload.get("ends_at"),
                1 if payload.get("all_day") else 0, payload.get("recurrence", "none"), payload.get("recurrence_until"),
                payload.get("reminder_minutes"), now, event_id,
            ),
        )
        if int(event["owner_user_id"]) == int(user_id) and "participant_ids" in payload:
            friends = _friend_ids(db, user_id)
            keep = {int(user_id)}
            for raw in payload.get("participant_ids") or []:
                try:
                    pid = int(raw)
                except (TypeError, ValueError):
                    continue
                if pid in friends and not _blocked_pair(db, user_id, pid):
                    keep.add(pid)
                    db.execute(
                        "INSERT OR IGNORE INTO schedule_event_members(event_id,user_id,role,created_at) VALUES(?,?, 'editor', ?)",
                        (event_id, pid, now),
                    )
            placeholders = ",".join("?" for _ in keep)
            db.execute(
                f"DELETE FROM schedule_event_members WHERE event_id=? AND role!='owner' AND user_id NOT IN ({placeholders})",
                (event_id, *keep),
            )
        row = db.execute("SELECT * FROM schedule_events WHERE id=?", (event_id,)).fetchone()
        return _event_dict(db, row, user_id), None


def delete_or_leave_schedule_event(user_id, event_id):
    with connect() as db:
        event = db.execute("SELECT owner_user_id FROM schedule_events WHERE id=?", (event_id,)).fetchone()
        if not event:
            return "not_found"
        member = db.execute(
            "SELECT role FROM schedule_event_members WHERE event_id=? AND user_id=?", (event_id, user_id)
        ).fetchone()
        if not member:
            return "forbidden"
        if int(event["owner_user_id"]) == int(user_id):
            db.execute("DELETE FROM schedule_events WHERE id=?", (event_id,))
            return "deleted"
        db.execute("DELETE FROM schedule_event_members WHERE event_id=? AND user_id=?", (event_id, user_id))
        return "left"


def list_notes(user_id, include_archived=False):
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM notes WHERE user_id=? AND (?=1 OR archived=0)
            ORDER BY pinned DESC, updated_at DESC
            """,
            (user_id, 1 if include_archived else 0),
        ).fetchall()
        return [dict(row, pinned=bool(row["pinned"]), archived=bool(row["archived"])) for row in rows]


def save_note(user_id, note_id, payload):
    now = utc_now()
    with connect() as db:
        existing = db.execute("SELECT user_id,created_at FROM notes WHERE id=?", (note_id,)).fetchone()
        if existing and int(existing["user_id"]) != int(user_id):
            return None
        created_at = existing["created_at"] if existing else now
        db.execute(
            """
            INSERT INTO notes(id,user_id,title,body,pinned,archived,reminder_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,body=excluded.body,pinned=excluded.pinned,archived=excluded.archived,
                reminder_at=excluded.reminder_at,updated_at=excluded.updated_at
            """,
            (
                note_id,user_id,payload.get("title", ""),payload.get("body", ""),
                1 if payload.get("pinned") else 0,1 if payload.get("archived") else 0,
                payload.get("reminder_at"),created_at,now,
            ),
        )
        row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return dict(row, pinned=bool(row["pinned"]), archived=bool(row["archived"]))


def delete_note(user_id, note_id):
    with connect() as db:
        cur = db.execute("DELETE FROM notes WHERE id=? AND user_id=?", (note_id, user_id))
        return cur.rowcount > 0


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _add_month(dt):
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _occurrences(event, window_start, window_end):
    start = _parse_iso(event["starts_at"])
    if not start:
        return []
    recurrence = event["recurrence"] if event["recurrence"] in ALLOWED_RECURRENCE else "none"
    until = _parse_iso(event["recurrence_until"])
    if recurrence == "none":
        return [start] if window_start <= start <= window_end else []

    current = start
    # Long-running daily/weekly recurrences must not stop working after the
    # first few hundred occurrences. Jump close to the requested window first.
    if current < window_start and recurrence in {"daily", "weekly"}:
        period = timedelta(days=1 if recurrence == "daily" else 7)
        step_seconds = period.total_seconds()
        steps = max(0, int((window_start - current).total_seconds() // step_seconds))
        current += period * steps
        while current < window_start:
            current += period
    elif current < window_start and recurrence == "monthly":
        # Monthly arithmetic has variable month lengths, so advance by months.
        # 2400 iterations covers 200 years while remaining bounded.
        for _ in range(2400):
            if current >= window_start:
                break
            current = _add_month(current)

    out = []
    for _ in range(1000):
        if until and current > until:
            break
        if current > window_end:
            break
        if current >= window_start:
            out.append(current)
        if recurrence == "daily":
            current += timedelta(days=1)
        elif recurrence == "weekly":
            current += timedelta(days=7)
        else:
            current = _add_month(current)
    return out


def _timezone_for_row(row):
    name = str(row["timezone_name"] or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    return timezone(timedelta(minutes=int(row["timezone_offset_minutes"] or 0)))


def collect_due_reminders(now=None, lookback_minutes=30):
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=max(1, lookback_minutes))
    window_end = now + timedelta(seconds=20)
    reminders = []
    with connect() as db:
        event_rows = db.execute(
            """
            SELECT e.*,m.user_id,u.telegram_id,s.timezone_name,s.timezone_offset_minutes
            FROM schedule_events e
            JOIN schedule_event_members m ON m.event_id=e.id
            JOIN users u ON u.id=m.user_id
            JOIN user_settings s ON s.user_id=m.user_id
            WHERE s.bot_notifications=1 AND e.reminder_minutes IS NOT NULL
            """
        ).fetchall()
        for row in event_rows:
            member_count = db.execute("SELECT COUNT(*) FROM schedule_event_members WHERE event_id=?", (row['id'],)).fetchone()[0]
            category = 'shared_reminders' if member_count > 1 else 'personal_reminders'
            if not notification_enabled(row['user_id'], category, db):
                continue
            if int(row["owner_user_id"]) != int(row["user_id"]) and _blocked_pair(db, row["owner_user_id"], row["user_id"]):
                continue
            reminder_minutes = int(row["reminder_minutes"] or 0)
            occurrence_window_start = window_start + timedelta(minutes=reminder_minutes)
            occurrence_window_end = window_end + timedelta(minutes=reminder_minutes)
            for occurrence in _occurrences(row, occurrence_window_start, occurrence_window_end):
                remind_at = occurrence - timedelta(minutes=reminder_minutes)
                if not (window_start <= remind_at <= window_end):
                    continue
                key = f"schedule:{row['id']}:{occurrence.isoformat()}:{reminder_minutes}"
                exists = db.execute(
                    "SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?",
                    (row["user_id"], key),
                ).fetchone()
                if exists:
                    continue
                reminders.append({
                    "user_id": row["user_id"], "telegram_id": row["telegram_id"], "key": key,
                    "kind": "schedule", "title": row["title"], "when": occurrence,
                    "timezone": _timezone_for_row(row), "reminder_minutes": reminder_minutes,
                })

        note_rows = db.execute(
            """
            SELECT n.*,u.telegram_id,s.timezone_name,s.timezone_offset_minutes
            FROM notes n JOIN users u ON u.id=n.user_id JOIN user_settings s ON s.user_id=n.user_id
            WHERE s.bot_notifications=1 AND n.archived=0 AND n.reminder_at IS NOT NULL
            """
        ).fetchall()
        for row in note_rows:
            if not notification_enabled(row['user_id'], 'note_reminders', db):
                continue
            remind_at = _parse_iso(row["reminder_at"])
            if not remind_at or not (window_start <= remind_at <= window_end):
                continue
            key = f"note:{row['id']}:{remind_at.isoformat()}"
            if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row["user_id"], key)).fetchone():
                continue
            reminders.append({
                "user_id": row["user_id"], "telegram_id": row["telegram_id"], "key": key,
                "kind": "note", "title": row["title"] or "Заметка", "body": row["body"],
                "when": remind_at, "timezone": _timezone_for_row(row),
            })

        finance_rows = db.execute(
            """
            SELECT f.user_id,f.state_json,u.telegram_id,s.timezone_name,s.timezone_offset_minutes
            FROM finance_state f JOIN users u ON u.id=f.user_id JOIN user_settings s ON s.user_id=f.user_id
            WHERE s.bot_notifications=1
            """
        ).fetchall()
        for row in finance_rows:
            try:
                state = json.loads(row["state_json"] or "{}")
            except json.JSONDecodeError:
                continue
            tz = _timezone_for_row(row)
            local_now = now.astimezone(tz)
            local_date = local_now.date().isoformat()
            # Finance reminders are intentionally sent once on the due date, after 09:00 local time.
            if local_now.hour < 9:
                continue
            for item in (state.get("mandatoryExpenses") or []) if notification_enabled(row['user_id'], 'finance_payments', db) else []:
                due = str(item.get("dueDate") or item.get("date") or "")[:10]
                if due != local_date or item.get("paid") is True:
                    continue
                key = f"finance:mandatory:{item.get('id')}:{due}"
                if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row["user_id"], key)).fetchone():
                    continue
                reminders.append({"user_id":row["user_id"],"telegram_id":row["telegram_id"],"key":key,"kind":"mandatory","title":item.get("title") or "Обязательный расход","amount":item.get("amount"),"when":now,"timezone":tz})
            for item in (state.get("debts") or []) if notification_enabled(row['user_id'], 'finance_debts', db) else []:
                due = str(item.get("dueDate") or "")[:10]
                if due != local_date or item.get("status") == "closed":
                    continue
                key = f"finance:debt:{item.get('id')}:{due}"
                if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row["user_id"], key)).fetchone():
                    continue
                reminders.append({"user_id":row["user_id"],"telegram_id":row["telegram_id"],"key":key,"kind":"debt","title":item.get("person") or "Долг","amount":item.get("amount"),"debt_kind":item.get("kind"),"when":now,"timezone":tz})
    return reminders


def mark_reminder_sent(user_id, key):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO bot_reminder_log(user_id,reminder_key,sent_at) VALUES(?,?,?)",
            (user_id, key, utc_now()),
        )


def reset_product_data_for_telegram(telegram_id):
    with connect() as db:
        row = db.execute("SELECT id FROM users WHERE telegram_id=?", (str(telegram_id),)).fetchone()
        if not row:
            return False
        user_id = row["id"]
        # Delete events owned by the user, leave events owned by friends.
        owned = [r["id"] for r in db.execute("SELECT id FROM schedule_events WHERE owner_user_id=?", (user_id,)).fetchall()]
        for event_id in owned:
            db.execute("DELETE FROM schedule_events WHERE id=?", (event_id,))
        db.execute("DELETE FROM schedule_event_members WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM notes WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM bot_reminder_log WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM notification_preferences WHERE user_id=?", (user_id,))
        return True


NOTIFICATION_DEFAULTS = {
    "personal_reminders": True, "shared_reminders": True,
    "note_reminders": True, "finance_payments": True, "finance_debts": True,
    "shared_created": True, "shared_updated": True, "shared_removed": True,
    "friend_accepted": True,
    "share_busy": False,  # Friends see occupancy only after explicit opt-in
}


def get_notification_preferences(user_id, db=None):
    if db is None:
        with connect() as connection:
            return get_notification_preferences(user_id, connection)
    row = db.execute("SELECT preferences_json FROM notification_preferences WHERE user_id=?", (user_id,)).fetchone()
    try:
        overrides = json.loads(row["preferences_json"]) if row else {}
    except (TypeError, json.JSONDecodeError):
        overrides = {}
    if not isinstance(overrides, dict):
        overrides = {}
    return {key: overrides.get(key) if type(overrides.get(key)) is bool else default for key, default in NOTIFICATION_DEFAULTS.items()}


def update_notification_preferences(user_id, patch):
    if not isinstance(patch, dict) or not patch or any(k not in NOTIFICATION_DEFAULTS or type(v) is not bool for k,v in patch.items()):
        raise ValueError("Invalid notification preference")
    with connect() as db:
        result = get_notification_preferences(user_id, db)
        result.update(patch)
        db.execute("""INSERT INTO notification_preferences(user_id,preferences_json,updated_at) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET preferences_json=excluded.preferences_json, updated_at=excluded.updated_at""",
                (user_id, json.dumps(result, separators=(',',':')), utc_now()))
        return result


def notification_enabled(user_id, category, db=None):
    if db is None:
        with connect() as connection:
            return notification_enabled(user_id, category, connection)
    row = db.execute("SELECT bot_notifications FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["bot_notifications"]) and get_notification_preferences(user_id, db).get(category, False)


def list_busy_availability(viewer_id, user_ids, start, end):
    """Give friends permission-gated occupancy, never titles/locations/descriptions."""
    start_dt, end_dt = _parse_iso(start), _parse_iso(end)
    if not start_dt or not end_dt or end_dt <= start_dt or end_dt-start_dt > timedelta(days=2):
        raise ValueError("Invalid availability interval")
    requested = [int(viewer_id)]
    for raw in (user_ids or []):
        try:
            uid = int(raw)
        except (ValueError,TypeError):
            continue
        if uid not in requested:
            requested.append(uid)
    out=[]
    with connect() as db:
        friends=_friend_ids(db,viewer_id)
        for uid in requested[:31]:
            if uid!=int(viewer_id) and (uid not in friends or _blocked_pair(db,viewer_id,uid)):
                continue
            if uid!=int(viewer_id) and not get_notification_preferences(uid,db)['share_busy']:
                out.append({'user_id':uid,'private':True,'busy':[]})
                continue
            events=db.execute("""SELECT e.* FROM schedule_events e JOIN schedule_event_members m ON m.event_id=e.id
                                WHERE m.user_id=?""",(uid,)).fetchall()
            intervals=[]
            for event in events:
                if uid!=int(event['owner_user_id']) and _blocked_pair(db,event['owner_user_id'],uid):
                    continue
                base=_parse_iso(event['starts_at']); base_end=_parse_iso(event['ends_at'])
                if not base:continue
                duration=max(timedelta(minutes=1), (base_end-base) if base_end else (timedelta(days=1) if event['all_day'] else timedelta(hours=1)))
                for occurrence in _occurrences(event,start_dt-min(duration,timedelta(days=7)),end_dt):
                    a,b=max(start_dt,occurrence),min(end_dt,occurrence+duration)
                    if b>a:intervals.append((a,b))
            intervals.sort()
            merged=[]
            for a,b in intervals:
                if merged and a<=merged[-1][1]:
                    merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
                else:merged.append((a,b))
            out.append({'user_id':uid,'private':False,'busy':[{'start':a.isoformat(),'end':b.isoformat()} for a,b in merged]})
    return out

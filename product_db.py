import calendar
import json
import sqlite3
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finance_db import connect, utc_now

ALLOWED_RECURRENCE = {"none", "daily", "weekly", "monthly"}
# Stored outside the served web root where possible, not packed in deployments.
ATTACHMENT_DIR = Path(os.environ.get('SVGTRACKER_ATTACHMENT_DIR') or (Path(__file__).resolve().parent.parent / '.svgtracker-private-note-files')).resolve()
MAX_NOTE_ATTACHMENTS = 25
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_USER_ATTACHMENT_BYTES = 500 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 768 * 1024


def ensure_attachment_dir():
    ATTACHMENT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        ATTACHMENT_DIR.chmod(0o700)
    except OSError:
        pass


def _repeat_offsets(event):
    try:
        raw = json.loads(event['repeat_day_offsets'] or '[]')
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, IndexError):
        raw = []
    if not isinstance(raw, list):
        raw = []
    offsets = sorted({n for n in raw if type(n) is int and 0 <= n <= 6})
    return offsets or [0]



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
                recurrence_timezone TEXT NOT NULL DEFAULT 'UTC',
                repeat_day_offsets TEXT NOT NULL DEFAULT '[]',
                reminder_minutes INTEGER,
                reminder_repeat_count INTEGER,
                reminder_repeat_interval INTEGER,
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
                reminder_repeat_count INTEGER,
                reminder_repeat_interval INTEGER,
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
            CREATE TABLE IF NOT EXISTS note_attachments(
                id TEXT PRIMARY KEY,
                note_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_note_attachment_owner ON note_attachments(user_id,note_id);
            CREATE TABLE IF NOT EXISTS note_upload_sessions(
                id TEXT PRIMARY KEY,
                note_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                expected_bytes INTEGER NOT NULL,
                received_bytes INTEGER NOT NULL DEFAULT 0,
                next_index INTEGER NOT NULL DEFAULT 0,
                stored_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
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
        # Migrate existing V29 installations in place without touching user events.
        columns = {r['name'] for r in db.execute('PRAGMA table_info(schedule_events)')}
        if 'repeat_day_offsets' not in columns:
            db.execute("ALTER TABLE schedule_events ADD COLUMN repeat_day_offsets TEXT NOT NULL DEFAULT '[]'")
        if 'recurrence_timezone' not in columns:
            db.execute("ALTER TABLE schedule_events ADD COLUMN recurrence_timezone TEXT NOT NULL DEFAULT 'UTC'")
        # V47 additive migration: old events/notes keep a single reminder by default.
        for name in ('reminder_repeat_count', 'reminder_repeat_interval'):
            if name not in columns:
                db.execute(f'ALTER TABLE schedule_events ADD COLUMN {name} INTEGER')
        # V34 notes: additive migration, existing notes and attachments are preserved.
        note_columns = {r['name'] for r in db.execute('PRAGMA table_info(notes)')}
        if 'folder' not in note_columns:
            db.execute("ALTER TABLE notes ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
        if 'tags_json' not in note_columns:
            db.execute("ALTER TABLE notes ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
        for name in ('reminder_repeat_count', 'reminder_repeat_interval'):
            if name not in note_columns:
                db.execute(f'ALTER TABLE notes ADD COLUMN {name} INTEGER')
        if 'favorite' not in note_columns:
            db.execute("ALTER TABLE notes ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
        db.execute('CREATE INDEX IF NOT EXISTS idx_notes_user_folder ON notes(user_id,folder,archived)')
    ensure_attachment_dir()
    # Clean abandoned partial uploads after an interrupted client session.
    cutoff = (datetime.now(timezone.utc)-timedelta(days=1)).isoformat().replace('+00:00','Z')
    with connect() as db:
        stale=db.execute('SELECT stored_name FROM note_upload_sessions WHERE created_at<?',(cutoff,)).fetchall()
        db.execute('DELETE FROM note_upload_sessions WHERE created_at<?',(cutoff,))
    for row in stale:
        (ATTACHMENT_DIR / row['stored_name']).unlink(missing_ok=True)


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
        "repeat_day_offsets": _repeat_offsets(row),
        "recurrence_timezone": row["recurrence_timezone"],
        "reminder_minutes": row["reminder_minutes"],
        "reminder_repeat_count": row["reminder_repeat_count"],
        "reminder_repeat_interval": row["reminder_repeat_interval"],
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
                id,owner_user_id,title,details,starts_at,ends_at,all_day,recurrence,recurrence_until,recurrence_timezone,repeat_day_offsets,reminder_minutes,reminder_repeat_count,reminder_repeat_interval,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                payload.get("recurrence_timezone", "UTC"),
                json.dumps(payload.get("repeat_day_offsets") or [0]),
                payload.get("reminder_minutes"),
                payload.get("reminder_repeat_count"), payload.get("reminder_repeat_interval"),
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
            UPDATE schedule_events SET title=?,details=?,starts_at=?,ends_at=?,all_day=?,recurrence=?,recurrence_until=?,recurrence_timezone=?,repeat_day_offsets=?,reminder_minutes=?,reminder_repeat_count=?,reminder_repeat_interval=?,updated_at=?
            WHERE id=?
            """,
            (
                payload["title"], payload.get("details", ""), payload["starts_at"], payload.get("ends_at"),
                1 if payload.get("all_day") else 0, payload.get("recurrence", "none"), payload.get("recurrence_until"),
                payload.get("recurrence_timezone", "UTC"), json.dumps(payload.get("repeat_day_offsets") or [0]), payload.get("reminder_minutes"), payload.get("reminder_repeat_count"),payload.get("reminder_repeat_interval"), now, event_id,
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


def _attachments_for_note(db, user_id, note_id):
    rows = db.execute('SELECT id,display_name,media_type,byte_size,created_at FROM note_attachments WHERE user_id=? AND note_id=? ORDER BY created_at,id', (user_id,note_id)).fetchall()
    return [dict(r) for r in rows]


def _note_with_attachments(db, row):
    result = dict(row, pinned=bool(row['pinned']), archived=bool(row['archived']))
    try:
        tags = json.loads(row['tags_json'] or '[]')
    except (ValueError, TypeError):
        tags = []
    result['tags'] = tags if isinstance(tags, list) else []
    result['favorite'] = bool(row['favorite'])
    result['attachments'] = _attachments_for_note(db, row['user_id'], row['id'])
    return result


def note_owned(db,user_id,note_id):
    return db.execute('SELECT 1 FROM notes WHERE id=? AND user_id=?', (note_id,user_id)).fetchone() is not None


def list_note_files(user_id, note_id):
    with connect() as db:
        if not note_owned(db,user_id,note_id):
            return None
        return _attachments_for_note(db,user_id,note_id)


def get_note_file(user_id,note_id,attachment_id):
    with connect() as db:
        row = db.execute('SELECT * FROM note_attachments WHERE user_id=? AND note_id=? AND id=?', (user_id,note_id,attachment_id)).fetchone()
        return dict(row) if row else None


def attachment_usage(db,user_id):
    return int(db.execute('SELECT COALESCE(SUM(byte_size),0) AS total FROM note_attachments WHERE user_id=?',(user_id,)).fetchone()['total'])


def delete_note_file(user_id,note_id,attachment_id):
    with connect() as db:
        row = db.execute('SELECT stored_name FROM note_attachments WHERE id=? AND note_id=? AND user_id=?',(attachment_id,note_id,user_id)).fetchone()
        if not row:return False
        db.execute('DELETE FROM note_attachments WHERE id=? AND note_id=? AND user_id=?',(attachment_id,note_id,user_id))
    (ATTACHMENT_DIR / row['stored_name']).unlink(missing_ok=True)
    return True


def list_notes(user_id, include_archived=False):
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM notes WHERE user_id=? AND (?=1 OR archived=0)
            ORDER BY pinned DESC, updated_at DESC
            """,
            (user_id, 1 if include_archived else 0),
        ).fetchall()
        return [_note_with_attachments(db,row) for row in rows]


def save_note(user_id, note_id, payload, *, require_existing=False):
    now = utc_now()
    with connect() as db:
        existing = db.execute("SELECT user_id,created_at FROM notes WHERE id=?", (note_id,)).fetchone()
        if (require_existing and not existing) or (existing and int(existing["user_id"]) != int(user_id)):
            return None
        created_at = existing["created_at"] if existing else now
        db.execute(
            """
            INSERT INTO notes(id,user_id,title,body,pinned,archived,reminder_at,created_at,updated_at,folder,tags_json,favorite,reminder_repeat_count,reminder_repeat_interval)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,body=excluded.body,pinned=excluded.pinned,archived=excluded.archived,
                reminder_at=excluded.reminder_at,updated_at=excluded.updated_at,
                folder=excluded.folder,tags_json=excluded.tags_json,favorite=excluded.favorite,
                reminder_repeat_count=excluded.reminder_repeat_count,reminder_repeat_interval=excluded.reminder_repeat_interval
            """,
            (
                note_id,user_id,payload.get("title", ""),payload.get("body", ""),
                1 if payload.get("pinned") else 0,1 if payload.get("archived") else 0,
                payload.get("reminder_at"),created_at,now,
                payload.get('folder',''),json.dumps(payload.get('tags',[]),ensure_ascii=False),
                1 if payload.get('favorite') else 0,
                payload.get('reminder_repeat_count'),payload.get('reminder_repeat_interval'),
            ),
        )
        row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return _note_with_attachments(db,row)


def delete_note(user_id, note_id):
    with connect() as db:
        names = [r['stored_name'] for r in db.execute('SELECT stored_name FROM note_attachments WHERE user_id=? AND note_id=?',(user_id,note_id))]
        names += [r['stored_name'] for r in db.execute('SELECT stored_name FROM note_upload_sessions WHERE user_id=? AND note_id=?',(user_id,note_id))]
        cur = db.execute("DELETE FROM notes WHERE id=? AND user_id=?", (note_id, user_id))
    if cur.rowcount:
        for name in names:(ATTACHMENT_DIR/name).unlink(missing_ok=True)
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

    if recurrence == 'weekly':
        offsets = _repeat_offsets(event)
        # Expand each selected weekday from the original anchor week. Offsets
        # are relative to the local day chosen in the editor, avoiding UTC
        # midnight boundary errors for selected weekdays.
        try:
            tz = ZoneInfo(event['recurrence_timezone'] or 'UTC')
        except (ZoneInfoNotFoundError, KeyError, TypeError, ValueError):
            tz = timezone.utc
        anchor_local = start.astimezone(tz)
        window_local = window_start.astimezone(tz)
        week_index = max(0, int((window_local - anchor_local).total_seconds() // (7 * 86400)) - 1)
        out = []
        for _ in range(1002):
            week_local = anchor_local + timedelta(weeks=week_index)
            if week_local.astimezone(timezone.utc) > window_end:
                break
            for offset in offsets:
                occurrence = (week_local + timedelta(days=offset)).astimezone(timezone.utc)
                if until and occurrence > until:
                    continue
                if window_start <= occurrence <= window_end:
                    out.append(occurrence)
            if until and week_local.astimezone(timezone.utc) > until:
                break
            week_index += 1
        return sorted(out)

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


def reminder_repeat_policy(row, preferences):
    """Per-record values override account defaults; never exceed six deliveries."""
    count = row['reminder_repeat_count'] if row['reminder_repeat_count'] is not None else preferences['default_reminder_count']
    interval = row['reminder_repeat_interval'] if row['reminder_repeat_interval'] is not None else preferences['default_reminder_interval']
    count = count if type(count) is int and 1 <= count <= 6 else 1
    interval = interval if type(interval) is int and interval in (5, 10, 15, 30, 60) else 5
    return count, interval


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
            preferences = get_notification_preferences(row['user_id'], db)
            repeat_count, interval = reminder_repeat_policy(row, preferences)
            # Scan occurrences that can have a repeat attempt due in this window.
            occurrence_window_start = window_start + timedelta(minutes=reminder_minutes - (repeat_count-1)*interval)
            occurrence_window_end = window_end + timedelta(minutes=reminder_minutes)
            for occurrence in _occurrences(row, occurrence_window_start, occurrence_window_end):
                base_time = occurrence - timedelta(minutes=reminder_minutes)
                for attempt in range(repeat_count):
                    remind_at = base_time + timedelta(minutes=attempt*interval)
                    if not (window_start <= remind_at <= window_end):
                        continue
                    # Prevent a burst of late repeat notifications after downtime.
                    if attempt and (now - remind_at).total_seconds() > 120:
                        continue
                    # Attempt #0 preserves V46 keys, including previous dedup records.
                    key = f"schedule:{row['id']}:{occurrence.isoformat()}:{reminder_minutes}"
                    if attempt:
                        key += f":repeat:{attempt}:{interval}"
                    if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row['user_id'], key)).fetchone():
                        continue
                    reminders.append({
                        'user_id':row['user_id'], 'telegram_id':row['telegram_id'], 'key':key,
                        'kind':'schedule', 'title':row['title'], 'when':occurrence,
                        'timezone':_timezone_for_row(row), 'reminder_minutes':reminder_minutes,
                        'repeat_attempt':attempt, 'repeat_total':repeat_count,
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
            remind_at = _parse_iso(row['reminder_at'])
            if not remind_at:
                continue
            preferences = get_notification_preferences(row['user_id'], db)
            repeat_count, interval = reminder_repeat_policy(row, preferences)
            for attempt in range(repeat_count):
                due = remind_at + timedelta(minutes=attempt*interval)
                if not (window_start <= due <= window_end):
                    continue
                if attempt and (now - due).total_seconds() > 120:
                    continue
                key = f"note:{row['id']}:{remind_at.isoformat()}"
                if attempt:
                    key += f":repeat:{attempt}:{interval}"
                if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row['user_id'],key)).fetchone():
                    continue
                reminders.append({
                    'user_id':row['user_id'], 'telegram_id':row['telegram_id'], 'key':key,
                    'kind':'note', 'title':row['title'] or 'Заметка', 'body':row['body'],
                    'when':remind_at, 'timezone':_timezone_for_row(row),
                    'repeat_attempt':attempt,'repeat_total':repeat_count,
                })

        # Training days do not store a start time. A user can explicitly opt in
        # to one daily plan reminder at a chosen local clock time.
        training_rows = db.execute("""
            SELECT t.user_id,t.state_json,u.telegram_id,s.timezone_name,s.timezone_offset_minutes
            FROM training_state t JOIN users u ON u.id=t.user_id
            JOIN user_settings s ON s.user_id=t.user_id WHERE s.bot_notifications=1
        """).fetchall()
        for row in training_rows:
            if not notification_enabled(row['user_id'], 'training_reminders', db):
                continue
            try:
                training = json.loads(row['state_json'] or '{}')
            except (TypeError,ValueError):
                continue
            if not isinstance(training,dict):
                continue
            tz = _timezone_for_row(row)
            local_now = now.astimezone(tz)
            day_key = local_now.date().isoformat()
            preferences=get_notification_preferences(row['user_id'],db)
            time_parts=preferences['training_reminder_time'].split(':')
            due=local_now.replace(hour=int(time_parts[0]),minute=int(time_parts[1]),second=0,microsecond=0).astimezone(timezone.utc)
            if not (window_start <= due <= window_end):
                continue
            effective_from=(training.get('planMeta') or {}).get('effectiveFrom') if isinstance(training.get('planMeta'),dict) else None
            if effective_from and str(effective_from)[:10] > day_key:
                continue
            overrides=training.get('planOverrides') or {}
            override=overrides.get(day_key) if isinstance(overrides,dict) else None
            workout=''
            if isinstance(override,dict) and override.get('type')=='cancel':
                continue
            if isinstance(override,dict) and override.get('type')=='workout':
                workout=str(override.get('workout') or '').strip()
            else:
                plan=training.get('plan') or {}
                name=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][local_now.weekday()]
                if isinstance(plan,dict):
                    workout=str(plan.get(name) or '').strip()
            if not workout:
                continue
            history=training.get('history') or []
            if any(isinstance(h,dict) and h.get('dateKey')==day_key and h.get('status')=='completed' for h in (history if isinstance(history,list) else [])):
                continue
            active=training.get('activeWorkout') or {}
            if isinstance(active,dict) and active.get('dateKey')==day_key:
                continue
            key=f'training:daily:{day_key}'
            if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?",(row['user_id'],key)).fetchone():
                continue
            reminders.append({'user_id':row['user_id'],'telegram_id':row['telegram_id'],'key':key,'kind':'training',
                              'title':workout[:80],'when':due,'timezone':tz})

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
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(state, dict):
                continue
            tz = _timezone_for_row(row)
            local_now = now.astimezone(tz)
            local_date = local_now.date().isoformat()
            # Finance reminders are intentionally sent once on the due date, after 09:00 local time.
            if local_now.hour < 9:
                continue
            for item in (state.get("mandatoryExpenses") if isinstance(state.get("mandatoryExpenses"), list) else []) if notification_enabled(row['user_id'], 'finance_payments', db) else []:
                if not isinstance(item,dict): continue
                due = str(item.get("dueDate") or item.get("date") or "")[:10]
                if due != local_date or item.get("paid") is True:
                    continue
                key = f"finance:mandatory:{item.get('id')}:{due}"
                if db.execute("SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?", (row["user_id"], key)).fetchone():
                    continue
                reminders.append({"user_id":row["user_id"],"telegram_id":row["telegram_id"],"key":key,"kind":"mandatory","title":item.get("title") or "Обязательный расход","amount":item.get("amount"),"when":now,"timezone":tz})
            for item in (state.get("debts") if isinstance(state.get("debts"), list) else []) if notification_enabled(row['user_id'], 'finance_debts', db) else []:
                if not isinstance(item,dict): continue
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
        names = [r['stored_name'] for r in db.execute('SELECT stored_name FROM note_attachments WHERE user_id=?',(user_id,))]
        names += [r['stored_name'] for r in db.execute('SELECT stored_name FROM note_upload_sessions WHERE user_id=?',(user_id,))]
        db.execute("DELETE FROM notes WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM bot_reminder_log WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM notification_preferences WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM automation_rules WHERE user_id=?", (user_id,))
    for name in names:
        (ATTACHMENT_DIR / name).unlink(missing_ok=True)
    return True


NOTIFICATION_DEFAULTS = {
    "personal_reminders": True, "shared_reminders": True,
    "note_reminders": True, "finance_payments": True, "finance_debts": True,
    "shared_created": True, "shared_updated": True, "shared_removed": True,
    "friend_accepted": True,
    "training_reminders": False, "training_reminder_time": "18:00",
    "daily_report": False, "weekly_report": False,
    "default_reminder_count": 1, "default_reminder_interval": 5,
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
    return {key: (overrides.get(key) if (
        (type(default) is bool and type(overrides.get(key)) is bool) or
        (key == 'default_reminder_count' and type(overrides.get(key)) is int and 1 <= overrides[key] <= 6) or
        (key == 'default_reminder_interval' and type(overrides.get(key)) is int and overrides[key] in (5,10,15,30,60)) or
        (key == 'training_reminder_time' and isinstance(overrides.get(key), str) and len(overrides[key]) == 5 and overrides[key][:2].isdigit() and overrides[key][2] == ':' and overrides[key][3:].isdigit() and 0 <= int(overrides[key][:2]) <= 23 and 0 <= int(overrides[key][3:]) <= 59)
    ) else default) for key, default in NOTIFICATION_DEFAULTS.items()}


def update_notification_preferences(user_id, patch):
    if not isinstance(patch, dict) or not patch or any(
        k not in NOTIFICATION_DEFAULTS or not (
            (type(NOTIFICATION_DEFAULTS[k]) is bool and type(v) is bool) or
            (k == 'default_reminder_count' and type(v) is int and 1 <= v <= 6) or
            (k == 'default_reminder_interval' and type(v) is int and v in (5, 10, 15, 30, 60)) or
            (k == 'training_reminder_time' and type(v) is str and len(v) == 5 and v[:2].isdigit() and v[2] == ':' and v[3:].isdigit() and 0 <= int(v[:2]) <= 23 and 0 <= int(v[3:]) <= 59)
        ) for k,v in patch.items()
    ):
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

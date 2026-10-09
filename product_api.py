"""HTTP API for schedule and notes.

Kept outside main.py so product domains can evolve independently from the
Telegram/system administration process. Authentication is injected by main.py
because SVGTracker supports both Telegram initData and HttpOnly web sessions.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid
import mimetypes
import re
from urllib.parse import unquote
from pathlib import Path
from fastapi.responses import FileResponse

from fastapi import APIRouter, Header, HTTPException, Request

from product_db import (
    create_schedule_event,
    delete_note,
    delete_or_leave_schedule_event,
    get_schedule_event,
    list_notes,
    list_schedule_events,
    save_note,
    update_schedule_event,
    get_notification_preferences, update_notification_preferences,
    notification_enabled, list_busy_availability,
    connect, utc_now, ATTACHMENT_DIR, MAX_NOTE_ATTACHMENTS,
    MAX_ATTACHMENT_BYTES, MAX_USER_ATTACHMENT_BYTES, MAX_UPLOAD_CHUNK_BYTES,
    note_owned, attachment_usage, get_note_file, delete_note_file,
)


def _clean_iso_datetime(value, field_name, allow_none=False):
    if value in (None, "") and allow_none:
        return None
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail=f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field_name} must be ISO datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_schedule_payload(payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()[:120]
    if not title:
        raise HTTPException(status_code=422, detail="Название события обязательно")
    starts_at = _clean_iso_datetime(payload.get("starts_at"), "starts_at")
    ends_at = _clean_iso_datetime(payload.get("ends_at"), "ends_at", allow_none=True)
    if ends_at:
        start_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        if end_dt < start_dt:
            raise HTTPException(status_code=422, detail="Окончание не может быть раньше начала")
    recurrence = str(payload.get("recurrence") or "none").lower()
    if recurrence not in {"none", "daily", "weekly", "monthly"}:
        recurrence = "none"
    recurrence_until = _clean_iso_datetime(payload.get("recurrence_until"), "recurrence_until", allow_none=True)
    if recurrence_until:
        until_dt = datetime.fromisoformat(recurrence_until.replace("Z", "+00:00"))
        start_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        if until_dt < start_dt:
            raise HTTPException(status_code=422, detail="Дата окончания повтора раньше первого события")
    reminder = payload.get("reminder_minutes")
    if reminder in (None, "", -1, "-1"):
        reminder = None
    else:
        try:
            reminder = int(reminder)
        except (TypeError, ValueError):
            reminder = None
        if reminder is not None:
            reminder = max(0, min(reminder, 7 * 24 * 60))
    participant_ids = payload.get("participant_ids") if isinstance(payload.get("participant_ids"), list) else []
    raw_offsets = payload.get('repeat_day_offsets', [0])
    if not isinstance(raw_offsets, list) or len(raw_offsets)>7 or any(type(n) is not int or not 0<=n<=6 for n in raw_offsets):
        raise HTTPException(status_code=422, detail='Недопустимые дни повторения')
    repeat_day_offsets = sorted(set(raw_offsets)) if recurrence=='weekly' else [0]
    recurrence_timezone = str(payload.get('recurrence_timezone') or 'UTC')[:80]
    try:
        ZoneInfo(recurrence_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422,detail='Неверный часовой пояс')
    if recurrence == 'weekly' and not repeat_day_offsets:
        raise HTTPException(status_code=422, detail='Выбери хотя бы один день недели')
    return {
        "title": title,
        "repeat_day_offsets": repeat_day_offsets,
        "recurrence_timezone": recurrence_timezone,
        "details": str(payload.get("details") or "").strip()[:2000],
        "starts_at": starts_at,
        "ends_at": ends_at,
        "all_day": bool(payload.get("all_day")),
        "recurrence": recurrence,
        "recurrence_until": recurrence_until,
        "reminder_minutes": reminder,
        "participant_ids": participant_ids[:50],
    }


def _clean_note_payload(payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()[:120]
    body = str(payload.get("body") or "").strip()[:12000]
    if not title and not body:
        raise HTTPException(status_code=422, detail="Заметка не может быть пустой")
    return {
        "title": title,
        "body": body,
        "pinned": bool(payload.get("pinned")),
        "archived": bool(payload.get("archived")),
        "reminder_at": _clean_iso_datetime(payload.get("reminder_at"), "reminder_at", allow_none=True),
    }


def build_product_router(*, authenticate, bot, main_keyboard, get_user_settings, get_user_telegram_id, logger):
    router = APIRouter()

    async def notify_user(user_id, text, category):
        settings = get_user_settings(user_id) or {}
        telegram_id = get_user_telegram_id(user_id)
        if not telegram_id or not notification_enabled(user_id, category):
            return False
        try:
            await bot.send_message(chat_id=telegram_id, text=text, reply_markup=main_keyboard())
            return True
        except Exception:
            logger.exception("Failed to send product notification to Telegram user %s", telegram_id)
            return False

    @router.get("/api/notifications/preferences")
    def notification_preferences(request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        return {"status":"ok","preferences":get_notification_preferences(user_id)}

    @router.put("/api/notifications/preferences")
    def save_notification_preferences(payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        try:
            result=update_notification_preferences(user_id,payload.get('preferences'))
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        return {"status":"ok","preferences":result}

    @router.post("/api/schedule/availability")
    def schedule_availability(payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        ids=payload.get('user_ids',[])
        if not isinstance(ids,list) or len(ids)>30:
            raise HTTPException(status_code=422,detail='Слишком много участников')
        start=_clean_iso_datetime(payload.get('start'),'start')
        end=_clean_iso_datetime(payload.get('end'),'end')
        try:
            result=list_busy_availability(user_id,ids,start,end)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        return {"status":"ok","availability":result}

    @router.get("/api/schedule/events")
    def schedule_events(request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        return {"status": "ok", "events": list_schedule_events(user_id)}

    @router.post("/api/schedule/events")
    async def schedule_create(payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        user, user_id = authenticate(request, x_telegram_init_data)
        clean = _clean_schedule_payload(payload)
        # IDs are server-generated so a client cannot collide with or probe an
        # existing shared event by choosing its identifier.
        event_id = f"event_{uuid.uuid4().hex}"
        event = create_schedule_event(user_id, event_id, clean)
        owner_name = user.get("first_name") or user.get("username") or "Друг"
        for participant in event.get("participants", []):
            if participant.get("role") == "owner":
                continue
            await notify_user(
                participant["id"],
                f"📅 {owner_name} добавил совместное событие\n\n{event['title']}", 'shared_created',
            )
        return {"status": "ok", "event": event}

    @router.put("/api/schedule/events/{event_id}")
    async def schedule_update(event_id: str, payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        user, user_id = authenticate(request, x_telegram_init_data)
        before = get_schedule_event(user_id, event_id)
        event, error = update_schedule_event(user_id, event_id, _clean_schedule_payload(payload))
        if error == "not_found":
            raise HTTPException(status_code=404, detail="Событие не найдено")
        if error == "forbidden":
            raise HTTPException(status_code=403, detail="Нет доступа к событию")

        actor_name = user.get("first_name") or user.get("username") or "Друг"
        before_people = {int(p["id"]): p for p in (before or {}).get("participants", [])}
        after_people = {int(p["id"]): p for p in (event or {}).get("participants", [])}
        actor_id = int(user_id)

        # Newly added friends get a clear invitation-like notification; removed
        # participants are told that the event disappeared from their calendar.
        for participant_id in sorted(set(after_people) - set(before_people) - {actor_id}):
            await notify_user(participant_id, f"📅 {actor_name} добавил вас в совместное событие\n\n{event['title']}", 'shared_created')
        for participant_id in sorted(set(before_people) - set(after_people) - {actor_id}):
            await notify_user(participant_id, f"📅 {actor_name} убрал совместное событие из вашего расписания\n\n{(before or {}).get('title') or event['title']}", 'shared_removed')
        for participant_id in sorted((set(after_people) & set(before_people)) - {actor_id}):
            await notify_user(participant_id, f"📅 {actor_name} изменил совместное событие\n\n{event['title']}", 'shared_updated')
        return {"status": "ok", "event": event}

    @router.delete("/api/schedule/events/{event_id}")
    async def schedule_delete(event_id: str, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        user, user_id = authenticate(request, x_telegram_init_data)
        before = get_schedule_event(user_id, event_id)
        result = delete_or_leave_schedule_event(user_id, event_id)
        if result == "not_found":
            raise HTTPException(status_code=404, detail="Событие не найдено")
        if result == "forbidden":
            raise HTTPException(status_code=403, detail="Нет доступа к событию")
        if before:
            actor_name = user.get("first_name") or user.get("username") or "Друг"
            if result == "deleted":
                for participant in before.get("participants", []):
                    if int(participant["id"]) == int(user_id):
                        continue
                    await notify_user(participant["id"], f"📅 {actor_name} удалил совместное событие\n\n{before['title']}", 'shared_removed')
            elif result == "left":
                owner_id = before.get("owner_user_id")
                if owner_id and int(owner_id) != int(user_id):
                    await notify_user(owner_id, f"📅 {actor_name} вышел из совместного события\n\n{before['title']}", 'shared_removed')
        return {"status": "ok", "result": result}

    @router.get("/api/notes")
    def notes(request: Request, archived: bool = False, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        return {"status": "ok", "notes": list_notes(user_id, include_archived=archived)}

    @router.post("/api/notes")
    def note_create(payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        note_id = f"note_{uuid.uuid4().hex}"
        note = save_note(user_id, note_id, _clean_note_payload(payload))
        return {"status": "ok", "note": note}

    @router.put("/api/notes/{note_id}")
    def note_update(note_id: str, payload: dict, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        note = save_note(user_id, note_id, _clean_note_payload(payload))
        if not note:
            raise HTTPException(status_code=403, detail="Нет доступа к заметке")
        return {"status": "ok", "note": note}

    @router.delete("/api/notes/{note_id}")
    def note_delete(note_id: str, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id = authenticate(request, x_telegram_init_data)
        if not delete_note(user_id, note_id):
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return {"status": "ok"}

    @router.post('/api/notes/{note_id}/attachments/chunks')
    async def note_upload_chunk(
        note_id: str, request: Request,
        x_upload_token: str = Header(default=''), x_chunk_index: int = Header(default=-1),
        x_chunk_total: int = Header(default=0), x_file_name: str = Header(default=''),
        x_file_size: int = Header(default=-1),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        _, user_id = authenticate(request, x_telegram_init_data)
        if (not re.fullmatch(r'[0-9a-f]{32}', x_upload_token) or x_chunk_index<0
            or not 1<=x_chunk_total<=110 or x_chunk_index>=x_chunk_total
            or not 0<x_file_size<=MAX_ATTACHMENT_BYTES):
            raise HTTPException(status_code=422,detail='Некорректные параметры загрузки')
        name = unquote(x_file_name).replace('\\','/').split('/')[-1].replace('\x00','').strip()[:180]
        if not name or name in ('.','..'):
            raise HTTPException(status_code=422, detail='Некорректное имя файла')
        media_type=(request.headers.get('content-type') or 'application/octet-stream').split(';')[0].lower().strip()[:100]
        if not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', media_type):
            media_type='application/octet-stream'
        content=bytearray()
        async for block in request.stream():
            content.extend(block)
            if len(content)>MAX_UPLOAD_CHUNK_BYTES:
                raise HTTPException(status_code=413, detail='Часть файла слишком большая')
        if not content:
            raise HTTPException(status_code=422,detail='Пустая часть файла')
        # All chunks use an authenticated owner-bound session. Database lock
        # serializes concurrent requests for the same upload token.
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not note_owned(db,user_id,note_id):
                raise HTTPException(status_code=404,detail='Заметка не найдена')
            session=db.execute('SELECT * FROM note_upload_sessions WHERE id=?',(x_upload_token,)).fetchone()
            if session is None:
                if x_chunk_index!=0:raise HTTPException(status_code=409,detail='Загрузка должна начаться с первой части')
                if db.execute('SELECT COUNT(*) AS n FROM note_attachments WHERE note_id=? AND user_id=?',(note_id,user_id)).fetchone()['n']>=MAX_NOTE_ATTACHMENTS:
                    raise HTTPException(status_code=413,detail='Слишком много вложений')
                inflight=db.execute('SELECT COUNT(*) AS n, COALESCE(SUM(expected_bytes),0) AS bytes FROM note_upload_sessions WHERE user_id=?',(user_id,)).fetchone()
                if inflight['n']>=3 or inflight['bytes']+x_file_size>120*1024*1024:
                    raise HTTPException(status_code=429,detail='Заверши текущие загрузки')
                if attachment_usage(db,user_id)+x_file_size>MAX_USER_ATTACHMENT_BYTES:
                    raise HTTPException(status_code=413,detail='Превышен объём хранения файлов')
                stored_name='upload_'+x_upload_token+'.part'
                db.execute('INSERT INTO note_upload_sessions(id,note_id,user_id,display_name,media_type,expected_bytes,received_bytes,next_index,stored_name,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (x_upload_token,note_id,user_id,name,media_type,x_file_size,0,0,stored_name,utc_now()))
                session=db.execute('SELECT * FROM note_upload_sessions WHERE id=?',(x_upload_token,)).fetchone()
            if (session['user_id']!=user_id or session['note_id']!=note_id):
                raise HTTPException(status_code=404,detail='Загрузка не найдена')
            if (session['next_index']!=x_chunk_index or session['expected_bytes']!=x_file_size
                or session['display_name']!=name or session['media_type']!=media_type):
                raise HTTPException(status_code=409,detail='Неверная последовательность загрузки')
            if session['received_bytes']+len(content)>session['expected_bytes']:
                raise HTTPException(status_code=413,detail='Превышен заявленный размер файла')
            next_bytes=session['received_bytes']+len(content)
            if (x_chunk_index+1<x_chunk_total and next_bytes>=x_file_size) or (x_chunk_index+1==x_chunk_total and next_bytes!=x_file_size):
                raise HTTPException(status_code=422,detail='Неверный объём или количество частей')
            file_path=ATTACHMENT_DIR/session['stored_name']
            with file_path.open('xb' if x_chunk_index==0 else 'ab') as out:
                out.write(content)
            if x_chunk_index+1<x_chunk_total:
                db.execute('UPDATE note_upload_sessions SET received_bytes=?, next_index=? WHERE id=?',(next_bytes,x_chunk_index+1,x_upload_token))
                return {'status':'ok','uploaded_chunks':x_chunk_index+1,'total_chunks':x_chunk_total}
            if next_bytes!=x_file_size:
                raise HTTPException(status_code=422,detail='Загружен не весь файл')
            if db.execute('SELECT COUNT(*) AS n FROM note_attachments WHERE note_id=? AND user_id=?',(note_id,user_id)).fetchone()['n']>=MAX_NOTE_ATTACHMENTS:
                raise HTTPException(status_code=413,detail='Слишком много вложений')
            if attachment_usage(db,user_id)+x_file_size>MAX_USER_ATTACHMENT_BYTES:
                raise HTTPException(status_code=413,detail='Превышен объём хранения файлов')
            attachment_id='file_'+uuid.uuid4().hex
            final_name=attachment_id+'.bin'
            file_path.rename(ATTACHMENT_DIR/final_name)
            db.execute('INSERT INTO note_attachments(id,note_id,user_id,display_name,media_type,byte_size,stored_name,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (attachment_id,note_id,user_id,name,media_type,x_file_size,final_name,utc_now()))
            db.execute('DELETE FROM note_upload_sessions WHERE id=?',(x_upload_token,))
            return {'status':'ok','attachment':{'id':attachment_id,'display_name':name,'media_type':media_type,'byte_size':x_file_size}}

    @router.get('/api/notes/{note_id}/attachments/{attachment_id}')
    def note_download_file(note_id: str, attachment_id: str, request: Request, x_telegram_init_data: str | None = Header(default=None)):
        _, user_id=authenticate(request,x_telegram_init_data)
        attachment=get_note_file(user_id,note_id,attachment_id)
        if not attachment:
            raise HTTPException(status_code=404,detail='Файл не найден')
        path=ATTACHMENT_DIR/attachment['stored_name']
        if not path.is_file():
            raise HTTPException(status_code=404,detail='Файл не найден на сервере')
        mime=attachment['media_type']
        safe_preview=(mime in {'image/jpeg','image/png','image/webp','image/gif','audio/mpeg','audio/mp4','audio/ogg','audio/wav','audio/webm','video/mp4','video/webm','text/plain','application/pdf'})
        return FileResponse(path, filename=attachment['display_name'],media_type=mime if safe_preview else 'application/octet-stream',
                    content_disposition_type='inline' if safe_preview else 'attachment',
                    headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.delete('/api/notes/{note_id}/attachments/{attachment_id}')
    def note_delete_file(note_id: str,attachment_id: str,request: Request,x_telegram_init_data: str | None = Header(default=None)):
        _,user_id=authenticate(request,x_telegram_init_data)
        if not delete_note_file(user_id,note_id,attachment_id):
            raise HTTPException(status_code=404,detail='Файл не найден')
        return {'status':'ok'}

    return router

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

from config import BOT_TOKEN
from key import main_keyboard
from finance_db import (
    block_user,
    create_friend_request,
    create_shortcut_token,
    get_or_create_user,
    get_profile_data,
    get_user_by_shortcut_token,
    get_training_state,
    get_finance_state,
    get_user_settings,
    init_db,
    remove_friend,
    revoke_shortcut_tokens,
    reset_user_data,
    resolve_friend_request,
    save_training_state,
    save_finance_state,
    set_bot_notifications,
    unblock_user,
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI(title="SVGTracker API")

MAX_TRAINING_STATE_BYTES = 1_000_000
MAX_FINANCE_STATE_BYTES = 600_000
INIT_DATA_MAX_AGE_SECONDS = 6 * 60 * 60
logger = logging.getLogger("svgtracker")

SHORTCUT_DEFAULT_CATEGORIES = [
    {"id": "cat_food", "name": "Еда", "color": "#ff9f0a"},
    {"id": "cat_home", "name": "Дом", "color": "#64d2ff"},
    {"id": "cat_transport", "name": "Транспорт", "color": "#0a84ff"},
    {"id": "cat_fun", "name": "Развлечения", "color": "#bf5af2"},
    {"id": "cat_health", "name": "Здоровье", "color": "#30d158"},
    {"id": "cat_other", "name": "Другое", "color": "#8e8e93"},
]


def shortcut_user(authorization: str | None):
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Shortcut token is required")
    record = get_user_by_shortcut_token(authorization[len(prefix):].strip())
    if not record:
        raise HTTPException(status_code=401, detail="Shortcut token is invalid or revoked")
    return record


def finance_state_for_shortcut(user_id):
    record = get_finance_state(user_id)
    state = dict(record["state"]) if record and isinstance(record.get("state"), dict) else {}
    categories = state.get("categories") if isinstance(state.get("categories"), list) else []
    if not categories:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        categories = [dict(item, updatedAt=now) for item in SHORTCUT_DEFAULT_CATEGORIES]
        state["categories"] = categories
    state.setdefault("version", 5)
    state.setdefault("monthlyIncome", 0)
    state.setdefault("monthlyBudgets", {})
    state.setdefault("mandatoryExpenses", [])
    state.setdefault("expenses", [])
    state.setdefault("incomes", [])
    state.setdefault("debts", [])
    sync = state.setdefault("sync", {})
    sync.setdefault("tombstones", {})
    for key in ("expenses", "incomes", "mandatoryExpenses", "debts", "categories"):
        sync["tombstones"].setdefault(key, {})
    return state


def verify_telegram_init_data(init_data: str):
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram init data is required")

    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash")
        auth_date = int(values.get("auth_date", "0"))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    if not auth_date or abs(int(time.time()) - auth_date) > INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram init data has expired")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Telegram init data signature mismatch")

    try:
        user = json.loads(values["user"])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Telegram user is missing")

    if not isinstance(user, dict) or "id" not in user:
        raise HTTPException(status_code=401, detail="Telegram user is invalid")
    return user


def authenticated_user(x_telegram_init_data: str | None):
    user = verify_telegram_init_data(x_telegram_init_data or "")
    user_id = get_or_create_user(user)
    return user, user_id


@app.get("/api/test")
def api_test():
    return {"status": "ok", "service": "SVGTracker API"}


@app.post("/api/user")
def api_user(x_telegram_init_data: str | None = Header(default=None)):
    user, user_id = authenticated_user(x_telegram_init_data)
    return {"status": "ok", "user_id": user_id, "telegram_id": str(user["id"])}


@app.get("/api/training/state")
def api_training_state(x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    record = get_training_state(user_id)
    if not record:
        return {"status": "ok", "exists": False, "state": None}
    return {
        "status": "ok",
        "exists": True,
        "state": record["state"],
        "updated_at": record["updated_at"],
    }


@app.get("/api/finance/state")
def api_finance_state(x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    record = get_finance_state(user_id)
    if not record:
        return {"status": "ok", "exists": False, "state": None}
    return {
        "status": "ok",
        "exists": True,
        "state": record["state"],
        "updated_at": record["updated_at"],
    }


@app.put("/api/finance/state")
def api_save_finance_state(
    payload: dict,
    x_telegram_init_data: str | None = Header(default=None),
):
    _, user_id = authenticated_user(x_telegram_init_data)
    state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    base_updated_at = payload.get("baseUpdatedAt") if isinstance(payload.get("state"), dict) else None
    encoded = json.dumps(state, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_FINANCE_STATE_BYTES:
        raise HTTPException(status_code=413, detail="Finance state is too large")

    current = get_finance_state(user_id)
    if base_updated_at and current and current["updated_at"] != base_updated_at:
        return JSONResponse(
            status_code=409,
            content={
                "status": "conflict",
                "state": current["state"],
                "updated_at": current["updated_at"],
            },
        )

    allowed = {
        "version", "monthlyIncome", "monthlyBudgets", "categories",
        "mandatoryExpenses", "expenses", "incomes", "debts", "sync"
    }
    clean_state = {key: state.get(key) for key in allowed if key in state}
    updated_at = save_finance_state(user_id, clean_state)
    return {"status": "ok", "updated_at": updated_at}


@app.get("/api/shortcut/finance/options")
def api_shortcut_finance_options(authorization: str | None = Header(default=None)):
    user = shortcut_user(authorization)
    state = finance_state_for_shortcut(user["user_id"])
    categories = []
    for item in state.get("categories", []):
        if not isinstance(item, dict):
            continue
        category_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if category_id and name:
            categories.append({
                "id": category_id,
                "name": name[:32],
                "color": str(item.get("color") or "#8e8e93"),
            })
    return {
        "status": "ok",
        "currency": "RUB",
        "types": [
            {"id": "expense", "name": "Расход"},
            {"id": "income", "name": "Доход"},
        ],
        "categories": categories,
        # Shortcuts can display a plain list much more cleanly than a list of dictionaries.
        "category_names": [item["name"] for item in categories],
    }


@app.post("/api/shortcut/finance/transaction")
async def api_shortcut_finance_transaction(
    payload: dict,
    authorization: str | None = Header(default=None),
):
    user = shortcut_user(authorization)
    kind = str(payload.get("type") or "").strip().lower()
    if kind not in {"expense", "income"}:
        raise HTTPException(status_code=422, detail="type must be expense or income")
    try:
        amount = round(float(payload.get("amount")), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Укажи корректную сумму")
    if not (0 < amount <= 100_000_000):
        raise HTTPException(status_code=422, detail="Сумма должна быть больше нуля")

    date_value = str(payload.get("date") or "").strip()
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    state = finance_state_for_shortcut(user["user_id"])
    now = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    entry_id = f"shortcut_{kind}_{uuid.uuid4().hex}"

    if kind == "expense":
        category_id = str(payload.get("category_id") or "").strip()
        category_name = str(payload.get("category_name") or "").strip().casefold()
        category = next(
            (item for item in state["categories"] if category_id and str(item.get("id")) == category_id),
            None,
        )
        if not category and category_name:
            category = next(
                (item for item in state["categories"] if str(item.get("name") or "").strip().casefold() == category_name),
                None,
            )
        if not category:
            raise HTTPException(status_code=422, detail="Категория не найдена. Обнови список в Shortcut.")
        category_id = str(category.get("id"))
        title = str(payload.get("title") or category.get("name") or "Расход").strip()[:80]
        entry = {
            "id": entry_id,
            "title": title or "Расход",
            "amount": amount,
            "categoryId": category_id,
            "date": date_value,
            "updatedAt": now,
        }
        state["expenses"].append(entry)
        confirmation = f"Расход записан: {amount:g} ₽ · {category.get('name', 'Другое')}"
    else:
        title = str(payload.get("title") or "Доход").strip()[:80] or "Доход"
        entry = {
            "id": entry_id,
            "title": title,
            "amount": amount,
            "date": date_value,
            "updatedAt": now,
        }
        state["incomes"].append(entry)
        confirmation = f"Доход записан: +{amount:g} ₽"

    state["version"] = max(int(state.get("version") or 0), 5)
    state.setdefault("sync", {})["updatedAt"] = now
    updated_at = save_finance_state(user["user_id"], state)

    try:
        await bot.send_message(
            chat_id=user["telegram_id"],
            text=confirmation,
            reply_markup=main_keyboard(),
        )
    except Exception:
        logger.exception("Failed to send Shortcut confirmation to Telegram user %s", user["telegram_id"])

    return {
        "status": "ok",
        "type": kind,
        "amount": amount,
        "message": confirmation,
        "updated_at": updated_at,
    }


@app.get("/api/profile")
def api_profile(x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    return {"status": "ok", **get_profile_data(user_id)}


@app.put("/api/profile/notifications")
def api_profile_notifications(
    payload: dict,
    x_telegram_init_data: str | None = Header(default=None),
):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=422, detail="enabled must be boolean")
    settings = set_bot_notifications(user_id, payload["enabled"])
    return {"status": "ok", "enabled": settings["bot_notifications"]}


@app.post("/api/friends/request")
async def api_friend_request(
    payload: dict,
    x_telegram_init_data: str | None = Header(default=None),
):
    user, user_id = authenticated_user(x_telegram_init_data)
    result = create_friend_request(user_id, payload.get("username"))
    status = result.get("status")
    if status == "invalid":
        raise HTTPException(status_code=422, detail="Укажи username")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Пользователь пока не зарегистрирован в SVGTracker")
    if status == "self":
        raise HTTPException(status_code=400, detail="Нельзя добавить самого себя")
    if status == "blocked":
        raise HTTPException(status_code=403, detail="Запрос этому пользователю недоступен")
    if status == "created":
        target_id = result.get("target_user_id")
        target_telegram_id = result.get("target_telegram_id")
        settings = get_user_settings(target_id) if target_id else {"bot_notifications": False}
        if target_telegram_id and settings.get("bot_notifications"):
            sender = user.get("first_name") or user.get("username") or "Пользователь SVGTracker"
            sender_username = f" (@{user['username']})" if user.get("username") else ""
            try:
                await bot.send_message(
                    chat_id=target_telegram_id,
                    text=f"{sender}{sender_username} хочет добавить тебя в друзья в SVGTracker. Открой приложение, чтобы принять запрос.",
                    reply_markup=main_keyboard(),
                )
            except Exception:
                # The request itself remains valid, but delivery failures must stay observable.
                logger.exception(
                    "Failed to send friend-request notification to Telegram user %s",
                    target_telegram_id,
                )
    return {"status": "ok", "result": status, "target": result.get("target")}


@app.post("/api/friends/requests/{request_id}/accept")
def api_friend_accept(request_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not resolve_friend_request(user_id, request_id, True):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return {"status": "ok"}


@app.post("/api/friends/requests/{request_id}/reject")
def api_friend_reject(request_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not resolve_friend_request(user_id, request_id, False):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return {"status": "ok"}


@app.delete("/api/friends/{friend_user_id}")
def api_friend_remove(friend_user_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not remove_friend(user_id, friend_user_id):
        raise HTTPException(status_code=404, detail="Друг не найден")
    return {"status": "ok"}


@app.post("/api/friends/{target_user_id}/block")
def api_friend_block(target_user_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not block_user(user_id, target_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {"status": "ok"}


@app.delete("/api/friends/{target_user_id}/block")
def api_friend_unblock(target_user_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not unblock_user(user_id, target_user_id):
        raise HTTPException(status_code=404, detail="Блокировка не найдена")
    return {"status": "ok"}


@app.put("/api/training/state")
def api_save_training_state(
    payload: dict,
    x_telegram_init_data: str | None = Header(default=None),
):
    _, user_id = authenticated_user(x_telegram_init_data)

    # v10 clients send an envelope with the state plus the server version they
    # last observed. Legacy clients that send the state directly remain valid.
    if isinstance(payload.get("state"), dict):
        state = payload["state"]
        base_updated_at = payload.get("baseUpdatedAt")
    else:
        state = payload
        base_updated_at = None

    encoded = json.dumps(state, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_TRAINING_STATE_BYTES:
        raise HTTPException(status_code=413, detail="Training state is too large")

    current = get_training_state(user_id)
    if base_updated_at and current and current["updated_at"] != base_updated_at:
        return JSONResponse(
            status_code=409,
            content={
                "status": "conflict",
                "state": current["state"],
                "updated_at": current["updated_at"],
            },
        )

    allowed = {
        "goals",
        "plan",
        "planOverrides",
        "history",
        "attendance",
        "planMeta",
        "activeWorkout",
        "sync",
    }
    clean_state = {key: state.get(key) for key in allowed}
    updated_at = save_training_state(user_id, clean_state)
    return {"status": "ok", "updated_at": updated_at}


@dp.message(Command("start"))
async def start(message: Message):
    username = message.from_user.first_name
    await message.answer(
        text=f"""
<b>Добро пожаловать, {username}, в SVGTracker</b>

Твой персональный центр управления жизнью.

Здесь ты сможешь:
<tg-emoji emoji-id="5893236738372932548">🚀</tg-emoji> Формировать полезные привычки
<tg-emoji emoji-id="6030399199030284183">🏋️</tg-emoji> Отслеживать тренировки и прогресс
<tg-emoji emoji-id="5904462880941545555">💰</tg-emoji> Контролировать свои расходы
<tg-emoji emoji-id="5938195768832692153">📚</tg-emoji> Развивать навыки и достигать целей

Все инструменты уже внутри приложения 👇
""",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.message(Command("shortcut"))
async def shortcut_setup(message: Message):
    if not message.from_user:
        return
    data = {
        "id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "photo_url": None,
    }
    user_id = get_or_create_user(data)
    token = create_shortcut_token(user_id)
    await message.answer(
        "Токен для iPhone Action Button создан.\n\n"
        f"{token}\n\n"
        "Скопируй токен в свою команду Shortcuts. Никому его не отправляй. "
        "Повторная команда /shortcut автоматически отключит предыдущий токен. "
        "Для отключения используй /shortcut_revoke."
    )


@dp.message(Command("shortcut_revoke"))
async def shortcut_revoke(message: Message):
    if not message.from_user:
        return
    data = {
        "id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "photo_url": None,
    }
    user_id = get_or_create_user(data)
    revoked = revoke_shortcut_tokens(user_id)
    await message.answer(
        "Доступ iPhone Shortcut отключён." if revoked else "Активного Shortcut-токена нет."
    )


@dp.message(Command("resetdata", "resetdb"))
async def reset_data_request(message: Message):
    if not message.from_user:
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Очистить мои тестовые данные",
                    callback_data=f"resetdata_confirm:{message.from_user.id}",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="resetdata_cancel")],
        ]
    )
    await message.answer(
        "Это очистит твои тренировочные, финансовые и связанные тестовые данные в базе. "
        "Действие нельзя отменить.",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("resetdata_confirm:"))
async def reset_data_confirm(callback: CallbackQuery):
    if not callback.from_user or not callback.data:
        return
    requested_user_id = callback.data.split(":", 1)[1]
    if requested_user_id != str(callback.from_user.id):
        await callback.answer("Эта кнопка не для твоего аккаунта", show_alert=True)
        return

    reset_user_data(callback.from_user.id)
    await callback.answer("Данные очищены")
    if callback.message:
        await callback.message.edit_text(
            "Тестовые данные очищены. Закрой и заново открой WebApp — он загрузит пустое состояние из базы."
        )


@dp.callback_query(F.data == "resetdata_cancel")
async def reset_data_cancel(callback: CallbackQuery):
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("Очистка отменена.")


async def main():
    init_db()
    print("Бот запущен")

    api_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    api_server = uvicorn.Server(api_config)

    await asyncio.gather(dp.start_polling(bot), api_server.serve())


if __name__ == "__main__":
    asyncio.run(main())

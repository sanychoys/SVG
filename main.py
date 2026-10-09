import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import threading
from collections import deque
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qsl
from urllib import request as urllib_request

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types.error_event import ErrorEvent
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

from config import BOT_TOKEN
from key import main_keyboard
from finance_db import (
    block_user,
    create_friend_request,
    cancel_friend_request,
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

APP_VERSION = "23"
MAX_TRAINING_STATE_BYTES = 1_000_000
MAX_FINANCE_STATE_BYTES = 600_000
INIT_DATA_MAX_AGE_SECONDS = 6 * 60 * 60
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("svgtracker")
FINANCE_WRITE_LOCK = threading.RLock()

SHORTCUT_DEFAULT_CATEGORIES = [
    {"id": "cat_food", "name": "Еда", "color": "#ff9f0a", "order": 0},
    {"id": "cat_home", "name": "Дом", "color": "#64d2ff", "order": 1},
    {"id": "cat_transport", "name": "Транспорт", "color": "#0a84ff", "order": 2},
    {"id": "cat_fun", "name": "Развлечения", "color": "#bf5af2", "order": 3},
    {"id": "cat_health", "name": "Здоровье", "color": "#30d158", "order": 4},
    {"id": "cat_other", "name": "Другое", "color": "#8e8e93", "order": 5},
]


ADMIN_TELEGRAM_ID = int(os.environ.get("SVGTRACKER_ADMIN_ID", "382257126"))
ADMIN_STATE_DIR = Path(os.environ.get("SVGTRACKER_ADMIN_STATE_DIR", "/var/lib/svgtracker-admin"))
ADMIN_BACKUP_DIR = Path(os.environ.get("SVGTRACKER_BACKUP_DIR", "/var/backups/svgtracker"))
ADMIN_DEPLOY_HELPER = Path(__file__).parent / "deploy" / "admin_deploy.py"
ADMIN_PENDING_DEPLOYS: dict[str, dict] = {}
ADMIN_ERROR_COOLDOWN: dict[str, float] = {}
ADMIN_MAX_ZIP_BYTES = 20 * 1024 * 1024
PUBLIC_BASE_URL = os.environ.get("SVGTRACKER_PUBLIC_URL", "https://starslix.ru").rstrip("/")
ADMIN_LOG_DIR = ADMIN_STATE_DIR / "logs"
FRONTEND_LOG_FILE = ADMIN_LOG_DIR / "frontend.log"
FRONTEND_RATE_WINDOW_SECONDS = 60
FRONTEND_RATE_LIMIT = 60
FRONTEND_RATE: dict[str, deque[float]] = {}
FRONTEND_LOG_LOCK = threading.RLock()


def is_admin_telegram_id(value) -> bool:
    try:
        return int(value) == ADMIN_TELEGRAM_ID
    except (TypeError, ValueError):
        return False


def is_admin_message(message: Message) -> bool:
    return bool(message.from_user and is_admin_telegram_id(message.from_user.id))


async def setup_bot_commands() -> None:
    common = [
        BotCommand(command="start", description="Открыть SVGTracker"),
        BotCommand(command="shortcut", description="Токен для iPhone Action Button"),
        BotCommand(command="shortcut_revoke", description="Отключить Action Button"),
        BotCommand(command="resetdata", description="Очистить мои тестовые данные"),
    ]
    admin = common + [
        BotCommand(command="admin", description="Админ-панель"),
        BotCommand(command="deploy", description="Обновить production ZIP-файлом"),
        BotCommand(command="deploy_status", description="Статус последнего deploy"),
        BotCommand(command="server_status", description="Состояние VPS и API"),
        BotCommand(command="backup", description="Создать backup"),
        BotCommand(command="backups", description="Список backup"),
        BotCommand(command="rollback", description="Откатить код"),
        BotCommand(command="errors", description="Ошибки сервера"),
        BotCommand(command="logs", description="Общие логи"),
        BotCommand(command="logs_frontend", description="Frontend runtime логи"),
        BotCommand(command="logs_backend", description="Backend/API логи"),
        BotCommand(command="logs_bot", description="Telegram bot логи"),
        BotCommand(command="logs_system", description="systemd/watchdog логи"),
        BotCommand(command="logs_deploy", description="Deploy/GitHub логи"),
        BotCommand(command="diagnostics", description="Диагностический снимок"),
        BotCommand(command="restart", description="Перезапустить SVGTracker"),
        BotCommand(command="github_setup", description="Подключить GitHub Deploy Key"),
        BotCommand(command="github_test", description="Проверить запись в GitHub"),
        BotCommand(command="github_status", description="Статус синхронизации GitHub"),
        BotCommand(command="github_sync", description="Синхронизировать production → GitHub"),
        BotCommand(command="watchdog_on", description="Включить watchdog"),
        BotCommand(command="watchdog_off", description="Выключить watchdog"),
    ]
    try:
        await bot.set_my_commands(common)
        await bot.set_my_commands(admin, scope=BotCommandScopeChat(chat_id=ADMIN_TELEGRAM_ID))
    except Exception:
        logger.exception("Failed to configure Telegram bot commands")


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статус", callback_data="admin:status"),
            InlineKeyboardButton(text="Ошибки", callback_data="admin:errors"),
        ],
        [
            InlineKeyboardButton(text="Deploy ZIP", callback_data="admin:deploy"),
            InlineKeyboardButton(text="Backup", callback_data="admin:backup"),
        ],
        [
            InlineKeyboardButton(text="Backups", callback_data="admin:backups"),
            InlineKeyboardButton(text="Rollback", callback_data="admin:rollback"),
        ],
        [
            InlineKeyboardButton(text="Логи", callback_data="admin:logs"),
            InlineKeyboardButton(text="Диагностика", callback_data="admin:diagnostics"),
        ],
        [
            InlineKeyboardButton(text="Frontend", callback_data="admin:logs_frontend"),
            InlineKeyboardButton(text="Restart", callback_data="admin:restart"),
        ],
        [
            InlineKeyboardButton(text="GitHub", callback_data="admin:github"),
        ],
    ])


def admin_state_dirs() -> None:
    (ADMIN_STATE_DIR / "incoming").mkdir(parents=True, exist_ok=True)
    ADMIN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def admin_trim(text: str, limit: int = 3600) -> str:
    clean = str(text or "").replace(BOT_TOKEN, "***")
    if len(clean) <= limit:
        return clean
    return "…" + clean[-limit:]


def redact_log_text(value, limit: int = 1800) -> str:
    text = str(value or "")
    if BOT_TOKEN:
        text = text.replace(BOT_TOKEN, "***")
    # Never persist bearer credentials or Telegram init data accidentally passed by a client.
    import re
    text = re.sub(r"Bearer\s+[^\s]+", "Bearer ***", text, flags=re.I)
    text = re.sub(r"(?:tgWebAppData|initData)=([^\s&#]+)", r"\1=***", text, flags=re.I)
    return text[:limit]


def append_json_log(path: Path, payload: dict, max_bytes: int = 2_000_000) -> None:
    admin_state_dirs()
    safe = {str(k)[:64]: redact_log_text(v) for k, v in payload.items() if v is not None}
    safe.setdefault("server_ts", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    line = json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n"
    with FRONTEND_LOG_LOCK:
        try:
            if path.exists() and path.stat().st_size > max_bytes:
                rotated = path.with_suffix(path.suffix + ".1")
                rotated.unlink(missing_ok=True)
                path.replace(rotated)
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def tail_text_file(path: Path, lines: int = 60) -> str:
    try:
        if not path.is_file():
            return "Лог пока пуст."
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return admin_trim("\n".join(content[-max(1, min(lines, 120)):]))
    except Exception as exc:
        return f"Не удалось прочитать {path.name}: {exc}"


def tail_json_log_pretty(path: Path, lines: int = 40) -> str:
    try:
        if not path.is_file():
            return "Лог пока пуст."
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 80)):]
        rows = []
        for line in raw:
            try:
                item = json.loads(line)
                stamp = str(item.get("client_ts") or item.get("ts") or item.get("server_ts") or "")
                if "T" in stamp:
                    stamp = stamp.split("T", 1)[1][:8]
                stage = str(item.get("stage") or "event")
                level = str(item.get("level") or "info").upper()
                message = str(item.get("message") or "").strip()
                suffix = f" · {message}" if message else ""
                rows.append(f"{stamp or '—'} {level} {stage}{suffix}")
                if item.get("stack"):
                    rows.append("  " + str(item.get("stack"))[:420].replace("\n", " ↳ "))
            except Exception:
                rows.append(line)
        return admin_trim("\n".join(rows) or "Лог пока пуст.")
    except Exception as exc:
        return f"Не удалось прочитать {path.name}: {exc}"


def admin_component_journal(component: str, lines: int = 120) -> str:
    raw = admin_journal(lines, errors_only=False)
    rows = raw.splitlines()
    component = component.lower()
    if component == "backend":
        keys = ("svgtracker:", "uvicorn", '"get /api/', '"post /api/', '"put /api/', '"delete /api/', "fastapi")
        rows = [r for r in rows if any(k in r.lower() for k in keys) and "aiogram" not in r.lower()]
    elif component == "bot":
        keys = ("aiogram", "telegram", "polling", "bot @")
        rows = [r for r in rows if any(k in r.lower() for k in keys)]
    elif component == "system":
        keys = ("systemd[", "watchdog", "started svgtracker", "stopped svgtracker", "deactivated", "failed")
        rows = [r for r in rows if any(k in r.lower() for k in keys)]
    return admin_trim("\n".join(rows[-60:]) or f"Нет записей для компонента {component}.")


def frontend_log_summary() -> str:
    if not FRONTEND_LOG_FILE.is_file():
        return "нет событий"
    try:
        rows = FRONTEND_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        if not rows:
            return "нет событий"
        last = json.loads(rows[-1])
        stage = last.get("stage") or last.get("kind") or last.get("message") or "event"
        return f"{len(rows)} events · last: {str(stage)[:46]}"
    except Exception:
        return "лог есть, формат повреждён"


def admin_public_web_diagnostics() -> str:
    checks = [
        ("index", f"{PUBLIC_BASE_URL}/", "script.js?v=23"),
        ("script", f"{PUBLIC_BASE_URL}/script.js?v=23", "SVGTRACKER_BOOT_STAGE"),
        ("style", f"{PUBLIC_BASE_URL}/style.css?v=23", ":root"),
    ]
    rows = []
    for name, url, expected in checks:
        started = time.monotonic()
        try:
            req = urllib_request.Request(url, headers={"User-Agent": "SVGTracker-Diagnostics/23", "Cache-Control": "no-cache"})
            with urllib_request.urlopen(req, timeout=4) as response:
                body = response.read(512_000).decode("utf-8", "replace")
                elapsed = int((time.monotonic() - started) * 1000)
                ok = response.status == 200 and expected in body
                rows.append(f"{name}: {'OK' if ok else 'WARN'} · HTTP {response.status} · {len(body)} chars · {elapsed}ms")
        except Exception as exc:
            rows.append(f"{name}: FAIL · {type(exc).__name__}: {exc}")
    return "\n".join(rows)


def admin_db_diagnostics() -> str:
    db_path = Path(__file__).parent / "svgtracker.db"
    if not db_path.is_file():
        return "DB: missing"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            conn.close()
        wal = Path(str(db_path) + "-wal")
        shm = Path(str(db_path) + "-shm")
        return f"quick_check={quick} · tables={tables} · db={db_path.stat().st_size/1024:.1f}KB · wal={(wal.stat().st_size if wal.exists() else 0)/1024:.1f}KB · shm={(shm.stat().st_size if shm.exists() else 0)/1024:.1f}KB"
    except Exception as exc:
        return f"DB check FAIL · {type(exc).__name__}: {exc}"


def admin_diagnostics_text() -> str:
    status = admin_server_status_text()
    latest_frontend = tail_json_log_pretty(FRONTEND_LOG_FILE, 12)
    deploy = admin_latest_status()
    return admin_trim(
        status
        + "\n\nPublic web:\n" + admin_public_web_diagnostics()
        + "\n\nSQLite:\n" + admin_db_diagnostics()
        + "\n\nFrontend telemetry (последние):\n" + latest_frontend
        + "\n\nDeploy:\n"
        + f"status={deploy.get('status', '—')} updated={deploy.get('updated_at', '—')}\n"
        + str(deploy.get('message', ''))
    )


def admin_journal(lines: int = 40, errors_only: bool = False) -> str:
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u", "svgtracker.service",
                "-u", "svgtracker-watchdog.service",
                "-n", str(max(1, min(lines, 250))),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True, text=True, timeout=8,
        )
        output = proc.stdout or proc.stderr or "Логи пусты"
        if errors_only:
            keywords = (" error", "exception", "traceback", "failed", "critical", "warning", " 400 ", " 401 ", " 403 ", " 404 ", " 409 ", " 422 ", " 429 ", " 500 ", " 502 ", " 503 ")
            filtered = [line for line in output.splitlines() if any(key in line.lower() for key in keywords)]
            output = "\n".join(filtered[-45:]) or "За последние записи явных ошибок не найдено."
        return admin_trim(output)
    except Exception as exc:
        return f"Не удалось прочитать journald: {exc}"


def admin_latest_status() -> dict:
    path = ADMIN_STATE_DIR / "deploy_status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception:
        return {}


def admin_list_backups(limit: int = 8) -> list[Path]:
    admin_state_dirs()
    return sorted(ADMIN_BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def admin_git_state() -> str:
    try:
        head = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=4).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(Path(__file__).parent), "status", "--porcelain"], capture_output=True, text=True, timeout=4).stdout.strip()
        return f"{head or '—'}{' · local changes' if dirty else ' · clean'}"
    except Exception:
        return "—"


def admin_frontend_status() -> str:
    root = Path(__file__).parent
    required = {"index.html": 1000, "script.js": 1000, "style.css": 1000}
    missing = []
    for name, minimum in required.items():
        path = root / name
        if not path.is_file() or path.stat().st_size < minimum:
            missing.append(name)
    if missing:
        return "FAIL · " + ", ".join(missing)
    try:
        html = (root / "index.html").read_text(encoding="utf-8", errors="replace")
        if "/script.js?v=23" not in html or "/style.css?v=23" not in html:
            return "WARN · asset version mismatch"
    except Exception:
        return "FAIL · index unreadable"
    return "OK · v23 assets"


def admin_server_status_text() -> str:
    active = subprocess.run(["systemctl", "is-active", "svgtracker"], capture_output=True, text=True).stdout.strip() or "unknown"
    nginx = subprocess.run(["systemctl", "is-active", "nginx"], capture_output=True, text=True).stdout.strip() or "unknown"
    try:
        with urllib_request.urlopen("http://127.0.0.1:8000/api/test", timeout=2.5) as response:
            api = "OK" if response.status == 200 else f"HTTP {response.status}"
    except Exception as exc:
        api = f"FAIL · {type(exc).__name__}"
    disk = shutil.disk_usage(Path(__file__).parent)
    db_path = Path(__file__).parent / "svgtracker.db"
    db_size = db_path.stat().st_size if db_path.exists() else 0
    mem_available = "—"
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0])
        mem_available = f"{info.get('MemAvailable', 0) // 1024} MB"
    except Exception:
        pass
    latest = admin_latest_status()
    deploy_line = latest.get("status") or "—"
    watchdog = subprocess.run(["systemctl", "is-active", "svgtracker-watchdog.timer"], capture_output=True, text=True).stdout.strip() or "unknown"
    return (
        "SVGTracker · server status\n\n"
        f"Version: {APP_VERSION}\n"
        f"Service: {active}\n"
        f"Nginx: {nginx}\n"
        f"API: {api}\n"
        f"Frontend: {admin_frontend_status()}\n"
        f"Frontend logs: {frontend_log_summary()}\n"
        f"Watchdog: {watchdog}\n"
        f"Git: {admin_git_state()}\n"
        f"DB: {db_size / 1024:.1f} KB\n"
        f"RAM available: {mem_available}\n"
        f"Disk free: {disk.free / (1024**3):.1f} GB\n"
        f"Last deploy: {deploy_line}"
    )


async def notify_admin_error(title: str, detail: str) -> None:
    fingerprint = hashlib.sha1(f"{title}|{detail[:500]}".encode("utf-8", "ignore")).hexdigest()
    now = time.time()
    if now - ADMIN_ERROR_COOLDOWN.get(fingerprint, 0) < 300:
        return
    ADMIN_ERROR_COOLDOWN[fingerprint] = now
    try:
        await bot.send_message(
            ADMIN_TELEGRAM_ID,
            admin_trim(f"⚠️ SVGTracker\n{title}\n\n{detail}", 3800),
        )
    except Exception:
        logger.exception("Failed to notify admin about %s", title)


def admin_launch_transient(args: list[str], prefix: str) -> str:
    if not ADMIN_DEPLOY_HELPER.is_file():
        raise RuntimeError("deploy/admin_deploy.py not found")
    unit = f"svgtracker-{prefix}-{int(time.time())}"
    python_bin = Path(__file__).parent / "venv" / "bin" / "python"
    python_cmd = str(python_bin) if python_bin.is_file() else (shutil.which("python3") or "python3")
    command = [
        "systemd-run", "--unit", unit, "--collect", "--property=Type=oneshot",
        python_cmd, str(ADMIN_DEPLOY_HELPER), *args,
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "systemd-run failed").strip())
    return unit



async def admin_helper_json(command: str, timeout: int = 70) -> dict:
    if not ADMIN_DEPLOY_HELPER.is_file():
        raise RuntimeError("deploy/admin_deploy.py not found")
    python_bin = Path(__file__).parent / "venv" / "bin" / "python"
    python_cmd = str(python_bin) if python_bin.is_file() else (shutil.which("python3") or "python3")
    proc = await asyncio.to_thread(
        subprocess.run,
        [python_cmd, str(ADMIN_DEPLOY_HELPER), command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{command} failed").strip())
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except Exception as exc:
        raise RuntimeError(f"Invalid helper response: {lines[-1][:800]}") from exc


def github_status_text(payload: dict) -> str:
    sync_labels = {
        "synced": "синхронизирован",
        "ahead": "VPS впереди GitHub",
        "behind": "GitHub впереди VPS",
        "diverged": "ветки разошлись",
        "fetch_failed": "не удалось проверить remote",
        "unknown": "неизвестно",
    }
    configured = "да" if payload.get("configured") else "нет"
    clean = "да" if payload.get("clean") else "нет"
    return (
        "GitHub sync\n\n"
        f"Repository: {payload.get('repository') or '—'}\n"
        f"Branch: {payload.get('branch') or '—'}\n"
        f"Deploy Key: {configured}\n"
        f"Working tree clean: {clean}\n"
        f"HEAD: {payload.get('head') or '—'}\n"
        f"origin: {payload.get('origin_head') or '—'}\n"
        f"State: {sync_labels.get(payload.get('sync'), payload.get('sync') or '—')}"
        + (f"\nError: {payload.get('error')}" if payload.get("error") else "")
    )

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


@app.middleware("http")
async def monitor_http_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled API error %s %s", request.method, request.url.path)
        await notify_admin_error(
            "Ошибка API",
            f"{request.method} {request.url.path}\n{type(exc).__name__}: {exc}",
        )
        raise


@app.get("/api/test")
def api_test():
    return {"status": "ok", "service": "SVGTracker API", "version": APP_VERSION}


@app.post("/api/diagnostics/frontend")
async def api_frontend_diagnostics(request: Request):
    """Receive privacy-minimized browser runtime diagnostics.

    This endpoint intentionally does not require Telegram auth: it must work even
    when the Telegram SDK itself is the thing that failed to load. It stores no
    cookies, initData, request bodies or authorization headers.
    """
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = FRONTEND_RATE.setdefault(host, deque())
    while bucket and now - bucket[0] > FRONTEND_RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= FRONTEND_RATE_LIMIT:
        return JSONResponse(status_code=429, content={"status": "rate_limited"})
    bucket.append(now)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "invalid_json"})
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        events = [payload] if isinstance(payload, dict) else []
    accepted = 0
    for item in events[:20]:
        if not isinstance(item, dict):
            continue
        append_json_log(FRONTEND_LOG_FILE, {
            "version": APP_VERSION,
            "session": item.get("session"),
            "level": item.get("level") or "info",
            "stage": item.get("stage") or item.get("kind") or "event",
            "message": item.get("message"),
            "stack": item.get("stack"),
            "path": item.get("path"),
            "asset": item.get("asset"),
            "ready_state": item.get("readyState"),
            "online": item.get("online"),
            "sdk": item.get("sdk"),
            "ua": item.get("ua"),
            "client_ts": item.get("ts"),
        })
        accepted += 1
    return {"status": "ok", "accepted": accepted, "version": APP_VERSION}


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

    with FINANCE_WRITE_LOCK:
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
    for index, item in enumerate(state.get("categories", [])):
        if not isinstance(item, dict):
            continue
        category_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if category_id and name:
            try:
                order = int(item.get("order", index))
            except (TypeError, ValueError):
                order = index
            categories.append({
                "id": category_id,
                "name": name[:32],
                "color": str(item.get("color") or "#8e8e93"),
                "order": order,
            })
    categories.sort(key=lambda item: (item.get("order", 0), item.get("name", "").casefold()))
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
        return {"status": "error", "ok": False, "message": "Выбери: расход или доход"}
    try:
        amount = round(float(payload.get("amount")), 2)
    except (TypeError, ValueError):
        return {"status": "error", "ok": False, "message": "Укажи корректную сумму"}
    if not (0 < amount <= 100_000_000):
        return {"status": "error", "ok": False, "message": "Сумма должна быть больше нуля"}

    date_value = str(payload.get("date") or "").strip()
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return {"status": "error", "ok": False, "message": "Некорректная дата. Нужен формат YYYY-MM-DD"}

    now = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    entry_id = f"shortcut_{kind}_{uuid.uuid4().hex}"

    with FINANCE_WRITE_LOCK:
        state = finance_state_for_shortcut(user["user_id"])
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
                return {"status": "error", "ok": False, "message": "Категория не найдена. Обнови список в Shortcut."}
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

    logger.info(
        "Shortcut finance transaction user_id=%s type=%s amount=%s date=%s entry_id=%s",
        user["user_id"], kind, amount, date_value, entry_id,
    )

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
        "ok": True,
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
    kind = str(payload.get("kind") or "bot").strip().lower()
    if kind not in {"bot", "friends"}:
        raise HTTPException(status_code=422, detail="kind must be bot or friends")
    settings = set_bot_notifications(user_id, payload["enabled"], kind=kind)
    enabled_key = "friend_request_notifications" if kind == "friends" else "bot_notifications"
    return {"status": "ok", "kind": kind, "enabled": settings[enabled_key]}


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
        settings = get_user_settings(target_id) if target_id else {"bot_notifications": False, "friend_request_notifications": False}
        if target_telegram_id and settings.get("friend_request_notifications", True):
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


@app.delete("/api/friends/requests/{request_id}")
def api_friend_cancel(request_id: int, x_telegram_init_data: str | None = Header(default=None)):
    _, user_id = authenticated_user(x_telegram_init_data)
    if not cancel_friend_request(user_id, request_id):
        raise HTTPException(status_code=404, detail="Исходящий запрос не найден")
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


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin_message(message):
        return
    await message.answer(
        "SVGTracker Admin\n\n"
        "Production управляется отсюда. ZIP-deploy делает backup, проверяет файлы, "
        "перезапускает сервис и автоматически откатывается, если API не поднимается.\n\n"
        "После настройки GitHub успешный ZIP-deploy автоматически делает commit + push в origin/main.\n\n"
        "Команды: /deploy /deploy_status /server_status /diagnostics /logs_frontend /logs_backend /logs_bot /logs_system /logs_deploy /backup /backups /rollback /logs /errors /restart "
        "/github_setup /github_test /github_status /github_sync",
        reply_markup=admin_keyboard(),
    )




@dp.message(Command("watchdog_on"))
async def admin_watchdog_on(message: Message):
    if not is_admin_message(message):
        return
    proc = await asyncio.to_thread(
        subprocess.run,
        ["systemctl", "enable", "--now", "svgtracker-watchdog.timer"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode == 0:
        await message.answer("Watchdog включён. V21 ждёт несколько health-check попыток и больше не должен тревожить во время обычного запуска сервиса.")
    else:
        await message.answer(admin_trim(f"Watchdog error: {proc.stderr or proc.stdout}"))


@dp.message(Command("watchdog_off"))
async def admin_watchdog_off(message: Message):
    if not is_admin_message(message):
        return
    proc = await asyncio.to_thread(
        subprocess.run,
        ["systemctl", "disable", "--now", "svgtracker-watchdog.timer"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode == 0:
        await message.answer("Watchdog выключен. Основной svgtracker.service продолжает работать и автоматически перезапускается systemd.")
    else:
        await message.answer(admin_trim(f"Watchdog error: {proc.stderr or proc.stdout}"))


@dp.message(Command("github_setup"))
async def admin_github_setup(message: Message):
    if not is_admin_message(message):
        return
    try:
        payload = await admin_helper_json("github-setup", timeout=35)
        public_key = str(payload.get("public_key") or "").strip()
        repo = payload.get("repository") or "sanychoys/SVG"
        if not public_key:
            raise RuntimeError("Public key was not generated")
        await message.answer(
            "GitHub Deploy Key создан на VPS. Приватный ключ остаётся только на сервере.\n\n"
            f"Repository: {repo}\n\n"
            "1. Открой GitHub → SVG → Settings → Deploy keys → Add deploy key.\n"
            "2. Title: SVGTracker VPS\n"
            "3. Вставь ключ из следующего сообщения.\n"
            "4. Обязательно включи Allow write access.\n"
            "5. Нажми Add key.\n"
            "6. Вернись сюда и отправь /github_test.\n\n"
            f"Прямая страница: https://github.com/{repo}/settings/keys"
        )
        await message.answer(public_key)
    except Exception as exc:
        logger.exception("GitHub setup failed")
        await message.answer(admin_trim(f"GitHub setup error: {exc}"))


@dp.message(Command("github_test"))
async def admin_github_test(message: Message):
    if not is_admin_message(message):
        return
    try:
        payload = await admin_helper_json("github-test", timeout=55)
        await message.answer(
            "GitHub write access: OK\n"
            f"Repository: {payload.get('repository', '—')}\n"
            f"Branch: {payload.get('branch', '—')}\n\n"
            "Если V21 был установлен ZIP-ботом поверх старой Git-версии, теперь отправь /github_sync один раз. "
            "После этого будущие ZIP-deploy будут автоматически push'иться в GitHub."
        )
    except Exception as exc:
        await message.answer(
            admin_trim(
                "GitHub test failed. Проверь, что публичный ключ добавлен именно в Deploy keys этого репозитория и включён Allow write access.\n\n"
                f"{exc}"
            )
        )


@dp.message(Command("github_status"))
async def admin_github_status(message: Message):
    if not is_admin_message(message):
        return
    try:
        payload = await admin_helper_json("github-status", timeout=55)
        await message.answer(admin_trim(github_status_text(payload)))
    except Exception as exc:
        await message.answer(admin_trim(f"GitHub status error: {exc}"))


@dp.message(Command("github_sync"))
async def admin_github_sync(message: Message):
    if not is_admin_message(message):
        return
    try:
        payload = await admin_helper_json("github-sync", timeout=90)
        files = payload.get("files") or []
        await message.answer(
            "GitHub синхронизирован.\n"
            f"Repository: {payload.get('repository', '—')}\n"
            f"Branch: {payload.get('branch', '—')}\n"
            f"Commit: {payload.get('commit', '—')}\n"
            f"Зафиксировано файлов: {len(files)}"
        )
    except Exception as exc:
        await message.answer(admin_trim(f"GitHub sync остановлен: {exc}"))


@dp.message(Command("deploy"))
async def admin_deploy_help(message: Message):
    if not is_admin_message(message):
        return
    await message.answer(
        "Пришли ZIP проекта прямо в этот чат. Я покажу имя/размер и попрошу подтверждение.\n\n"
        "Production config.py, SQLite, WAL/SHM, .env, venv, .git, uploads и логи ZIP-deploy не перезаписывает.\n\n"
        "V21+: deploy запускается только когда VPS синхронизирован с GitHub. После health-check бот автоматически создаёт commit и push."
    )


async def admin_server_status_text_async() -> str:
    return await asyncio.to_thread(admin_server_status_text)


@dp.message(Command("deploy_status"))
async def admin_deploy_status(message: Message):
    if not is_admin_message(message):
        return
    status = admin_latest_status()
    if not status:
        await message.answer("Deploy-истории пока нет.")
        return
    await message.answer(admin_trim(
        f"Deploy status: {status.get('status', '—')}\n"
        f"Обновлено: {status.get('updated_at', '—')}\n\n"
        f"{status.get('message', '')}"
    ))


@dp.message(Command("server_status", "health"))
async def admin_server_status(message: Message):
    if not is_admin_message(message):
        return
    await message.answer(await admin_server_status_text_async())


async def create_manual_backup_for_admin() -> str:
    admin_state_dirs()
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(ADMIN_DEPLOY_HELPER), "backup"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "backup failed").strip())
    payload = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
    return payload.get("name") or Path(payload.get("path") or "").name


@dp.message(Command("backup"))
async def admin_backup(message: Message):
    if not is_admin_message(message):
        return
    try:
        name = await create_manual_backup_for_admin()
        await message.answer(f"Backup создан: {name}\nКод + безопасный snapshot SQLite сохранены на VPS.")
    except Exception as exc:
        logger.exception("Admin backup failed")
        await message.answer(admin_trim(f"Backup error: {exc}"))


@dp.message(Command("backups"))
async def admin_backups(message: Message):
    if not is_admin_message(message):
        return
    backups = admin_list_backups()
    if not backups:
        await message.answer("Backups пока нет.")
        return
    lines = ["Последние backups:"]
    for index, path in enumerate(backups, 1):
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m %H:%M")
        lines.append(f"{index}. {path.name} · {stamp} · {path.stat().st_size / 1024:.0f} KB")
    lines.append("\n/rollback откатывает код к последнему backup. БД при rollback не откатывается, чтобы не потерять новые данные.")
    await message.answer("\n".join(lines))


@dp.message(Command("rollback"))
async def admin_rollback(message: Message):
    if not is_admin_message(message):
        return
    backups = admin_list_backups(1)
    if not backups:
        await message.answer("Нет backup для rollback.")
        return
    backup = backups[0]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Откатить", callback_data="admin:rollback_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="admin:cancel")],
    ])
    await message.answer(
        f"Откатить production к {backup.name}?\n\nБаза данных останется текущей.",
        reply_markup=keyboard,
    )


@dp.message(Command("logs"))
async def admin_logs(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("Последние логи:\n\n" + admin_journal(45))


@dp.message(Command("logs_frontend"))
async def admin_logs_frontend(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("Frontend runtime:\n\n" + tail_json_log_pretty(FRONTEND_LOG_FILE, 55))


@dp.message(Command("logs_backend"))
async def admin_logs_backend(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("Backend / API:\n\n" + admin_component_journal("backend", 180))


@dp.message(Command("logs_bot"))
async def admin_logs_bot(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("Telegram bot:\n\n" + admin_component_journal("bot", 180))


@dp.message(Command("logs_system"))
async def admin_logs_system(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("systemd / watchdog:\n\n" + admin_component_journal("system", 180))


@dp.message(Command("logs_deploy"))
async def admin_logs_deploy(message: Message):
    if not is_admin_message(message):
        return
    path = ADMIN_LOG_DIR / "deploy.log"
    await message.answer("Deploy / GitHub:\n\n" + tail_json_log_pretty(path, 55))


@dp.message(Command("diagnostics"))
async def admin_diagnostics(message: Message):
    if not is_admin_message(message):
        return
    await message.answer(await asyncio.to_thread(admin_diagnostics_text))


@dp.message(Command("errors"))
async def admin_errors(message: Message):
    if not is_admin_message(message):
        return
    await message.answer("Ошибки/предупреждения:\n\n" + admin_journal(180, errors_only=True))


@dp.message(Command("restart"))
async def admin_restart(message: Message):
    if not is_admin_message(message):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перезапустить", callback_data="admin:restart_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="admin:cancel")],
    ])
    await message.answer("Перезапустить SVGTracker? systemd поднимет API и бота автоматически.", reply_markup=keyboard)


@dp.message(F.document)
async def admin_zip_upload(message: Message):
    if not is_admin_message(message) or not message.document:
        return
    name = str(message.document.file_name or "update.zip")
    if not name.lower().endswith(".zip"):
        return
    size = int(message.document.file_size or 0)
    if size <= 0 or size > ADMIN_MAX_ZIP_BYTES:
        await message.answer("ZIP слишком большой. Лимит admin-deploy: 20 MB.")
        return
    admin_state_dirs()
    token = uuid.uuid4().hex[:10]
    target = ADMIN_STATE_DIR / "incoming" / f"{int(time.time())}-{token}.zip"
    try:
        await bot.download(message.document, destination=target)
    except Exception as exc:
        logger.exception("Failed to download admin deploy ZIP")
        await message.answer(f"Не удалось скачать ZIP: {exc}")
        return
    ADMIN_PENDING_DEPLOYS[token] = {
        "path": str(target), "name": name, "size": size, "created_at": time.time()
    }
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Проверить и обновить", callback_data=f"admin:deploy_confirm:{token}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"admin:deploy_cancel:{token}")],
    ])
    await message.answer(
        f"ZIP получен: {name}\nРазмер: {size / 1024:.0f} KB\n\n"
        "Перед заменой будет backup. config.py и production-БД защищены. При провале health-check или GitHub push сработает rollback. "
        "После успешной проверки изменения автоматически попадут в GitHub.",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery):
    if not callback.from_user or not is_admin_telegram_id(callback.from_user.id) or not callback.data:
        await callback.answer("Недоступно", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "cancel":
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.edit_text("Действие отменено.")
        return
    if action == "status":
        await callback.answer()
        if callback.message:
            await callback.message.answer(await admin_server_status_text_async())
        return
    if action == "errors":
        await callback.answer()
        if callback.message:
            await callback.message.answer("Ошибки/предупреждения:\n\n" + admin_journal(180, errors_only=True))
        return
    if action == "logs":
        await callback.answer()
        if callback.message:
            await callback.message.answer("Последние логи:\n\n" + admin_journal(45))
        return
    if action == "logs_frontend":
        await callback.answer()
        if callback.message:
            await callback.message.answer("Frontend runtime:\n\n" + tail_json_log_pretty(FRONTEND_LOG_FILE, 55))
        return
    if action == "diagnostics":
        await callback.answer()
        if callback.message:
            await callback.message.answer(await asyncio.to_thread(admin_diagnostics_text))
        return
    if action == "github":
        await callback.answer()
        if callback.message:
            try:
                payload = await admin_helper_json("github-status", timeout=55)
                await callback.message.answer(
                    admin_trim(github_status_text(payload))
                    + "\n\nНастройка: /github_setup → добавить Deploy Key на GitHub → /github_test → /github_sync"
                )
            except Exception as exc:
                await callback.message.answer(admin_trim(f"GitHub status error: {exc}"))
        return
    if action == "deploy":
        await callback.answer()
        if callback.message:
            await callback.message.answer("Пришли ZIP проекта в этот чат. После загрузки появится подтверждение deploy.")
        return
    if action == "backup":
        await callback.answer("Создаю backup…")
        try:
            name = await create_manual_backup_for_admin()
            if callback.message:
                await callback.message.answer(f"Backup создан: {name}")
        except Exception as exc:
            if callback.message:
                await callback.message.answer(admin_trim(f"Backup error: {exc}"))
        return
    if action == "backups":
        await callback.answer()
        backups = admin_list_backups()
        text = "Backups пока нет." if not backups else "Последние backups:\n" + "\n".join(
            f"{i}. {path.name}" for i, path in enumerate(backups, 1)
        )
        if callback.message:
            await callback.message.answer(text)
        return
    if action == "rollback":
        await callback.answer()
        backups = admin_list_backups(1)
        if not backups:
            if callback.message:
                await callback.message.answer("Нет backup для rollback.")
            return
        backup = backups[0]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Откатить", callback_data="admin:rollback_confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data="admin:cancel")],
        ])
        if callback.message:
            await callback.message.answer(f"Rollback к {backup.name}? БД останется текущей.", reply_markup=keyboard)
        return
    if action == "rollback_confirm":
        backups = admin_list_backups(1)
        if not backups:
            await callback.answer("Backup не найден", show_alert=True)
            return
        path = backups[0].resolve()
        try:
            unit = admin_launch_transient(["rollback", str(path), "--admin-chat", str(ADMIN_TELEGRAM_ID)], "rollback")
            await callback.answer("Rollback запущен")
            if callback.message:
                await callback.message.edit_text(f"Rollback запущен ({unit}). Итог придёт отдельным сообщением.")
        except Exception as exc:
            await callback.answer("Ошибка запуска", show_alert=True)
            if callback.message:
                await callback.message.answer(admin_trim(str(exc)))
        return
    if action == "restart_confirm":
        try:
            unit = f"svgtracker-restart-{int(time.time())}"
            proc = subprocess.run(
                ["systemd-run", "--unit", unit, "--collect", "--on-active=2s", "systemctl", "restart", "svgtracker"],
                capture_output=True, text=True, timeout=8,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout).strip())
            await callback.answer("Перезапуск запланирован")
            if callback.message:
                await callback.message.edit_text("Перезапуск запланирован. systemd автоматически поднимет сервис.")
        except Exception as exc:
            await callback.answer("Ошибка", show_alert=True)
            if callback.message:
                await callback.message.answer(admin_trim(str(exc)))
        return
    if action == "deploy_cancel" and len(parts) >= 3:
        token = parts[2]
        pending = ADMIN_PENDING_DEPLOYS.pop(token, None)
        if pending:
            try:
                Path(pending["path"]).unlink(missing_ok=True)
            except Exception:
                pass
        await callback.answer("Deploy отменён")
        if callback.message:
            await callback.message.edit_text("Deploy отменён.")
        return
    if action == "deploy_confirm" and len(parts) >= 3:
        token = parts[2]
        pending = ADMIN_PENDING_DEPLOYS.pop(token, None)
        if not pending or time.time() - pending.get("created_at", 0) > 1800:
            await callback.answer("ZIP устарел. Отправь его снова.", show_alert=True)
            return
        path = Path(pending["path"])
        if not path.is_file():
            await callback.answer("ZIP не найден", show_alert=True)
            return
        try:
            unit = admin_launch_transient(["deploy", str(path), "--admin-chat", str(ADMIN_TELEGRAM_ID)], "deploy")
            await callback.answer("Deploy запущен")
            if callback.message:
                await callback.message.edit_text(
                    f"Deploy запущен ({unit}). Проверка, backup, restart, health-check, commit и GitHub push выполняются автоматически. Итог придёт отдельным сообщением."
                )
        except Exception as exc:
            await callback.answer("Ошибка запуска", show_alert=True)
            if callback.message:
                await callback.message.answer(admin_trim(str(exc)))
        return
    await callback.answer("Команда не распознана", show_alert=True)


@dp.errors()
async def telegram_error_handler(event: ErrorEvent):
    logger.error("Unhandled Telegram update error", exc_info=(type(event.exception), event.exception, event.exception.__traceback__))
    await notify_admin_error("Ошибка Telegram bot", f"{type(event.exception).__name__}: {event.exception}")
    return True


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
    logger.info("SVGTracker starting")
    await setup_bot_commands()

    api_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    api_server = uvicorn.Server(api_config)
    bot_task = asyncio.create_task(dp.start_polling(bot), name="telegram-polling")
    api_task = asyncio.create_task(api_server.serve(), name="uvicorn-api")

    done, pending = await asyncio.wait(
        {bot_task, api_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in done:
        if task.cancelled():
            continue
        error = task.exception()
        if error:
            logger.error("SVGTracker component %s stopped with an error", task.get_name(), exc_info=(type(error), error, error.__traceback__))
            await notify_admin_error(
                "Компонент SVGTracker остановился",
                f"{task.get_name()}\n{type(error).__name__}: {error}\nSystemd попытается перезапустить сервис.",
            )
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise error
        logger.warning("SVGTracker component %s stopped; terminating process so systemd can restart it", task.get_name())
        await notify_admin_error(
            "Компонент SVGTracker неожиданно завершился",
            f"{task.get_name()} завершился без исключения. Процесс остановится, systemd поднимет его заново.",
        )

    for pending_task in pending:
        pending_task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

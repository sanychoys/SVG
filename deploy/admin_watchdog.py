#!/usr/bin/env python3
"""External SVGTracker watchdog. Runs outside svgtracker.service via systemd timer.

The watchdog deliberately uses several health attempts before and after a restart.
SVGTracker normally needs a few seconds to import dependencies, initialize SQLite,
start Telegram polling and bind Uvicorn, so a single early probe must never create
an outage alert.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("SVGTRACKER_PROJECT_ROOT", "/var/www/SVG")).resolve()
STATE_DIR = Path(os.environ.get("SVGTRACKER_ADMIN_STATE_DIR", "/var/lib/svgtracker-admin"))
ADMIN_ID = int(os.environ.get("SVGTRACKER_ADMIN_ID", "382257126"))
HEALTH_URL = "http://127.0.0.1:8000/api/test"
STATE_FILE = STATE_DIR / "watchdog.json"
DEPLOY_LOCK = STATE_DIR / "deploy.lock"
COOLDOWN = 15 * 60
INITIAL_ATTEMPTS = 3
INITIAL_DELAY = 2.0
RESTART_TIMEOUT = 35.0
RESTART_DELAY = 2.0


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def healthy():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as response:
            body = response.read(2048).decode("utf-8", "replace")
            return response.status == 200 and '"status":"ok"' in body.replace(" ", "")
    except Exception:
        return False


def wait_healthy(timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy():
            return True
        time.sleep(delay)
    return healthy()


def bot_token():
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from config import BOT_TOKEN  # type: ignore
        return str(BOT_TOKEN or "").strip()
    except Exception:
        return ""
    finally:
        try:
            sys.path.remove(str(PROJECT_ROOT))
        except ValueError:
            pass


def notify(text):
    token = bot_token()
    if not token:
        return
    data = urllib.parse.urlencode({"chat_id": str(ADMIN_ID), "text": text[:3900]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=8).read(1024)
    except Exception:
        pass


def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def write_state(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, STATE_FILE)


def deploy_in_progress() -> bool:
    if not DEPLOY_LOCK.exists():
        return False
    try:
        if time.time() - DEPLOY_LOCK.stat().st_mtime < 20 * 60:
            return True
        DEPLOY_LOCK.unlink(missing_ok=True)
    except OSError:
        return True
    return False


def main():
    if deploy_in_progress():
        return 0

    # Avoid reacting to a tiny startup/reload gap.
    for attempt in range(INITIAL_ATTEMPTS):
        if healthy():
            state = read_state()
            if state.get("down"):
                state.update({"down": False, "recovered_at": now_iso(), "last_check": now_iso()})
                write_state(state)
                notify("✅ SVGTracker watchdog\nAPI снова доступен.")
            return 0
        if attempt + 1 < INITIAL_ATTEMPTS:
            time.sleep(INITIAL_DELAY)

    state = read_state()
    before = time.time()
    proc = subprocess.run(["systemctl", "restart", "svgtracker"], capture_output=True, text=True)
    recovered = proc.returncode == 0 and wait_healthy(RESTART_TIMEOUT, RESTART_DELAY)
    last_notice = float(state.get("last_notice_epoch") or 0)
    state.update({
        "down": not recovered,
        "last_check": now_iso(),
        "last_restart_ok": proc.returncode == 0,
        "last_recovered": recovered,
    })
    if before - last_notice >= COOLDOWN:
        state["last_notice_epoch"] = before
        if recovered:
            notify("⚠️ SVGTracker watchdog\nAPI действительно перестал отвечать. Сервис перезапущен и снова работает.")
        else:
            detail = (proc.stderr or proc.stdout or "restart completed but API is still unavailable").strip()
            notify(
                "🚨 SVGTracker watchdog\n"
                f"API недоступен после restart и {int(RESTART_TIMEOUT)} секунд ожидания.\n\n{detail[:1800]}"
            )
    write_state(state)
    # Keep the timer unit healthy; the outage itself is recorded in state and Telegram.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

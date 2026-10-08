#!/usr/bin/env python3
"""Safe, admin-triggered SVGTracker deploy/backup/rollback helper.

This helper is intentionally stdlib-only so it can run in a transient systemd
unit even while svgtracker.service is being restarted.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from html.parser import HTMLParser

PROJECT_ROOT = Path(os.environ.get("SVGTRACKER_PROJECT_ROOT", "/var/www/SVG")).resolve()
SERVICE_NAME = os.environ.get("SVGTRACKER_SERVICE_NAME", "svgtracker")
SERVICE_FILE = Path(os.environ.get("SVGTRACKER_SERVICE_FILE", "/etc/systemd/system/svgtracker.service"))
SYSTEMD_UNITS = {
    "svgtracker.service": SERVICE_FILE,
    "svgtracker-watchdog.service": Path(os.environ.get("SVGTRACKER_WATCHDOG_SERVICE_FILE", "/etc/systemd/system/svgtracker-watchdog.service")),
    "svgtracker-watchdog.timer": Path(os.environ.get("SVGTRACKER_WATCHDOG_TIMER_FILE", "/etc/systemd/system/svgtracker-watchdog.timer")),
}
BACKUP_DIR = Path(os.environ.get("SVGTRACKER_BACKUP_DIR", "/var/backups/svgtracker"))
STATE_DIR = Path(os.environ.get("SVGTRACKER_ADMIN_STATE_DIR", "/var/lib/svgtracker-admin"))
STATUS_FILE = STATE_DIR / "deploy_status.json"
DEPLOY_LOCK = STATE_DIR / "deploy.lock"
DB_PATH = PROJECT_ROOT / "svgtracker.db"
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_FILES = 800
HEALTH_URL = "http://127.0.0.1:8000/api/test"

PROTECTED_NAMES = {
    "config.py", ".env", "svgtracker.db", "svgtracker.db-wal", "svgtracker.db-shm",
}
PROTECTED_PARTS = {".git", "venv", "__pycache__", "uploads", "logs", ".admin_deploy"}
ALLOWED_EXACT = {".gitignore", "requirements.txt", "pyproject.toml", "package.json", "package-lock.json"}
ALLOWED_SUFFIXES = {
    ".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".service", ".timer", ".toml", ".yml", ".yaml",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"
}
CORE_FILES = {"main.py", "finance_db.py", "index.html", "script.js", "style.css"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dirs() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "incoming").mkdir(parents=True, exist_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_status(status: str, message: str, **extra) -> None:
    payload = {"status": status, "message": message, "updated_at": utc_now(), **extra}
    atomic_json(STATUS_FILE, payload)


def safe_rel_path(raw_name: str, strip_prefix: str | None = None) -> Path | None:
    name = str(raw_name or "").replace("\\", "/")
    if strip_prefix and name.startswith(strip_prefix + "/"):
        name = name[len(strip_prefix) + 1 :]
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe archive path: {raw_name}")
    parts = tuple(part for part in pure.parts if part not in ("", "."))
    if not parts:
        return None
    if any(part in PROTECTED_PARTS for part in parts):
        return None
    if parts[-1] in PROTECTED_NAMES:
        return None
    rel = Path(*parts)
    if rel.name in ALLOWED_EXACT or rel.suffix.lower() in ALLOWED_SUFFIXES:
        return rel
    return None


def archive_prefix(infos: list[zipfile.ZipInfo]) -> str | None:
    roots = set()
    for info in infos:
        raw = info.filename.replace("\\", "/")
        pure = PurePosixPath(raw)
        # Validate before considering a common wrapper directory. Otherwise a
        # malicious path such as ../evil.py could be mistaken for a wrapper.
        if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
            raise ValueError(f"Unsafe archive path: {info.filename}")
        name = raw.strip("/")
        if not name:
            continue
        root = name.split("/", 1)[0]
        if root in (".", ".."):
            raise ValueError(f"Unsafe archive path: {info.filename}")
        roots.add(root)
    if len(roots) == 1:
        root = next(iter(roots))
        if any("/" in info.filename.replace("\\", "/").strip("/") for info in infos):
            return root
    return None


def inspect_zip(zip_path: Path) -> tuple[list[tuple[zipfile.ZipInfo, Path]], str | None]:
    if not zip_path.is_file():
        raise ValueError("ZIP file not found")
    if zip_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP is too large")
    selected: list[tuple[zipfile.ZipInfo, Path]] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError("Too many files in ZIP")
        prefix = archive_prefix(infos)
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"Symlinks are not allowed: {info.filename}")
            total += int(info.file_size or 0)
            if total > MAX_EXPANDED_BYTES:
                raise ValueError("ZIP expands to too much data")
            rel = safe_rel_path(info.filename, prefix)
            if rel is not None:
                selected.append((info, rel))
    if not selected:
        raise ValueError("ZIP does not contain deployable project files")
    return selected, prefix


def extract_selected(zip_path: Path, target: Path) -> list[Path]:
    selected, _ = inspect_zip(zip_path)
    output = []
    with zipfile.ZipFile(zip_path) as zf:
        for info, rel in selected:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, dst.open("wb") as out:
                shutil.copyfileobj(src, out)
            output.append(rel)
    return output



class _IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.append(value)


def validate_frontend_contract(root: Path) -> list[str]:
    notes = []
    html_path = root / "index.html" if (root / "index.html").is_file() else PROJECT_ROOT / "index.html"
    js_path = root / "script.js" if (root / "script.js").is_file() else PROJECT_ROOT / "script.js"
    if not html_path.is_file():
        return notes
    html = html_path.read_text(encoding="utf-8")
    parser = _IdParser(); parser.feed(html)
    seen, duplicates = set(), set()
    for value in parser.ids:
        if value in seen: duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError("Duplicate HTML id: " + ", ".join(sorted(duplicates)[:12]))
    notes.append(f"HTML ids checked: {len(parser.ids)}")
    if js_path.is_file():
        js = js_path.read_text(encoding="utf-8")
        handlers = set(re.findall(r'on(?:click|change|input|submit)=["\']\s*([A-Za-z_$][\w$]*)\s*\(', html))
        declared = set(re.findall(r'(?m)^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', js))
        missing = sorted(handlers - declared)
        if missing:
            raise ValueError("Missing JS handlers referenced by HTML: " + ", ".join(missing[:12]))
        notes.append(f"HTML handlers checked: {len(handlers)}")
    return notes

def validate_release(root: Path, relpaths: list[Path]) -> list[str]:
    notes: list[str] = []
    present = {str(path).replace("\\", "/") for path in relpaths}
    if not (CORE_FILES & present):
        raise ValueError("Archive does not contain any SVGTracker core files")
    for rel in relpaths:
        if rel.suffix.lower() == ".py":
            source = (root / rel).read_text(encoding="utf-8")
            compile(source, str(rel), "exec")
    node = shutil.which("node")
    if node:
        for rel in relpaths:
            if rel.suffix.lower() == ".js":
                proc = subprocess.run([node, "--check", str(root / rel)], capture_output=True, text=True)
                if proc.returncode != 0:
                    raise ValueError(f"JavaScript syntax error in {rel}: {proc.stderr.strip()[:900]}")
        notes.append("JS syntax checked with node")
    else:
        notes.append("node not installed; JS syntax check skipped")
    notes.extend(validate_frontend_contract(root))
    return notes


def git_tracked_files() -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "ls-files"], capture_output=True, text=True, check=True
        )
        result = []
        for line in proc.stdout.splitlines():
            try:
                rel = safe_rel_path(line)
            except ValueError:
                continue
            if rel is not None and (PROJECT_ROOT / rel).is_file():
                result.append(rel)
        return result
    except Exception:
        result = []
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(PROJECT_ROOT)
                safe = safe_rel_path(rel.as_posix())
            except Exception:
                continue
            if safe is not None:
                result.append(safe)
        return result



def project_backup_files() -> list[Path]:
    """Return all deployable project files, including files added by bot deploys but not yet tracked by Git."""
    result: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(PROJECT_ROOT)
            safe = safe_rel_path(rel.as_posix())
        except Exception:
            continue
        if safe is not None:
            result.append(safe)
    return sorted(set(result), key=lambda item: item.as_posix())


def sqlite_snapshot(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    source = sqlite3.connect(src, timeout=30)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return True


def create_backup(relpaths: list[Path] | None = None, *, include_db: bool = False, label: str = "deploy") -> tuple[Path, dict]:
    ensure_dirs()
    relpaths = list(dict.fromkeys(relpaths or git_tracked_files()))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1((timestamp + label).encode()).hexdigest()[:7]
    archive = BACKUP_DIR / f"{timestamp}-{label}-{digest}.tar.gz"
    manifest = {
        "created_at": utc_now(),
        "label": label,
        "project_root": str(PROJECT_ROOT),
        "files": [],
        "created_files": [],
        "systemd_units": {},
        "database_snapshot": False,
    }
    with tempfile.TemporaryDirectory(prefix="svgbackup-") as td:
        temp = Path(td)
        db_snapshot = temp / "svgtracker.db"
        if include_db and sqlite_snapshot(DB_PATH, db_snapshot):
            manifest["database_snapshot"] = True
        with tarfile.open(archive, "w:gz") as tf:
            for rel in relpaths:
                src = PROJECT_ROOT / rel
                if src.is_file():
                    tf.add(src, arcname=f"project/{rel.as_posix()}", recursive=False)
                    manifest["files"].append(rel.as_posix())
                else:
                    manifest["created_files"].append(rel.as_posix())
            for unit_name, unit_path in SYSTEMD_UNITS.items():
                existed = unit_path.is_file()
                manifest["systemd_units"][unit_name] = existed
                if existed:
                    tf.add(unit_path, arcname=f"systemd/{unit_name}", recursive=False)
            if manifest["database_snapshot"]:
                tf.add(db_snapshot, arcname="data/svgtracker.db", recursive=False)
            data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mtime = time.time()
            tf.addfile(info, io.BytesIO(data))
    prune_backups()
    return archive, manifest


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tf:
        member = tf.getmember("manifest.json")
        file_obj = tf.extractfile(member)
        if not file_obj:
            raise ValueError("Backup manifest is missing")
        return json.loads(file_obj.read().decode("utf-8"))


def restore_backup(archive: Path, *, restore_db: bool = False) -> dict:
    manifest = read_manifest(archive)
    with tarfile.open(archive, "r:gz") as tf:
        for rel_name in manifest.get("files", []):
            member_name = f"project/{rel_name}"
            try:
                member = tf.getmember(member_name)
            except KeyError:
                continue
            rel = safe_rel_path(rel_name)
            if rel is None:
                continue
            dst = PROJECT_ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src:
                with dst.open("wb") as out:
                    shutil.copyfileobj(src, out)
        for rel_name in manifest.get("created_files", []):
            try:
                rel = safe_rel_path(rel_name)
            except ValueError:
                continue
            if rel is not None:
                path = PROJECT_ROOT / rel
                if path.is_file():
                    path.unlink()
        unit_states = manifest.get("systemd_units")
        if not isinstance(unit_states, dict):
            # Backwards compatibility with pre-V20 backups.
            unit_states = {"svgtracker.service": bool(manifest.get("systemd_service"))}
        for unit_name, existed in unit_states.items():
            target = SYSTEMD_UNITS.get(unit_name)
            if target is None:
                continue
            if existed:
                try:
                    member = tf.getmember(f"systemd/{unit_name}")
                    src = tf.extractfile(member)
                    if src:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("wb") as out:
                            shutil.copyfileobj(src, out)
                except KeyError:
                    pass
            elif target.is_file():
                target.unlink()
        if restore_db and manifest.get("database_snapshot"):
            member = tf.getmember("data/svgtracker.db")
            src = tf.extractfile(member)
            if src:
                with tempfile.NamedTemporaryFile(prefix="svgdb-restore-", delete=False, dir=str(PROJECT_ROOT)) as temp:
                    shutil.copyfileobj(src, temp)
                    temp_name = temp.name
                os.replace(temp_name, DB_PATH)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    return manifest


def prune_backups(limit: int = 20) -> None:
    backups = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[limit:]:
        try:
            old.unlink()
        except OSError:
            pass


def apply_release(extracted: Path, relpaths: list[Path]) -> list[str]:
    changed = []
    for rel in relpaths:
        src = extracted / rel
        dst = PROJECT_ROOT / rel
        if not src.is_file():
            continue
        before = hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else None
        after = hashlib.sha256(src.read_bytes()).hexdigest()
        if before == after:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".deploying")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        changed.append(rel.as_posix())
    unit_changed = False
    for unit_name, unit_target in SYSTEMD_UNITS.items():
        release_unit = PROJECT_ROOT / "deploy" / unit_name
        if not release_unit.is_file():
            continue
        if not unit_target.is_file() or release_unit.read_bytes() != unit_target.read_bytes():
            unit_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(release_unit, unit_target)
            changed.append(str(unit_target))
            unit_changed = True
    if unit_changed:
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        if (PROJECT_ROOT / "deploy" / "svgtracker-watchdog.timer").is_file():
            subprocess.run(["systemctl", "enable", "--now", "svgtracker-watchdog.timer"], check=False)
    return changed


def service_restart() -> None:
    proc = subprocess.run(["systemctl", "restart", SERVICE_NAME], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "systemctl restart failed").strip())


def wait_health(timeout: float = 25.0) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = "no response"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2.5) as response:
                body = response.read(4096).decode("utf-8", "replace")
                if response.status == 200 and '"status":"ok"' in body.replace(" ", ""):
                    return True, body
                last = f"HTTP {response.status}: {body[:300]}"
        except Exception as exc:
            last = str(exc)
        time.sleep(1.0)
    return False, last


def load_bot_token() -> str | None:
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from config import BOT_TOKEN  # type: ignore
        return str(BOT_TOKEN or "").strip() or None
    except Exception:
        return None
    finally:
        try:
            sys.path.remove(str(PROJECT_ROOT))
        except ValueError:
            pass


def telegram_notify(chat_id: str | int | None, text: str) -> None:
    if not chat_id:
        return
    token = load_bot_token()
    if not token:
        return
    payload = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text[:3900]}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read(1024)
    except Exception:
        pass


def git_summary() -> str:
    try:
        head = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(PROJECT_ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return f"{head or '—'}{' · local changes' if dirty else ''}"
    except Exception:
        return "—"


def deploy(zip_path: Path, admin_chat: str | int | None) -> int:
    ensure_dirs()
    deployment_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    DEPLOY_LOCK.write_text(json.dumps({"deployment_id": deployment_id, "started_at": utc_now()}), encoding="utf-8")
    write_status("validating", "Проверяю ZIP", deployment_id=deployment_id, archive=zip_path.name)
    try:
        with tempfile.TemporaryDirectory(prefix="svgdeploy-") as td:
            extracted = Path(td) / "release"
            extracted.mkdir()
            relpaths = extract_selected(zip_path, extracted)
            notes = validate_release(extracted, relpaths)
            changed_targets = [rel for rel in relpaths if not (PROJECT_ROOT / rel).is_file() or (PROJECT_ROOT / rel).read_bytes() != (extracted / rel).read_bytes()]
            if not changed_targets:
                message = "ZIP проверен: изменений относительно production нет."
                write_status("no_changes", message, deployment_id=deployment_id, archive=zip_path.name)
                telegram_notify(admin_chat, "SVGTracker deploy\n\n" + message)
                return 0
            backup, manifest = create_backup(changed_targets, include_db=False, label="predeploy")
            write_status(
                "deploying", "Файлы проверены, применяю обновление", deployment_id=deployment_id,
                archive=zip_path.name, backup=backup.name, files=[p.as_posix() for p in changed_targets], notes=notes,
            )
            changed = apply_release(extracted, relpaths)
            service_restart()
            ok, health_detail = wait_health()
            if not ok:
                restore_backup(backup)
                service_restart()
                rollback_ok, rollback_detail = wait_health()
                message = (
                    "Обновление не прошло health-check и было автоматически откатано.\n"
                    f"Причина: {health_detail[:600]}\n"
                    f"Rollback: {'OK' if rollback_ok else rollback_detail[:300]}"
                )
                write_status(
                    "rolled_back", message, deployment_id=deployment_id, archive=zip_path.name,
                    backup=backup.name, files=changed, health=health_detail,
                )
                telegram_notify(admin_chat, "⚠️ SVGTracker deploy\n\n" + message)
                return 2
            message = (
                f"Обновление успешно. Изменено файлов: {len(changed)}.\n"
                f"Backup: {backup.name}\nGit: {git_summary()}\nAPI: OK"
            )
            write_status(
                "success", message, deployment_id=deployment_id, archive=zip_path.name,
                backup=backup.name, files=changed, health="ok", notes=notes,
            )
            telegram_notify(admin_chat, "✅ SVGTracker deploy\n\n" + message)
            return 0
    except Exception as exc:
        message = f"Deploy error: {type(exc).__name__}: {exc}"
        write_status("error", message, deployment_id=deployment_id, archive=zip_path.name)
        telegram_notify(admin_chat, "❌ SVGTracker deploy\n\n" + message[:3500])
        return 1
    finally:
        try:
            DEPLOY_LOCK.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            incoming = (STATE_DIR / "incoming").resolve()
            if zip_path.resolve().parent == incoming:
                zip_path.unlink(missing_ok=True)
        except Exception:
            pass


def rollback(backup: Path, admin_chat: str | int | None) -> int:
    ensure_dirs()
    try:
        if not backup.is_absolute():
            backup = BACKUP_DIR / backup
        backup = backup.resolve()
        if backup.parent != BACKUP_DIR.resolve() or not backup.is_file():
            raise ValueError("Backup not found")
        safety_backup, _ = create_backup(project_backup_files(), include_db=False, label="pre-rollback")
        restore_backup(backup)
        service_restart()
        ok, detail = wait_health()
        if not ok:
            restore_backup(safety_backup)
            service_restart()
            wait_health()
            raise RuntimeError(f"Rollback target failed health-check: {detail}")
        message = f"Rollback выполнен: {backup.name}\nSafety backup: {safety_backup.name}\nAPI: OK"
        write_status("rollback_success", message, backup=backup.name, safety_backup=safety_backup.name)
        telegram_notify(admin_chat, "✅ SVGTracker rollback\n\n" + message)
        return 0
    except Exception as exc:
        message = f"Rollback error: {type(exc).__name__}: {exc}"
        write_status("rollback_error", message, backup=backup.name if backup else None)
        telegram_notify(admin_chat, "❌ SVGTracker rollback\n\n" + message)
        return 1


def manual_backup(include_db: bool = True) -> int:
    archive, manifest = create_backup(project_backup_files(), include_db=include_db, label="manual")
    print(json.dumps({"path": str(archive), "name": archive.name, "database": manifest.get("database_snapshot")}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("zip_path")
    deploy_parser.add_argument("--admin-chat")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--no-db", action="store_true")
    rollback_parser = sub.add_parser("rollback")
    rollback_parser.add_argument("backup")
    rollback_parser.add_argument("--admin-chat")
    args = parser.parse_args()
    if args.command == "deploy":
        return deploy(Path(args.zip_path), args.admin_chat)
    if args.command == "backup":
        return manual_backup(include_db=not args.no_db)
    if args.command == "rollback":
        return rollback(Path(args.backup), args.admin_chat)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

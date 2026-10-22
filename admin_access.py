"""Persistent, private Telegram administration roles for SVGTracker.

Root admin comes from deployment configuration. Additional admins are kept outside
web root and survive ZIP updates. Only Telegram bot handlers can mutate this file.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger('svgtracker.admin_access')
MAX_ADMINS = 20


def _id(value) -> int:
    if isinstance(value, bool):
        raise ValueError('Некорректный Telegram ID')
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal() or len(text) > 19:
        raise ValueError('Telegram ID должен содержать только цифры')
    number = int(text)
    if not 1 <= number <= 9223372036854775807:
        raise ValueError('Некорректный Telegram ID')
    return number


class AdminRegistry:
    def __init__(self, owner_id: int, state_dir: Path):
        self.owner_id = _id(owner_id)
        self.directory = Path(state_dir)
        self.path = self.directory / 'admins.json'
        self.lock_path = self.directory / 'admins.lock'
        self.audit_path = self.directory / 'admins_audit.jsonl'

    def _ensure_directory(self):
        self.directory.mkdir(parents=True, exist_ok=True)

    def _read(self) -> set[int]:
        if not self.path.exists():
            return {self.owner_id}
        try:
            body = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(body, dict) or body.get('version') != 1 or not isinstance(body.get('admins'), list):
                raise ValueError('Неправильная структура файла администраторов')
            users = {_id(x) for x in body['admins']}
            if len(users) > MAX_ADMINS:
                raise ValueError('Превышено число администраторов')
            return users | {self.owner_id}
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError('Повреждён файл администраторов; требуется проверка VPS') from exc

    def list_ids(self) -> list[int]:
        try:
            return sorted(self._read())
        except RuntimeError:
            logger.exception('Admin registry unavailable; retaining only bootstrap owner')
            return [self.owner_id]

    def is_admin(self, value) -> bool:
        try:
            user_id = _id(value)
        except (ValueError, TypeError):
            return False
        if user_id == self.owner_id:
            return True
        return user_id in self.list_ids()

    def _write(self, members: set[int]):
        payload = {'version': 1, 'admins': sorted(members | {self.owner_id})}
        fd, tmp = tempfile.mkstemp(prefix='.admins-', suffix='.tmp', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                os.fchmod(file.fileno(), 0o600)
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _audit(self, action: str, actor: int, target: int):
        data = {'time': datetime.now(timezone.utc).isoformat(), 'action': action,
                'actor_id': actor, 'target_id': target}
        fd = os.open(str(self.audit_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as file:
            file.write(json.dumps(data, ensure_ascii=False) + '\n')

    def change(self, actor, target, action: str) -> bool:
        actor_id, target_id = _id(actor), _id(target)
        if action not in ('grant', 'revoke'):
            raise ValueError('Недопустимое действие')
        if target_id == self.owner_id:
            raise ValueError('Основного администратора нельзя изменить')
        if actor_id == target_id and action == 'revoke':
            raise ValueError('Нельзя удалить собственные права')
        self._ensure_directory()
        lock_fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(lock_fd, 'r+') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            members = self._read()
            if actor_id not in members:
                raise PermissionError('Доступ администратора отозван')
            was_present = target_id in members
            if action == 'grant':
                if len(members) >= MAX_ADMINS and not was_present:
                    raise ValueError('Достигнут лимит администраторов')
                members.add(target_id)
            else:
                members.discard(target_id)
            changed = (target_id in members) != was_present
            if changed:
                self._write(members)
                try:
                    self._audit(action, actor_id, target_id)
                except OSError:
                    # Rights are already committed atomically; never report failure
                    # after a successful permission change due to logging I/O.
                    logger.exception('Admin change committed but audit log write failed')
            return changed

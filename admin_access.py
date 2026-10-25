"""Persistent, private Telegram administration roles for Site Control.

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

logger = logging.getLogger('sitecontrol.bot')
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
            if actor_id not in members or not self.can(actor_id,'admins.manage'):
                raise PermissionError('Нет права изменять состав администраторов')
            actor_access=self.get_access(actor_id)
            if action=='revoke' and actor_access['role']=='custom' and target_id in members:
                target_access=self.get_access(target_id)
                if target_access['role']!='custom' or not set(target_access['permissions']).issubset(actor_access['permissions']):
                    raise PermissionError('Недостаточно прав для отзыва доступа этого участника')
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
                if action=='grant' and actor_access['role']=='custom':
                    roles=_role_read(self)
                    roles[str(target_id)]={'role':'custom','permissions':actor_access['permissions'],
                                            'projects':actor_access['projects']}
                    fd3,tmp3=tempfile.mkstemp(prefix='.roles-',dir=self.directory)
                    with os.fdopen(fd3,'w',encoding='utf-8') as f3:
                        os.fchmod(f3.fileno(),0o600)
                        json.dump({'version':1,'roles':roles},f3,ensure_ascii=False)
                        f3.flush();os.fsync(f3.fileno())
                    os.replace(tmp3,_role_file(self))
                if action=='revoke':
                    try:
                        roles=_role_read(self)
                        if str(target_id) in roles:
                            roles.pop(str(target_id))
                            fd2,tmp2=tempfile.mkstemp(prefix='.roles-',dir=self.directory)
                            with os.fdopen(fd2,'w',encoding='utf-8') as f2:
                                os.fchmod(f2.fileno(),0o600)
                                json.dump({'version':1,'roles':roles},f2,ensure_ascii=False)
                                f2.flush();os.fsync(f2.fileno())
                            os.replace(tmp2,_role_file(self))
                    except OSError:logger.exception('Could not purge permissions after admin revocation')
                try:
                    self._audit(action, actor_id, target_id)
                except OSError:
                    # Rights are already committed atomically; never report failure
                    # after a successful permission change due to logging I/O.
                    logger.exception('Admin change committed but audit log write failed')
            return changed


# Site Control permission catalogue. `full` includes privileged commands;
# `custom` grants only explicitly selected capabilities.
CAPABILITIES = {
    'projects.view': 'Просмотр проектов',
    'projects.manage': 'Создание, архив и удаление проектов',
    'files.manage': 'Загрузка и публикация файлов',
    'bots.manage': 'Клиентские Telegram-боты',
    'servers.view': 'Просмотр серверов',
    'servers.manage': 'SSH, подготовка и отключение VPS',
    'servers.root': 'Root-команды на клиентских VPS',
    'domains.manage': 'Управление доменами',
    'backups.manage': 'Создание и восстановление копий',
    'logs.view': 'Просмотр журналов',
    'platform.manage': 'Управление нашей платформой',
    'platform.root': 'Root-команды нашего VPS',
    'admins.manage': 'Выдача и изменение доступов',
}


def _role_file(self):
    return self.directory / 'admin_roles.json'


def _role_read(self):
    path=_role_file(self)
    if not path.exists():return {}
    try:
        obj=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(obj,dict) or obj.get('version')!=1 or not isinstance(obj.get('roles'),dict):raise ValueError()
        return obj['roles']
    except (OSError,ValueError,TypeError) as exc:
        # Fail closed rather than unexpectedly granting all access after corruption.
        logger.exception('Admin permissions corrupted; refusing access')
        raise RuntimeError('Повреждён реестр прав администраторов') from exc


def _get_access(self, value):
    uid=_id(value)
    if not self.is_admin(uid):return None
    if uid==self.owner_id:
        return {'role':'owner','permissions':list(CAPABILITIES),'projects':None}
    raw=_role_read(self).get(str(uid))
    # Preserve legacy V1.14 administrators with complete access. New admins
    # can immediately work like cofounders; owner can restrict their role.
    if raw is None:return {'role':'full','permissions':list(CAPABILITIES),'projects':None}
    if not isinstance(raw,dict):raise RuntimeError('Неверный формат прав')
    role=raw.get('role')
    if role=='full':return {'role':'full','permissions':list(CAPABILITIES),'projects':None}
    if role!='custom':raise RuntimeError('Неверная роль')
    perms=raw.get('permissions',[])
    projects=raw.get('projects')
    if not isinstance(perms,list) or any(p not in CAPABILITIES for p in perms):raise RuntimeError('Неверный список прав')
    if projects is not None and (not isinstance(projects,list) or any(type(x)!=int or x<1 for x in projects)):
        raise RuntimeError('Неверный список проектов')
    return {'role':'custom','permissions':list(dict.fromkeys(perms)),
            'projects':list(dict.fromkeys(projects)) if projects is not None else None}


def _can(self, actor, permission, project_id=None):
    try:
        info=_get_access(self,actor)
        if info is None:return False
        if info['role']=='owner':return True
        if permission not in info['permissions']:return False
        if project_id is None:return True
        if int(project_id)==0:
            # A client-project grant must never imply permission to manage the platform.
            return 'platform.manage' in info['permissions']
        return info['projects'] is None or int(project_id) in info['projects']
    except (ValueError,TypeError,RuntimeError):return False


def _set_access(self, actor, target, role, permissions=None, projects=None):
    actor,target=_id(actor),_id(target)
    if target==self.owner_id:raise ValueError('Права владельца изменить нельзя')
    if not self.is_admin(target):raise LookupError('Администратор не найден')
    if not self.can(actor,'admins.manage'):raise PermissionError('Недостаточно прав для управления командой')
    actor_access=self.get_access(actor)
    if actor==target:raise ValueError('Собственные полномочия нельзя повышать через эту форму')
    if role not in ('full','custom'):raise ValueError('Укажите полный или выборочный доступ')
    if role=='custom':
        if not isinstance(permissions,list) or len(permissions)>len(CAPABILITIES) or any(not isinstance(x,str) or x not in CAPABILITIES for x in permissions):
            raise ValueError('Некорректный набор разрешений')
        if projects is not None and (not isinstance(projects,list) or len(projects)>500 or any(type(x)!=int or x<1 for x in projects)):
            raise ValueError('Некорректный перечень проектов')
        normalized=set(permissions)
        if 'platform.root' in normalized:normalized.add('platform.manage')
        if 'servers.root' in normalized:normalized.update(('servers.manage','servers.view'))
        value={'role':'custom','permissions':sorted(normalized),
               'projects':sorted(set(projects)) if projects is not None else None}
    else:value={'role':'full'}
    if actor_access and actor_access['role']=='custom':
        if role=='full' or not set(value['permissions']).issubset(set(actor_access['permissions'])):
            raise PermissionError('Нельзя выдавать права выше собственных')
        scope=actor_access['projects']
        if scope is not None and (value['projects'] is None or not set(value['projects']).issubset(set(scope))):
            raise PermissionError('Нельзя выдавать доступ к чужим проектам')
    self._ensure_directory()
    fd=os.open(str(self.lock_path),os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        if not self.is_admin(target):raise LookupError('Администратор удалён')
        items=_role_read(self)
        items[str(target)]=value
        descriptor,tmp=tempfile.mkstemp(prefix='.roles-',suffix='.tmp',dir=self.directory)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as writer:
                os.fchmod(writer.fileno(),0o600)
                json.dump({'version':1,'roles':items},writer,ensure_ascii=False,indent=2)
                writer.flush();os.fsync(writer.fileno())
            os.replace(tmp,_role_file(self))
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        self._audit('permissions.update',actor,target)
        return self.get_access(target)

AdminRegistry.get_access=_get_access
AdminRegistry.can=_can
AdminRegistry.set_access=_set_access
AdminRegistry.catalogue=lambda self: dict(CAPABILITIES)

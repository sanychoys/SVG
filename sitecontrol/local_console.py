"""Read-only local VPS diagnostics and constrained interactive console.

No credential-changing subprocess arguments are used. On some existing VPS
installations systemd blocks setgroups()/setuid() even when euid is zero.
Running arbitrary shell as root is deliberately prohibited.
"""
from __future__ import annotations

import os
import gc
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESETS = {
    'uptime': ['uptime'],
    'disk': ['df', '-h'],
    'memory': ['free', '-m'],
    'site_status': ['systemctl', 'is-active', 'svgtracker.service'],
    'site_logs': ['journalctl', '-u', 'svgtracker.service', '-n', '80', '--since=24 hours ago', '--utc', '--no-pager', '--output=short-iso'],
    'files': ['ls', '-lah', str(ROOT)],
    'overview': [],
    'ram_cleanup': [],
}

class ConsolePermissionError(RuntimeError):
    """A restricted systemd service cannot safely perform this operation."""


def _size(n: int) -> str:
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if n < 1024 or unit == 'ТБ':
            return f'{n:.1f} {unit}' if unit != 'Б' else f'{n} {unit}'
        n = n / 1024
    return str(n)


def _read_memory() -> str:
    vals = {}
    with open('/proc/meminfo', encoding='ascii') as file:
        for line in file:
            key, _, value = line.partition(':')
            if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
                vals[key] = int(value.strip().split()[0]) * 1024
    total = vals['MemTotal']; free = vals['MemAvailable']
    out = [f'Оперативная память: {_size(total)}', f'Используется: {_size(max(0,total-free))}', f'Доступно: {_size(free)}']
    if vals.get('SwapTotal'):
        out.append(f'Swap: {_size(vals["SwapTotal"]-vals.get("SwapFree",0))} из {_size(vals["SwapTotal"])}')
    return '\n'.join(out)


def _read_disk() -> str:
    data = os.statvfs(ROOT)
    total = data.f_blocks * data.f_frsize
    available = data.f_bavail * data.f_frsize
    used = (data.f_blocks-data.f_bfree) * data.f_frsize
    return f'Раздел с Site Control: {ROOT}\nВсего: {_size(total)}\nЗанято: {_size(used)}\nДоступно: {_size(available)}'


def _read_uptime() -> str:
    with open('/proc/uptime', encoding='ascii') as file:
        seconds = int(float(file.read().split()[0]))
    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes = remain // 60
    return f'Время работы VPS: {days} д. {hours} ч. {minutes} мин.'


def _read_files() -> str:
    # Only names and basic types, no reading secret files or symlink targets.
    items = sorted(ROOT.iterdir(), key=lambda p:p.name.lower())[:80]
    lines = [f'Папка приложения: {ROOT}']
    lines.extend(('DIR  ' if p.is_dir() and not p.is_symlink() else 'FILE ')+p.name for p in items)
    return '\n'.join(lines)


def _read_logs() -> str:
    from . import diagnostics
    return diagnostics.tail('site', limit=30, hours=24).strip()


def _read_status() -> str:
    # This is the running application's process, not a claim that other units are healthy.
    return f'Site Control API запущен\nPID: {os.getpid()}\nПользователь процесса (UID): {os.geteuid()}\nСостояние других служб проверяйте отдельно.'


def _system_overview() -> str:
    """Read-only diagnostics: no shell, no tokens/secret environment exposure."""
    cpus=os.cpu_count() or 1
    with open('/proc/loadavg',encoding='ascii') as f:
        load=f.read().split()[:3]
    try:
        with open('/proc/self/status',encoding='ascii') as f:
            stats={k.strip():v.strip() for line in f if ':' in line for k,v in [line.split(':',1)]}
    except OSError:stats={}
    net=[]
    try:
        for x in sorted(Path('/sys/class/net').iterdir()):
            if x.name=='lo':continue
            state=(x/'operstate').read_text().strip()
            net.append(f'{x.name}: {state}')
    except OSError:pass
    return '\n'.join([
        'СЕРВЕР / ОБЗОР',
        f'Хост: {platform.node()[:80]}',
        f'ОС: {platform.system()} {platform.release()[:60]}',
        f'CPU: {cpus} ядра / потока',
        f'Нагрузка 1 / 5 / 15 мин: {" / ".join(load)}',
        _read_uptime(),'',_read_memory(),'',_read_disk(),'',
        f'Python: {platform.python_version()}',
        f'Site Control PID: {os.getpid()}, UID: {os.geteuid()}',
        'Сеть: '+(', '.join(net) if net else 'нет сведений'),
    ])


def _app_memory_bytes():
    try:
        with open('/proc/self/status',encoding='ascii') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1])*1024
    except (OSError,IndexError,ValueError):pass
    return 0


def _ram_cleanup():
    """Collect Python objects in THIS app, never drop Linux page cache or other processes."""
    before=_app_memory_bytes()
    objects=gc.collect()
    # malloc_trim is an optional userspace allocator hint, never kernel cache clearing.
    try:
        import ctypes
        lib=ctypes.CDLL('libc.so.6')
        lib.malloc_trim(0)
    except (OSError,AttributeError,TypeError):pass
    after=_app_memory_bytes()
    return ('Очистка памяти приложения завершена.\n'
            f'Python-объектов собрано: {objects}\n'
            f'Память процесса до: {_size(before)}\n'
            f'Память процесса после: {_size(after)}\n'
            'Кеш ОС и процессы других сайтов не затронуты.')


NATIVE = {
    'uptime': _read_uptime,
    'disk': _read_disk,
    'memory': _read_memory,
    'site_status': _read_status,
    'site_logs': _read_logs,
    'files': _read_files,
    'overview': _system_overview,
    'ram_cleanup': _ram_cleanup,
}

# On a root service, even a read-only shell is not allowed. The following
# exactly matched strings map to native diagnostics and never invoke a shell.
SAFE_ROOT_COMMANDS = {
    'uptime': 'uptime',
    'df -h': 'disk',
    'free -m': 'memory',
    'free -h': 'memory',
    'site-control status': 'site_status',
    'site-control logs': 'site_logs',
    'site-control overview': 'overview',
}


def execute(template, command=None):
    if template not in (*PRESETS, 'manual'):
        raise ValueError('Неизвестная команда')
    if template != 'manual':
        try:
            output = NATIVE[template]()
            return {'ok': True, 'exit_code': 0, 'output': output or '(Данных пока нет)'}
        except (OSError, ValueError, KeyError) as exc:
            # Do not resort to subprocess credentials; a clear error is preferable.
            raise RuntimeError(f'Не удалось прочитать состояние VPS: {type(exc).__name__}') from exc
    if not isinstance(command, str) or not 1 <= len(command.strip()) <= 500 or '\x00' in command or '\n' in command:
        raise ValueError('Введите команду до 500 символов без перевода строки')
    command = command.strip()
    if os.geteuid() == 0:
        preset = SAFE_ROOT_COMMANDS.get(command)
        if preset:
            return execute(preset)
        raise ConsolePermissionError(
            'Ручные команды отключены: веб-служба работает с UID 0 (root). '
            'Чтобы выполнять произвольные команды, настройте отдельного '
            'непривилегированного пользователя для консоли. '
            'Проверка RAM, диска, нагрузки, файлов и журналов уже доступна кнопками.'
        )
    try:
        p = subprocess.run(
            ['/bin/sh', '-c', command],
            cwd='/tmp', capture_output=True, text=True, errors='replace',
            timeout=25, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8'},
        )
        return {'ok': p.returncode == 0, 'exit_code': p.returncode,
                'output': ((p.stdout or '') + (p.stderr or ''))[-100_000:] or '(Вывод пустой)'}
    except subprocess.TimeoutExpired:
        raise RuntimeError('Команда превысила лимит 25 секунд') from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Системная политика VPS запрещает запуск команд из веб-службы. '
                           'Проверьте ограничения systemd и используйте SSH для настройки консоли.') from exc

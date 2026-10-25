"""Site Control 1.7: admin Mini App, project management and remote VPS control."""
from __future__ import annotations
import asyncio
import hmac
import json
import logging
import traceback
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, WebAppInfo, BufferedInputFile, ErrorEvent, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeAllChatAdministrators, BotCommandScopeChat, MenuButtonCommands)
from fastapi import Request, HTTPException
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import uvicorn

from config import BOT_TOKEN
from admin_access import AdminRegistry
from sitecontrol import store, web, operations, diagnostics, remote, project_files, root_executor, local_console
from sitecontrol.maintenance import snapshot
from sitecontrol.telegram_bots import manager

diagnostics.configure()
log=logging.getLogger('sitecontrol.bot')
admin_id=382257126  # permanent bootstrap owner; legacy SVGTRACKER_ADMIN_ID must not override
registry=AdminRegistry(admin_id,Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin')))
bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()
app=web.app
web.wire(registry,manager,bot)
ROOT=Path(__file__).resolve().parent
DEPLOY=ROOT/'deploy'/'admin_deploy.py'
STATE=Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin'))
PENDING={}
ADMIN_INPUT={}  # actor ID -> deadline for admin-add flow
PROJECT_UPLOAD={}  # admin Telegram ID -> (project ID, expires)
ROOT_INPUT={}  # actor -> expiry for a confirmed, privileged own-VPS command


LAST_MENU={}  # chat_id -> message_id; keep one navigational bot message per chat

async def screen(message:Message,text:str,reply_markup=None):
    """Replace an existing bot screen when possible; never modify a user's message."""
    if getattr(message,'from_user',None) and message.from_user.is_bot:
        try:
            return await message.edit_text(text,reply_markup=reply_markup)
        except Exception:
            pass
    return await message.answer(text,reply_markup=reply_markup)

async def send_chunks(message:Message, text:str):
    for pos in range(0,len(text),3700):
        await message.answer(text[pos:pos+3700])


async def send_error(message:Message, label:str='Ошибка'):
    # Detailed exception goes into the private rotating log, never the chat.
    error_id=uuid.uuid4().hex[:8].upper()
    log.error('%s [событие %s]\n%s',label,error_id,traceback.format_exc())
    lower=traceback.format_exc().lower()
    hint=('Не удаётся запустить установщик через systemd. Нужна разовая проверка прав службы на VPS.'
          if 'systemd-run' in lower else 'Подробности доступны в «Логи» → «Telegram-бот».')
    await message.answer(f'{label}.\n{hint}\nКод: {error_id}',reply_markup=log_keyboard())


def bot_can(actor, capability, project_id=None):
    return registry.can(actor,capability,project_id) if hasattr(registry,'can') else registry.is_admin(actor)


def is_admin(msg:Message):
    return bool(msg.from_user and msg.chat.type=='private' and registry.is_admin(msg.from_user.id))


def worker(args:list[str],purpose:str):
    if not DEPLOY.is_file():raise RuntimeError('Установщик отсутствует')
    unit=f'sitecontrol-{purpose}-{int(time.time())}-{uuid.uuid4().hex[:7]}'
    py=ROOT/'venv/bin/python'
    cmd=['systemd-run','--unit',unit,'--collect','--property=Type=oneshot',str(py if py.is_file() else sys.executable),str(DEPLOY),*args]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=13)
    if p.returncode:
        raise RuntimeError('systemd-run failed ('+str(p.returncode)+'): '+((p.stderr or p.stdout or 'systemd unavailable').strip()[:1200]))
    return unit


def helper(subcommand):
    py=ROOT/'venv/bin/python'
    p=subprocess.run([str(py if py.is_file() else sys.executable),str(DEPLOY),subcommand],capture_output=True,text=True,timeout=55)
    if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:850])
    return (p.stdout or '')[:4000]


def miniapp_url(admin_user_id=None):
    base=web.SITE_URL+'/?mode=miniapp'
    if admin_user_id and registry.is_admin(admin_user_id):
        return base+'&ticket='+web.issue_mini_launch(admin_user_id)
    return base

def keyboard(admin_user_id=None):
    """The only visible controls: Mini App, status, logs, ZIP, backup."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть Mini App',web_app=WebAppInfo(url=miniapp_url(admin_user_id)))],
        [InlineKeyboardButton(text='Проекты',callback_data='sc:sites'),
         InlineKeyboardButton(text='Серверы',callback_data='sc:servers')],
        [InlineKeyboardButton(text='Состояние',callback_data='sc:status'),
         InlineKeyboardButton(text='Логи',callback_data='sc:logs')],
        [InlineKeyboardButton(text='Наш VPS · управление',callback_data='sc:ourvps')],
        [InlineKeyboardButton(text='Администраторы',callback_data='sc:admins')],
        [InlineKeyboardButton(text='Обновить ZIP',callback_data='sc:upload'),
         InlineKeyboardButton(text='Резервная копия',callback_data='sc:backup')],
    ])


async def send_status(message:Message):
    active=await asyncio.to_thread(subprocess.run,['systemctl','is-active','svgtracker.service'],capture_output=True,text=True,timeout=5)
    ok=active.returncode==0 and active.stdout.strip()=='active'
    output=await asyncio.to_thread(operations.status_text, STATE/'deploy_status.json',service_ok=ok,api_ok=(await asyncio.to_thread(operations.probe_api)))
    if len(output)<=3700: await screen(message,output)
    else: await send_chunks(message,output)
    # Exactly one attachment: the current user guide. No status dump in a file.
    guide=ROOT/'ИНСТРУКЦИЯ.txt'
    if guide.is_file():
        await message.answer_document(BufferedInputFile(guide.read_bytes(),filename='Site_Control_Инструкция.txt'),caption='Актуальная инструкция Site Control')


def log_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Telegram-бот',callback_data='sc:log:bot'),
         InlineKeyboardButton(text='Сайт / API',callback_data='sc:log:site')],
        [InlineKeyboardButton(text='Назад',callback_data='sc:home')]
    ])


async def send_log(message:Message,kind:str,viewer_id:int|None=None):
    content=await asyncio.to_thread(diagnostics.tail,kind,30,24)
    label='бота' if kind=='bot' else 'сайта и API'
    if viewer_id is None and message.from_user and not message.from_user.is_bot:viewer_id=message.from_user.id
    timezone_name=store.get_admin_timezone(viewer_id) if viewer_id else 'UTC'
    content=diagnostics.format_for_timezone(content,timezone_name)
    await message.answer_document(BufferedInputFile(content.encode('utf-8'),filename='Site_Control_'+kind+'_log.txt'),
        caption=f'Журнал {label} · последние 24 часа, до 30 событий · время {timezone_name}.')


@dp.message(Command('start'))
async def welcome(message:Message):
    if not is_admin(message):
        await message.answer('Site Control. Доступ разрешён только администраторам.')
        return
    arg=(message.text or '').partition(' ')[2].strip()
    if arg.startswith('login_') and 6 <= len(arg) <= 110:
        approved=store.approve_login(arg[6:],message.from_user.id)
        if approved:log.info('Администратор вошёл через Telegram')
        await message.answer('Вход подтверждён. Вернитесь на сайт.' if approved else 'Код входа неверен или устарел.')
        return
    chat_id=message.chat.id
    last=LAST_MENU.get(chat_id)
    content='Site Control\n\nПроекты · Серверы · Обновления'
    if last:
        try:
            await bot.edit_message_text(content,chat_id,last,reply_markup=keyboard(message.from_user.id))
            return
        except Exception:pass
    sent=await message.answer(content,reply_markup=keyboard(message.from_user.id))
    LAST_MENU[chat_id]=sent.message_id


@dp.message(Command('logs'))
async def logs(message:Message):
    if is_admin(message):await message.answer('Какой журнал показать?',reply_markup=log_keyboard())



def site_keyboard():
    rows=[[InlineKeyboardButton(text='◆ Наша платформа',callback_data='sc:site:0')]]
    for r in store.list_sites()[:20]:
        rows.append([InlineKeyboardButton(text=str(r['client_name'])[:36],callback_data=f'sc:site:{r["id"]}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_sites(message:Message):
    await screen(message,'Проекты Site Control. Выберите продукт:',reply_markup=site_keyboard())


@dp.message(Command('sites'))
async def sites_command(message:Message):
    if is_admin(message):await show_sites(message)


def server_keyboard():
    rows=[]
    for r in store.list_servers()[:20]:
        rows.append([InlineKeyboardButton(text=f'{r["name"]} · {r["host"]}',callback_data=f'sc:server:{r["id"]}')])
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text='Подключение — инструкция',callback_data='sc:serverhelp')]])


@dp.message(Command('servers'))
async def servers_command(message:Message):
    if is_admin(message):await message.answer('Серверы: выберите подключение. Для настройки используйте веб-панель.',reply_markup=server_keyboard())


@dp.message(Command('server'))
async def server_command(message:Message):
    if not is_admin(message):return
    parts=(message.text or '').split(maxsplit=2)
    if len(parts)!=3 or not parts[1].isdigit() or parts[2] not in remote.TEMPLATES:
        await message.answer('Команда: /server ID uptime\nШаблоны: '+', '.join(remote.TEMPLATES));return
    await prepare_remote(message,int(parts[1]),parts[2])


@dp.message(Command('exec'))
async def exec_command(message:Message):
    if not is_admin(message):return
    parts=(message.text or '').split(maxsplit=2)
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer('Ручная SSH-команда: /exec ID команда. Перед выполнением потребуется подтверждение.');return
    await prepare_remote(message,int(parts[1]),'manual',parts[2])


async def prepare_remote(message,server_id,template,command=None):
    server=store.get_server(server_id)
    if not server:
        await message.answer('Сервер не найден.');return
    try: text=remote.build_command(server,template,command)
    except ValueError as exc:await message.answer(str(exc));return
    token=uuid.uuid4().hex[:12]
    PENDING[token]=(message.chat.id, (server_id,template,command), 'remote',time.time()+300)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Выполнить SSH-команду',callback_data='sc:run:'+token)],[InlineKeyboardButton(text='Отменить',callback_data='sc:cancel')]])
    await message.answer(f'Сервер: {server["name"]}\nКоманда:\n{text[:1300]}\n\nВыполнить?',reply_markup=kb)


@dp.errors()
async def catch_errors(event:ErrorEvent):
    log.error('Ошибка обработки Telegram update',exc_info=(type(event.exception),event.exception,event.exception.__traceback__))
    return True


@dp.message(Command('login'))
async def login(message:Message):
    if not is_admin(message):return
    code=(message.text or '').partition(' ')[2].strip()
    if not 8<=len(code)<=100:
        await message.answer('Сначала откройте сайт Site Control и нажмите «Войти через Telegram». Затем отправьте /login КОД.')
        return
    approved=store.approve_login(code,message.from_user.id)
    await message.answer('Вход подтверждён. Вернитесь на сайт.' if approved else 'Код неверный или устарел. Создайте новый код на сайте.')


def admin_keyboard():
    admins=registry.list_ids()
    rows=[[InlineKeyboardButton(text=f'Удалить {uid}',callback_data=f'sc:admrm:{uid}')]
          for uid in admins if uid!=registry.owner_id]
    rows.extend([[InlineKeyboardButton(text='Добавить администратора',callback_data='sc:admadd')],
                 [InlineKeyboardButton(text='Назад',callback_data='sc:home')]])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_admins(message:Message):
    ids=registry.list_ids()
    text='Администраторы Site Control\n\n'+'\n'.join(
        f'• {i}'+(' · владелец' if i==registry.owner_id else '') for i in ids)
    await screen(message,text,reply_markup=admin_keyboard())

@dp.message(Command('admins'))
async def admins(message:Message):
    if is_admin(message) and bot_can(message.from_user.id,'admins.manage'):await show_admins(message)


@dp.message(Command('admin_add','admin_remove'))
async def admin_change(message:Message):
    if not is_admin(message) or not bot_can(message.from_user.id,'admins.manage'):return
    text=(message.text or '').strip().split()
    if len(text)!=2 or not text[1].isascii() or not text[1].isdecimal():
        await message.answer('Пример: /admin_add 123456789');return
    action='grant' if text[0].split('@')[0]=='/admin_add' else 'revoke'
    # Two-step confirmation prevents accidentally granting server rights.
    code=uuid.uuid4().hex[:11]
    PENDING[code]=(message.from_user.id,int(text[1]),action,time.time()+600)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Подтвердить изменение',callback_data='sc:role:'+code)],[InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
    await message.answer(f'Действие: {action}; Telegram ID: {text[1]}. Подтвердите изменение:',reply_markup=kb)


@dp.message(Command('status'))
async def server_status(message:Message):
    if not is_admin(message):return
    try:await send_status(message)
    except Exception:
        log.exception('Ошибка формирования отчёта о состоянии')
        await send_error(message,'Ошибка проверки состояния')


@dp.message(Command('backup'))
async def backup(message:Message):
    if not is_admin(message):return
    try:
        new_file=await asyncio.to_thread(snapshot)
        # Retain rollback compatibility with the historical V48 installer.
        await asyncio.to_thread(helper,'backup')
        await message.answer('Резервная копия создана\n'+operations.pretty_time(time.time())+
            '\nSite Control: '+new_file.name+'\n\nКопии хранятся на VPS. Для защиты от потери сервера нужна отдельная внешняя копия.')
    except Exception:
        log.exception('Backup failed')
        await send_error(message,'Ошибка резервного копирования')


@dp.message(Command('cleanup_legacy'))
async def ask_cleanup(message:Message):
    if not is_admin(message):return
    token=uuid.uuid4().hex[:12]
    PENDING[token]=(message.from_user.id,'old-data','cleanup',time.time()+600)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Подтверждаю очистку старого трекера',callback_data='sc:cleanup:'+token)],
        [InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]
    ])
    await message.answer('Удалить остатки SVGTracker (старые исходники, тесты, отдельную старую SQLite)?\n'
        'Рабочая база Site Control и токены клиентских ботов сохраняются.\n'
        'Перед удалением система создаст отдельную проверенную резервную копию старых данных. '
        'Обычные старые архивы на VPS автоматически не удаляются.',reply_markup=kb)


@dp.message(Command('rollback'))
async def rollback_info(message:Message):
    if not is_admin(message):return
    backups=sorted(Path(os.environ.get('SVGTRACKER_BACKUP_DIR','/var/backups/svgtracker')).glob('*.tar.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not backups:
        await message.answer('Резервные копии для отката не найдены.');return
    token=uuid.uuid4().hex[:12]
    PENDING[token]=(message.from_user.id,str(backups[0]),'rollback',time.time()+600)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Откатить приложение',callback_data='sc:rollback:'+token)],[InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
    await message.answer('Откатить код на '+backups[0].name+'? Это восстановит прежний интерфейс и перезапустит приложение. База Site Control сохранится отдельно.',reply_markup=kb)


@dp.message(F.text)
async def admin_id_text(message:Message):
    if is_admin(message) and message.from_user.id in ROOT_INPUT:
        expires=ROOT_INPUT.pop(message.from_user.id)
        if expires<time.time():
            await message.answer('Время ввода истекло. Откройте «Наш VPS» заново.');return
        if not bot_can(message.from_user.id,'platform.root',0):
            await message.answer('ROOT-доступ не разрешён для этого администратора.');return
        cmd=(message.text or '').strip()
        if not 1<=len(cmd)<=1200 or '\n' in cmd or any(ord(c)<32 and c!='\t' for c in cmd):
            await message.answer('Допускается только одна строка до 1200 символов.');return
        code=uuid.uuid4().hex[:12]
        PENDING[code]=(message.from_user.id,cmd,'ownroot',time.time()+180)
        await message.answer('Наш VPS / root\nКоманда: '+cmd[:1200]+'\n\nВыполнить с полными системными правами?',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text='Подтверждаю ROOT',callback_data='sc:rootrun:'+code)],
          [InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]]))
        return
    if not is_admin(message) or not bot_can(message.from_user.id,'admins.manage'):return
    expires=ADMIN_INPUT.get(message.from_user.id)
    if not expires:return
    ADMIN_INPUT.pop(message.from_user.id,None)
    if expires<time.time():
        await message.answer('Время добавления истекло. Откройте «Администраторы» снова.')
        return
    try:
        from admin_access import _id
        target=_id((message.text or '').strip())
        if target==registry.owner_id:raise ValueError('Этот пользователь уже главный администратор')
    except (ValueError,TypeError) as exc:
        await message.answer('Неверный Telegram ID. Отправьте только цифры (например, 123456789).')
        return
    code=uuid.uuid4().hex[:11]
    PENDING[code]=(message.from_user.id,target,'grant',time.time()+300)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'Добавить {target}',callback_data='sc:role:'+code)],
        [InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
    await message.answer(f'Предоставить полный доступ администратору {target}?',reply_markup=kb)

@dp.message(F.document)
async def upload_zip(message:Message):
    if not is_admin(message) or not message.document:return
    if not bot_can(message.from_user.id,'files.manage', PROJECT_UPLOAD.get(message.from_user.id,(0,))[0] if message.from_user.id in PROJECT_UPLOAD else 0):return
    name=str(message.document.file_name or '')
    pending_project=PROJECT_UPLOAD.get(message.from_user.id)
    if pending_project and pending_project[1] >= time.time():
        project_id=pending_project[0];PROJECT_UPLOAD.pop(message.from_user.id,None)
        if not 0<int(message.document.file_size or 0)<=project_files.MAX_UPLOAD:
            await message.answer('Файл должен быть не больше 30 МБ.');return
        try:
            from io import BytesIO
            buffer=BytesIO()
            await bot.download(message.document,destination=buffer)
            release=await asyncio.to_thread(project_files.stage,project_id,buffer.getvalue(),name)
            servers=([None] if project_id==0 else [r for r in store.list_servers() if r['site_id']==project_id])
            if not servers:
                await message.answer('Файлы проверены, но VPS проекта пока не подключён. Подключите его через Mini App.');return
            choices=[]
            for srv in servers[:12]:
                sid=srv['id'] if srv else 0
                token=uuid.uuid4().hex[:10]
                PENDING[token]=(message.from_user.id,(project_id,release['id'],sid),'projectrelease',time.time()+900)
                choices.append([InlineKeyboardButton(text='Опубликовать: '+(srv['name'] if srv else 'Наша платформа'),callback_data='sc:pub:'+token)])
            await message.answer('Файл проверен и подготовлен. Выберите сервер для публикации:',reply_markup=InlineKeyboardMarkup(inline_keyboard=choices))
        except Exception:
            await send_error(message,'Не удалось подготовить файлы проекта')
        return
    if not name.lower().endswith('.zip'):return
    size=int(message.document.file_size or 0)
    if not 0<size<=20*1024*1024:
        await message.answer('ZIP не должен превышать 20 МБ.');return
    incoming=STATE/'incoming'; incoming.mkdir(parents=True,exist_ok=True)
    incoming.chmod(0o700)
    token=uuid.uuid4().hex[:12];path=incoming/f'{token}.zip'
    try:await bot.download(message.document,destination=path)
    except Exception as exc:await send_error(message,'Ошибка загрузки ZIP');return
    PENDING[token]=(message.from_user.id,str(path),'zip',time.time()+1800)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Установить ZIP',callback_data='sc:deploy:'+token)],[InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
    await message.answer(f'Архив: {name} · {size//1024} КБ\nУстановить обновление?',reply_markup=kb)


@dp.callback_query(F.data.startswith('sc:'))
async def callbacks(callback:CallbackQuery):
    if not callback.from_user or not registry.is_admin(callback.from_user.id) or not callback.message or callback.message.chat.type!='private':
        await callback.answer('Нет доступа',show_alert=True);return
    parts=(callback.data or '').split(':')
    action=parts[1] if len(parts)>1 else ''
    actor=callback.from_user.id
    permissions={'admins':'admins.manage','admadd':'admins.manage','admrm':'admins.manage','role':'admins.manage',
                 'logs':'logs.view','log':'logs.view','status':'projects.view','backup':'backups.manage',
                 'sites':'projects.view','servers':'servers.view','serverhelp':'servers.view','ourvps':'platform.manage','rootstate':'platform.manage',
                 'upload':'platform.manage','cleanup':'platform.manage','rollback':'platform.manage','deploy':'platform.manage'}
    if action in permissions and not bot_can(actor,permissions[action]):
        await callback.answer('Недостаточно прав',show_alert=True);return
    pid=None
    if action in ('site','pfile','pbacks','bdown') and len(parts)>2 and parts[2].isdigit():pid=int(parts[2])
    if action in ('server','tmpl') and len(parts)>2 and parts[2].isdigit():
        srv=store.get_server(int(parts[2]));pid=srv['site_id'] if srv else -1
    scoped={'site':'projects.view','pfile':'files.manage','pbacks':'backups.manage',
            'bdown':'backups.manage','server':'servers.view','tmpl':'servers.manage'}
    if action in scoped and (pid is None or not bot_can(actor,scoped[action],pid)):
        await callback.answer('Нет доступа к этому проекту',show_alert=True);return
    if action=='tmpl' and len(parts)>2 and parts[2].isdigit():
        server=store.get_server(int(parts[2]))
        if server and server.get('username')=='root' and not bot_can(actor,'servers.root',pid):
            await callback.answer('Нет права root для VPS',show_alert=True);return

    if action=='cancel':
        await callback.answer('Отменено');await callback.message.edit_text('Действие отменено.');return
    if action=='home':
        await callback.answer()
        await screen(callback.message,'Site Control',reply_markup=keyboard(callback.from_user.id))
        return
    if action=='upload':
        await callback.answer()
        await screen(callback.message,'Отправьте ZIP документом в этот чат.')
        return
    if action=='logs':
        await callback.answer()
        await screen(callback.message,'Выберите журнал:',reply_markup=log_keyboard())
        return
    if action=='log' and len(parts)==3 and parts[2] in diagnostics.KINDS:
        await callback.answer()
        try:await send_log(callback.message,parts[2],callback.from_user.id)
        except Exception:
            log.exception('Не удалось отправить журнал %s',parts[2])
            await send_error(callback.message,'Ошибка чтения журнала')
        return
    if action in ('status','backup'):
        await callback.answer()
        if action=='status':
            try:await send_status(callback.message)
            except Exception:
                log.exception('Ошибка состояния')
                await send_error(callback.message,'Ошибка состояния')
        else:
            try:
                path=await asyncio.to_thread(snapshot)
                await asyncio.to_thread(helper,'backup')
                log.info('Создана резервная копия Site Control')
                await screen(callback.message,'Резервная копия создана · '+operations.pretty_time(time.time())+'\nСохраните копию отдельно от VPS.')
            except Exception:
                log.exception('Ошибка создания резервной копии')
                await send_error(callback.message,'Ошибка резервного копирования')
        return
    if action=='admins':
        await callback.answer();await show_admins(callback.message);return
    if action=='admadd':
        ADMIN_INPUT[callback.from_user.id]=time.time()+180
        await callback.answer()
        await screen(callback.message,'Отправьте Telegram ID нового администратора одним числом.\nНапример: 123456789\nДалее потребуется подтверждение.')
        return
    if action=='admrm' and len(parts)==3:
        try:
            from admin_access import _id
            uid=_id(parts[2])
            if uid==registry.owner_id:raise ValueError('Владельца удалить нельзя')
            if uid==callback.from_user.id:raise ValueError('Собственные права отозвать нельзя')
            if not registry.is_admin(uid):raise ValueError('Этот ID не является администратором')
        except (ValueError,TypeError) as exc:
            await callback.answer(str(exc)[:150],show_alert=True);return
        token=uuid.uuid4().hex[:11]
        PENDING[token]=(callback.from_user.id,uid,'revoke',time.time()+300)
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f'Удалить {uid}',callback_data='sc:role:'+token)],
            [InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
        await callback.answer()
        await screen(callback.message,f'Отозвать доступ администратора {uid}?',reply_markup=kb)
        return
    if action=='ourvps':
        await callback.answer()
        ready=await asyncio.to_thread(root_executor.status)
        has_root=bot_can(actor,'platform.root',0)
        rows=[[InlineKeyboardButton(text='Открыть консоль Site Control',web_app=WebAppInfo(url=miniapp_url(actor)))],
              [InlineKeyboardButton(text='Проверить ROOT',callback_data='sc:rootstate')],
              [InlineKeyboardButton(text='Root-команда',callback_data='sc:rootinput')],
              [InlineKeyboardButton(text='Назад',callback_data='sc:home')]]
        await screen(callback.message,'НАШ VPS / SITE CONTROL\n\n'
                     +'Root-служба: '+('доступна' if ready else 'не включена')+'\n'
                     +'Ваш доступ: '+('ROOT разрешён' if has_root else 'без ROOT')+'\n'
                     +'Полная консоль и диагностика — по кнопке ниже.',
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if action=='rootinput':
        if not bot_can(actor,'platform.root',0):
            await callback.answer('Недостаточно прав ROOT',show_alert=True);return
        if not await asyncio.to_thread(root_executor.status):
            await callback.answer('Root-служба выключена',show_alert=True)
            await screen(callback.message,'Сначала включите службу через консоль провайдера.\n\n'+web.root_setup_command()+'\n\nСкопируйте команду. После включения повторите действие.')
            return
        ROOT_INPUT[actor]=time.time()+180
        await callback.answer()
        await screen(callback.message,'Отправьте одну команду для выполнения на НАШЕМ VPS.\nДо 1200 символов, 3 минуты. Затем потребуется дополнительное подтверждение. Не вводите пароли и секреты.')
        return
    if action=='rootstate':
        await callback.answer()
        ready=await asyncio.to_thread(root_executor.status)
        permitted=bot_can(actor,'platform.root',0)
        text=('Root-служба работает. Можно выполнять команды из Mini App и бота.' if ready and permitted
              else 'Первое включение root (из консоли VPS-провайдера или SSH на телефоне):\n'+web.root_setup_command()+'\nПосле этого нажмите «Проверить ROOT».' if permitted
              else 'У вас не выдано разрешение ROOT. Владелец может включить его в «Администраторы».')
        await screen(callback.message,text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[
          [InlineKeyboardButton(text='Открыть Mini App',web_app=WebAppInfo(url=miniapp_url(actor)))],
          [InlineKeyboardButton(text='Назад',callback_data='sc:ourvps')]]))
        return
    if action=='sites':
        await callback.answer();await show_sites(callback.message);return
    if action=='servers':
        await callback.answer();await screen(callback.message,'Подключённые серверы:',reply_markup=server_keyboard());return
    if action=='serverhelp':
        await callback.answer();await screen(callback.message,'Создание подключений к VPS — в Site Control → Серверы.');return
    if action=='site' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        pid=int(parts[2]);site=store.get_site(pid) if pid else None
        if pid and not site:await screen(callback.message,'Проект не найден');return
        title=site['client_name'] if site else 'Наша платформа'
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Загрузить ZIP или файл',callback_data=f'sc:pfile:{pid}')],
            [InlineKeyboardButton(text='Резервные копии',callback_data=f'sc:pbacks:{pid}')],
            [InlineKeyboardButton(text='Открыть Mini App',web_app=WebAppInfo(url=miniapp_url(callback.from_user.id)))],
            [InlineKeyboardButton(text='Назад к проектам',callback_data='sc:sites')]])
        await screen(callback.message,title+' · проект',reply_markup=kb)
        return
    if action=='pfile' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        PROJECT_UPLOAD[callback.from_user.id]=(int(parts[2]),time.time()+900)
        await screen(callback.message,'Отправьте отдельный файл или ZIP документом. После проверки выберете целевой VPS. Подтверждение действует 15 минут.')
        return
    if action=='pbacks' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        pid=int(parts[2]);backups=project_files.list_backups(pid)[:8]
        if not backups:await screen(callback.message,'Для этого проекта бэкапов нет. Создать копию можно в Mini App.');return
        rows=[]
        for b in backups:
            rows.append([InlineKeyboardButton(text=f'↓ {b["id"][:8]} · {b["server_name"][:23]}',callback_data=f'sc:bdown:{pid}:{b["id"]}')])
        rows.append([InlineKeyboardButton(text='Назад',callback_data=f'sc:site:{pid}')])
        await screen(callback.message,'Резервные копии проекта. Выберите архив:',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if action=='bdown' and len(parts)==4 and parts[2].isdigit():
        await callback.answer()
        pid=int(parts[2]);bid=parts[3]
        try:
            _,archive=project_files.lookup_backup(pid,bid)
            if archive.stat().st_size>45*1024*1024:
                await screen(callback.message,'Архив крупный, скачайте его в Mini App.');return
            from aiogram.types import FSInputFile
            await callback.message.answer_document(FSInputFile(archive,filename=f'project-{pid}-{bid}.zip'))
        except Exception:await send_error(callback.message,'Не удалось скачать бэкап')
        return
    if action=='server' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        sid=int(parts[2]);server=store.get_server(sid)
        if not server:await screen(callback.message,'Сервер не найден');return
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Нагрузка',callback_data=f'sc:tmpl:{sid}:uptime'),InlineKeyboardButton(text='Диск',callback_data=f'sc:tmpl:{sid}:disk')],
            [InlineKeyboardButton(text='Память',callback_data=f'sc:tmpl:{sid}:memory'),InlineKeyboardButton(text='Служба',callback_data=f'sc:tmpl:{sid}:site_status')],
            [InlineKeyboardButton(text='Логи',callback_data=f'sc:tmpl:{sid}:site_logs'),InlineKeyboardButton(text='Перезапуск',callback_data=f'sc:tmpl:{sid}:site_restart')]])
        await screen(callback.message,f'{server["name"]} · {server["host"]}',reply_markup=kb);return
    if action=='tmpl' and len(parts)==4 and parts[2].isdigit():
        await callback.answer();await prepare_remote(callback.message,int(parts[2]),parts[3]);return
    if len(parts)!=3:await callback.answer('Неверный запрос',show_alert=True);return
    token=parts[2]
    pending=PENDING.get(token)
    if not pending or pending[0]!=callback.from_user.id or pending[-1]<time.time():
        await callback.answer('Подтверждение устарело',show_alert=True);return
    if action=='pub' and pending[2]=='projectrelease' and not bot_can(actor,'files.manage',pending[1][0]):
        await callback.answer('Недостаточно прав',show_alert=True);return
    if action=='rootrun' and pending[2]=='ownroot':
        if not bot_can(actor,'platform.root',0):
            await callback.answer('Недостаточно прав ROOT',show_alert=True);return
        PENDING.pop(token,None)
        await callback.answer('Выполняется')
        try:
            result=await asyncio.to_thread(root_executor.execute,pending[1],actor)
            output=f'НАШ VPS / ROOT\nКод: {result["exit_code"]}\n'+result['output']
            await screen(callback.message,output[:3800],reply_markup=InlineKeyboardMarkup(inline_keyboard=[
              [InlineKeyboardButton(text='Наш VPS',callback_data='sc:ourvps')]]))
        except Exception:
            log.exception('Ошибка подтверждённой root-команды бота')
            await send_error(callback.message,'Root-команда не выполнена')
        return
    if action=='pub' and pending[2]=='projectrelease':
        PENDING.pop(token,None)
        pid,rid,sid=pending[1]
        await callback.answer('Публикация')
        await callback.message.edit_text('Создаётся резервная копия и обновляются файлы…')
        try:
            result=await asyncio.to_thread(project_files.deploy,pid,rid,sid or None)
            await screen(callback.message,'Файлы опубликованы. Бэкап: '+result['backup']['id'][:8]+'. Изменения Python могут потребовать перезапуска службы.')
        except Exception:await send_error(callback.message,'Ошибка публикации файлов')
        return
    if action=='run' and pending[2]=='remote':
        srv=store.get_server(pending[1][0]);pid=srv['site_id'] if srv else -1
        if not bot_can(actor,'servers.manage',pid) or (srv and srv.get('username')=='root' and not bot_can(actor,'servers.root',pid)):
            await callback.answer('Недостаточно прав SSH',show_alert=True);return
        PENDING.pop(token,None)
        sid,template,command=pending[1]
        server=store.get_server(sid)
        if not server:await callback.answer('Сервер не найден',show_alert=True);return
        await callback.answer('Запущено')
        await callback.message.edit_text('SSH-команда выполняется…')
        try:
            result=await asyncio.to_thread(remote.execute,server,template,command)
            store.log_server_run(sid,'admin:'+str(callback.from_user.id),template,result['ok'])
            await send_chunks(callback.message,f'{server["name"]} · код {result["exit_code"]}\n\n'+result['output']+('\n\nВывод сокращён.' if result['truncated'] else ''))
        except Exception:
            log.exception('Сбой SSH-управления %s',sid)
            store.log_server_run(sid,'admin:'+str(callback.from_user.id),template,False)
            await send_error(callback.message,'Ошибка SSH-команды')
        return
    if action=='role':
        if not bot_can(actor,'admins.manage'):
            await callback.answer('Недостаточно прав',show_alert=True);return
        PENDING.pop(token,None)
        _,uid,role,_=pending
        try:changed=registry.change(callback.from_user.id,uid,role)
        except Exception as exc:await callback.answer('Не выполнено',show_alert=True);await screen(callback.message,str(exc)[:600]);return
        await callback.answer();await callback.message.edit_text(('Администратор добавлен.' if role=='grant' else 'Доступ отозван.') if changed else 'Изменений нет.')
        await show_admins(callback.message)
        return
    if action=='cleanup' and pending[2]=='cleanup':
        try:await asyncio.to_thread(worker,['cleanup-legacy','--admin-chat',str(callback.from_user.id)],'cleanup')
        except Exception:
            log.exception('Ошибка запуска очистки старых файлов')
            await callback.answer('Ошибка запуска',show_alert=True)
            await send_error(callback.message,'Ошибка очистки')
            return
        PENDING.pop(token,None)
        await callback.answer('Очистка запущена')
        await callback.message.edit_text('Очистка запущена. Результат поступит в этот чат.')
        return
    if action=='rollback' and pending[2]=='rollback':
        _,path,_,_=pending
        try:unit=await asyncio.to_thread(worker,['rollback',path,'--admin-chat',str(callback.from_user.id)],'rollback')
        except Exception as exc:
            await callback.answer('Ошибка запуска',show_alert=True)
            await send_error(callback.message,'Ошибка запуска');return
        PENDING.pop(token,None)
        await callback.answer('Откат запущен')
        await callback.message.edit_text('Откат запущен. Результат придёт сюда.')
        return
    if action=='deploy' and pending[2]=='zip':
        _,path,_,_=pending
        try:unit=await asyncio.to_thread(worker,['deploy',path,'--admin-chat',str(callback.from_user.id)],'deploy')
        except Exception as exc:
            await callback.answer('Ошибка запуска',show_alert=True)
            await send_error(callback.message,'Ошибка запуска');return
        PENDING.pop(token,None)
        await callback.answer('Установка запущена')
        log.info('Запущено ZIP-обновление')
        await callback.message.edit_text('Установка началась. Результат придёт сюда.')
        return
    await callback.answer('Неизвестная команда',show_alert=True)


@app.post('/api/sc/hooks/{hook_key}')
async def client_webhook(hook_key:str,request:Request):
    if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',hook_key):raise HTTPException(404)
    row=store.get_bot_by_key(hook_key)
    if not row:raise HTTPException(404)
    supplied=request.headers.get('X-Telegram-Bot-Api-Secret-Token','')
    if not hmac.compare_digest(supplied,row['hook_secret']):raise HTTPException(403,'Forbidden')
    if int(request.headers.get('content-length') or 0)>1024*1024:raise HTTPException(413)
    body=await request.body()
    if len(body)>1024*1024:raise HTTPException(413)
    try:payload=json.loads(body)
    except (ValueError,TypeError):raise HTTPException(400,'Invalid JSON')
    if not isinstance(payload,dict):raise HTTPException(400,'Invalid JSON')
    try:
        with store.connect() as db:
            site=db.execute('SELECT id FROM sites WHERE tenant_id=? LIMIT 1',(row['tenant_id'],)).fetchone()
        if site:diagnostics.project_event(site['id'],'bot','Получено событие клиентского Telegram-бота')
        await manager.receive(row,payload)
    except Exception:
        log.exception('Ошибка webhook клиентского бота')
        if site:diagnostics.project_exception(site['id'],'bot','Ошибка Telegram webhook',traceback.format_exc())
        raise HTTPException(503,'Temporary processing failure')
    return {'ok':True}


async def refresh_command_menu():
    # Telegram stores different lists for languages/scopes; clearing only DEFAULT
    # leaves stale SVGTracker commands in the iOS menu for admins.
    scopes=[BotCommandScopeDefault(),BotCommandScopeAllPrivateChats(),
            BotCommandScopeAllGroupChats(),BotCommandScopeAllChatAdministrators()]
    for uid in registry.list_ids():
        scopes.append(BotCommandScopeChat(chat_id=uid))
    failures=0
    for scope in scopes:
        for language in (None,'ru','en'):
            try:
                await bot.delete_my_commands(scope=scope,language_code=language)
            except Exception:
                failures+=1
    if failures:
        log.warning('Не все прежние наборы Telegram-команд очищены: %s',failures)
    commands=[BotCommand(command='start',description='Открыть панель'),
              BotCommand(command='status',description='Состояние'),
              BotCommand(command='logs',description='Логи')]
    await bot.set_my_commands(commands,scope=BotCommandScopeDefault())
    await bot.set_my_commands(commands,scope=BotCommandScopeAllPrivateChats())
    for uid in registry.list_ids():
        try:await bot.set_my_commands(commands,scope=BotCommandScopeChat(chat_id=uid))
        except Exception:log.warning('Команды не установлены для отдельного чата')
    try:await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:log.exception('Не удалось включить командное меню Telegram')
    log.info('Меню Telegram обновлено: 3 команды')


async def main():
    store.init()
    config=uvicorn.Config(app,host=os.environ.get('SVGTRACKER_BIND_HOST','127.0.0.1'),port=8000,server_header=False,log_level='warning',access_log=False)
    server=uvicorn.Server(config)
    await refresh_command_menu()

    service=asyncio.create_task(server.serve(),name='api')
    polling=asyncio.create_task(dp.start_polling(bot,handle_signals=False),name='admin-bot')
    done,pending=await asyncio.wait({service,polling},return_when=asyncio.FIRST_COMPLETED)
    for task in pending:task.cancel()
    await asyncio.gather(*pending,return_exceptions=True)
    await manager.close()
    await bot.session.close()
    for task in done:
        if not task.cancelled() and task.exception():raise task.exception()
    raise RuntimeError('Site Control service stopped unexpectedly')


if __name__=='__main__':
    asyncio.run(main())

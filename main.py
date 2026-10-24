"""Site Control 1.3: admin Mini App, project management and remote VPS control."""
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
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, WebAppInfo, BufferedInputFile, ErrorEvent
from fastapi import Request, HTTPException
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import uvicorn

from config import BOT_TOKEN
from admin_access import AdminRegistry
from sitecontrol import store, web, operations, diagnostics, remote
from sitecontrol.maintenance import snapshot
from sitecontrol.telegram_bots import manager

diagnostics.configure()
log=logging.getLogger('sitecontrol.bot')
admin_id=int(os.environ.get('SVGTRACKER_ADMIN_ID','382257126'))
registry=AdminRegistry(admin_id,Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin')))
bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()
app=web.app
web.wire(registry,manager,bot)
ROOT=Path(__file__).resolve().parent
DEPLOY=ROOT/'deploy'/'admin_deploy.py'
STATE=Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin'))
PENDING={}


async def send_chunks(message:Message, text:str):
    for pos in range(0,len(text),3700):
        await message.answer(text[pos:pos+3700])


async def send_error(message:Message, label:str='Ошибка'):
    # Send complete traceback in chunks; Telegram messages have a size limit.
    report=label+'\n'+traceback.format_exc()
    await send_chunks(message,report)


def is_admin(msg:Message):
    return bool(msg.from_user and msg.chat.type=='private' and registry.is_admin(msg.from_user.id))


def worker(args:list[str],purpose:str):
    if not DEPLOY.is_file():raise RuntimeError('Установщик отсутствует')
    unit=f'sitecontrol-{purpose}-{int(time.time())}-{uuid.uuid4().hex[:7]}'
    py=ROOT/'venv/bin/python'
    cmd=['systemd-run','--unit',unit,'--collect','--property=Type=oneshot',str(py if py.is_file() else sys.executable),str(DEPLOY),*args]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=13)
    if p.returncode:
        raise RuntimeError(p.stderr or p.stdout or 'systemd-run failed')
    return unit


def helper(subcommand):
    py=ROOT/'venv/bin/python'
    p=subprocess.run([str(py if py.is_file() else sys.executable),str(DEPLOY),subcommand],capture_output=True,text=True,timeout=55)
    if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:850])
    return (p.stdout or '')[:4000]


def miniapp_url():
    return web.SITE_URL+'/?mode=miniapp'

def keyboard():
    """The only visible controls: Mini App, status, logs, ZIP, backup."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть Mini App',web_app=WebAppInfo(url=miniapp_url()))],
        [InlineKeyboardButton(text='Мои сайты',callback_data='sc:sites'),
         InlineKeyboardButton(text='Серверы',callback_data='sc:servers')],
        [InlineKeyboardButton(text='Состояние',callback_data='sc:status'),
         InlineKeyboardButton(text='Логи',callback_data='sc:logs')],
        [InlineKeyboardButton(text='Обновить ZIP',callback_data='sc:upload'),
         InlineKeyboardButton(text='Резервная копия',callback_data='sc:backup')],
    ])


async def send_status(message:Message):
    active=await asyncio.to_thread(subprocess.run,['systemctl','is-active','svgtracker.service'],capture_output=True,text=True,timeout=5)
    ok=active.returncode==0 and active.stdout.strip()=='active'
    output=await asyncio.to_thread(operations.status_text, STATE/'deploy_status.json',service_ok=ok,api_ok=(await asyncio.to_thread(operations.probe_api)))
    await send_chunks(message,output)
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


async def send_log(message:Message,kind:str):
    content=await asyncio.to_thread(diagnostics.tail,kind,300)
    label='бота' if kind=='bot' else 'сайта и API'
    await message.answer_document(BufferedInputFile(content.encode('utf-8'),filename='Site_Control_'+kind+'_log.txt'),
        caption=f'Журнал {label} · последние записи. Ошибки сохранены полностью в пределах выбранного фрагмента.')


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
    await message.answer('Site Control\nПроекты, серверы и состояние — здесь и в Mini App.',reply_markup=keyboard())


@dp.message(Command('logs'))
async def logs(message:Message):
    if is_admin(message):await message.answer('Какой журнал показать?',reply_markup=log_keyboard())



def site_keyboard():
    rows=[]
    for r in store.list_sites()[:20]:
        rows.append([InlineKeyboardButton(text=f'Редактировать: {str(r["client_name"])[:35]}',callback_data=f'sc:site:{r["id"]}')])
    rows.append([InlineKeyboardButton(text='Новый проект — инструкция',callback_data='sc:newhelp')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_sites(message:Message):
    sites=store.list_sites()
    if not sites:
        await message.answer('Проектов ещё нет. Добавить: /newsite Название | slug',reply_markup=site_keyboard())
    else:
        await message.answer(f'Мои сайты: {len(sites)}\nВыберите проект:',reply_markup=site_keyboard())


@dp.message(Command('sites'))
async def sites_command(message:Message):
    if is_admin(message):await show_sites(message)


@dp.message(Command('newsite'))
async def newsite(message:Message):
    if not is_admin(message):return
    parts=(message.text or '').partition(' ')[2].split('|')
    if len(parts)!=2:
        await message.answer('Создание сайта: /newsite Название компании | latin-slug');return
    try:
        sid=store.create_site(parts[0].strip(),parts[1].strip().lower())
        await message.answer(f'Сайт #{sid} создан как черновик.\nРедактировать: /edit {sid} title Новый заголовок\nПубликация: /publish {sid}',reply_markup=site_keyboard())
    except (ValueError,LookupError) as exc:await message.answer(str(exc))


BOT_FIELDS={'title','tagline','description','phone','address','hours','button'}


@dp.message(Command('edit'))
async def edit_site(message:Message):
    if not is_admin(message):return
    parts=(message.text or '').split(' ',3)
    if len(parts)<4 or not parts[1].isdigit() or parts[2] not in BOT_FIELDS:
        await message.answer('Изменить поле: /edit ID title Новый заголовок\nПоля: '+', '.join(sorted(BOT_FIELDS)));return
    try:
        sid=int(parts[1]);site=store.get_site(sid)
        if not site:raise LookupError('Сайт не найден')
        data=store.json_obj(site['draft']);data[parts[2]]=parts[3]
        store.save_draft(sid,data)
        await message.answer(f'Сайт #{sid}: черновик сохранён. Чтобы изменения увидели посетители: /publish {sid}')
    except (ValueError,LookupError) as exc:await message.answer(str(exc))


@dp.message(Command('publish'))
async def publish_site_bot(message:Message):
    if not is_admin(message):return
    arg=(message.text or '').partition(' ')[2].strip()
    if not arg.isdigit():await message.answer('Использование: /publish ID');return
    try:
        version=store.publish(int(arg),'admin:'+str(message.from_user.id))
        await message.answer(f'Сайт #{arg} опубликован · версия {version}')
    except (ValueError,LookupError) as exc:await message.answer(str(exc))


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


@dp.message(Command('admins'))
async def admins(message:Message):
    if is_admin(message):await message.answer('Администраторы: '+', '.join(str(n) for n in registry.list_ids()))


@dp.message(Command('admin_add','admin_remove'))
async def admin_change(message:Message):
    if not is_admin(message):return
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


@dp.message(F.document)
async def upload_zip(message:Message):
    if not is_admin(message) or not message.document:return
    name=str(message.document.file_name or '')
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
    if action=='cancel':
        await callback.answer('Отменено');await callback.message.edit_text('Действие отменено.');return
    if action=='home':
        await callback.answer()
        await callback.message.answer('Site Control',reply_markup=keyboard())
        return
    if action=='upload':
        await callback.answer()
        await callback.message.answer('Отправьте ZIP документом в этот чат.')
        return
    if action=='logs':
        await callback.answer()
        await callback.message.answer('Выберите журнал:',reply_markup=log_keyboard())
        return
    if action=='log' and len(parts)==3 and parts[2] in diagnostics.KINDS:
        await callback.answer()
        try:await send_log(callback.message,parts[2])
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
                await callback.message.answer('Резервная копия создана · '+operations.pretty_time(time.time())+'\nСохраните копию отдельно от VPS.')
            except Exception:
                log.exception('Ошибка создания резервной копии')
                await send_error(callback.message,'Ошибка резервного копирования')
        return
    if action=='sites':
        await callback.answer();await show_sites(callback.message);return
    if action=='servers':
        await callback.answer();await callback.message.answer('Подключённые серверы:',reply_markup=server_keyboard());return
    if action=='newhelp':
        await callback.answer();await callback.message.answer('Создание: /newsite Кафе Уют | cafe-uyut');return
    if action=='serverhelp':
        await callback.answer();await callback.message.answer('Создание подключений к VPS — в Site Control → Серверы.');return
    if action=='site' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        site=store.get_site(int(parts[2]))
        if not site:await callback.message.answer('Проект не найден');return
        d=store.json_obj(site['draft'])
        await callback.message.answer(f'#{site["id"]} · {site["client_name"]}\nЗаголовок: {d["title"]}\nПубликация: {site["published_version"]}\n\nИзменить: /edit {site["id"]} title Новый заголовок\nОпубликовать: /publish {site["id"]}')
        return
    if action=='server' and len(parts)==3 and parts[2].isdigit():
        await callback.answer()
        sid=int(parts[2]);server=store.get_server(sid)
        if not server:await callback.message.answer('Сервер не найден');return
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Нагрузка',callback_data=f'sc:tmpl:{sid}:uptime'),InlineKeyboardButton(text='Диск',callback_data=f'sc:tmpl:{sid}:disk')],
            [InlineKeyboardButton(text='Память',callback_data=f'sc:tmpl:{sid}:memory'),InlineKeyboardButton(text='Служба',callback_data=f'sc:tmpl:{sid}:site_status')],
            [InlineKeyboardButton(text='Логи',callback_data=f'sc:tmpl:{sid}:site_logs'),InlineKeyboardButton(text='Перезапуск',callback_data=f'sc:tmpl:{sid}:site_restart')]])
        await callback.message.answer(f'{server["name"]} · {server["host"]}',reply_markup=kb);return
    if action=='tmpl' and len(parts)==4 and parts[2].isdigit():
        await callback.answer();await prepare_remote(callback.message,int(parts[2]),parts[3]);return
    if len(parts)!=3:await callback.answer('Неверный запрос',show_alert=True);return
    token=parts[2]
    pending=PENDING.get(token)
    if not pending or pending[0]!=callback.from_user.id or pending[-1]<time.time():
        await callback.answer('Подтверждение устарело',show_alert=True);return
    if action=='run' and pending[2]=='remote':
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
        PENDING.pop(token,None)
        _,uid,role,_=pending
        try:changed=registry.change(callback.from_user.id,uid,role)
        except Exception as exc:await callback.answer('Не выполнено',show_alert=True);await callback.message.answer(str(exc)[:600]);return
        await callback.answer();await callback.message.edit_text('Права обновлены.' if changed else 'Права не изменились.')
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
    try:await manager.receive(row,payload)
    except Exception:
        log.exception('Ошибка webhook клиентского бота')
        raise HTTPException(503,'Temporary processing failure')
    return {'ok':True}


async def main():
    store.init()
    config=uvicorn.Config(app,host=os.environ.get('SVGTRACKER_BIND_HOST','127.0.0.1'),port=8000,server_header=False,log_level='warning',access_log=False)
    server=uvicorn.Server(config)
    await bot.set_my_commands([BotCommand(command='start',description='Открыть Site Control'),BotCommand(command='status',description='Состояние + инструкция'),BotCommand(command='sites',description='Мои сайты'),BotCommand(command='servers',description='Удалённые серверы'),BotCommand(command='logs',description='Логи бота и сайта'),BotCommand(command='backup',description='Создать резервную копию')])
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

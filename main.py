"""Site Control 1.1: replaces the SVGTracker runtime; keeps its trusted ZIP deploy path."""
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
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from fastapi import Request, HTTPException
import uvicorn

from config import BOT_TOKEN
from admin_access import AdminRegistry
from sitecontrol import store, web, operations
from sitecontrol.maintenance import snapshot
from sitecontrol.telegram_bots import manager

logging.basicConfig(level=logging.WARNING,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logging.getLogger('uvicorn.access').disabled=True
logging.getLogger('aiogram').setLevel(logging.ERROR)
logging.getLogger('httpx').setLevel(logging.WARNING)
log=logging.getLogger('sitecontrol')
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


def keyboard():
    """Only useful day-to-day actions appear in the admin bot."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть панель',url=web.SITE_URL)],
        [InlineKeyboardButton(text='Состояние',callback_data='sc:status'),
         InlineKeyboardButton(text='Последнее изменение',callback_data='sc:last')],
        [InlineKeyboardButton(text='Обновить ZIP',callback_data='sc:upload'),
         InlineKeyboardButton(text='Резервная копия',callback_data='sc:backup')],
    ])


@dp.message(Command('start','admin','menu','help'))
async def welcome(message:Message):
    if not is_admin(message):
        await message.answer('Site Control. Доступ к управлению закрыт.')
        return
    arg=(message.text or '').partition(' ')[2].strip()
    if arg.startswith('login_') and 6 <= len(arg) <= 110:
        approved=store.approve_login(arg[6:],message.from_user.id)
        await message.answer('Вход подтверждён. Вернитесь в браузер.' if approved else 'Код входа неверен или устарел.')
        return
    await message.answer('Site Control\n\nПроекты и дизайн — в веб-панели. Здесь только обслуживание и важные события.',reply_markup=keyboard())


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


@dp.message(Command('deploy'))
async def deploy_info(message:Message):
    if is_admin(message):await message.answer('Пришлите ZIP файлом в этот чат. Я проверю архив и предложу установку. Перед обновлением создаётся резервная копия.')


@dp.message(Command('status','server_status'))
async def server_status(message:Message):
    if not is_admin(message):return
    try:
        active=await asyncio.to_thread(subprocess.run,['systemctl','is-active','svgtracker.service'],capture_output=True,text=True,timeout=5)
        ok=active.returncode==0 and active.stdout.strip()=='active'
        text=await asyncio.to_thread(operations.status_text, STATE/'deploy_status.json',service_ok=ok,api_ok=(await asyncio.to_thread(operations.probe_api)))
        await message.answer(text,reply_markup=keyboard())
    except Exception:
        log.exception('Server status failed')
        await send_error(message,'Ошибка проверки сервера')


@dp.message(Command('last','deploy_check','deploy_status'))
async def last_change(message:Message):
    if not is_admin(message):return
    try:
        # /deploy_check shows the deployment only; /last shows the latest meaningful action.
        cmd=(message.text or '').split()[0].split('@')[0].lower()
        if cmd=='/last':
            evt=await asyncio.to_thread(operations.last_event,STATE/'deploy_status.json')
            result='Последнее изменение\n'+evt['label']+'\n'+evt['when']
            if evt['error']:result+='\n\n'+evt['message']
        else:
            result=operations.deploy_status_text(STATE/'deploy_status.json')
        await send_chunks(message,result)
    except Exception:
        log.exception('Operation status failed')
        await send_error(message,'Ошибка чтения состояния')


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
    if action=='upload':
        await callback.answer();await callback.message.answer('Отправьте ZIP как файл в этот чат.');return
    if action in ('status','last','backup'):
        await callback.answer()
        if action=='status':
            try:
                active=await asyncio.to_thread(subprocess.run,['systemctl','is-active','svgtracker.service'],capture_output=True,text=True,timeout=5)
                text=await asyncio.to_thread(operations.status_text, STATE/'deploy_status.json',service_ok=(active.returncode==0 and active.stdout.strip()=='active'),api_ok=(await asyncio.to_thread(operations.probe_api)))
                await callback.message.answer(text,reply_markup=keyboard())
            except Exception:
                log.exception('Status callback failed');await send_error(callback.message)
        elif action=='last':
            try:
                event=await asyncio.to_thread(operations.last_event,STATE/'deploy_status.json')
                await send_chunks(callback.message,'Последнее изменение\n'+event['label']+'\n'+event['when']+('\n\n'+event['message'] if event['error'] else ''))
            except Exception:
                log.exception('Last event failed')
                await send_error(callback.message)
        else:
            try:
                path=await asyncio.to_thread(snapshot)
                await asyncio.to_thread(helper,'backup')
                await callback.message.answer('Резервная копия создана\n'+operations.pretty_time(time.time())+'\nФайл: '+path.name+'\nДля безопасности сохраните копию вне VPS.')
            except Exception:
                log.exception('Backup callback failed');await send_error(callback.message)
        return
    if len(parts)!=3:await callback.answer('Неверный запрос',show_alert=True);return
    token=parts[2]
    pending=PENDING.get(token)
    if not pending or pending[0]!=callback.from_user.id or pending[-1]<time.time():
        await callback.answer('Подтверждение устарело',show_alert=True);return
    if action=='role':
        PENDING.pop(token,None)
        _,uid,role,_=pending
        try:changed=registry.change(callback.from_user.id,uid,role)
        except Exception as exc:await callback.answer('Не выполнено',show_alert=True);await callback.message.answer(str(exc)[:600]);return
        await callback.answer();await callback.message.edit_text('Права обновлены.' if changed else 'Права не изменились.')
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
        log.exception('Webhook handler failed for bot ID %s',row['id'])
        raise HTTPException(503,'Temporary processing failure')
    return {'ok':True}


async def main():
    store.init()
    config=uvicorn.Config(app,host=os.environ.get('SVGTRACKER_BIND_HOST','127.0.0.1'),port=8000,server_header=False,log_level='warning',access_log=False)
    server=uvicorn.Server(config)
    await bot.set_my_commands([BotCommand(command='start',description='Главное меню'),BotCommand(command='status',description='Состояние сервера'),BotCommand(command='last',description='Последнее изменение'),BotCommand(command='backup',description='Резервная копия')])
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

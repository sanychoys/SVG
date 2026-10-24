"""Site Control 1.0: replaces the SVGTracker runtime; keeps its trusted ZIP deploy path."""
from __future__ import annotations
import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import Request, HTTPException
import uvicorn

from config import BOT_TOKEN
from admin_access import AdminRegistry
from sitecontrol import store, web
from sitecontrol.maintenance import snapshot
from sitecontrol.telegram_bots import manager

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
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


def is_admin(msg:Message):
    return bool(msg.from_user and msg.chat.type=='private' and registry.is_admin(msg.from_user.id))


def worker(args:list[str],purpose:str):
    if not DEPLOY.is_file():raise RuntimeError('Установщик отсутствует')
    unit=f'sitecontrol-{purpose}-{int(time.time())}-{uuid.uuid4().hex[:7]}'
    py=ROOT/'venv/bin/python'
    cmd=['systemd-run','--unit',unit,'--collect','--property=Type=oneshot',str(py if py.is_file() else sys.executable),str(DEPLOY),*args]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=13)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'systemd-run failed')[:850])
    return unit


def helper(subcommand):
    py=ROOT/'venv/bin/python'
    p=subprocess.run([str(py if py.is_file() else sys.executable),str(DEPLOY),subcommand],capture_output=True,text=True,timeout=55)
    if p.returncode:raise RuntimeError((p.stderr or p.stdout)[:850])
    return (p.stdout or '')[:4000]


def keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть Site Control',url=web.SITE_URL)],
        [InlineKeyboardButton(text='Загрузить ZIP',callback_data='sc:upload')],
        [InlineKeyboardButton(text='Проверить сервер',callback_data='sc:status')]
    ])


@dp.message(Command('start','admin'))
async def welcome(message:Message):
    if not is_admin(message):
        await message.answer('Site Control — внутренняя платформа веб-студии. Доступ только администраторам.\nВаш ID: '+str(message.from_user.id if message.from_user else '—'))
        return
    arg=(message.text or '').partition(' ')[2].strip()
    if arg.startswith('login_') and 6 <= len(arg) <= 110:
        approved=store.approve_login(arg[6:],message.from_user.id)
        await message.answer('Вход подтверждён. Вернитесь на сайт.' if approved else 'Код устарел или неверен.')
        return
    await message.answer('Site Control v1.0\n\nСайты редактируются в веб-панели.\n\nКоманды: /login КОД · /deploy · /backup · /deploy_check · /rollback · /admins · /admin_add ID · /admin_remove ID · /server_status · /myid',reply_markup=keyboard())


@dp.message(Command('myid'))
async def my_id(message:Message):
    await message.answer('Ваш Telegram ID: '+str(message.from_user.id if message.from_user else '—'))


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
    if is_admin(message):await message.answer('Отправьте ZIP сюда как документ. Подтвердите установку кнопкой. Перед обновлением создаётся backup; требуется настроенный GitHub deploy key. Проверить: /deploy_check.')


@dp.message(Command('server_status'))
async def server_status(message:Message):
    if not is_admin(message):return
    try:
        active=await asyncio.to_thread(subprocess.run,['systemctl','is-active','svgtracker.service'],capture_output=True,text=True,timeout=5)
        disk=shutil.disk_usage('/')
        count=len(store.list_sites())
        await message.answer(f'Site Control\nСлужба: {active.stdout.strip() or "?"}\nСайтов: {count}\nДиск свободен: {disk.free/1024**3:.1f} ГБ\nПроверка API: /api/test')
    except Exception as exc:await message.answer('Ошибка: '+str(exc)[:450])


@dp.message(Command('backup'))
async def backup(message:Message):
    if not is_admin(message):return
    try:
        new_file=await asyncio.to_thread(snapshot)
        previous=await asyncio.to_thread(helper,'backup')
        await message.answer('Созданы резервные копии:\nSite Control: '+str(new_file)+'\nПрежний контур (для отката ZIP): '+previous[:700]+'\n\nХраните ещё одну копию вне этого VPS.')
    except Exception as exc:await message.answer('Backup error: '+str(exc)[:800])


@dp.message(Command('deploy_check','deploy_status'))
async def deploy_status(message:Message):
    if not is_admin(message):return
    try:
        p=STATE/'deploy_status.json'
        if not p.is_file():
            # Install helper stores status under this basename in some releases.
            candidates=list(STATE.glob('*status*.json'))
            p=max(candidates,key=lambda f:f.stat().st_mtime) if candidates else p
        status=p.read_text(encoding='utf-8')[-2100:] if p.is_file() else 'Нет статуса деплоя'
        await message.answer('Site Control\nAPI: /api/test\n\n'+status[:3000])
    except Exception as exc:await message.answer('Ошибка: '+str(exc)[:500])


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
    except Exception as exc:await message.answer('Не удалось скачать: '+str(exc)[:400]);return
    PENDING[token]=(message.from_user.id,str(path),'zip',time.time()+1800)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Проверить и установить ZIP',callback_data='sc:deploy:'+token)],[InlineKeyboardButton(text='Отмена',callback_data='sc:cancel')]])
    await message.answer(f'Получен {name} ({size//1024} КБ).\nПосле проверки изменения будут опубликованы с автоматическим откатом при ошибке API/GitHub.',reply_markup=kb)


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
    if action=='status':
        await callback.answer();await callback.message.answer('Проверьте /deploy_check и веб-панель Site Control.');return
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
            await callback.message.answer('Ошибка: '+str(exc)[:1000]);return
        PENDING.pop(token,None)
        await callback.answer('Откат запущен')
        await callback.message.edit_text('Откат запущен: '+unit+'. Следите за результатом в Telegram.')
        return
    if action=='deploy' and pending[2]=='zip':
        _,path,_,_=pending
        try:unit=await asyncio.to_thread(worker,['deploy',path,'--admin-chat',str(callback.from_user.id)],'deploy')
        except Exception as exc:
            await callback.answer('Ошибка запуска',show_alert=True)
            await callback.message.answer('Ошибка: '+str(exc)[:1000]);return
        PENDING.pop(token,None)
        await callback.answer('Установка запущена')
        await callback.message.edit_text('Запущена задача '+unit+'. Результат придёт в Telegram; можно проверить /deploy_check.')
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
    config=uvicorn.Config(app,host=os.environ.get('SVGTRACKER_BIND_HOST','127.0.0.1'),port=8000,server_header=False,log_level='info')
    server=uvicorn.Server(config)
    await bot.set_my_commands([])
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

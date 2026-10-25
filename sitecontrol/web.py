"""Admin API, isolated public lead forms, safe public site rendering."""
from __future__ import annotations
import asyncio
import html
import ipaddress
import json
import logging
import hmac
import os
import re
import secrets
import time
import traceback
import threading
import hashlib
import pwd
import shlex
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from . import store
from . import diagnostics, miniapp_auth, remote, project_files, local_console, self_update, domains, host_verification, root_executor

app = FastAPI(title='Site Control API', docs_url=None, redoc_url=None, openapi_url=None)
registry = None
manager = None
main_bot = None
SITE_URL = os.environ.get('SITE_CONTROL_PUBLIC_URL') or os.environ.get('SVGTRACKER_PUBLIC_URL') or 'https://starslix.ru'
SITE_URL = SITE_URL.strip().rstrip('/')
site_log = logging.getLogger('sitecontrol.site')
COOKIE = 'sc_admin_session'
LOGIN_COOKIE = 'sc_login_verify'
LEAD_LIMIT = defaultdict(deque)
LOGIN_LIMIT = defaultdict(deque)

def issue_mini_launch(admin_id: int) -> str:
    """Issue from authenticated private bot chat. Shared across API workers."""
    if registry is None or not registry.is_admin(admin_id):
        raise PermissionError('Только администратор может открыть Mini App')
    return store.issue_mini_ticket(int(admin_id),90)


def consume_mini_launch(ticket: str) -> int | None:
    admin_id=store.consume_mini_ticket(ticket)
    if admin_id is not None and registry is not None and registry.is_admin(admin_id):
        return admin_id
    return None


def telegram_init_token():
    """Use the active bot instance rather than a potentially stale config import."""
    if main_bot is not None and getattr(main_bot,"token",None):
        return main_bot.token
    from config import BOT_TOKEN
    return BOT_TOKEN



def wire(admin_registry, bot_manager, admin_bot):
    global registry, manager, main_bot
    registry, manager, main_bot = admin_registry, bot_manager, admin_bot



@app.middleware('http')
async def minimal_site_logging(request:Request, call_next):
    path=diagnostics.scrub_path(request.url.path)
    try:
        response=await call_next(request)
        # No routine GET/access noise. Record updates and failed requests only.
        if response.status_code>=500:
            site_log.error('%s %s · HTTP %s',request.method,path,response.status_code)
        elif response.status_code>=400 and not path.startswith('/api/sc/auth/') and not path.startswith(('/api/notes','/api/training/','/api/finance/','/api/profile','/api/schedule/','/api/diagnostics/frontend')):
            site_log.warning('%s %s · HTTP %s',request.method,path,response.status_code)
        elif request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<400 and not path.startswith('/api/sc/auth/'):
            site_log.info('%s %s · HTTP %s',request.method,path,response.status_code)
        return response
    except Exception:
        site_log.exception('Ошибка сайта: %s %s',request.method,path)
        raise


def require_admin(request: Request):
    signed = request.headers.get('X-Telegram-Init-Data')
    if signed:
        try: verified=miniapp_auth.verify(signed,telegram_init_token())
        except ValueError:
            # A valid short-lived, HttpOnly Mini App session is also accepted.
            # A failed signature alone must NEVER grant access.
            session_id=store.get_session(request.cookies.get(COOKIE))
            if session_id and registry.is_admin(session_id):return session_id
            raise HTTPException(401,'Подпись Mini App недействительна')
        if not registry.is_admin(verified):raise HTTPException(403,'Доступ запрещён')
        return verified
    admin_id = store.get_session(request.cookies.get(COOKIE))
    if not admin_id or not registry.is_admin(admin_id):
        raise HTTPException(401, 'Войдите через Telegram')
    return admin_id


def require_permission(request, capability, project_id=None):
    actor=require_admin(request)
    # Graceful compatibility for legacy in-process testing registries.
    authorized = registry.can(actor,capability,project_id) if hasattr(registry,'can') else (
        registry.is_admin(actor) and (capability not in ('platform.root','servers.root') or actor==registry.owner_id))
    if not authorized:raise HTTPException(403,'Недостаточно прав для этого действия или проекта')
    return actor


def _can(actor, capability, project_id=None):
    return (registry.can(actor,capability,project_id) if hasattr(registry,'can') else registry.is_admin(actor))


def _project_from_tenant(tenant_id):
    with store.connect() as db:
        row=db.execute('SELECT id FROM sites WHERE tenant_id=?',(tenant_id,)).fetchone()
    return int(row['id']) if row else -1


def _route_capability(path, method):
    """Deny-by-default on protected API and scope actions to their projects."""
    bits=path.split('/')
    if len(bits)<4 or bits[:3]!=['','api','sc']:return None,None
    target=bits[3]
    if target in ('auth','health','public'):return None,None
    if target=='profile':return 'projects.view',None
    if target=='admins':return 'admins.manage',None
    if target=='media':return None,None  # public media authorization within endpoint
    if target=='platform':
        return 'platform.manage',0
    if target=='exports':return 'logs.view',0
    if target=='preview':return 'projects.view',int(bits[4]) if len(bits)>4 and bits[4].isdigit() else None
    if target=='ssh':return 'servers.manage',None
    if target=='bots':
        cap='bots.manage' if method!='GET' else 'projects.view'
        project=None
        if len(bits)>4 and bits[4].isdigit():
            bot=next((b for b in store.list_bots() if b['id']==int(bits[4])),None)
            project=_project_from_tenant(bot['tenant_id']) if bot else -1
        return cap,project
    if target=='servers':
        project=None
        if len(bits)>4 and bits[4].isdigit():
            server=store.get_server(int(bits[4]));project=server['site_id'] if server else -1
        cap=('servers.view' if method=='GET' else 'servers.manage')
        return cap,project
    if target in ('projects','sites'):
        project=int(bits[4]) if len(bits)>4 and bits[4].isdigit() else None
        if project==0:
            return ('logs.view' if 'logs' in bits and method=='GET' else 'platform.manage'),0
        if target=='sites':
            if method=='GET':cap='projects.view'
            elif len(bits)==4:cap='projects.manage'
            elif 'media' in bits:cap='files.manage'
            elif 'leads' in bits:cap='projects.manage'
            else:cap='files.manage'
        else:
            if method=='GET':
                cap=('logs.view' if 'logs' in bits else 'backups.manage' if 'backups' in bits else 'projects.view')
            elif 'domains' in bits:cap='domains.manage'
            elif 'backup' in path:cap='backups.manage'
            elif any(x in bits for x in ('archive','unarchive')) or method=='DELETE':cap='projects.manage'
            else:cap='files.manage'
        return cap,project
    # Any new protected route is restricted until deliberately classified.
    return 'platform.manage',None


@app.middleware('http')
async def enforce_admin_permissions(request:Request,call_next):
    path=request.url.path
    if path.startswith('/api/sc/'):
        try:
            cap,project=_route_capability(path,request.method)
            if cap:require_permission(request,cap,project)
        except HTTPException as exc:
            return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    return await call_next(request)


def write_guard(request):
    actor = require_admin(request)
    if request.headers.get('X-Site-Control') != '1':
        raise HTTPException(403, 'Недопустимый запрос')
    origin = request.headers.get('origin')
    if origin:
        try:
            from urllib.parse import urlsplit
            a,b=urlsplit(origin),urlsplit(str(request.base_url))
            if a.hostname != b.hostname or a.scheme not in ('https','http'):
                raise HTTPException(403, 'Неверный Origin')
        except ValueError:
            raise HTTPException(403,'Неверный Origin')
    return actor


async def json_body(request, max_bytes=140_000):
    if int(request.headers.get('content-length') or '0') > max_bytes:
        raise HTTPException(413,'Слишком большой запрос')
    body = await request.body()
    if len(body)>max_bytes:raise HTTPException(413,'Слишком большой запрос')
    try:
        obj=json.loads(body)
        if not isinstance(obj,dict):raise ValueError()
        return obj
    except (ValueError,TypeError):
        raise HTTPException(400,'Некорректный JSON')


def error(exc):
    if isinstance(exc, LookupError):raise HTTPException(404,str(exc))
    if isinstance(exc, ValueError):raise HTTPException(400,str(exc))
    raise exc


@app.get('/api/test')
def readiness():
    try:
        with store.connect() as db:
            for table in ('tenants','sites','bots','leads','admin_sessions'):
                db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
    except Exception:
        raise HTTPException(503, 'Database unavailable')
    return {'status':'ok','service':'Site Control','version':'1.15.0'}


@app.get('/api/sc/health')
def health():
    return readiness()


@app.post('/api/sc/auth/start')
async def auth_start(request: Request):
    peer=real_ip(request)
    bucket=LOGIN_LIMIT[peer]
    timestamp=time.monotonic()
    while bucket and bucket[0]<timestamp-60: bucket.popleft()
    if len(bucket)>=8: raise HTTPException(429,'Слишком много попыток входа')
    bucket.append(timestamp)
    code, verifier = store.create_login()
    me = await main_bot.get_me()
    response=JSONResponse({'code':code,'bot_username':me.username,'expires_seconds':600})
    response.set_cookie(LOGIN_COOKIE,verifier,httponly=True,secure=True,samesite='strict',max_age=600,path='/api/sc')
    response.headers['Cache-Control']='no-store'
    return response


@app.get('/api/sc/auth/status')
def auth_status(request: Request, code: str=''):
    if len(code)>100:raise HTTPException(400,'Неверный код')
    verifier=request.cookies.get(LOGIN_COOKIE,'')
    admin_id=store.consume_login(code,verifier) if verifier else None
    if not admin_id:return {'authorized':False}
    if not registry.is_admin(admin_id):raise HTTPException(403,'Доступ отозван')
    session=store.create_session(admin_id)
    response=JSONResponse({'authorized':True,'admin_id':admin_id})
    response.set_cookie(COOKIE,session,httponly=True,secure=True,samesite='lax',max_age=7*86400,path='/')
    response.delete_cookie(LOGIN_COOKIE,path='/api/sc')
    response.headers['Cache-Control']='no-store'
    return response



@app.post('/api/sc/auth/miniapp')
async def auth_miniapp(request:Request):
    """Prefer Telegram-signed initData; allow a single-use ticket issued by the
    authenticated admin bot at /start. Never trust Telegram initDataUnsafe or ID
    from an unauthenticated request body.
    """
    body=await json_body(request,max_bytes=10000)
    admin_id=None
    reason=''
    signed=body.get('init_data')
    if signed:
        try:admin_id=miniapp_auth.verify(signed,telegram_init_token())
        except ValueError as exc:reason=str(exc)
    if admin_id is None:
        admin_id=consume_mini_launch(body.get('launch_ticket'))
        if admin_id is None:
            existing=store.get_session(request.cookies.get(COOKIE))
            if existing and registry.is_admin(existing):admin_id=existing
        if admin_id is None:
            # No user secrets or raw Telegram parameters in logs.
            site_log.warning('Вход Mini App не подтверждён: %s',reason or 'нет проверенных данных')
            raise HTTPException(401,'Откройте Mini App через свежую кнопку /start в главном боте.')
    if body.get('launch_ticket') and signed and not reason:
        consume_mini_launch(body.get('launch_ticket'))
    if not registry.is_admin(admin_id):
        site_log.warning('Mini App: вход пользователя без прав администратора')
        raise HTTPException(403,'Ваш Telegram-аккаунт не имеет прав администратора')
    response=JSONResponse({'authorized':True,'admin_id':admin_id})
    response.headers['Cache-Control']='no-store'
    if signed:
        try:miniapp_auth.verify(signed,telegram_init_token())
        except ValueError:
            # Only a verified one-time bot ticket can create this scoped session.
            session=store.create_session(admin_id,ttl=2*3600)
            response.set_cookie(COOKIE,session,httponly=True,secure=True,samesite='strict',max_age=2*3600,path='/')
    else:
        session=store.create_session(admin_id,ttl=2*3600)
        response.set_cookie(COOKIE,session,httponly=True,secure=True,samesite='strict',max_age=2*3600,path='/')
    site_log.info('Авторизован администратор Mini App')
    return response


@app.post('/api/sc/profile/timezone')
async def save_device_timezone(request:Request):
    admin=write_guard(request)
    data=await json_body(request,1000)
    try:store.set_admin_timezone(admin,data.get('timezone'))
    except ValueError as exc:error(exc)
    return {'ok':True}


@app.get('/api/sc/admins')
def list_admins(request:Request):
    require_admin(request)
    return {'owner_id':registry.owner_id,'admins':registry.list_ids(), 'access':{str(i):registry.get_access(i) for i in registry.list_ids()} if hasattr(registry,'get_access') else {},'capabilities':registry.catalogue() if hasattr(registry,'catalogue') else {}}


@app.post('/api/sc/admins')
async def add_admin(request:Request):
    actor=write_guard(request)
    body=await json_body(request,1000)
    if body.get('confirm')!='ADD':raise HTTPException(400,'Требуется подтверждение ADD')
    require_permission(request,'admins.manage')
    try:changed=registry.change(actor,body.get('telegram_id'),'grant')
    except (ValueError,TypeError,PermissionError) as exc:raise HTTPException(400,str(exc))
    site_log.info('Обновлены права администраторов')
    return {'ok':True,'changed':changed,'admins':registry.list_ids()}


@app.delete('/api/sc/admins/{telegram_id}')
def remove_admin(telegram_id:int,request:Request,confirm:str=''):
    actor=write_guard(request)
    if confirm!='REMOVE':raise HTTPException(400,'Требуется подтверждение REMOVE')
    require_permission(request,'admins.manage')
    try:changed=registry.change(actor,telegram_id,'revoke')
    except (ValueError,TypeError,PermissionError) as exc:raise HTTPException(400,str(exc))
    site_log.info('Отозваны права администратора')
    return {'ok':True,'changed':changed,'admins':registry.list_ids()}


@app.put('/api/sc/admins/{telegram_id}/access')
async def update_admin_access(telegram_id:int,request:Request):
    actor=write_guard(request)
    require_permission(request,'admins.manage')
    body=await json_body(request,6000)
    if body.get('confirm')!='ACCESS':raise HTTPException(400,'Подтвердите изменение прав')
    try:
        changed=registry.set_access(actor,telegram_id,body.get('role'),body.get('permissions'),body.get('projects'))
    except PermissionError as exc:raise HTTPException(403,str(exc))
    except LookupError as exc:raise HTTPException(404,str(exc))
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
    site_log.warning('Права администратора %s изменены пользователем %s',telegram_id,actor)
    return {'ok':True,'access':changed}


@app.get('/api/sc/auth/me')
def auth_me(request:Request):
    actor=require_admin(request)
    return {'admin_id':actor,'access':registry.get_access(actor) if hasattr(registry,'get_access') else {}}


@app.post('/api/sc/auth/logout')
def auth_logout(request:Request):
    write_guard(request)
    store.revoke_session(request.cookies.get(COOKIE))
    result=JSONResponse({'ok':True})
    result.delete_cookie(COOKIE,path='/')
    return result


@app.get('/api/sc/sites')
def sites(request:Request):
    require_admin(request)
    actor=require_admin(request)
    return {'sites':[item for item in store.list_sites() if _can(actor,'projects.view',item['id'])]}


@app.post('/api/sc/sites')
async def create_site(request:Request):
    write_guard(request)
    body=await json_body(request)
    if hasattr(registry,'get_access') and (registry.get_access(require_admin(request)) or {}).get('projects') is not None:
        raise HTTPException(403,'Создавать новые проекты можно только с доступом ко всем проектам')
    try: site_id=store.create_site(body.get('name',''), body.get('slug',''), body.get('client_telegram_id'))
    except Exception as exc: error(exc)
    return {'ok':True,'id':site_id}


@app.get('/api/sc/sites/{site_id}')
def site_detail(site_id:int,request:Request):
    require_admin(request)
    row=store.get_site(site_id)
    if not row:raise HTTPException(404,'Сайт не найден')
    return {'site':store.site_payload(row),'leads':store.leads(site_id),
            'versions':store.versions(site_id),'bots':store.bots_for_tenant(row['tenant_id'])}


@app.put('/api/sc/sites/{site_id}')
async def update_site(site_id:int,request:Request):
    write_guard(request)
    body=await json_body(request)
    try:
        value=store.save_draft(site_id,body.get('draft',{}),body.get('client_telegram_id'))
    except Exception as exc:error(exc)
    return {'ok':True,'draft':value}


@app.post('/api/sc/sites/{site_id}/publish')
def publish_site(site_id:int,request:Request):
    write_guard(request)
    try: version=store.publish(site_id,'admin:'+str(require_admin(request)))
    except Exception as exc: error(exc)
    return {'ok':True,'version':version}


@app.post('/api/sc/sites/{site_id}/rollback/{version}')
def rollback_site(site_id:int,version:int,request:Request):
    write_guard(request)
    try: latest=store.rollback(site_id,version)
    except Exception as exc:error(exc)
    return {'ok':True,'version':latest}


@app.patch('/api/sc/sites/{site_id}/leads/{lead_id}')
async def update_lead(site_id:int,lead_id:int,request:Request):
    admin=write_guard(request)
    body=await json_body(request)
    row=store.get_site(site_id)
    if not row:raise HTTPException(404,'Сайт не найден')
    try:store.lead_status(row['tenant_id'],lead_id,body.get('status'),f'admin:{admin}')
    except Exception as exc:error(exc)
    return {'ok':True}


@app.get('/api/sc/bots')
def bots(request:Request):
    require_admin(request)
    actor=require_admin(request)
    return {'bots':[b for b in store.list_bots() if _can(actor,'projects.view',_project_from_tenant(b['tenant_id']))],'public_url':SITE_URL}


@app.post('/api/sc/ssh/key')
async def ssh_create_key(request:Request):
    write_guard(request)
    data=await json_body(request,2000)
    try:pub=await asyncio.to_thread(remote.public_key,data.get('alias'),True)
    except (ValueError,LookupError) as exc:error(exc)
    return {'public_key':pub}


@app.post('/api/sc/ssh/scan')
async def ssh_scan(request:Request):
    admin=write_guard(request)
    data=await json_body(request,2000)
    try:key,fingerprint=await asyncio.to_thread(remote.host_scan,data.get('host'),data.get('port',22))
    except ValueError as exc:error(exc)
    except Exception as exc:
        site_log.exception('SSH host-key scan failed')
        raise HTTPException(502,'Сервер недоступен для SSH-сканирования')
    token=host_verification.issue(admin,key,fingerprint,ttl=180)
    key_type=key.split()[1]
    return {'token':token,'fingerprint':fingerprint,
            'warning':'Сверьте SHA256 с публичным ключом SSH-сервера соответствующего типа ('+key_type+'). Не подтверждайте незнакомый сервер.'}


@app.post('/api/sc/ssh/trust')
async def ssh_trust(request:Request):
    admin=write_guard(request)
    data=await json_body(request,1000)
    token=data.get('token');expected=data.get('fingerprint')
    try:
        host_verification.confirm(admin,token,expected,remote.trust_host)
    except (LookupError,ValueError) as exc:error(exc)
    return {'ok':True}


@app.get('/api/sc/servers')
def servers(request:Request):
    require_admin(request)
    actor=require_admin(request)
    return {'servers':[v for v in store.list_servers() if _can(actor,'servers.view',v.get('site_id'))],'templates':{k:v[0] for k,v in remote.TEMPLATES.items()}}


@app.post('/api/sc/servers')
async def server_create(request:Request):
    actor=write_guard(request)
    data=await json_body(request,5000)
    try:
        cleaned=remote.validate(data.get('name'),data.get('host'),data.get('port',22),
            data.get('username'),data.get('key_alias'),data.get('service','sitecontrol'),data.get('site_id'),data.get('deploy_root',''))
        require_permission(request,'servers.manage',cleaned.get('site_id'))
        server_id=store.add_server(cleaned,'admin:'+str(actor))
    except (ValueError,LookupError) as exc:error(exc)
    return {'ok':True,'id':server_id}


@app.post('/api/sc/servers/{server_id}/prepare')
async def server_prepare(server_id:int,request:Request):
    actor=write_guard(request)
    server=store.get_server(server_id)
    if not server or not server.get('site_id'):raise HTTPException(404,'Подключите VPS к клиентскому проекту')
    site=store.get_site(server['site_id'])
    if not site:raise HTTPException(404,'Проект не найден')
    body=await json_body(request,1000)
    if body.get('confirm')!='PREPARE':raise HTTPException(400,'Подтвердите подготовку VPS')
    try:
        path=await asyncio.to_thread(remote.prepare_deploy_root,server,site['slug'])
        store.server_set_root(server_id,path,'admin:'+str(actor))
        store.log_server_run(server_id,'admin:'+str(actor),'prepare',True)
    except Exception as exc:
        store.log_server_run(server_id,'admin:'+str(actor),'prepare',False)
        site_log.warning('VPS preparation failure: %s',type(exc).__name__)
        raise HTTPException(502,'Не удалось подготовить папку проекта на VPS: '+str(exc)[:180])
    return {'ok':True,'deploy_root':path,'message':'Папка создана на клиентском VPS'}


@app.delete('/api/sc/servers/{server_id}')
def server_remove(server_id:int,request:Request):
    actor=write_guard(request)
    try:store.remove_server(server_id,'admin:'+str(actor))
    except (LookupError,ValueError) as exc:error(exc)
    return {'ok':True}


@app.post('/api/sc/servers/{server_id}/execute')
async def server_execute(server_id:int,request:Request):
    actor=write_guard(request)
    server=store.get_server(server_id)
    if not server:raise HTTPException(404,'Сервер не найден')
    body=await json_body(request,3000)
    template=body.get('template')
    custom=body.get('command')
    # Destructive actions and arbitrary shell are never silently executed.
    if template in ('manual','site_restart'):
        expected='ROOT' if server['username']=='root' else 'EXECUTE'
        if body.get('confirm')!=expected:
            raise HTTPException(400,'Подтвердите команду словом '+expected)
    if server.get('username')=='root' and template in ('manual','site_restart'):
        require_permission(request,'servers.root',server.get('site_id'))
    try:remote.build_command(server,template,custom)
    except ValueError as exc:error(exc)
    try:
        result=await asyncio.to_thread(remote.execute,server,template,custom)
    except Exception as exc:
        store.log_server_run(server_id,'admin:'+str(actor),str(template),False)
        site_log.exception('SSH ошибка сервера %s',server_id)
        raise HTTPException(502,'Не удалось выполнить SSH-команду: '+str(exc)[:240])
    store.log_server_run(server_id,'admin:'+str(actor),str(template),result['ok'])
    return result


@app.post('/api/sc/bots')
async def attach_bot(request:Request):
    write_guard(request)
    data=await json_body(request)
    token=str(data.get('token','')).strip()
    try:tenant_id=int(data.get('tenant_id'))
    except (ValueError,TypeError): raise HTTPException(400,'Выберите клиента')
    from config import BOT_TOKEN
    if hmac.compare_digest(token, BOT_TOKEN):
        raise HTTPException(400,'Нельзя подключить управляющего бота как клиентского: это отключит его polling.')
    if not re.fullmatch(r'[0-9]{5,15}:[A-Za-z0-9_-]{30,}',token):
        raise HTTPException(400,'Неверный формат токена BotFather')
    if not SITE_URL.startswith('https://'):
        raise HTTPException(503,'Для webhook нужен SITE_CONTROL_PUBLIC_URL на HTTPS')
    require_permission(request,'bots.manage',_project_from_tenant(tenant_id))
    try:
        result=await manager.attach(tenant_id,token,SITE_URL)
    except (LookupError,ValueError) as exc:error(exc)
    return {'ok':True,'bot':{'id':result['id'],'username':result['username']}}


@app.post('/api/sc/bots/{bot_id}/disable')
async def detach_bot(bot_id:int,request:Request):
    actor=write_guard(request)
    data=await json_body(request,1000)
    if data.get('confirm')!='DISABLE':
        raise HTTPException(400,'Подтвердите отключение клиентского бота')
    bots={row['id']:row for row in store.list_bots()}
    row=bots.get(bot_id)
    if not row or not row['enabled']:
        raise HTTPException(404,'Активный бот не найден')
    try:
        await manager.detach(bot_id)
    except (LookupError,ValueError) as exc:error(exc)
    except Exception:
        site_log.exception('Не удалось отключить webhook клиентского бота %s',bot_id)
        raise HTTPException(502,'Telegram недоступен: бот не отключён. Повторите операцию позже.')
    with store.connect() as db:
        store.audit(db,'admin:'+str(actor),'bot.disconnect',row['tenant_id'],str(bot_id))
    return {'ok':True}


@app.post('/api/sc/sites/{site_id}/media')
async def media_upload(site_id:int,request:Request):
    write_guard(request)
    row=store.get_site(site_id)
    if not row:raise HTTPException(404,'Сайт не найден')
    if int(request.headers.get('content-length') or '0') > 900_000:
        raise HTTPException(413,'Максимальный размер изображения: 900 КБ')
    body=await request.body()
    if not body or len(body)>900_000:raise HTTPException(413,'Максимальный размер изображения: 900 КБ')
    if body.startswith(b'\x89PNG\r\n\x1a\n'): ext='png'; ctype='image/png'
    elif body.startswith(b'\xff\xd8\xff'): ext='jpg'; ctype='image/jpeg'
    elif body.startswith(b'RIFF') and body[8:12]==b'WEBP': ext='webp'; ctype='image/webp'
    else:raise HTTPException(400,'Разрешены только PNG, JPEG или WebP')
    path=store.STATE/'media'/str(row['tenant_id'])
    path.mkdir(parents=True,exist_ok=True)
    path.chmod(0o700)
    name=secrets.token_hex(12)+'.'+ext
    fd=os.open(path/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(body)
    return {'url':f'/api/sc/media/{row["tenant_id"]}/{name}','type':ctype}


@app.get('/api/sc/media/{tenant_id}/{filename}')
def media(tenant_id:int,filename:str,request:Request):
    if not re.fullmatch(r'[a-f0-9]{24}\.(png|jpg|webp)',filename):raise HTTPException(404)
    with store.connect() as db:
        found=db.execute('SELECT 1 FROM sites WHERE tenant_id=? AND published IS NOT NULL',(tenant_id,)).fetchone()
    if not found:
        try:require_permission(request,'projects.view',_project_from_tenant(tenant_id))
        except HTTPException:raise HTTPException(404)
    p=store.STATE/'media'/str(tenant_id)/filename
    if not p.is_file():raise HTTPException(404)
    ext=filename.rsplit('.',1)[-1]
    return Response(p.read_bytes(),media_type={'jpg':'image/jpeg','png':'image/png','webp':'image/webp'}[ext],
                    headers={'Cache-Control':'public, max-age=31536000, immutable','X-Content-Type-Options':'nosniff'})


def safe(s):
    return html.escape(str(s or ''),quote=True)


def render_site(slug, data, preview=False):
    title=safe(data.get('title','Сайт'))
    desc=safe(data.get('description'))
    tagline=safe(data.get('tagline'))
    phone=safe(data.get('phone'))
    address=safe(data.get('address'))
    hours=safe(data.get('hours'))
    button=safe(data.get('button') or 'Оставить заявку')
    accent=data['accent']; bg=data['background']
    theme=data['theme']; animation=data['animation']
    if theme == 'dark': bg='#111218'; fg='#f9f9fb'; panel='#1b1c26'; muted='#b1b1c1'
    elif theme == 'editorial': fg='#191919';panel='#f5f0e8';muted='#6b665d'
    else:fg='#16171b';panel='#f6f6f8';muted='#666977'
    img=data.get('hero_image') or ''
    hero=f'<img class="hero-img" src="{safe(img)}" alt="{title}" loading="lazy">' if img else ''
    cards=''.join(f'<article class="item"><h3>{safe(i["name"])}</h3><p>{safe(i["description"])}</p><strong>{safe(i["price"])}</strong></article>' for i in data['items'])
    cards=cards or '<p class="muted">Оставьте заявку — расскажем об услугах подробнее.</p>'
    link=f'/api/sc/public/{quote(slug)}'
    preview_banner='<aside class="preview">Предпросмотр черновика · формы заявок отключены</aside>' if preview else ''
    script='''<script>
      const f=document.querySelector('#leadform'),status=document.querySelector('#status');
      if(f)f.addEventListener('submit',async e=>{e.preventDefault();const b=f.querySelector('button');b.disabled=true;status.textContent='Отправляем...';
      try{const r=await fetch(f.action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
      const d=await r.json();if(!r.ok)throw Error(d.detail||'Ошибка отправки');status.textContent='Заявка отправлена. Спасибо!';f.reset();}
      catch(x){status.textContent=String(x.message||x)}finally{b.disabled=false}});
    </script>'''
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <meta name="description" content="{desc[:200]}"><title>{title}</title><style>
      :root{{--accent:{accent};--bg:{bg};--fg:{fg};--panel:{panel};--muted:{muted}}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
      body{{margin:0;background:var(--bg);color:var(--fg);font-family:Inter,-apple-system,BlinkMacSystemFont,Arial,sans-serif;line-height:1.55}}
      .wrap{{max-width:1100px;padding:0 24px;margin:auto}}header{{padding:26px 0;display:flex;justify-content:space-between;align-items:center;gap:16px}}
      .brand{{font-size:20px;font-weight:800;letter-spacing:-.6px}}a{{color:inherit}}.btn{{border:0;background:var(--accent);color:#fff;text-decoration:none;padding:14px 22px;border-radius:13px;display:inline-block;font:inherit;font-weight:700;cursor:pointer}}
      .hero{{padding:88px 0 100px;display:grid;grid-template-columns:1.1fr 1fr;gap:40px;align-items:center}}.eyebrow{{color:var(--accent);font-weight:700}}
      h1{{font-size:clamp(40px,7vw,78px);line-height:1.03;letter-spacing:-.065em;margin:14px 0 24px}}h2{{font-size:clamp(30px,4vw,46px);letter-spacing:-.05em;margin:0 0 16px}}h3{{margin:0 0 8px}}
      .lead{{font-size:19px;color:var(--muted);max-width:580px;margin:0 0 28px;white-space:pre-wrap}}.hero-img{{width:100%;max-height:450px;object-fit:cover;border-radius:28px}}
      .abstract{{height:350px;border-radius:28px;background:radial-gradient(circle at 35% 40%,var(--accent),transparent 55%),linear-gradient(120deg,var(--panel),var(--bg));border:1px solid var(--panel)}}
      section{{padding:62px 0}}.muted{{color:var(--muted)}}.items{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:26px 0 0}}.item{{padding:24px;background:var(--panel);border-radius:20px;min-height:165px}}
      .item p{{color:var(--muted);white-space:pre-wrap}}.item strong{{color:var(--accent)}}.contact{{display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:start}}
      input,textarea{{width:100%;border:1px solid #8885;border-radius:12px;background:var(--panel);color:var(--fg);padding:14px;font:inherit;margin-bottom:12px;font-size:16px}}
      textarea{{min-height:110px;resize:vertical}}.small{{font-size:14px}}footer{{padding:40px 0;color:var(--muted);border-top:1px solid #8883;margin-top:50px}}
      .preview{{background:#222;color:white;text-align:center;padding:9px;font-size:13px}}
      {'.hero,.item{animation:appear .6s both}@keyframes appear{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}' if animation=='soft' else '.item{transition:transform .25s}.item:hover{transform:translateY(-5px)}' if animation=='lift' else ''}
      @media(max-width:760px){{.hero,.contact{{grid-template-columns:1fr}}.hero{{padding:45px 0}}.items{{grid-template-columns:1fr}}.abstract{{height:200px}}header{{padding:20px 0}}section{{padding:36px 0}}}}
      @media(prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;transition:none!important}}}}
      </style></head><body>{preview_banner}<div class="wrap"><header><div class="brand">{title}</div><a href="#contact">Контакты ↗</a></header>
      <main><section class="hero"><div><div class="eyebrow">Добро пожаловать</div><h1>{title}</h1><p class="lead">{tagline}</p><a class="btn" href="#contact">{button}</a></div>
      <div>{hero or '<div class="abstract" role="img" aria-label="Декоративный элемент"></div>'}</div></section>
      <section id="about"><h2>О нас</h2><p class="lead">{desc}</p></section>
      <section id="services"><h2>Товары и услуги</h2><div class="items">{cards}</div></section>
      <section id="contact" class="contact"><div><h2>Связаться с нами</h2><p>{phone}</p><p>{address}</p><p>{hours}</p></div>
      <div><h3>Оставить заявку</h3>{'<p class="muted">Предпросмотр: отправка отключена.</p>' if preview else f'<form id="leadform" action="/api/sc/public/{quote(slug)}/lead"><input name="customer" placeholder="Ваше имя" maxlength="120" required><input name="contact" placeholder="Телефон или Telegram" maxlength="160" required><textarea name="message" placeholder="Что вас интересует?" maxlength="1200"></textarea><input name="website" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px" aria-hidden="true"><button class="btn" type="submit">Отправить заявку</button><p id="status" role="status" class="small"></p><p class="small muted">Отправляя заявку, вы соглашаетесь, что компания свяжется с вами по указанным контактам.</p></form>'}</div></section></main><footer>{title} · {phone}</footer></div>{'' if preview else script}</body></html>'''


# ------------------ v1.5 file-based projects ---------------------

def _target(pid,server_id):
    try:return project_files.root_for(pid,server_id)
    except (ValueError,LookupError) as exc:error(exc)


@app.post('/api/sc/projects/{project_id}/archive')
async def archive_project(project_id:int, request:Request):
    actor=write_guard(request)
    if project_id<=0:raise HTTPException(400,'Нашу платформу удалить нельзя')
    body=await json_body(request,2000)
    if body.get('confirm')!='ARCHIVE':raise HTTPException(400,'Подтвердите перенос в архив')
    try:return store.archive_site(project_id,True,actor)
    except (ValueError,LookupError) as exc:error(exc)

@app.post('/api/sc/projects/{project_id}/unarchive')
async def unarchive_project(project_id:int, request:Request):
    actor=write_guard(request)
    if project_id<=0:raise HTTPException(400,'Недопустимый проект')
    try:return store.archive_site(project_id,False,actor)
    except (ValueError,LookupError) as exc:error(exc)

@app.delete('/api/sc/projects/{project_id}')
async def permanent_project_delete(project_id:int, request:Request):
    actor=write_guard(request)
    if project_id<=0:raise HTTPException(400,'Нашу платформу удалить нельзя')
    body=await json_body(request,2000)
    if body.get('confirm')!='DELETE':raise HTTPException(400,'Требуется подтверждение удаления')
    try:return store.delete_site(project_id,body.get('slug'),actor)
    except (ValueError,LookupError) as exc:error(exc)

def root_setup_command() -> str:
    # Copy/paste bootstrap for the authorised VPS owner, never executed by FastAPI.
    web_user=pwd.getpwuid(os.geteuid()).pw_name
    # The installed paths are controlled by our own deployment config.
    app_dir=project_files.ROOT
    owner=int(registry.owner_id)
    return ('cd '+shlex.quote(str(app_dir))+' && sudo python3 '
            'deploy/configure_root_executor.py --web-user '+shlex.quote(web_user)
            +' --owner-id '+str(owner)+' --enable-root && '
            'sudo systemctl is-active sitecontrol-root-executor.service')

@app.get('/api/sc/platform/overview')
def platform_overview(request:Request):
    actor=require_admin(request)
    import platform
    return {'service':'Site Control','app_root':str(project_files.ROOT),
            'python':platform.python_version(),'server':'Локальный VPS',
            'bot_name':'Главный бот Site Control','owner_id':registry.owner_id,
            'console_mode':'root_executor' if root_executor.status() else ('diagnostics_only' if os.geteuid()==0 else 'restricted_shell'),
            'root_executor_ready':root_executor.status(),
            'root_owner_only':False,
            'root_setup_command':root_setup_command() if _can(actor,'platform.root',0) else None,
            'root_allowed':_can(actor,'platform.root',0),
            'updater_ready':self_update.UPDATER_MARKER.is_file()}

@app.post('/api/sc/platform/execute')
async def platform_execute(request:Request):
    actor=write_guard(request)
    body=await json_body(request,2000)
    template=body.get('template')
    if body.get('as_root') is True:
        # Root actions require an explicit admin capability and an operator-enabled service.
        require_permission(request,'platform.root',0)
        if template!='manual' or body.get('confirm')!='ROOT':
            raise HTTPException(400,'Подтвердите root-команду словом ROOT')
        cmd=body.get('command')
        try:
            result=await asyncio.to_thread(root_executor.execute,cmd,int(actor))
        except ValueError as exc:error(exc)
        except root_executor.RootUnavailable as exc:
            raise HTTPException(409,str(exc)) from None
        with store.connect() as db:
            store.audit(db,'admin:'+str(actor),'platform.root_command',None,'root command SHA256:'+hashlib.sha256(cmd.encode()).hexdigest())
        site_log.warning('Выполнена root-операция администратором ID %s; код выхода %s',actor,result['exit_code'])
        return result
    if template=='manual' and body.get('confirm')!='EXECUTE':
        raise HTTPException(400,'Подтвердите команду')
    if template in ('site_restart',):
        raise HTTPException(400,'Перезапуск текущего сервиса выполняйте через безопасный установщик')
    try:
        result=await asyncio.to_thread(local_console.execute,template,body.get('command'))
    except ValueError as exc:error(exc)
    except local_console.ConsolePermissionError as exc:
        site_log.warning('Ручная консоль требует безопасной настройки прав: %s',exc)
        raise HTTPException(409,str(exc)) from None
    except Exception:
        site_log.exception('Ошибка локальной консоли')
        raise HTTPException(502,'Не удалось прочитать состояние сервера. Подробности — в журнале сайта.')
    with store.connect() as db:
        store.audit(db,'admin:'+str(actor),'platform.command',None,str(template))
    return result

@app.get('/api/sc/projects')
def projects(request:Request):
    require_admin(request)
    actor=require_admin(request)
    return {'projects':([{'id':0,'name':'Наша платформа','kind':'own','slug':'site-control','bot_name':'главный бот'}] if _can(actor,'projects.view',0) else [])+[
        {'id':s['id'],'name':s['client_name'],'kind':'client','slug':s['slug'],'bot_name':s['bot_name'],'new_leads':s['new_leads'],'archived_at':s.get('archived_at')}
        for s in store.list_sites() if _can(actor,'projects.view',s['id'])]}


@app.get('/api/sc/projects/{project_id}/domains')
def project_domains(project_id:int,request:Request):
    require_admin(request)
    if not store.get_site(project_id):raise HTTPException(404,'Проект не найден')
    rows=store.list_project_domains(project_id)
    for row in rows:
        row['dns']=domains.dns_state(row['hostname'],row['server_host'])
        row['nginx_example']=domains.nginx_example(row['hostname'],row['deploy_root'] or '/home/scdeploy/sitecontrol-sites/PROJECT')
    return {'domains':rows}

@app.post('/api/sc/projects/{project_id}/domains')
async def domain_create(project_id:int,request:Request):
    actor=write_guard(request)
    data=await json_body(request,2000)
    try:
        domain=domains.clean_domain(data.get('hostname'))
        server_id=int(data.get('server_id'))
        item=store.add_project_domain(project_id,server_id,domain,'admin:'+str(actor))
    except (ValueError,TypeError,LookupError) as exc:error(exc)
    return {'ok':True,'id':item,'message':'Домен записан. Настройте DNS у регистратора и Nginx/HTTPS на VPS.'}

@app.delete('/api/sc/projects/{project_id}/domains/{domain_id}')
def domain_delete(project_id:int,domain_id:int,request:Request):
    actor=write_guard(request)
    if request.query_params.get('confirm')!='REMOVE':raise HTTPException(400,'Требуется подтверждение')
    try:store.remove_project_domain(project_id,domain_id,'admin:'+str(actor))
    except LookupError as exc:error(exc)
    return {'ok':True}


@app.post('/api/sc/platform/updates/stage')
async def self_update_stage(request: Request,filename:str='Site_Control.zip'):
    write_guard(request)
    chunks=[]; size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>self_update.MAX_SIZE:raise HTTPException(413,'Установочный ZIP больше 25 МБ')
        chunks.append(chunk)
    try:return {'update':await asyncio.to_thread(self_update.stage,b''.join(chunks),filename)}
    except ValueError as exc:error(exc)

@app.post('/api/sc/platform/updates/{rid}/install')
async def self_update_install(rid:str,request:Request):
    write_guard(request)
    body=await json_body(request,1000)
    if body.get('confirm')!='INSTALL':raise HTTPException(400,'Подтвердите установку')
    try:return {'update':await asyncio.to_thread(self_update.launch,rid)}
    except (ValueError,LookupError) as exc:error(exc)
    except RuntimeError as exc:raise HTTPException(503,str(exc))

@app.get('/api/sc/platform/updates/{rid}')
def self_update_status(rid:str,request:Request):
    require_admin(request)
    try:return {'update':self_update.status(rid)}
    except (ValueError,LookupError) as exc:error(exc)

@app.get('/api/sc/projects/{project_id}/releases')
def releases(project_id:int,request:Request):
    require_admin(request)
    try:return {'releases':project_files.list_releases(project_id)}
    except (ValueError,LookupError) as exc:error(exc)


@app.post('/api/sc/projects/{project_id}/upload')
async def project_upload(project_id:int,request:Request,filename:str=''):
    write_guard(request)
    if int(request.headers.get('content-length') or '0')>project_files.MAX_UPLOAD:
        raise HTTPException(413,'Файл больше 30 МБ')
    parts=[];total=0
    async for chunk in request.stream():
        total+=len(chunk)
        if total>project_files.MAX_UPLOAD:raise HTTPException(413,'Файл больше 30 МБ')
        parts.append(chunk)
    try:
        result=await asyncio.to_thread(project_files.stage,project_id,b''.join(parts),filename)
        if project_id>0:diagnostics.project_event(project_id,'deploy','Загружены и проверены файлы обновления')
        return {'release':result}
    except (ValueError,LookupError) as exc:error(exc)


@app.post('/api/sc/projects/{project_id}/apply/{release_id}')
async def project_apply(project_id:int,release_id:str,request:Request):
    write_guard(request)
    body=await json_body(request,2000)
    if body.get('confirm')!='DEPLOY':raise HTTPException(400,'Подтвердите публикацию словом DEPLOY')
    try:
        result=await asyncio.to_thread(project_files.deploy,project_id,release_id,body.get('server_id'))
        if project_id>0:diagnostics.project_event(project_id,'deploy','Обновление опубликовано на сервере')
        return result
    except (ValueError,LookupError) as exc:error(exc)
    except Exception:
        site_log.exception('Не удалось опубликовать файлы проекта %s',project_id)
        if project_id>0:diagnostics.project_exception(project_id,'deploy','Ошибка публикации',traceback.format_exc())
        raise HTTPException(502,'Ошибка обновления. Подробности в логах.')


@app.get('/api/sc/projects/{project_id}/backups')
def project_backups(project_id:int,request:Request):
    require_admin(request)
    try:return {'backups':project_files.list_backups(project_id)}
    except (ValueError,LookupError) as exc:error(exc)


@app.post('/api/sc/projects/{project_id}/backups')
async def project_backup(project_id:int,request:Request):
    write_guard(request)
    body=await json_body(request,2000)
    try:
        result=await asyncio.to_thread(project_files.create_backup,project_id,body.get('server_id'))
        if project_id>0:diagnostics.project_event(project_id,'deploy','Создан файловый бэкап')
        return {'backup':result}
    except (ValueError,LookupError) as exc:error(exc)
    except Exception:
        site_log.exception('Не удалось создать бэкап проекта %s',project_id)
        if project_id>0:diagnostics.project_exception(project_id,'deploy','Ошибка резервной копии',traceback.format_exc())
        raise HTTPException(502,'Ошибка создания бэкапа; см. логи')


@app.post('/api/sc/projects/{project_id}/backups/import')
async def project_backup_import(project_id:int,request:Request,server_id:int|None=None):
    write_guard(request)
    chunks=[];total=0
    async for chunk in request.stream():
        total+=len(chunk)
        if total>project_files.MAX_BACKUP:raise HTTPException(413,'Бэкап больше 110 МБ')
        chunks.append(chunk)
    try:return {'backup':await asyncio.to_thread(project_files.import_backup,project_id,b''.join(chunks),server_id)}
    except (ValueError,LookupError) as exc:error(exc)


@app.get('/api/sc/projects/{project_id}/backups/{backup_id}/download')
def project_backup_download(project_id:int,backup_id:str,request:Request):
    require_admin(request)
    try:meta,path=project_files.lookup_backup(project_id,backup_id)
    except (ValueError,LookupError) as exc:error(exc)
    return Response(path.read_bytes(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="project-{project_id}-{backup_id}.zip"','Cache-Control':'no-store'})


@app.post('/api/sc/projects/{project_id}/backups/{backup_id}/restore')
async def project_restore(project_id:int,backup_id:str,request:Request):
    write_guard(request)
    body=await json_body(request,2000)
    if body.get('confirm')!='RESTORE':raise HTTPException(400,'Подтвердите восстановление словом RESTORE')
    try:return await asyncio.to_thread(project_files.restore,project_id,backup_id)
    except (ValueError,LookupError) as exc:error(exc)
    except Exception:
        site_log.exception('Ошибка восстановления проекта %s',project_id)
        if project_id>0:diagnostics.project_exception(project_id,'deploy','Ошибка восстановления',traceback.format_exc())
        raise HTTPException(502,'Ошибка восстановления; см. логи')


@app.delete('/api/sc/projects/{project_id}/backups/{backup_id}')
def project_backup_delete(project_id:int,backup_id:str,request:Request,confirm:str=''):
    write_guard(request)
    if confirm!='DELETE':raise HTTPException(400,'Подтвердите удаление')
    try:return project_files.delete_backup(project_id,backup_id)
    except (ValueError,LookupError) as exc:error(exc)


@app.patch('/api/sc/servers/{server_id}/deploy-root')
async def configure_deploy_root(server_id:int,request:Request):
    admin=write_guard(request)
    body=await json_body(request,2000)
    current=store.get_server(server_id)
    if not current:raise HTTPException(404,'Сервер не найден')
    try:
        validated=remote.validate(current['name'],current['host'],current['port'],current['username'],current['key_alias'],current['service'],current['site_id'],body.get('deploy_root',''))
        if not validated['deploy_root']:raise ValueError('Укажите путь к файлам продукта')
        store.server_set_root(server_id,validated['deploy_root'],'admin:'+str(admin))
    except (ValueError,LookupError) as exc:error(exc)
    return {'ok':True}


@app.get('/api/sc/projects/{project_id}/logs/{kind}')
def project_log(project_id:int,kind:str,request:Request,server_id:int|None=None,download:int=0):
    require_admin(request)
    if project_id<=0 or not store.get_site(project_id):raise HTTPException(404,'Проект не найден')
    if kind in ('bot','site','deploy'):
        result=diagnostics.project_tail(project_id,kind,limit=30,hours=24)
    elif kind=='server':
        srv=store.get_server(server_id or 0)
        if not srv or srv['site_id']!=project_id:raise HTTPException(403,'Этот VPS не принадлежит проекту')
        try:result=diagnostics.recent_journal(remote.execute(srv,'site_logs')['output'])
        except Exception:site_log.exception('Ошибка журналов VPS проекта %s',project_id);raise HTTPException(502,'Не удалось получить журнал VPS')
    else:raise HTTPException(404,'Неизвестный журнал')
    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-SC-Log-Timezone':'UTC'}
    if download:headers['Content-Disposition']=f'attachment; filename="project-{project_id}-{kind}.txt"'
    return Response(result,media_type='text/plain; charset=utf-8',headers=headers)

@app.get('/api/sc/exports/{kind}')
def export_logs(kind:str,request:Request,server_id:int|None=None,download:int=1):
    require_admin(request)
    from . import diagnostics
    if kind in ('bot','site'):
        result=diagnostics.tail(kind,30,hours=24)
    elif kind=='deploy':
        logpath=Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin'))/'logs'/'deploy.log'
        result=diagnostics.recent_file(logpath,limit=30,hours=24) if logpath.is_file() else 'Записей ещё нет'
    elif kind=='platform':
        try:result=diagnostics.recent_journal(local_console.execute('site_logs')['output'])
        except Exception:site_log.exception('Журнал локального сервера');raise HTTPException(502,'Локальные логи недоступны')
    elif kind=='server':
        srv=store.get_server(int(server_id or 0))
        if not srv:raise HTTPException(404,'Сервер не найден')
        try:result=diagnostics.recent_journal(remote.execute(srv,'site_logs')['output'])
        except Exception:site_log.exception('Логи VPS');raise HTTPException(502,'Не удалось получить логи VPS')
    else:raise HTTPException(404,'Неизвестный журнал')
    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-SC-Log-Timezone':'UTC'}
    if download:headers['Content-Disposition']=f'attachment; filename="site-control-{kind}.txt"'
    return Response(result,media_type='text/plain; charset=utf-8',headers=headers)


@app.get('/api/sc/public/{slug}',response_class=HTMLResponse)
def public_page(slug:str):
    row=store.public_site(slug)
    if not row or not row.get('published'):raise HTTPException(404,'Сайт ещё не опубликован')
    return HTMLResponse(render_site(slug,row['published']),headers={'Cache-Control':'public, max-age=30','X-Content-Type-Options':'nosniff'})


@app.get('/api/sc/preview/{site_id}',response_class=HTMLResponse)
def preview(site_id:int,request:Request):
    require_admin(request)
    row=store.get_site(site_id)
    if not row:raise HTTPException(404)
    return HTMLResponse(render_site(row['slug'],store.json_obj(row['draft']),True),headers={'Cache-Control':'no-store','X-Frame-Options':'SAMEORIGIN'})


def real_ip(request):
    peer = request.client.host if request.client else 'unknown'
    if peer in ('127.0.0.1','::1'):
        candidate=request.headers.get('x-forwarded-for','').split(',')[0].strip()
        try: return str(ipaddress.ip_address(candidate))
        except ValueError: pass
    return peer


@app.post('/api/sc/public/{slug}/lead')
async def public_lead(slug:str,request:Request):
    if not store.SLUG.fullmatch(slug): raise HTTPException(404)
    bucket=(slug,real_ip(request))
    timestamp=time.monotonic()
    queue=LEAD_LIMIT[bucket]
    while queue and queue[0] < timestamp-60:queue.popleft()
    if len(queue)>=5:raise HTTPException(429,'Слишком много заявок. Попробуйте позже.')
    queue.append(timestamp)
    if len(LEAD_LIMIT)>20000:
        for k in list(LEAD_LIMIT)[:10000]:
            if not LEAD_LIMIT[k] or LEAD_LIMIT[k][-1]<timestamp-60: LEAD_LIMIT.pop(k,None)
    data=await json_body(request,5_000)
    if data.get('website'):return {'ok':True}
    for field in ('customer','contact','message'):
        if not isinstance(data.get(field,''),str):raise HTTPException(400,'Проверьте форму')
    try:lead=store.add_lead(slug,data.get('customer',''),data.get('contact',''),data.get('message',''))
    except (LookupError,ValueError) as exc:error(exc)
    diagnostics.project_event(lead['site_id'],'site','Получена новая заявка (данные посетителя не записываются в журнал)')
    try:await manager.notify_lead(lead)
    except Exception: pass # persisted lead must not fail due to Telegram outage
    return {'ok':True,'id':lead['id']}

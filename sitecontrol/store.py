"""Site Control MVP: per-tenant content, audit log, sessions, releases and leads.

Stores current project data outside the deployed working tree.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

STATE = Path(os.environ.get('SITE_CONTROL_DATA_DIR', '/var/lib/sitecontrol'))
DB_PATH = STATE / 'sitecontrol.db'
TOKEN_DIR = STATE / 'tokens'
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{2,55}$')
HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
DEFAULT = {
    'title': 'Новый сайт', 'tagline': 'Расскажите о себе и своих услугах',
    'description': 'Мы рады новым клиентам. Оставьте заявку, и мы свяжемся с вами.',
    'phone': '', 'address': '', 'hours': '', 'button': 'Оставить заявку',
    'accent': '#ff862e', 'background': '#ffffff', 'theme': 'clean',
    'animation': 'soft', 'hero_image': '', 'items': [],
}

def now():
    return int(time.time())


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE.chmod(0o700); TOKEN_DIR.chmod(0o700)
    except OSError:
        pass


@contextmanager
def connect():
    prepare()
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            slug TEXT UNIQUE NOT NULL, draft TEXT NOT NULL, published TEXT,
            published_version INTEGER NOT NULL DEFAULT 0,
            client_telegram_id INTEGER, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sites_tenant ON sites(tenant_id);
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            username TEXT NOT NULL, hook_key TEXT UNIQUE NOT NULL,
            hook_secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            site_id INTEGER NOT NULL REFERENCES sites(id),
            customer TEXT NOT NULL, contact TEXT NOT NULL,
            message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_leads_tenant ON leads(tenant_id,created_at);
        CREATE TABLE IF NOT EXISTS releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL REFERENCES sites(id),
            version INTEGER NOT NULL, data TEXT NOT NULL,
            author TEXT NOT NULL, created_at INTEGER NOT NULL,
            UNIQUE(site_id,version)
        );
        CREATE TABLE IF NOT EXISTS admin_sessions (
            digest TEXT PRIMARY KEY, admin_id INTEGER NOT NULL,
            expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_requests (
            code_digest TEXT PRIMARY KEY,
            verifier_digest TEXT NOT NULL,
            admin_id INTEGER, expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_updates (
            bot_id INTEGER NOT NULL, update_id INTEGER NOT NULL,
            received_at INTEGER NOT NULL, PRIMARY KEY(bot_id, update_id)
        );
        CREATE TABLE IF NOT EXISTS remote_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            host TEXT NOT NULL, port INTEGER NOT NULL,
            username TEXT NOT NULL, key_alias TEXT NOT NULL,
            service TEXT NOT NULL, site_id INTEGER REFERENCES sites(id),
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS remote_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER NOT NULL,
            actor TEXT NOT NULL, template TEXT NOT NULL,
            success INTEGER NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
            action TEXT NOT NULL, tenant_id INTEGER,
            detail TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        ''')
    with connect() as db:
        cols=[r['name'] for r in db.execute('PRAGMA table_info(remote_servers)')]
        if 'deploy_root' not in cols:
            db.execute("ALTER TABLE remote_servers ADD COLUMN deploy_root TEXT NOT NULL DEFAULT ''")
        columns=[r['name'] for r in db.execute('PRAGMA table_info(sites)')]
        if 'archived_at' not in columns:
            db.execute('ALTER TABLE sites ADD COLUMN archived_at INTEGER')
    try: DB_PATH.chmod(0o600)
    except OSError: pass


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def json_obj(value):
    return json.loads(value) if value else None


def audit(db, actor, action, tenant_id=None, detail=''):
    db.execute('INSERT INTO audit(actor, action, tenant_id, detail, created_at) VALUES(?,?,?,?,?)',
               (str(actor), action, tenant_id, str(detail)[:350], now()))


def clean_data(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid site settings')
    data = dict(DEFAULT)
    for k in ['title', 'tagline', 'description', 'phone', 'address', 'hours', 'button', 'hero_image']:
        if k in value:
            v = value[k]
            if not isinstance(v, str) or len(v) > (1500 if k == 'description' else 350):
                raise ValueError('Недопустимое значение: ' + k)
            data[k] = v.strip()
    for k in ['accent', 'background']:
        if k in value:
            if not isinstance(value[k], str) or not HEX.fullmatch(value[k]):
                raise ValueError('Цвет должен иметь формат #RRGGBB')
            data[k] = value[k]
    if 'theme' in value:
        if value['theme'] not in ['clean', 'dark', 'editorial']:
            raise ValueError('Неизвестный шаблон')
        data['theme'] = value['theme']
    if 'animation' in value:
        if value['animation'] not in ['soft', 'none', 'lift']:
            raise ValueError('Неизвестная анимация')
        data['animation'] = value['animation']
    if 'items' in value:
        items = value['items']
        if not isinstance(items, list) or len(items) > 60:
            raise ValueError('Максимум 60 товаров или услуг')
        output = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Ошибка в списке товаров')
            name, price, desc = item.get('name'), item.get('price', ''), item.get('description', '')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
                raise ValueError('Укажите название товара или услуги')
            if not isinstance(price, str) or len(price) > 90:
                raise ValueError('Некорректная цена')
            if not isinstance(desc, str) or len(desc) > 300:
                raise ValueError('Слишком длинное описание')
            output.append({'id': str(item.get('id') or secrets.token_hex(5))[:30],
                           'name': name.strip(), 'price': price.strip(), 'description': desc.strip()})
        data['items'] = output
    if data['hero_image'] and not (data['hero_image'].startswith('/api/sc/media/') or data['hero_image'].startswith('https://')):
        raise ValueError('Изображение должно быть HTTPS-ссылкой или загруженным файлом')
    return data


def create_site(name, slug, client_id=None):
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 90:
        raise ValueError('Название клиента: от 2 до 90 символов')
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ValueError('Адрес: латиница, цифры, дефис, от 3 до 56 символов')
    if client_id not in (None, ''):
        try: client_id = int(client_id)
        except (ValueError, TypeError): raise ValueError('Некорректный Telegram ID')
        if not 1 <= client_id <= 9223372036854775807:
            raise ValueError('Некорректный Telegram ID')
    else: client_id = None
    content = dict(DEFAULT, title=name.strip())
    with connect() as db:
        cur = db.execute('INSERT INTO tenants(name,created_at) VALUES(?,?)', (name.strip(), now()))
        tenant_id = cur.lastrowid
        try:
            cur = db.execute('INSERT INTO sites(tenant_id,slug,draft,client_telegram_id,created_at) VALUES(?,?,?,?,?)',
                             (tenant_id, slug, json.dumps(content, ensure_ascii=False), client_id, now()))
        except sqlite3.IntegrityError:
            raise ValueError('Этот адрес уже занят')
        audit(db, 'admin', 'site.create', tenant_id, slug)
        return cur.lastrowid


def get_site(site_id):
    with connect() as db:
        row = db.execute('''SELECT sites.*, tenants.name as client_name,
                (SELECT username FROM bots WHERE bots.tenant_id=sites.tenant_id AND bots.enabled=1 ORDER BY id DESC LIMIT 1) AS bot_name
                FROM sites JOIN tenants ON sites.tenant_id=tenants.id WHERE sites.id=?''', (site_id,)).fetchone()
        return dict(row) if row else None


def list_sites():
    with connect() as db:
        rows = db.execute('''SELECT s.id,s.tenant_id,s.slug,s.draft,s.published_version,s.client_telegram_id,s.archived_at,
           t.name AS client_name, (SELECT count(*) FROM leads l WHERE l.site_id=s.id AND l.status='new') AS new_leads,
           (SELECT username FROM bots b WHERE b.tenant_id=s.tenant_id AND b.enabled=1 ORDER BY id DESC LIMIT 1) AS bot_name
           FROM sites s JOIN tenants t ON s.tenant_id=t.id ORDER BY s.id DESC''').fetchall()
    return [{**dict(r), 'draft': json_obj(r['draft'])} for r in rows]


def site_payload(row):
    result = dict(row)
    result['draft'] = json_obj(row['draft']); result['published'] = json_obj(row['published'])
    return result


def save_draft(site_id, data, client_id=None):
    content = clean_data(data)
    with connect() as db:
        row = db.execute('SELECT tenant_id,client_telegram_id FROM sites WHERE id=?', (site_id,)).fetchone()
        if not row: raise LookupError('Сайт не найден')
        if client_id is not None:
            if client_id == '': owner = None
            else:
                try: owner = int(client_id)
                except (ValueError, TypeError): raise ValueError('Некорректный Telegram ID')
                if not 1 <= owner <= 9223372036854775807: raise ValueError('Некорректный Telegram ID')
            db.execute('UPDATE sites SET draft=?,client_telegram_id=? WHERE id=?',
                       (json.dumps(content, ensure_ascii=False), owner, site_id))
        else:
            db.execute('UPDATE sites SET draft=? WHERE id=?', (json.dumps(content, ensure_ascii=False), site_id))
        audit(db, 'admin', 'draft.save', row['tenant_id'], str(site_id))
    return content


def publish(site_id, author='admin', override=None):
    with connect() as db:
        row = db.execute('SELECT tenant_id,draft,published_version FROM sites WHERE id=?', (site_id,)).fetchone()
        if not row: raise LookupError('Сайт не найден')
        data = clean_data(override if override is not None else json_obj(row['draft']))
        version = row['published_version'] + 1
        payload = json.dumps(data, ensure_ascii=False)
        db.execute('UPDATE sites SET published=?,published_version=? WHERE id=?', (payload, version, site_id))
        db.execute('INSERT INTO releases(site_id,version,data,author,created_at) VALUES(?,?,?,?,?)',
                   (site_id,version,payload,author,now()))
        audit(db, author, 'site.publish', row['tenant_id'], f'{site_id}:v{version}')
    return version


def versions(site_id):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT version,author,created_at FROM releases WHERE site_id=? ORDER BY version DESC LIMIT 30', (site_id,))]


def rollback(site_id, version):
    with connect() as db:
        item = db.execute('SELECT data FROM releases WHERE site_id=? AND version=?',(site_id,version)).fetchone()
    if not item: raise LookupError('Версия не найдена')
    return publish(site_id, f'rollback(v{version})', json_obj(item['data']))


def public_site(slug):
    with connect() as db:
        row = db.execute('SELECT id,tenant_id,slug,draft,published,published_version FROM sites WHERE slug=?', (slug,)).fetchone()
        return site_payload(row) if row else None


def add_lead(slug, customer, contact, message):
    if not (1 <= len(customer.strip()) <= 120 and 3 <= len(contact.strip()) <= 160 and len(message) <= 1200):
        raise ValueError('Проверьте имя, контакты и текст заявки')
    with connect() as db:
        row = db.execute('SELECT id, tenant_id FROM sites WHERE slug=? AND published IS NOT NULL',(slug,)).fetchone()
        if not row: raise LookupError('Сайт не опубликован')
        cur = db.execute('INSERT INTO leads(tenant_id,site_id,customer,contact,message,created_at) VALUES(?,?,?,?,?,?)',
            (row['tenant_id'],row['id'],customer.strip(),contact.strip(),message.strip(),now()))
        audit(db, 'visitor', 'lead.create', row['tenant_id'], str(cur.lastrowid))
        return {'id':cur.lastrowid, 'site_id':row['id'], 'tenant_id':row['tenant_id']}


def leads(site_id, limit=80):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM leads WHERE site_id=? ORDER BY id DESC LIMIT ?', (site_id, limit))]


def lead_status(tenant_id, lead_id, status, actor):
    if status not in {'new','in_progress','done','rejected'}: raise ValueError('Некорректный статус')
    with connect() as db:
        item = db.execute('SELECT site_id FROM leads WHERE id=? AND tenant_id=?',(lead_id,tenant_id)).fetchone()
        if not item: raise LookupError('Заявка не найдена')
        db.execute('UPDATE leads SET status=? WHERE id=? AND tenant_id=?', (status,lead_id,tenant_id))
        audit(db, actor,'lead.status',tenant_id,f'{lead_id}:{status}')


def list_bots():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT id,tenant_id,username,enabled,created_at FROM bots ORDER BY id DESC')]


def get_bot_by_key(key):
    with connect() as db:
        row=db.execute('SELECT * FROM bots WHERE hook_key=? AND enabled=1',(key,)).fetchone()
        return dict(row) if row else None


def bots_for_tenant(tenant_id):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT id,username,enabled FROM bots WHERE tenant_id=?',(tenant_id,))]


def add_bot(tenant_id, username, token):
    with connect() as db:
        if not db.execute('SELECT id FROM tenants WHERE id=?', (tenant_id,)).fetchone():
            raise LookupError('Клиент не найден')
        if db.execute('SELECT 1 FROM bots WHERE tenant_id=? AND enabled=1', (tenant_id,)).fetchone():
            raise ValueError('Для этого клиента уже подключён бот. Сначала отключите его.')
        hook_key = secrets.token_urlsafe(22)
        secret = secrets.token_urlsafe(32)
        cur=db.execute('INSERT INTO bots(tenant_id,username,hook_key,hook_secret,created_at) VALUES(?,?,?,?,?)',
                      (tenant_id,username,hook_key,secret,now()))
        bot_id=cur.lastrowid
        save_token(bot_id,token)
        audit(db,'admin','bot.attach',tenant_id,username)
        return {'id':bot_id, 'hook_key':hook_key,'hook_secret':secret,'username':username,'tenant_id':tenant_id}


def disable_bot(bot_id):
    with connect() as db:
        row=db.execute('SELECT tenant_id FROM bots WHERE id=? AND enabled=1',(bot_id,)).fetchone()
        if not row: raise LookupError('Бот не найден')
        db.execute('UPDATE bots SET enabled=0 WHERE id=?',(bot_id,))
        audit(db,'admin','bot.disable',row['tenant_id'],str(bot_id))
    remove_token(bot_id)


def save_token(bot_id, token):
    prepare()
    path=TOKEN_DIR/f'{int(bot_id)}.token'
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f: f.write(token)


def read_token(bot_id):
    return (TOKEN_DIR/f'{int(bot_id)}.token').read_text().strip()


def remove_token(bot_id):
    (TOKEN_DIR/f'{int(bot_id)}.token').unlink(missing_ok=True)


def create_login():
    code=secrets.token_urlsafe(9)
    verifier=secrets.token_urlsafe(25)
    with connect() as db:
        db.execute('DELETE FROM login_requests WHERE expires<?',(now(),))
        db.execute('INSERT INTO login_requests(code_digest,verifier_digest,expires) VALUES(?,?,?)',
                   (digest(code),digest(verifier),now()+600))
    return code, verifier


def approve_login(code, admin_id):
    with connect() as db:
        cur=db.execute('UPDATE login_requests SET admin_id=? WHERE code_digest=? AND admin_id IS NULL AND expires>?',
                       (admin_id,digest(code),now()))
        return cur.rowcount == 1


def consume_login(code, verifier):
    with connect() as db:
        row=db.execute('SELECT admin_id,verifier_digest,expires FROM login_requests WHERE code_digest=?',(digest(code),)).fetchone()
        if not row or not row['admin_id'] or row['expires']<now() or not secrets.compare_digest(row['verifier_digest'],digest(verifier)):
            return None
        db.execute('DELETE FROM login_requests WHERE code_digest=?',(digest(code),))
        return row['admin_id']


def create_session(admin_id):
    token=secrets.token_urlsafe(38)
    with connect() as db:
        db.execute('DELETE FROM admin_sessions WHERE expires<?',(now(),))
        db.execute('INSERT INTO admin_sessions(digest,admin_id,expires) VALUES(?,?,?)',(digest(token),admin_id,now()+7*86400))
    return token


def get_session(token):
    if not token:return None
    with connect() as db:
        row=db.execute('SELECT admin_id FROM admin_sessions WHERE digest=? AND expires>?',(digest(token),now())).fetchone()
        return row['admin_id'] if row else None


def revoke_session(token):
    if token:
        with connect() as db: db.execute('DELETE FROM admin_sessions WHERE digest=?',(digest(token),))


def tenant_owner(bot_id, telegram_id):
    with connect() as db:
        row=db.execute('''SELECT s.id,s.tenant_id,s.slug,s.draft,s.published,s.published_version,s.client_telegram_id
            FROM sites s JOIN bots b ON b.tenant_id=s.tenant_id WHERE b.id=? AND b.enabled=1 AND s.client_telegram_id=?''',
            (bot_id,telegram_id)).fetchone()
        return site_payload(row) if row else None


def client_edit(site, field, value, actor):
    if field not in {'title','tagline','description','phone','address','hours','button'}:
        raise ValueError('Недоступное поле')
    draft = dict(site['draft']); draft[field]=value
    clean=clean_data(draft)
    with connect() as db:
        db.execute('UPDATE sites SET draft=? WHERE id=? AND tenant_id=?',
                   (json.dumps(clean,ensure_ascii=False),site['id'],site['tenant_id']))
        audit(db,actor,'content.change',site['tenant_id'],field)
    if site['published_version']:
        return publish(site['id'],actor,clean)
    return None


def client_items(site, items, actor):
    draft=dict(site['draft']); draft['items']=items
    clean=clean_data(draft)
    with connect() as db:
        db.execute('UPDATE sites SET draft=? WHERE id=? AND tenant_id=?',(json.dumps(clean,ensure_ascii=False),site['id'],site['tenant_id']))
        audit(db,actor,'items.change',site['tenant_id'],str(len(items)))
    if site['published_version']:
        return publish(site['id'],actor,clean)
    return None


def claim_update(bot_id, update_id):
    """Persist Telegram update idempotency across webhook retries and restarts."""
    with connect() as db:
        cur=db.execute('INSERT OR IGNORE INTO telegram_updates(bot_id,update_id,received_at) VALUES(?,?,?)',
                       (int(bot_id),int(update_id),now()))
        if int(update_id)%100==0:
            db.execute('DELETE FROM telegram_updates WHERE received_at<?',(now()-7*86400,))
        return cur.rowcount == 1


def release_update(bot_id, update_id):
    with connect() as db:
        db.execute('DELETE FROM telegram_updates WHERE bot_id=? AND update_id=?',
                   (int(bot_id),int(update_id)))


# Remote server metadata never contains passwords, SSH private keys or arbitrary scripts.
def list_servers():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT r.*,s.slug AS site_slug FROM remote_servers r LEFT JOIN sites s ON s.id=r.site_id ORDER BY r.id DESC')]


def get_server(server_id):
    with connect() as db:
        row=db.execute('SELECT * FROM remote_servers WHERE id=?',(server_id,)).fetchone()
    return dict(row) if row else None


def add_server(data, actor):
    with connect() as db:
        cur=db.execute('INSERT INTO remote_servers(name,host,port,username,key_alias,service,site_id,created_at,deploy_root) VALUES(?,?,?,?,?,?,?,?,?)',
            (data['name'],data['host'],data['port'],data['username'],data['key_alias'],data['service'],data['site_id'],now(),data.get('deploy_root','')))
        audit(db,actor,'server.create',None,data['name'])
        return cur.lastrowid


def remove_server(server_id,actor):
    with connect() as db:
        row=db.execute('SELECT name FROM remote_servers WHERE id=?',(server_id,)).fetchone()
        if not row:raise LookupError('Сервер не найден')
        db.execute('DELETE FROM remote_servers WHERE id=?',(server_id,))
        audit(db,actor,'server.remove',None,row['name'])


def log_server_run(server_id,actor,template,success):
    with connect() as db:
        db.execute('INSERT INTO remote_runs(server_id,actor,template,success,created_at) VALUES(?,?,?,?,?)',
            (server_id,actor,template,int(success),now()))
        audit(db,actor,'server.command',None,str(server_id)+':'+str(template))


def server_set_root(server_id, root, actor):
    with connect() as db:
        row=db.execute('SELECT site_id FROM remote_servers WHERE id=?',(server_id,)).fetchone()
        if row is None:raise LookupError('Сервер не найден')
        db.execute('UPDATE remote_servers SET deploy_root=? WHERE id=?',(root,server_id))
        audit(db,actor,'server.deploy_root',None,str(server_id))


# Site Control v1.6: reversible archive and explicit permanent removal.
def archive_site(site_id:int, archived:bool, actor):
    with connect() as db:
        row=db.execute('SELECT tenant_id, archived_at FROM sites WHERE id=?',(site_id,)).fetchone()
        if row is None:raise LookupError('Проект не найден')
        db.execute('UPDATE sites SET archived_at=? WHERE id=?',(now() if archived else None,site_id))
        audit(db,'admin:'+str(actor),'project.archive' if archived else 'project.restore',row['tenant_id'],str(site_id))
    return {'ok':True}


def delete_site(site_id:int,slug:str,actor):
    if not isinstance(slug,str):raise ValueError('Подтвердите название проекта')
    with connect() as db:
        row=db.execute('SELECT slug,tenant_id,archived_at FROM sites WHERE id=?',(site_id,)).fetchone()
        if not row:raise LookupError('Проект не найден')
        if row['archived_at'] is None:raise ValueError('Сначала перенесите проект в архив')
        if row['slug']!=slug:raise ValueError('Название проекта не совпадает')
        # Fail closed: attached VPS must be removed manually before irreversible deletion.
        if db.execute('SELECT 1 FROM remote_servers WHERE site_id=?',(site_id,)).fetchone():
            raise ValueError('Сначала отключите VPS от проекта, чтобы не потерять управление')
        tenant=row['tenant_id']
        if db.execute('SELECT 1 FROM sites WHERE tenant_id=? AND id!=?',(tenant,site_id)).fetchone():
            raise ValueError('Клиент связан с другим сайтом; удаление требует отдельного переноса данных')
        # Tokens on disk are not deleted here. Keep them encrypted/offline until explicit cleanup.
        db.execute('DELETE FROM telegram_updates WHERE bot_id IN (SELECT id FROM bots WHERE tenant_id=?)',(tenant,))
        db.execute('DELETE FROM bots WHERE tenant_id=?',(tenant,))
        db.execute('DELETE FROM leads WHERE site_id=?',(site_id,))
        db.execute('DELETE FROM releases WHERE site_id=?',(site_id,))
        db.execute('DELETE FROM sites WHERE id=?',(site_id,))
        db.execute('DELETE FROM tenants WHERE id=?',(tenant,))
        audit(db,'admin:'+str(actor),'project.delete',tenant,'project ID '+str(site_id))
    # Staged files and backups remain on disk for manual recovery; never delete admin state.
    return {'ok':True,'orphaned_file_backups_preserved':True}

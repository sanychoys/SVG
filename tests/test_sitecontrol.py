import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from sitecontrol import store, web


class Admins:
    def is_admin(self, value):return int(value)==777


class Bot:
    async def get_me(self):
        class Me: username='SiteControlDemoBot'
        return Me()


class Manager:
    def __init__(self):self.sent=[]
    async def notify_lead(self, item):self.sent.append(item)


@pytest.fixture()
def context(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'STATE',tmp_path/'private')
    monkeypatch.setattr(store,'DB_PATH',store.STATE/'sitecontrol.db')
    monkeypatch.setattr(store,'TOKEN_DIR',store.STATE/'tokens')
    store.init()
    manager=Manager()
    web.wire(Admins(),manager,Bot())
    web.LEAD_LIMIT.clear()
    with TestClient(web.app,base_url='https://example.org') as client:
        yield client,manager


def admin_login(client):
    result=client.post('/api/sc/auth/start').json()
    assert store.approve_login(result['code'],777)
    auth=client.get('/api/sc/auth/status',params={'code':result['code']})
    assert auth.status_code==200 and auth.json()['authorized']
    assert 'sc_admin_session' in client.cookies
    return result


def create(client,name,slug,owner=''):
    response=client.post('/api/sc/sites',headers={'X-Site-Control':'1'},json={
        'name':name,'slug':slug,'client_telegram_id':owner})
    assert response.status_code==200,response.text
    return response.json()['id']


def test_health_and_auth(context):
    client,_=context
    assert client.get('/api/test').json()['status']=='ok'
    assert client.get('/api/sc/sites').status_code==401
    code=admin_login(client)
    assert client.get('/api/sc/auth/me').json()['admin_id']==777
    assert client.get('/api/sc/auth/status',params={'code':code['code']}).json()['authorized']==False
    assert client.post('/api/sc/auth/logout',headers={'X-Site-Control':'1'}).status_code==200
    assert client.get('/api/sc/sites').status_code==401


def test_site_publication_and_forms(context):
    client,manager=context
    admin_login(client)
    a=create(client,'Кафе «Уют»','cafe-uyut','555')
    b=create(client,'Автосервис','auto-service','666')
    assert client.get('/api/sc/public/cafe-uyut').status_code==404
    site=client.get('/api/sc/sites/'+str(a)).json()['site']
    draft=site['draft']
    draft.update(title='<img src=x onerror=alert(1)>', tagline='Лучший кофе',phone='+7 999')
    draft['items']=[{'name':'Капучино','price':'250 ₽','description':'Свежесваренный'}]
    response=client.put(f'/api/sc/sites/{a}',headers={'X-Site-Control':'1'},json={'draft':draft,'client_telegram_id':'555'})
    assert response.status_code==200,response.text
    assert client.post(f'/api/sc/sites/{a}/publish',headers={'X-Site-Control':'1'}).json()['version']==1
    assert client.post(f'/api/sc/sites/{b}/publish',headers={'X-Site-Control':'1'}).status_code==200
    page=client.get('/api/sc/public/cafe-uyut')
    assert page.status_code==200
    assert '&lt;img src=x onerror=alert(1)&gt;' in page.text
    assert '<img src=x onerror=alert(1)>' not in page.text
    assert 'Капучино' in page.text
    assert 'Автосервис' not in page.text
    lead=client.post('/api/sc/public/cafe-uyut/lead',json={'customer':'Пётр','contact':'@petr','message':'Есть ли место?'})
    assert lead.status_code==200,lead.text
    assert manager.sent[0]['site_id']==a
    assert len(client.get(f'/api/sc/sites/{a}').json()['leads'])==1
    assert len(client.get(f'/api/sc/sites/{b}').json()['leads'])==0
    assert client.patch(f'/api/sc/sites/{b}/leads/{lead.json()["id"]}',headers={'X-Site-Control':'1'},json={'status':'done'}).status_code==404
    assert client.patch(f'/api/sc/sites/{a}/leads/{lead.json()["id"]}',headers={'X-Site-Control':'1'},json={'status':'done'}).status_code==200


def test_versions_bot_edits_and_tokens(context):
    client,_=context
    admin_login(client)
    id1=create(client,'Цветы','flower-world','313')
    version=client.post(f'/api/sc/sites/{id1}/publish',headers={'X-Site-Control':'1'}).json()['version']
    row=store.get_site(id1)
    store.client_edit(store.site_payload(row),'title','Большие цветы','client:313')
    assert store.public_site('flower-world')['published']['title']=='Большие цветы'
    assert store.versions(id1)[0]['version']==version+1
    response=client.post(f'/api/sc/sites/{id1}/rollback/1',headers={'X-Site-Control':'1'})
    assert response.json()['version']==version+2
    assert store.public_site('flower-world')['published']['title']=='Цветы'
    assert store.claim_update(1,123) is True
    assert store.claim_update(1,123) is False
    store.release_update(1,123)
    assert store.claim_update(1,123) is True
    record=store.add_bot(row['tenant_id'],'FlowersBot','123456:ABC')
    tokenfile=store.TOKEN_DIR/f'{record["id"]}.token'
    assert tokenfile.read_text()=='123456:ABC'
    assert oct(tokenfile.stat().st_mode & 0o777)=='0o600'
    assert store.tenant_owner(record['id'],313) is not None
    assert store.tenant_owner(record['id'],314) is None
    store.disable_bot(record['id'])
    assert not tokenfile.exists()


def test_inputs_permissions_ratelimit(context):
    client,_=context
    admin_login(client)
    assert client.post('/api/sc/sites',json={'name':'N','slug':'aaa'}).status_code==403
    assert client.post('/api/sc/sites',headers={'X-Site-Control':'1'},json={'name':'N','slug':'aaa'}).status_code==400
    sid=create(client,'Пекарня','bakery')
    assert client.post(f'/api/sc/sites/{sid}/publish',headers={'X-Site-Control':'1'}).status_code==200
    assert client.post('/api/sc/public/bakery/lead',json={'customer':'','contact':'x','message':''}).status_code==400
    for i in range(4):
        r=client.post('/api/sc/public/bakery/lead',json={'customer':f'Покупатель{i}','contact':'user@host','message':''})
        assert r.status_code==200
    assert client.post('/api/sc/public/bakery/lead',json={'customer':'N','contact':'contact','message':''}).status_code==429
    assert client.get('/api/sc/media/1/not-a-file').status_code==404
    bad=client.put(f'/api/sc/sites/{sid}',headers={'X-Site-Control':'1'},json={'draft':{'accent':'javascript:alert(1)'}})
    assert bad.status_code==400


def test_image_upload_preview_backup(context,tmp_path):
    client,_=context
    admin_login(client)
    sid=create(client,'Тест медиа','test-media')
    png=b'\x89PNG\r\n\x1a\n'+b'fakePNGDATA'
    upload=client.post(f'/api/sc/sites/{sid}/media',data=png,headers={'X-Site-Control':'1','Content-Type':'image/png'})
    assert upload.status_code==200,upload.text
    url=upload.json()['url']
    assert client.get(url).status_code==200
    draft=client.get(f'/api/sc/sites/{sid}').json()['site']['draft']
    draft['hero_image']=url
    assert client.put(f'/api/sc/sites/{sid}',headers={'X-Site-Control':'1'},json={'draft':draft}).status_code==200
    assert 'Тест медиа' in client.get(f'/api/sc/preview/{sid}').text
    from sitecontrol import maintenance
    maintenance.BACKUPS=tmp_path/'backups'
    file=maintenance.snapshot()
    assert file.is_file()
    assert oct(file.stat().st_mode & 0o777)=='0o600'


def test_archive_v48_validator():
    from importlib.util import spec_from_file_location,module_from_spec
    root=Path(__file__).resolve().parents[1]
    path=root/'deploy/admin_deploy.py'
    spec=spec_from_file_location('sc_deploy_validator',path)
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    files=['main.py','index.html','script.js','style.css','product.js','product.css',
           'sitecontrol/store.py','sitecontrol/web.py','sitecontrol/telegram_bots.py']
    notes=module.validate_release(root,[Path(x) for x in files])
    assert any('HTML ids checked' in line for line in notes)

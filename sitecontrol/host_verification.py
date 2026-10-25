"""Private, inter-worker single-use SSH fingerprint confirmation records."""
from __future__ import annotations
import fcntl,hashlib,hmac,json,os,secrets,time
from pathlib import Path
from . import store

DIR=store.STATE/'pending-host-keys'

def _folder():
    DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    DIR.chmod(0o700)
    return DIR

def _record(token:str)->Path:
    if not isinstance(token,str) or not 30<=len(token)<=150:
        raise ValueError('Проверка устарела или недействительна')
    return _folder()/(hashlib.sha256(token.encode('ascii')).hexdigest()+'.json')

def issue(admin:int,key:str,fingerprint:str,ttl:int=180)->str:
    token=secrets.token_urlsafe(28)
    path=_record(token)
    record={'admin':int(admin),'key':key,'fingerprint':fingerprint,'expires':time.time()+ttl}
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as file:json.dump(record,file)
    return token

def confirm(admin:int,token:str,fingerprint:str,trust)->None:
    path=_record(token)
    try:
        with path.open('r+',encoding='utf-8') as file:
            fcntl.flock(file,fcntl.LOCK_EX)
            data=json.load(file)
            if data['expires']<=time.time() or data['admin']!=int(admin):
                raise ValueError('Проверка SSH устарела или принадлежит другому администратору')
            if not isinstance(fingerprint,str) or not hmac.compare_digest(data['fingerprint'],fingerprint):
                raise ValueError('Отпечаток SSH не совпадает')
            trust(data['key'])
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        raise LookupError('Проверка SSH устарела; сканируйте сервер заново') from None

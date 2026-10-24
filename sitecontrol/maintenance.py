"""Consistent, private Site Control snapshots, separate from legacy ZIP rollback."""
from __future__ import annotations
import os
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from . import store

BACKUPS = Path(os.environ.get('SITE_CONTROL_BACKUP_DIR','/var/backups/sitecontrol'))


def snapshot():
    store.prepare()
    BACKUPS.mkdir(parents=True, exist_ok=True)
    BACKUPS.chmod(0o700)
    stamp=time.strftime('%Y%m%d-%H%M%S',time.gmtime())
    final=BACKUPS/f'sitecontrol-{stamp}.tar.gz'
    with tempfile.TemporaryDirectory(prefix='sc-snapshot-') as temp:
        consistent=Path(temp)/'sitecontrol.db'
        src=sqlite3.connect(store.DB_PATH)
        dst=sqlite3.connect(consistent)
        try:src.backup(dst)
        finally:dst.close();src.close()
        fd=os.open(final,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                archive.add(consistent,arcname='sitecontrol/sitecontrol.db')
                for sub in ('tokens','media'):
                    path=store.STATE/sub
                    if path.exists():
                        archive.add(path,arcname='sitecontrol/'+sub)
    return final

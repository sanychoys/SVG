"""Regression checks for reduced admin notifications / watchdog and deploy output."""
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import pytest

from sitecontrol import operations, store


@pytest.fixture
def local_state(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'STATE',tmp_path/'state')
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'state'/'sitecontrol.db')
    monkeypatch.setattr(store,'TOKEN_DIR',tmp_path/'state'/'tokens')
    store.init()
    return tmp_path


def test_short_health_is_actionable(local_state):
    store.create_site('Кафе','mycafe')
    path=local_state/'deploy.json'
    path.write_text(json.dumps({'status':'success','message':'Debug: long GitHub info backup details',
        'updated_at':datetime.now(timezone.utc).isoformat(),'github_commit':'abc123456789'}),encoding='utf-8')
    status=operations.status_text(path,service_ok=True)
    assert 'Сервис: работает' in status
    assert 'API: работает' in status
    assert 'Сайтов: 1' in status
    assert 'Последнее изменение:' in status
    assert 'Debug:' not in status
    deploy=operations.deploy_status_text(path)
    assert 'Обновление установлено' in deploy
    assert 'Код: abc123456' in deploy
    assert 'long GitHub info' not in deploy


def test_errors_keep_details(local_state):
    path=local_state/'deploy.json'
    path.write_text(json.dumps({'status':'error','updated_at':'2026-10-24T17:01:00Z',
      'message':'Traceback... exact cause and stack detail'}),encoding='utf-8')
    assert 'exact cause and stack detail' in operations.deploy_status_text(path)
    assert operations.last_event(path)['error'] is True


def test_last_activity_from_audit(local_state):
    store.create_site('Кафе','mycafe')
    status=operations.last_event(local_state/'missing.json')
    assert status['label']=='Создан сайт'
    assert status['when']!='—'


def test_watchdog_lock_is_not_stale(tmp_path,monkeypatch):
    import importlib.util,fcntl
    path=Path(__file__).resolve().parents[1]/'deploy/admin_watchdog.py'
    spec=importlib.util.spec_from_file_location('sc_watchdog',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    lock=tmp_path/'deploy.lock';lock.write_text('old lock')
    monkeypatch.setattr(module,'DEPLOY_LOCK',lock)
    assert module.deploy_in_progress() is False
    with lock.open('r+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert module.deploy_in_progress() is True
        fcntl.flock(f,fcntl.LOCK_UN)
    assert lock.exists()  # do not unlink lock path


def test_timezone_and_api_probe(local_state, monkeypatch):
    from datetime import timedelta
    tz=local_state/'tz.json'
    tz.write_text(json.dumps({'timezone_offset_minutes':180}),encoding='utf-8')
    monkeypatch.setattr(operations,'TIMEZONE_FILE',tz)
    assert operations.pretty_time('2026-10-24T12:00:00Z')=='24.10.2026 · 15:00'
    assert operations.probe_api('http://127.0.0.1:1/api/test') is False


def test_client_buttons_and_access_remain_in_source():
    source=(Path(__file__).resolve().parents[1]/'sitecontrol/telegram_bots.py').read_text(encoding='utf-8')
    for route in ['cl:leads','cl:items','cl:content','cl:help']:
        assert route in source
    for command in ['/price','/additem','/removeitem','/done']:
        assert command in source

"""Telegram Mini App initData validation per core.telegram.org/bots/webapps."""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def verify(init_data: str, bot_token: str, now: int | None = None) -> int:
    if not isinstance(init_data, str) or not 1 <= len(init_data) <= 8192:
        raise ValueError('Нет данных авторизации Mini App')
    try:
        pairs=parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=50)
    except (ValueError, TypeError):
        raise ValueError('Некорректные данные Telegram') from None
    values=dict(pairs)
    if len(pairs)!=len(values):
        raise ValueError('Повторяющиеся поля авторизации')
    received=values.pop('hash', '')
    # Signature is for Ed25519 third-party verification, excluded from HMAC check.
    values.pop('signature', None)
    if len(received)!=64:
        raise ValueError('Отсутствует подпись Telegram')
    check='\n'.join(f'{k}={v}' for k,v in sorted(values.items()))
    secret=hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received,expected):
        raise ValueError('Подпись Telegram не совпадает')
    try:
        timestamp=int(values['auth_date'])
        person=json.loads(values['user'])
        identifier=int(person['id'])
    except (KeyError,ValueError,TypeError,json.JSONDecodeError):
        raise ValueError('Не найден пользователь Telegram') from None
    current=int(now if now is not None else time.time())
    if timestamp>current+90 or current-timestamp>3600 or timestamp<=0:
        raise ValueError('Сессия Telegram устарела')
    if identifier<=0:
        raise ValueError('Неверный Telegram ID')
    return identifier

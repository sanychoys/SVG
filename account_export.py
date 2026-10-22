"""Readable, standalone, print-friendly account exports (without external assets)."""
from __future__ import annotations

from datetime import datetime
from html import escape

LABELS = {
    'account': 'Аккаунт', 'settings': 'Настройки аккаунта',
    'notification_preferences': 'Уведомления', 'friends': 'Друзья и профиль',
    'training': 'Тренировки', 'finance': 'Финансы',
    'schedule': 'Расписание', 'notes': 'Заметки',
    'automation_rules': 'Автоматизации',
    'telegram_id': 'Telegram ID', 'username': 'Имя пользователя',
    'first_name': 'Имя', 'last_name': 'Фамилия', 'created_at': 'Дата регистрации',
    'title': 'Название', 'name': 'Название', 'date': 'Дата',
    'start_at': 'Начало', 'end_at': 'Окончание', 'start': 'Начало', 'end': 'Окончание',
    'description': 'Описание', 'text': 'Текст', 'body': 'Содержание',
    'amount': 'Сумма', 'category': 'Категория', 'status': 'Статус',
    'enabled': 'Включено', 'notes': 'Заметки', 'history': 'История тренировок',
    'goals': 'Цели', 'plan': 'Недельный план', 'planOverrides': 'Переносы плана',
    'activeWorkout': 'Текущая тренировка', 'attendance': 'Посещения',
    'expenses': 'Расходы', 'incomes': 'Доходы', 'debts': 'Долги',
    'mandatoryExpenses': 'Обязательные платежи',
    'monthlyIncome': 'Ежемесячный доход', 'monthlyBudgets': 'Бюджеты',
    'categories': 'Категории', 'reminder_at': 'Напомнить',
    'archived': 'В архиве', 'favorite': 'Избранное',
    'folder': 'Папка', 'tags': 'Теги', 'attachments': 'Вложения',
    'timezone_name': 'Часовой пояс', 'bot_notifications': 'Уведомления бота',
    'friend_request_notifications': 'Уведомления о друзьях',
    'share_busy': 'Показывать занятость', 'frequency': 'Частота',
    'repeat_count': 'Число повторений', 'interval_minutes': 'Интервал (минуты)',
    'updated_at': 'Обновлено', 'id': 'Идентификатор', 'user_id': 'Пользователь',
    'sync': 'Служебные данные синхронизации', 'revision': 'Версия изменений',
}
SECTIONS = ('account','settings','notification_preferences','friends','training','finance','schedule','notes','automation_rules')


def label(key: str) -> str:
    return LABELS.get(str(key), str(key).replace('_', ' ').strip().capitalize())


def scalar(value) -> str:
    if value is None or value == '':
        return '—'
    if value is True:
        return 'Да'
    if value is False:
        return 'Нет'
    if isinstance(value, float):
        return f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
    if isinstance(value, (dict, list)):
        raise TypeError('Значение не является простым')
    return str(value)


def _table(items: list[dict], depth: int) -> str:
    keys = list(dict.fromkeys(k for row in items for k in row.keys()))
    if len(keys) > 9 or any(isinstance(value, (dict, list)) for row in items for value in row.values()):
        return ''.join(f'<article class="entry"><h4>Запись {idx}</h4>{_html(row, depth + 1)}</article>'
                       for idx, row in enumerate(items, 1))
    heads = ''.join(f'<th scope="col">{escape(label(key))}</th>' for key in keys)
    rows = ''.join('<tr>' + ''.join(f'<td>{escape(scalar(item.get(key)))}</td>' for key in keys) + '</tr>' for item in items)
    return f'<div class="scroll"><table><thead><tr>{heads}</tr></thead><tbody>{rows}</tbody></table></div>'


def _html(value, depth=0) -> str:
    if depth > 20:
        return '<p>Слишком глубокая структура данных</p>'
    if isinstance(value, dict):
        if not value:
            return '<p class="muted">Нет записей</p>'
        return '<dl>' + ''.join(
            f'<div class="field"><dt>{escape(label(key))}</dt><dd>{_html(item, depth + 1)}</dd></div>'
            for key, item in value.items()
        ) + '</dl>'
    if isinstance(value, list):
        if not value:
            return '<p class="muted">Нет записей</p>'
        if all(isinstance(item, dict) for item in value):
            return _table(value, depth)
        return '<ol>' + ''.join(f'<li>{_html(item, depth + 1)}</li>' for item in value) + '</ol>'
    return f'<span class="value">{escape(scalar(value))}</span>'


def export_html(data: dict) -> str:
    account = data.get('account') or {}
    person = ' '.join(str(account.get(k) or '') for k in ('first_name','last_name')).strip() or 'Пользователь SVGTracker'
    sections = ''.join(
        f'<section id="s{i}"><h2>{escape(label(key))}</h2>{_html(data.get(key))}</section>'
        for i, key in enumerate(SECTIONS, 1)
    )
    toc = ''.join(f'<a href="#s{i}">{escape(label(key))}</a>' for i, key in enumerate(SECTIONS, 1))
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Данные SVGTracker — {escape(person)}</title>
<style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#f5f6f8;color:#17191d;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}}main{{max-width:900px;margin:0 auto;padding:36px 22px 70px}}
h1{{font-size:29px;line-height:1.2;margin:8px 0 10px}}.intro{{color:#535862}}.small{{font-size:12px;color:#686d76}}nav{{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0}}nav a{{padding:7px 10px;background:white;border:1px solid #dedfe4;border-radius:8px;color:#151515;text-decoration:none;font-size:13px}}
section{{background:white;border:1px solid #e5e5e8;border-radius:13px;margin:16px 0;padding:22px;break-inside:avoid-page}}h2{{font-size:21px;margin:0 0 13px;border-bottom:1px solid #ebecf0;padding-bottom:10px}}h4{{margin:4px 0}}dl{{margin:0}}.field{{display:grid;grid-template-columns:minmax(125px,35%) 1fr;gap:10px;border-bottom:1px solid #f0f0f2;padding:9px 0}}.field:last-child{{border:0}}dt{{color:#60656e}}dd{{margin:0;min-width:0;overflow-wrap:anywhere;white-space:pre-wrap}}dd dl{{border-left:2px solid #eee;padding-left:12px}}.value{{white-space:pre-wrap;overflow-wrap:anywhere}}.muted{{color:#8a8d95}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px 10px;border:1px solid #e8e9ed;vertical-align:top;text-align:left;overflow-wrap:anywhere}}th{{background:#f4f5f6}}.entry{{border:1px solid #eee;padding:12px;margin:10px 0;border-radius:8px}}.notice{{padding:14px;border-left:3px solid #81818b;background:#f1f2f4;border-radius:5px}}@media(max-width:520px){{main{{padding:22px 12px}}section{{padding:14px}}.field{{grid-template-columns:1fr;gap:2px}}h1{{font-size:24px}}}}@media print{{body{{background:white}}main{{padding:0;max-width:none}}section{{border:none;padding:0;margin:20px 0;break-inside:auto}}nav{{display:none}}h2{{break-after:avoid}}thead{{display:table-header-group}}tr{{break-inside:avoid}}}}
</style></head><body><main><p class="small">SVGTRACKER · ЛИЧНЫЙ АРХИВ</p><h1>Копия данных аккаунта</h1><p class="intro">{escape(person)} · сформировано {escape(str(data.get('exported_at') or ''))}</p>
<p class="notice">Здесь перечислены ваши данные. Вложения в заметках представлены сведениями о файлах; сами изображения, аудио и другие файлы не вложены в документ. Берегите копию: она может содержать личную информацию.</p><nav aria-label="Содержание">{toc}</nav>{sections}
<p class="small">Этот документ создан SVGTracker для личного просмотра. Чтобы получить PDF, откройте его в браузере и выберите «Поделиться» → «Печать» → «Сохранить в файлы» (пункты зависят от устройства).</p></main></body></html>'''


def _plain(value, prefix='', depth=0):
    if depth > 20:
        return [prefix + '…']
    if isinstance(value, dict):
        lines=[]
        for key,item in value.items():
            title=prefix + label(key)
            if isinstance(item,(dict,list)):
                lines.append(title + ':')
                lines.extend(_plain(item, prefix + '  ', depth+1))
            else:
                lines.append(title + ': ' + scalar(item))
        return lines or [prefix + 'Нет записей']
    if isinstance(value, list):
        result=[]
        for i,item in enumerate(value, 1):
            result.append(f'{prefix}{i}.')
            result.extend(_plain(item, prefix+'  ', depth+1))
        return result or [prefix+'Нет записей']
    return [prefix + scalar(value)]


def export_text(data: dict) -> str:
    lines = ['SVGTracker — копия данных аккаунта', 'Дата экспорта: ' + str(data.get('exported_at') or ''),
             'Вложения перечислены, но содержимое файлов не включено.', '='*55]
    for key in SECTIONS:
        lines += ['', label(key).upper(), '-'*40, *_plain(data.get(key))]
    return '\n'.join(lines) + '\n'

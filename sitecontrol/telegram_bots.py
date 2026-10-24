"""One webhook gateway for any number of branded Telegram bots."""
from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Update
from . import store

log = logging.getLogger('sitecontrol.clients')
client_dp = Dispatcher()


class ClientBotManager:
    def __init__(self):
        self.cache: dict[int,Bot] = {}
        self.id_map: dict[int,int] = {}
        self.guard = asyncio.Lock()

    async def bot_for(self, bot_id:int) -> Bot:
        async with self.guard:
            if bot_id not in self.cache:
                bot=Bot(token=store.read_token(bot_id))
                self.cache[bot_id]=bot
                self.id_map[int(bot.id)]=bot_id
            return self.cache[bot_id]

    async def attach(self,tenant_id:int,token:str,site_url:str):
        bot=Bot(token=token)
        try:
            user=await bot.get_me()
            if not user.is_bot:raise ValueError('Токен не принадлежит боту')
            bot_id=int(user.id)
            for r in store.list_bots():
                # This check works across restarts and avoids token reuse across tenants.
                if r['enabled'] and r['username'].lower()==(user.username or '').lower():
                    raise ValueError('Этот бот уже подключён к Site Control')
            row=store.add_bot(tenant_id,user.username or str(bot_id),token)
            try:
                await bot.set_webhook(f'{site_url}/api/sc/hooks/{row["hook_key"]}',
                    secret_token=row['hook_secret'],allowed_updates=['message','callback_query'],drop_pending_updates=True)
            except Exception:
                store.disable_bot(row['id'])
                raise
            self.cache[row['id']]=bot
            self.id_map[int(bot.id)]=row['id']
            bot=None
            return row
        finally:
            if bot is not None:await bot.session.close()

    async def detach(self,bot_id:int):
        if not any(row['id']==bot_id and row['enabled'] for row in store.list_bots()):
            raise LookupError('Бот не найден')
        bot=await self.bot_for(bot_id)
        await bot.delete_webhook(drop_pending_updates=False)
        store.disable_bot(bot_id)
        self.id_map.pop(int(bot.id),None)
        self.cache.pop(bot_id,None)
        await bot.session.close()

    async def receive(self,row:dict,payload:dict):
        update_id=payload.get('update_id')
        if not isinstance(update_id,int) or update_id<0:
            raise ValueError('Invalid Telegram update ID')
        if not store.claim_update(row['id'],update_id):
            return  # duplicate delivered by Telegram; already handled
        try:
            bot=await self.bot_for(row['id'])
            update=Update.model_validate(payload,context={'bot':bot})
            await client_dp.feed_update(bot,update)
        except Exception:
            store.release_update(row['id'],update_id)
            raise

    async def notify_lead(self,lead):
        site=store.get_site(lead['site_id'])
        if not site or not site.get('client_telegram_id'):return
        for info in store.bots_for_tenant(site['tenant_id']):
            if not info['enabled']:continue
            bot=await self.bot_for(info['id'])
            try:
                await bot.send_message(site['client_telegram_id'],
                    f'Новая заявка #{lead["id"]}\nИмя: {lead["customer"][:120]}\nКонтакт: {lead["contact"][:120]}\n\n/leads — все заявки')
            except Exception:
                log.exception('Cannot deliver lead notification for client site %s',site['id'])

    async def close(self):
        for bot in self.cache.values():
            try:await bot.session.close()
            except Exception:pass
        self.cache.clear();self.id_map.clear()


manager=ClientBotManager()


def resolve(bot:Bot,user_id):
    if not user_id:return None
    bot_id=manager.id_map.get(int(bot.id))
    if not bot_id:return None
    return store.tenant_owner(bot_id,int(user_id))


def client_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Заявки',callback_data='cl:leads'),
         InlineKeyboardButton(text='Товары и услуги',callback_data='cl:items')],
        [InlineKeyboardButton(text='Данные сайта',callback_data='cl:content'),
         InlineKeyboardButton(text='Как изменить',callback_data='cl:help')],
    ])


def help_text():
    return ("Изменение сайта\n\n"
            "/title Название — название компании\n"
            "/tagline Текст — подзаголовок\n"
            "/description Текст — описание\n"
            "/phone Телефон — контакты\n"
            "/address Адрес — адрес\n"
            "/hours График — часы работы\n"
            "/additem Название | Цена | Описание — добавить\n"
            "/price ID | Цена — изменить цену\n"
            "/removeitem ID — удалить товар/услугу\n"
            "/done ID — закрыть заявку\n\n"
            "Все изменения применяются сразу.")


def content_text(site):
    d=site['draft']
    return (f'Данные сайта\n\nНазвание: {d["title"]}\n'
            f'Подзаголовок: {d["tagline"]}\n'
            f'Телефон: {d["phone"] or "—"}\n'
            f'Адрес: {d["address"] or "—"}\n'
            f'Часы работы: {d["hours"] or "—"}')[:3900]


def leads_text(site):
    rows=store.leads(site['id'],10)
    if not rows:return 'Новых заявок пока нет.'
    values={'new':'Новая','in_progress':'В работе','done':'Завершена','rejected':'Отклонена'}
    return ('\n\n'.join(f'#{r["id"]} · {values.get(r["status"],r["status"])}\n'
        f'{r["customer"]} · {r["contact"]}\n{r["message"][:250]}' for r in rows)[:3850]
        +'\n\nЗакрыть: /done ID')


def items_text(site):
    items=site['draft']['items']
    if not items:return 'Товаров и услуг пока нет. Добавить: /additem Название | Цена | Описание'
    return ('Товары и услуги\n\n'+'\n'.join(f'{i["id"]} · {i["name"]} — {i["price"]}' for i in items))[:3900]


@client_dp.message(Command('myid'))
async def myid(message:Message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}')


@client_dp.message(Command('start','help'))
async def start(message:Message,bot:Bot):
    site=resolve(bot,message.from_user.id if message.from_user else None)
    if not site:
        await message.answer('Этот бот предназначен для владельца сайта.\nДля подключения передайте администратору свой Telegram ID: /myid')
        return
    if (message.text or '').split()[0].split('@')[0].lower() == '/help':
        await message.answer(help_text(),reply_markup=client_keyboard())
        return
    await message.answer(f'Управление сайтом\n{site["draft"]["title"]}\n\nВыберите раздел:',reply_markup=client_keyboard())


@client_dp.message(Command('content'))
async def content(message:Message,bot:Bot):
    site=resolve(bot,message.from_user.id if message.from_user else None)
    if site:await message.answer(content_text(site),reply_markup=client_keyboard())


@client_dp.message(Command('leads'))
async def leads(message:Message,bot:Bot):
    site=resolve(bot,message.from_user.id if message.from_user else None)
    if site:await message.answer(leads_text(site),reply_markup=client_keyboard())


@client_dp.message(Command('items'))
async def items(message:Message,bot:Bot):
    site=resolve(bot,message.from_user.id if message.from_user else None)
    if site:await message.answer(items_text(site),reply_markup=client_keyboard())


@client_dp.callback_query(F.data.startswith('cl:'))
async def client_menu(callback:CallbackQuery,bot:Bot):
    site=resolve(bot,callback.from_user.id if callback.from_user else None)
    if not site or not callback.message:
        await callback.answer('Нет доступа',show_alert=True)
        return
    action=(callback.data or '').partition(':')[2]
    results={'leads':lambda:leads_text(site),'items':lambda:items_text(site),
             'content':lambda:content_text(site),'help':help_text}
    if action not in results:
        await callback.answer('Недоступно',show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(results[action](),reply_markup=client_keyboard())


@client_dp.message(F.text.startswith('/'))
async def client_commands(message:Message,bot:Bot):
    if not message.from_user:return
    site=resolve(bot,message.from_user.id)
    if not site:return
    raw=message.text or ''
    first,_,arg=raw.partition(' ')
    cmd=first.split('@')[0].lower()
    arg=arg.strip()
    fields={'/title':'title','/tagline':'tagline','/description':'description','/phone':'phone','/address':'address','/hours':'hours'}
    if cmd in fields:
        if not arg:await message.answer('Укажите значение после команды.');return
        try:
            store.client_edit(site,fields[cmd],arg,f'client:{message.from_user.id}')
        except ValueError as exc:
            await message.answer(f'Не удалось сохранить: {exc}');return
        await message.answer('Сохранено. Данные на сайте обновлены.')
        return
    if cmd=='/additem':
        vals=[v.strip() for v in arg.split('|',2)]
        if len(vals)<2 or not vals[0]:await message.answer('Формат: /additem Название | Цена | Описание');return
        current=list(site['draft']['items'])
        current.append({'name':vals[0],'price':vals[1],'description':vals[2] if len(vals)>2 else ''})
        try:store.client_items(site,current,f'client:{message.from_user.id}')
        except ValueError as exc:await message.answer(f'Ошибка: {exc}');return
        await message.answer('Товар/услуга добавлены.')
        return
    if cmd=='/price':
        vals=[v.strip() for v in arg.split('|',1)]
        if len(vals)!=2 or not vals[0]:await message.answer('Формат: /price ID | Новая цена');return
        current=list(site['draft']['items'])
        item=next((i for i in current if i['id']==vals[0]),None)
        if not item:await message.answer('Не найден ID. Посмотрите /items');return
        item['price']=vals[1]
        try:store.client_items(site,current,f'client:{message.from_user.id}')
        except ValueError as exc:await message.answer(f'Ошибка: {exc}');return
        await message.answer('Цена обновлена.')
        return
    if cmd=='/removeitem':
        current=list(site['draft']['items'])
        updated=[i for i in current if i['id']!=arg]
        if len(updated)==len(current):await message.answer('ID не найден. Посмотрите /items');return
        store.client_items(site,updated,f'client:{message.from_user.id}')
        await message.answer('Удалено.')
        return
    if cmd=='/done':
        try:store.lead_status(site['tenant_id'],int(arg),'done',f'client:{message.from_user.id}')
        except (ValueError,LookupError):await message.answer('Укажите номер существующей заявки: /done 123');return
        await message.answer('Заявка закрыта.')
        return
    await message.answer('Выберите раздел в меню /start.',reply_markup=client_keyboard())

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

from fastapi import FastAPI
import uvicorn

from config import BOT_TOKEN
from key import main_keyboard
from finance_db import init_db


bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# API встроен в основной файл проекта
# Отдельные backend-файлы не создаются
app = FastAPI(title="SVGTracker API")


@app.get("/api/test")
def api_test():
    return {
        "status": "ok",
        "service": "SVGTracker API"
    }


@dp.message(Command("start"))
async def start(message: Message):

    username = message.from_user.first_name

    await message.answer(
        text=f"""
<b>Добро пожаловать, {username}, в SVGTracker</b>

Твой персональный центр управления жизнью.

Здесь ты сможешь:
<tg-emoji emoji-id="5893236738372932548">🚀</tg-emoji> Формировать полезные привычки
<tg-emoji emoji-id="6030399199030284183">🏋️</tg-emoji> Отслеживать тренировки и прогресс
<tg-emoji emoji-id="5904462880941545555">💰</tg-emoji> Контролировать свои расходы
<tg-emoji emoji-id="5938195768832692153">📚</tg-emoji> Развивать навыки и достигать целей

Все инструменты уже внутри приложения 👇
""",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


async def main():

    init_db()
    print("Бот запущен")

    api_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )

    api_server = uvicorn.Server(api_config)

    await asyncio.gather(
        dp.start_polling(bot),
        api_server.serve()
    )


if __name__ == "__main__":
    asyncio.run(main())

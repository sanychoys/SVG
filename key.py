from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo


def main_keyboard():

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Открыть трекер",
                    web_app=WebAppInfo(
                        url="https://starslix.ru"
                    )
                )
            ]
        ]
    )

    return keyboard

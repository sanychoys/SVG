from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo


def main_keyboard():

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Открыть трекер",
                    web_app=WebAppInfo(
                        url="https://funpay.com/chat/?node=277854947",
                        parse_mode="HTML"
                    )
                )
            ]
        ]
    )

    return keyboard
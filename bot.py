"""
Taklif & Shikoyat Telegram Bot
Railway uchun: Webhook + Flask
"""

import json
import logging
import os
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# =====================================================
BOT_TOKEN = "8919742379:AAG_mBtlsxU4DluKoeXUvCfn2mscdZ1pP1M"
ADMIN_CHAT_ID = 7780854728
MINI_APP_URL = "https://karimov0814.github.io/feedback-bot/index.html"
PORT = int(os.environ.get("PORT", 5000))
WEBHOOK_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
# =====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
CORS(flask_app)

ptb_app = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    is_admin = (chat_id == ADMIN_CHAT_ID)

    if is_admin:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📊 Admin Panel",
                web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1")
            )
        ]])
        await update.message.reply_text(
            "👋 Salom, Admin!\n\nXodimlarning murojaatlari shu yerga keladi.",
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "💬 Taklif yoki Shikoyat yuborish",
                web_app=WebAppInfo(url=MINI_APP_URL)
            )
        ]])
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n"
            f"💡 <b>Taklif</b> — g'oyalaringizni yuboring\n"
            f"⚠️ <b>Shikoyat</b> — muammolaringizni bildiring\n\n"
            f"🔒 Anonim yuborish imkoniyati mavjud.\n\n"
            f"Boshlash uchun tugmani bosing 👇",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        raw = update.message.web_app_data.data
        data = json.loads(raw)

        msg_type = data.get('type', '')
        filial = data.get('filial', '')
        text = data.get('text', '')
        anon = data.get('anon', False)
        time = data.get('time', '')

        sender_user = update.effective_user
        if anon:
            sender_text = "🕵️ <b>Anonim</b>"
        else:
            name = sender_user.first_name
            if sender_user.last_name:
                name += ' ' + sender_user.last_name
            username = f" @{sender_user.username}" if sender_user.username else ""
            sender_text = f"👤 {name}{username}"

        type_emoji = "💡" if msg_type == "taklif" else "⚠️"
        type_label = "TAKLIF" if msg_type == "taklif" else "SHIKOYAT"

        message = (
            f"{type_emoji} <b>{type_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>Filial:</b> {filial}\n"
            f"{sender_text}\n"
            f"📅 <b>Vaqt:</b> {time}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💬 <b>Matn:</b>\n{text}"
        )

        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=message,
            parse_mode="HTML"
        )

        await update.message.reply_text(
            "✅ Murojaatingiz yuborildi!\n\nRahmat, tez orada ko'rib chiqiladi."
        )

        logger.info(f"Yangi {type_label}: {filial} — {'anonim' if anon else sender_user.id}")

    except Exception as e:
        logger.error(f"web_app_data xatolik: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


@flask_app.route("/", methods=["GET"])
def index():
    return "Bot ishlayapti! ✅", 200

@flask_app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    asyncio.run_coroutine_threadsafe(
        ptb_app.process_update(update),
        loop
    )
    return jsonify({"ok": True})


async def setup_webhook():
    domain = WEBHOOK_URL
    if not domain:
        logger.error("RAILWAY_PUBLIC_DOMAIN env yo'q!")
        return
    webhook_address = f"https://{domain}/webhook/{BOT_TOKEN}"
    await ptb_app.bot.set_webhook(webhook_address)
    logger.info(f"Webhook o'rnatildi: {webhook_address}")


def run():
    global ptb_app, loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ptb_app = Application.builder().token(BOT_TOKEN).build()
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))

    loop.run_until_complete(ptb_app.initialize())
    loop.run_until_complete(setup_webhook())

    import threading
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    logger.info(f"✅ Flask server port {PORT} da ishga tushdi!")
    flask_app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()

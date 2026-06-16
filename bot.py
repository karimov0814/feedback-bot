"""
Taklif & Shikoyat Telegram Bot
Railway uchun: Webhook + Flask
"""

import json
import logging
import os
import asyncio
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
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
loop = None

# Xabarlarni faylda saqlash
MESSAGES_FILE = '/app/messages.json'

def load_messages():
    try:
        with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_messages(msgs):
    try:
        with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
            json.dump(msgs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Faylga yozishda xatolik: {e}")


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
            sender_name = "Anonim"
        else:
            name = sender_user.first_name
            if sender_user.last_name:
                name += ' ' + sender_user.last_name
            username = f" @{sender_user.username}" if sender_user.username else ""
            sender_text = f"👤 {name}{username}"
            sender_name = name + (f" @{sender_user.username}" if sender_user.username else "")

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
        await update.message.reply_text("✅ Murojaatingiz yuborildi!\n\nRahmat, tez orada ko'rib chiqiladi.")
        logger.info(f"Yangi {type_label}: {filial}")

    except Exception as e:
        logger.error(f"web_app_data xatolik: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


# ==================== FLASK ROUTES ====================

@flask_app.route("/", methods=["GET"])
def index():
    return "Bot ishlayapti! ✅", 200


@flask_app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    asyncio.run_coroutine_threadsafe(ptb_app.process_update(update), loop)
    return jsonify({"ok": True})


@flask_app.route("/send", methods=["POST"])
def send_message():
    try:
        data = request.get_json(force=True)

        msg_type = data.get('type', '')
        filial = data.get('filial', '')
        text = data.get('text', '')
        anon = data.get('anon', False)
        time = data.get('time', '')
        sender_name = data.get('sender_name', "Noma'lum")
        username = data.get('username', '')

        if anon:
            sender_text = "🕵️ <b>Anonim</b>"
            display_name = "Anonim"
        else:
            uname = f" @{username}" if username else ""
            sender_text = f"👤 {sender_name}{uname}"
            display_name = sender_name + (f" @{username}" if username else "")

        type_emoji = "💡" if msg_type == "taklif" else "⚠️"
        type_label = "TAKLIF" if msg_type == "taklif" else "SHIKOYAT"

        tg_message = (
            f"{type_emoji} <b>{type_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>Filial:</b> {filial}\n"
            f"{sender_text}\n"
            f"📅 <b>Vaqt:</b> {time}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💬 <b>Matn:</b>\n{text}"
        )

        future = asyncio.run_coroutine_threadsafe(
            ptb_app.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=tg_message,
                parse_mode="HTML"
            ),
            loop
        )
        future.result(timeout=10)

        # Faylga saqlash
        import time as time_module
        msg_id = str(int(time_module.time() * 1000))
        msgs = load_messages()
        msgs.insert(0, {
            "id": msg_id,
            "type": msg_type,
            "filial": filial,
            "text": text,
            "anon": anon,
            "time": time,
            "sender": display_name,
            "status": "new"
        })
        # Faqat oxirgi 500 ta xabar
        if len(msgs) > 500:
            msgs = msgs[:500]
        save_messages(msgs)

        logger.info(f"Yangi {type_label}: {filial} — {'anonim' if anon else sender_name}")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"/send xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/messages", methods=["GET"])
def get_messages():
    """Admin panel uchun xabarlar ro'yxati"""
    return jsonify({"ok": True, "messages": load_messages()})


@flask_app.route("/status", methods=["POST"])
def update_status():
    """Xabar holatini yangilash"""
    try:
        data = request.get_json(force=True)
        msg_id = data.get('id')
        new_status = data.get('status')

        if not msg_id or not new_status:
            return jsonify({"ok": False, "error": "id va status kerak"}), 400

        if new_status not in ["new", "progress", "in_progress", "done"]:
            return jsonify({"ok": False, "error": "Noto'g'ri status"}), 400

        msgs = load_messages()
        updated = False
        for msg in msgs:
            if msg.get('id') == msg_id:
                msg['status'] = new_status
                updated = True
                break

        if updated:
            save_messages(msgs)
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Xabar topilmadi"}), 404

    except Exception as e:
        logger.error(f"/status xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ==================== MAIN ====================

async def setup_webhook():
    domain = WEBHOOK_URL
    if not domain:
        logger.warning("RAILWAY_PUBLIC_DOMAIN env yo'q!")
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

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    logger.info(f"✅ Flask server port {PORT} da ishga tushdi!")
    flask_app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()

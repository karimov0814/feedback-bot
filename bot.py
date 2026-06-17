"""
Taklif & Shikoyat Telegram Bot
Railway uchun: Webhook + Flask + PostgreSQL (Supabase)
"""

import json
import logging
import os
import asyncio
import threading
import time as time_module
import psycopg2
import psycopg2.extras
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
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# =====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

CORS(flask_app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

@flask_app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    return response

@flask_app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@flask_app.route('/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    return jsonify({"ok": True}), 200

ptb_app = None
loop = None


# ==================== DATABASE ====================

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """Jadval mavjud bo'lmasa yaratadi"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                type TEXT,
                filial TEXT,
                text TEXT,
                anon BOOLEAN DEFAULT FALSE,
                time TEXT,
                sender TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database jadval tayyor")
    except Exception as e:
        logger.error(f"init_db xatolik: {e}")

def load_messages():
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 500")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"load_messages xatolik: {e}")
        return []

def save_message(msg):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO messages (id, type, filial, text, anon, time, sender, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            msg['id'], msg['type'], msg['filial'], msg['text'],
            msg['anon'], msg['time'], msg['sender'], msg.get('status', 'new')
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"save_message xatolik: {e}")

def delete_message_db(msg_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE id = %s", (msg_id,))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"delete_message xatolik: {e}")
        return False

def update_status_db(msg_id, new_status):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE messages SET status = %s WHERE id = %s", (new_status, msg_id))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"update_status xatolik: {e}")
        return False


# ==================== BOT HANDLERS ====================

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
        time_str = data.get('time', '')
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
            f"📅 <b>Vaqt:</b> {time_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💬 <b>Matn:</b>\n{text}"
        )

        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode="HTML")
        await update.message.reply_text("✅ Murojaatingiz yuborildi!\n\nRahmat, tez orada ko'rib chiqiladi.")

    except Exception as e:
        logger.error(f"web_app_data xatolik: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi.")


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


@flask_app.route("/send", methods=["POST", "OPTIONS"])
def send_message():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True)

        msg_type    = data.get('type', '')
        filial      = data.get('filial', '')
        text        = data.get('text', '')
        anon        = data.get('anon', False)
        time_str    = data.get('time', '')
        sender_name = data.get('sender_name', "Noma'lum")
        username    = data.get('username', '')

        if anon:
            sender_text  = "🕵️ <b>Anonim</b>"
            display_name = "Anonim"
        else:
            uname        = f" @{username}" if username else ""
            sender_text  = f"👤 {sender_name}{uname}"
            display_name = sender_name + (f" @{username}" if username else "")

        type_emoji = "💡" if msg_type == "taklif" else "⚠️"
        type_label = "TAKLIF" if msg_type == "taklif" else "SHIKOYAT"

        tg_message = (
            f"{type_emoji} <b>{type_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>Filial:</b> {filial}\n"
            f"{sender_text}\n"
            f"📅 <b>Vaqt:</b> {time_str}\n"
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

        msg_id = str(int(time_module.time() * 1000))
        save_message({
            "id":     msg_id,
            "type":   msg_type,
            "filial": filial,
            "text":   text,
            "anon":   anon,
            "time":   time_str,
            "sender": display_name,
            "status": "new"
        })

        logger.info(f"Yangi {type_label}: {filial} — {'anonim' if anon else sender_name}")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error(f"/send xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/messages", methods=["GET", "OPTIONS"])
def get_messages():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    return jsonify({"ok": True, "messages": load_messages()})


@flask_app.route("/delete", methods=["POST", "OPTIONS"])
def delete_message():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data   = request.get_json(force=True)
        msg_id = data.get('id')
        if not msg_id:
            return jsonify({"ok": False, "error": "id kerak"}), 400
        found = delete_message_db(msg_id)
        if not found:
            return jsonify({"ok": False, "error": "Xabar topilmadi"}), 404
        logger.info(f"Xabar o'chirildi: {msg_id}")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"/delete xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/status", methods=["POST", "OPTIONS"])
def update_status():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data       = request.get_json(force=True)
        msg_id     = data.get('id')
        new_status = data.get('status')

        if not msg_id or not new_status:
            return jsonify({"ok": False, "error": "id va status kerak"}), 400

        if new_status not in ["new", "progress", "in_progress", "done"]:
            return jsonify({"ok": False, "error": "Noto'g'ri status"}), 400

        found = update_status_db(msg_id, new_status)
        if not found:
            return jsonify({"ok": False, "error": "Xabar topilmadi"}), 404
        return jsonify({"ok": True})

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

    init_db()

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

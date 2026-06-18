"""
Taklif & Shikoyat Telegram Bot
Railway: Webhook + Flask + PostgreSQL (Supabase) + Google Sheets
Ikki tilli: O'zbek / Rus
"""

import json
import logging
import os
import asyncio
import threading
import time as time_module
import psycopg2
import psycopg2.extras
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# =====================================================
BOT_TOKEN      = "8919742379:AAG_mBtlsxU4DluKoeXUvCfn2mscdZ1pP1M"
ADMINS = {
    7780854728: "superadmin",
    1488298476: "superadmin",
    555648201:  "moderator",
}
ADMIN_IDS     = list(ADMINS.keys())
ADMIN_CHAT_ID = ADMIN_IDS[0]
MINI_APP_URL   = "https://karimov0814.github.io/feedback-bot/index.html"
PORT           = int(os.environ.get("PORT", 5000))
WEBHOOK_URL    = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
DATABASE_URL   = os.environ.get("DATABASE_URL", "")
SHEET_ID       = "13Dy2zeKPn4dmEHLKsjvtTi5qj4_X8aEefhJYrOpV2wI"

def _build_google_creds():
    raw_key = os.environ.get("GOOGLE_PRIVATE_KEY", "")
    if "\\n" in raw_key:
        raw_key = raw_key.replace("\\n", "\n")
    return {
        "type": "service_account",
        "project_id": "feedback-bot-499705",
        "private_key_id": "4e34a0b830ead7e9f6995dd6efe1b6f9e12f8c51",
        "private_key": raw_key,
        "client_email": "feedback-bot@feedback-bot-499705.iam.gserviceaccount.com",
        "client_id": "103704228425417838636",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/feedback-bot%40feedback-bot-499705.iam.gserviceaccount.com",
        "universe_domain": "googleapis.com"
    }

GOOGLE_CREDS = _build_google_creds()
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

# notif_id -> { admin_id: message_id, ... } — "Hammadan o'chirish" uchun
SENT_NOTIFICATIONS = {}


# ==================== GOOGLE SHEETS ====================

SHEET_HEADERS = [
    "ID", "Tur / Тип", "Filial / Филиал",
    "Matn (UZ) / Текст (UZ)", "Matn (RU) / Текст (RU)",
    "Yuboruvchi / Отправитель", "Anonim / Анонимно",
    "Til / Язык", "Vaqt / Время", "Status"
]

def get_sheet():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1

        if sheet.row_count == 0 or sheet.cell(1, 1).value != "ID":
            sheet.insert_row(SHEET_HEADERS, index=1)
        elif sheet.cell(1, 4).value != "Matn (UZ) / Текст (UZ)":
            sheet.update('A1', [SHEET_HEADERS])

        return sheet
    except Exception as e:
        logger.error(f"get_sheet xatolik: {e}")
        return None

def append_to_sheet(msg):
    try:
        sheet = get_sheet()
        if not sheet:
            return

        msg_type = msg.get("type", "")
        lang = msg.get("lang", "uz")
        is_anon = msg.get("anon", False)

        if msg_type == "taklif":
            type_label = "Taklif / Предложение"
        else:
            type_label = "Shikoyat / Жалоба"

        anon_label = "Ha / Да" if is_anon else "Yo'q / Нет"
        lang_label = "O'zbek" if lang == "uz" else "Русский"

        status_raw = msg.get("status", "new")
        if status_raw == "new":
            status_label = "Yangi / Новое"
        elif status_raw in ("progress", "in_progress"):
            status_label = "Ko'rib chiqilmoqda / В обработке"
        else:
            status_label = "Yakunlandi / Завершено"

        sheet.append_row([
            msg.get("id", ""),
            type_label,
            msg.get("filial", ""),
            msg.get("text_uz") or msg.get("text", ""),
            msg.get("text_ru") or msg.get("text", ""),
            msg.get("sender", ""),
            anon_label,
            lang_label,
            msg.get("time", ""),
            status_label,
        ])
        logger.info(f"Google Sheets ga yozildi (ikki tilda): {msg.get('id')}")
    except Exception as e:
        logger.error(f"append_to_sheet xatolik: {e}")


# ==================== DATABASE ====================

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                type TEXT,
                filial TEXT,
                text TEXT,
                text_uz TEXT,
                text_ru TEXT,
                lang TEXT DEFAULT 'uz',
                anon BOOLEAN DEFAULT FALSE,
                time TEXT,
                sender TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        for col, col_type, default in [
            ("text_uz", "TEXT", "''"),
            ("text_ru", "TEXT", "''"),
            ("lang", "TEXT", "'uz'"),
        ]:
            try:
                cur.execute(f"ALTER TABLE messages ADD COLUMN IF NOT EXISTS {col} {col_type} DEFAULT {default}")
            except Exception:
                pass

        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database tayyor (ikki tilli)")
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
            INSERT INTO messages (id, type, filial, text, text_uz, text_ru, lang, anon, time, sender, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            msg['id'], msg['type'], msg['filial'],
            msg['text'],
            msg.get('text_uz', msg['text']),
            msg.get('text_ru', msg['text']),
            msg.get('lang', 'uz'),
            msg['anon'],
            msg['time'], msg['sender'],
            msg.get('status', 'new')
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
    is_admin = (chat_id in ADMIN_IDS)

    if is_admin:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Admin Panel", web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1"))
        ]])
        await update.message.reply_text(
            "👋 Salom, Admin!\n\nXodimlarning murojaatlari shu yerga keladi.",
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Taklif yoki Shikoyat yuborish", web_app=WebAppInfo(url=MINI_APP_URL))
        ]])
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n"
            f"💡 <b>Taklif</b> — g'oyalaringizni yuboring\n"
            f"⚠️ <b>Shikoyat</b> — muammolaringizni bildiring\n\n"
            f"🔒 Anonim yuborish imkoniyati mavjud.\n\n"
            f"👋 Привет, {user.first_name}!\n\n"
            f"💡 <b>Предложение</b> — поделитесь идеей\n"
            f"⚠️ <b>Жалоба</b> — сообщите о проблеме\n\n"
            f"🔒 Доступна анонимная отправка.\n\n"
            f"Boshlash / Начать 👇",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Eski (sendData) yo'l — endi asosiy oqim /send orqali ketadi, lekin fallback sifatida qoldiramiz."""
    try:
        raw = update.message.web_app_data.data
        data = json.loads(raw)
        msg_type = data.get('type', '')
        filial   = data.get('filial', '')

        type_label_uz = "SHIKOYAT" if msg_type == "shikoyat" else "TAKLIF"
        emoji = "⚠️" if msg_type == "shikoyat" else "💡"
        notif = f"{emoji} Yangi {type_label_uz} — {filial} filialidan"

        admin_panel_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Admin Panel", web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1"))
        ]])

        for _aid in ADMIN_IDS:
            await context.bot.send_message(chat_id=_aid, text=notif, reply_markup=admin_panel_kb)
        await update.message.reply_text("✅ Murojaatingiz yuborildi! / Ваше обращение отправлено!\n\nRahmat / Спасибо!")

    except Exception as e:
        logger.error(f"web_app_data xatolik: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. / Произошла ошибка.")


async def delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'🗑 Hammadan o'chirish' tugmasi — faqat superadmin uchun ishlaydi."""
    query = update.callback_query
    user_id = query.from_user.id

    if ADMINS.get(user_id) != "superadmin":
        await query.answer("Sizda bu amal uchun ruxsat yo'q", show_alert=True)
        return

    notif_id = query.data.split(":", 1)[1]
    sent_ids = SENT_NOTIFICATIONS.get(notif_id)

    if not sent_ids:
        await query.answer("Xabar topilmadi yoki muddati o'tgan", show_alert=True)
        return

    for _aid, _mid in sent_ids.items():
        try:
            await context.bot.delete_message(chat_id=_aid, message_id=_mid)
        except Exception as e:
            logger.error(f"delete_all_callback: {_aid} uchun o'chirib bo'lmadi: {e}")

    SENT_NOTIFICATIONS.pop(notif_id, None)
    await query.answer("Hammadan o'chirildi ✅")


# ==================== FLASK ROUTES ====================

@flask_app.route("/", methods=["GET"])
def index():
    return "Bot ishlayapti! / Бот работает! ✅", 200

@flask_app.route("/role/<int:telegram_id>", methods=["GET"])
def get_role(telegram_id):
    role = ADMINS.get(telegram_id)
    if role:
        return jsonify({"ok": True, "role": role, "is_admin": True})
    return jsonify({"ok": True, "role": "user", "is_admin": False})

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
        text_uz     = data.get('text_uz', text)
        text_ru     = data.get('text_ru', text)
        lang        = data.get('lang', 'uz')
        anon        = data.get('anon', False)
        time_str    = data.get('time', '')
        sender_name = data.get('sender_name', "Noma'lum / Неизвестно")
        username    = data.get('username', '')

        uname     = f" @{username}" if username else ""
        real_name = sender_name + uname

        if anon:
            display_name = "Anonim"
        else:
            display_name = real_name

        # ===== QISQA BILDIRISHNOMA (Telegram chatga shu boriladi) =====
        # Format: "⚠️ Yangi SHIKOYAT — Anhor filialidan"
        type_label_uz = "SHIKOYAT" if msg_type == "shikoyat" else "TAKLIF"
        emoji = "⚠️" if msg_type == "shikoyat" else "💡"
        notif_text = f"{emoji} Yangi {type_label_uz} — {filial} filialidan"

        msg_id = str(int(time_module.time() * 1000))

        # Har bir admin uchun rolga mos klaviatura va yuborilgan message_id larni saqlash
        sent_ids = {}
        for _aid in ADMIN_IDS:
            buttons = [InlineKeyboardButton("📊 Admin Panel", web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1"))]
            if ADMINS.get(_aid) == "superadmin":
                buttons.append(InlineKeyboardButton("🗑 Hammadan o'chirish", callback_data=f"delall:{msg_id}"))
            kb = InlineKeyboardMarkup([buttons])

            future = asyncio.run_coroutine_threadsafe(
                ptb_app.bot.send_message(chat_id=_aid, text=notif_text, reply_markup=kb),
                loop
            )
            sent_msg = future.result(timeout=10)
            sent_ids[_aid] = sent_msg.message_id

        SENT_NOTIFICATIONS[msg_id] = sent_ids

        # To'liq ma'lumot — faqat DB ga (Mini App buni o'qiydi)
        msg = {
            "id":      msg_id,
            "type":    msg_type,
            "filial":  filial,
            "text":    text,
            "text_uz": text_uz,
            "text_ru": text_ru,
            "lang":    lang,
            "anon":    anon,
            "time":    time_str,
            "sender":  display_name,
            "status":  "new"
        }
        save_message(msg)

        # Google Sheets ga — har doim to'liq ism + ikki tilda matn
        sheet_msg = {
            "id":      msg_id,
            "type":    msg_type,
            "filial":  filial,
            "text":    text,
            "text_uz": text_uz,
            "text_ru": text_ru,
            "lang":    lang,
            "anon":    anon,
            "time":    time_str,
            "sender":  real_name,
            "status":  "new"
        }
        threading.Thread(target=append_to_sheet, args=(sheet_msg,), daemon=True).start()

        logger.info(f"Yangi {msg_type}: {filial} — {'anonim' if anon else sender_name} [{lang}]")
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
    ptb_app.add_handler(CallbackQueryHandler(delete_all_callback, pattern=r"^delall:"))

    loop.run_until_complete(ptb_app.initialize())
    loop.run_until_complete(setup_webhook())

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    logger.info(f"✅ Flask server port {PORT} da ishga tushdi!")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    run()

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
import base64
import urllib.request
from io import BytesIO
import psycopg2
import psycopg2.extras
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request, jsonify, Response
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
    "ID", "Tur / Тип", "Kimdan / От кого", "Filial / Филиал",
    "Matn (UZ) / Текст (UZ)", "Matn (RU) / Текст (RU)",
    "Yuboruvchi / Отправитель", "Telefon / Телефон", "Anonim / Анонимно",
    "Til / Язык", "Vaqt / Время", "Status", "Rasm / Фото"
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
        elif sheet.cell(1, 3).value != "Kimdan / От кого":
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
        user_type = msg.get("user_type", "employee")

        if msg_type == "taklif":
            type_label = "Taklif / Предложение"
        else:
            type_label = "Shikoyat / Жалоба"

        kimdan_label = "Mehmon / Гость" if user_type == "guest" else "Xodim / Сотрудник"
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
            kimdan_label,
            msg.get("filial", ""),
            msg.get("text_uz") or msg.get("text", ""),
            msg.get("text_ru") or msg.get("text", ""),
            msg.get("sender", ""),
            msg.get("phone", "") or "",
            anon_label,
            lang_label,
            msg.get("time", ""),
            status_label,
            msg.get("photo_url", "") or "",
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
            ("user_type", "TEXT", "'employee'"),
            ("phone", "TEXT", "''"),
            ("photo_file_id", "TEXT", "''"),
            ("photo_url", "TEXT", "''"),
        ]:
            try:
                cur.execute(f"ALTER TABLE messages ADD COLUMN IF NOT EXISTS {col} {col_type} DEFAULT {default}")
            except Exception:
                pass

        try:
            cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS user_id BIGINT")
        except Exception:
            pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                user_id BIGINT PRIMARY KEY,
                phone TEXT,
                first_name TEXT,
                last_name TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id SERIAL PRIMARY KEY,
                message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
                admin_id BIGINT,
                admin_name TEXT,
                text TEXT,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_replies_message_id ON replies(message_id)")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database tayyor (ikki tilli, javoblar bilan)")
        init_chat_table()
    except Exception as e:
        logger.error(f"init_db xatolik: {e}")

def load_messages():
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT m.*,
                   COALESCE((SELECT COUNT(*) FROM replies r WHERE r.message_id = m.id), 0) AS reply_count
            FROM messages m
            ORDER BY m.created_at DESC LIMIT 500
        """)
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
            INSERT INTO messages (id, type, filial, text, text_uz, text_ru, lang, anon, time, sender, status, user_id,
                                   user_type, phone, photo_file_id, photo_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            msg['id'], msg['type'], msg['filial'],
            msg['text'],
            msg.get('text_uz', msg['text']),
            msg.get('text_ru', msg['text']),
            msg.get('lang', 'uz'),
            msg['anon'],
            msg['time'], msg['sender'],
            msg.get('status', 'new'),
            msg.get('user_id'),
            msg.get('user_type', 'employee'),
            msg.get('phone', '') or '',
            msg.get('photo_file_id', '') or '',
            msg.get('photo_url', '') or '',
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"save_message xatolik: {e}")

def save_contact(user_id, phone, first_name='', last_name=''):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO contacts (user_id, phone, first_name, last_name, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                phone = EXCLUDED.phone,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                updated_at = NOW()
        """, (user_id, phone, first_name, last_name))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"save_contact xatolik: {e}")

def get_contact_phone(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT phone FROM contacts WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else ''
    except Exception as e:
        logger.error(f"get_contact_phone xatolik: {e}")
        return ''

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


# ==================== JAVOBLAR (REPLIES) ====================

def save_reply(message_id, admin_id, admin_name, text):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO replies (message_id, admin_id, admin_name, text)
            VALUES (%s, %s, %s, %s)
            RETURNING id, message_id, admin_id, admin_name, text, created_at, is_read
        """, (message_id, admin_id, admin_name, text))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"save_reply xatolik: {e}")
        return None

def get_message_owner(message_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, user_id, type, filial FROM messages WHERE id = %s", (message_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_message_owner xatolik: {e}")
        return None

def load_my_messages(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM messages WHERE user_id = %s ORDER BY created_at DESC LIMIT 200", (user_id,))
        msgs = [dict(r) for r in cur.fetchall()]

        if msgs:
            ids = [m['id'] for m in msgs]
            cur.execute("SELECT * FROM replies WHERE message_id = ANY(%s) ORDER BY created_at ASC", (ids,))
            replies = [dict(r) for r in cur.fetchall()]
        else:
            replies = []

        cur.close()
        conn.close()

        by_msg = {}
        for r in replies:
            by_msg.setdefault(r['message_id'], []).append(r)

        for m in msgs:
            m_replies = by_msg.get(m['id'], [])
            m['replies'] = m_replies
            m['reply_count'] = len(m_replies)
            m['unread_count'] = sum(1 for r in m_replies if not r['is_read'])

        return msgs
    except Exception as e:
        logger.error(f"load_my_messages xatolik: {e}")
        return []

def mark_replies_read(message_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE replies SET is_read = TRUE WHERE message_id = %s AND is_read = FALSE", (message_id,))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return affected
    except Exception as e:
        logger.error(f"mark_replies_read xatolik: {e}")
        return 0


# ==================== CHAT ====================

def init_chat_table():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
                sender_type TEXT NOT NULL,  -- 'admin' or 'employee'
                sender_id BIGINT,
                sender_name TEXT,
                text TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chats_message_id ON chats(message_id)")
        try:
            cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS chat_closed BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Chat jadvali tayyor")
    except Exception as e:
        logger.error(f"init_chat_table xatolik: {e}")

def get_chat_messages(message_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, message_id, sender_type, sender_id, sender_name, text, is_read,
                   TO_CHAR(created_at, 'DD.MM.YYYY HH24:MI') as created_at
            FROM chats WHERE message_id = %s ORDER BY created_at ASC
        """, (message_id,))
        rows = [dict(r) for r in cur.fetchall()]
        # Also get chat_closed status
        cur.execute("SELECT chat_closed FROM messages WHERE id = %s", (message_id,))
        msg = cur.fetchone()
        cur.close()
        conn.close()
        return rows, (msg['chat_closed'] if msg else False)
    except Exception as e:
        logger.error(f"get_chat_messages xatolik: {e}")
        return [], False

def save_chat_message(message_id, sender_type, sender_id, sender_name, text):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO chats (message_id, sender_type, sender_id, sender_name, text)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, message_id, sender_type, sender_id, sender_name, text, created_at
        """, (message_id, sender_type, sender_id, sender_name, text))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"save_chat_message xatolik: {e}")
        return None

def set_chat_closed(message_id, closed):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE messages SET chat_closed = %s WHERE id = %s", (closed, message_id))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"set_chat_closed xatolik: {e}")
        return False

def get_chat_unread_count(message_id, for_admin=False):
    """Count unread messages for the OTHER side"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        sender_type = 'employee' if for_admin else 'admin'
        cur.execute(
            "SELECT COUNT(*) FROM chats WHERE message_id = %s AND sender_type = %s AND is_read = FALSE",
            (message_id, sender_type)
        )
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        logger.error(f"get_chat_unread_count xatolik: {e}")
        return 0

def mark_chat_read(message_id, reader_type):
    """Mark messages sent by OTHER side as read"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        other = 'employee' if reader_type == 'admin' else 'admin'
        cur.execute(
            "UPDATE chats SET is_read = TRUE WHERE message_id = %s AND sender_type = %s AND is_read = FALSE",
            (message_id, other)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"mark_chat_read xatolik: {e}")


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


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mini App'dagi 'Raqamni ulashish' tugmasi bosilganda Telegram avtomatik shu yerga contact yuboradi."""
    try:
        contact = update.message.contact
        if not contact:
            return
        user_id = update.effective_user.id
        # Foydalanuvchi faqat OʻZINING raqamini ulashishi kerak
        if contact.user_id and contact.user_id != user_id:
            return
        save_contact(user_id, contact.phone_number, contact.first_name or '', contact.last_name or '')
        await update.message.reply_text(
            "✅ Telefon raqamingiz uchun rahmat! Endi murojaatingizni Mini App orqali yuborishingiz mumkin.\n\n"
            "✅ Спасибо за номер телефона! Теперь вы можете отправить обращение через Mini App."
        )
    except Exception as e:
        logger.error(f"contact_received xatolik: {e}")


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

@flask_app.route("/contact/<int:user_id>", methods=["GET"])
def contact_check(user_id):
    phone = get_contact_phone(user_id)
    return jsonify({"ok": True, "phone": phone})

@flask_app.route("/photo/<message_id>", methods=["GET"])
def get_photo(message_id):
    """Rasmni Telegramdan olib, to'g'ridan-to'g'ri frontendga uzatadi.
    Bot tokeni hech qachon brauzerga chiqmaydi — hammasi server ichida bajariladi."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT photo_file_id FROM messages WHERE id = %s", (message_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row or not row[0]:
            return jsonify({"ok": False, "error": "Rasm topilmadi"}), 404

        file_id = row[0]
        file_future = asyncio.run_coroutine_threadsafe(ptb_app.bot.get_file(file_id), loop)
        tg_file = file_future.result(timeout=15)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{tg_file.file_path}"

        with urllib.request.urlopen(file_url, timeout=15) as resp:
            img_bytes = resp.read()

        return Response(img_bytes, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    except Exception as e:
        logger.error(f"/photo xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

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
        tg_user_id  = data.get('user_id') or None
        user_type   = data.get('user_type') or 'employee'
        if user_type not in ('employee', 'guest'):
            user_type = 'employee'
        photo_b64   = data.get('photo_base64')

        uname     = f" @{username}" if username else ""
        real_name = sender_name + uname

        if anon:
            display_name = "Anonim"
        else:
            display_name = real_name

        # Mehmon bo'lsa — Telegram orqali ulashilgan telefon raqamini bazadan olamiz
        phone = ''
        if user_type == 'guest' and tg_user_id:
            phone = get_contact_phone(int(tg_user_id))

        # Rasm bo'lsa — base64 dan decode qilamiz
        photo_bytes = None
        if photo_b64:
            try:
                if ',' in photo_b64:
                    photo_b64 = photo_b64.split(',', 1)[1]
                photo_bytes = base64.b64decode(photo_b64)
            except Exception as e:
                logger.error(f"photo decode xatolik: {e}")
                photo_bytes = None

        # ===== QISQA BILDIRISHNOMA (Telegram chatga shu boriladi) =====
        # Format: "⚠️ Yangi SHIKOYAT — Anhor filialidan (Mehmon)"
        type_label_uz = "SHIKOYAT" if msg_type == "shikoyat" else "TAKLIF"
        emoji = "⚠️" if msg_type == "shikoyat" else "💡"
        kimdan_lbl = "Mehmon" if user_type == "guest" else "Xodim"
        notif_text = f"{emoji} Yangi {type_label_uz} — {filial} filialidan ({kimdan_lbl})"

        msg_id = str(int(time_module.time() * 1000))

        # Har bir admin uchun rolga mos klaviatura va yuborilgan message_id larni saqlash
        sent_ids = {}
        photo_file_id = None
        for _aid in ADMIN_IDS:
            buttons = [InlineKeyboardButton("📊 Admin Panel", web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1"))]
            if ADMINS.get(_aid) == "superadmin":
                buttons.append(InlineKeyboardButton("🗑 Hammadan o'chirish", callback_data=f"delall:{msg_id}"))
            kb = InlineKeyboardMarkup([buttons])

            try:
                if photo_bytes:
                    if photo_file_id:
                        photo_to_send = photo_file_id
                    else:
                        bio = BytesIO(photo_bytes)
                        bio.name = 'photo.jpg'
                        photo_to_send = bio
                    future = asyncio.run_coroutine_threadsafe(
                        ptb_app.bot.send_photo(chat_id=_aid, photo=photo_to_send, caption=notif_text, reply_markup=kb),
                        loop
                    )
                    sent_msg = future.result(timeout=20)
                    if not photo_file_id:
                        photo_file_id = sent_msg.photo[-1].file_id
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        ptb_app.bot.send_message(chat_id=_aid, text=notif_text, reply_markup=kb),
                        loop
                    )
                    sent_msg = future.result(timeout=10)
                sent_ids[_aid] = sent_msg.message_id
            except Exception as e:
                logger.error(f"admin bildirishnoma xatolik ({_aid}): {e}")

        SENT_NOTIFICATIONS[msg_id] = sent_ids

        # Rasm uchun havola — bot tokenini frontendga chiqarmaslik uchun
        # o'z serverimizdagi proxy manzilidan foydalanamiz (/photo/<id>)
        photo_url = ''
        if photo_file_id and WEBHOOK_URL:
            photo_url = f"https://{WEBHOOK_URL}/photo/{msg_id}"

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
            "status":  "new",
            "user_id": tg_user_id,
            "user_type": user_type,
            "phone": phone,
            "photo_file_id": photo_file_id or '',
            "photo_url": photo_url,
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
            "status":  "new",
            "user_type": user_type,
            "phone": phone,
            "photo_url": photo_url,
        }
        threading.Thread(target=append_to_sheet, args=(sheet_msg,), daemon=True).start()

        logger.info(f"Yangi {msg_type} [{kimdan_lbl}]: {filial} — {'anonim' if anon else sender_name} [{lang}]")
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


@flask_app.route("/my-messages/<int:user_id>", methods=["GET", "OPTIONS"])
def my_messages(user_id):
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    return jsonify({"ok": True, "messages": load_my_messages(user_id)})


@flask_app.route("/reply", methods=["POST", "OPTIONS"])
def reply_message():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True)
        message_id = data.get('message_id')
        admin_id   = data.get('admin_id')
        admin_name = (data.get('admin_name') or 'Admin').strip()
        text       = (data.get('text') or '').strip()

        if not message_id or not text:
            return jsonify({"ok": False, "error": "message_id va text kerak"}), 400
        if ADMINS.get(admin_id) is None:
            return jsonify({"ok": False, "error": "Sizda bu amal uchun ruxsat yo'q"}), 403

        reply = save_reply(message_id, admin_id, admin_name, text)
        if not reply:
            return jsonify({"ok": False, "error": "Javob saqlanmadi"}), 500

        owner = get_message_owner(message_id)
        if owner and owner.get('user_id'):
            try:
                type_label_uz = "SHIKOYAT" if owner.get('type') == 'shikoyat' else "TAKLIF"
                notif = (
                    f"✉️ Sizning {type_label_uz}ingizga javob keldi!\n\n"
                    f"💬 {text}\n\n"
                    f"Ko'rish uchun pastdagi tugmani bosing 👇"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Mening murojaatlarim", web_app=WebAppInfo(url=MINI_APP_URL + "?view=my"))
                ]])
                future = asyncio.run_coroutine_threadsafe(
                    ptb_app.bot.send_message(chat_id=owner['user_id'], text=notif, reply_markup=kb),
                    loop
                )
                future.result(timeout=10)
            except Exception as e:
                logger.error(f"reply notif xatolik: {e}")

        logger.info(f"Javob yozildi: {message_id} <- admin {admin_id}")
        return jsonify({"ok": True, "reply": reply})

    except Exception as e:
        logger.error(f"/reply xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/reply/read", methods=["POST", "OPTIONS"])
def reply_read():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data       = request.get_json(force=True)
        message_id = data.get('message_id')
        if not message_id:
            return jsonify({"ok": False, "error": "message_id kerak"}), 400
        mark_replies_read(message_id)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"/reply/read xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ==================== CHAT ROUTES ====================

@flask_app.route("/chat/messages/<message_id>", methods=["GET", "OPTIONS"])
def chat_get(message_id):
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    reader = request.args.get('reader', 'employee')  # 'admin' or 'employee'
    mark_chat_read(message_id, reader)
    msgs, closed = get_chat_messages(message_id)
    return jsonify({"ok": True, "messages": msgs, "chat_closed": closed})


@flask_app.route("/chat/send", methods=["POST", "OPTIONS"])
def chat_send():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True)
        message_id  = data.get('message_id')
        sender_type = data.get('sender_type', 'employee')  # 'admin' or 'employee'
        sender_id   = data.get('sender_id')
        sender_name = (data.get('sender_name') or '').strip() or ('Admin' if sender_type == 'admin' else 'Xodim')
        text        = (data.get('text') or '').strip()

        if not message_id or not text:
            return jsonify({"ok": False, "error": "message_id va text kerak"}), 400

        # Check if chat is closed
        _, closed = get_chat_messages.__wrapped__(message_id) if hasattr(get_chat_messages, '__wrapped__') else (None, None)
        # Direct check
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT chat_closed FROM messages WHERE id = %s", (message_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0]:
                return jsonify({"ok": False, "error": "Chat yopilgan"}), 403
        except Exception:
            pass

        # Admin permission check
        if sender_type == 'admin' and sender_id and ADMINS.get(int(sender_id)) is None:
            return jsonify({"ok": False, "error": "Ruxsat yo'q"}), 403

        chat_msg = save_chat_message(message_id, sender_type, sender_id, sender_name, text)
        if not chat_msg:
            return jsonify({"ok": False, "error": "Xabar saqlanmadi"}), 500

        # Notify the other side via Telegram
        owner = get_message_owner(message_id)
        if sender_type == 'admin':
            # Notify employee
            if owner and owner.get('user_id'):
                try:
                    notif = (
                        f"💬 Sizga admin xabar yozdi!\n\n"
                        f"👮 {sender_name}: {text}\n\n"
                        f"Javob berish uchun 👇"
                    )
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("💬 Chatni ochish", web_app=WebAppInfo(url=MINI_APP_URL + "?view=my"))
                    ]])
                    future = asyncio.run_coroutine_threadsafe(
                        ptb_app.bot.send_message(chat_id=owner['user_id'], text=notif, reply_markup=kb),
                        loop
                    )
                    future.result(timeout=10)
                except Exception as e:
                    logger.error(f"chat notify employee xatolik: {e}")
        else:
            # Notify all admins
            if owner:
                type_label = "SHIKOYAT" if owner.get('type') == 'shikoyat' else "TAKLIF"
                notif = (
                    f"💬 {sender_name} chat orqali xabar yozdi!\n"
                    f"({type_label} — {owner.get('filial','')}) \n\n"
                    f"📝 {text}"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 Admin Panel", web_app=WebAppInfo(url=MINI_APP_URL + "?admin=1"))
                ]])
                for _aid in ADMIN_IDS:
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            ptb_app.bot.send_message(chat_id=_aid, text=notif, reply_markup=kb),
                            loop
                        )
                        future.result(timeout=10)
                    except Exception as e:
                        logger.error(f"chat notify admin {_aid} xatolik: {e}")

        return jsonify({"ok": True, "chat_message": chat_msg})
    except Exception as e:
        logger.error(f"/chat/send xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/chat/close", methods=["POST", "OPTIONS"])
def chat_close():
    if request.method == "OPTIONS":
        return jsonify({"ok": True}), 200
    try:
        data = request.get_json(force=True)
        message_id = data.get('message_id')
        admin_id   = data.get('admin_id')
        closed     = data.get('closed', True)

        if not message_id:
            return jsonify({"ok": False, "error": "message_id kerak"}), 400
        if not admin_id or ADMINS.get(int(admin_id)) is None:
            return jsonify({"ok": False, "error": "Faqat adminlar chatni yopa oladi"}), 403

        set_chat_closed(message_id, closed)

        # Notify employee
        owner = get_message_owner(message_id)
        if owner and owner.get('user_id'):
            try:
                if closed:
                    notif = "🔒 Sizning murojaatingiz bo'yicha chat yopildi.\n\nAdmin bilan muloqot yakunlandi."
                else:
                    notif = "🔓 Sizning murojaatingiz bo'yicha chat qayta ochildi."
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Mening murojaatlarim", web_app=WebAppInfo(url=MINI_APP_URL + "?view=my"))
                ]])
                future = asyncio.run_coroutine_threadsafe(
                    ptb_app.bot.send_message(chat_id=owner['user_id'], text=notif, reply_markup=kb),
                    loop
                )
                future.result(timeout=10)
            except Exception as e:
                logger.error(f"chat close notify xatolik: {e}")

        action = "yopildi" if closed else "ochildi"
        logger.info(f"Chat {action}: {message_id} by admin {admin_id}")
        return jsonify({"ok": True, "chat_closed": closed})
    except Exception as e:
        logger.error(f"/chat/close xatolik: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@flask_app.route("/chat/unread/<int:user_id>", methods=["GET"])
def chat_unread_total(user_id):
    """Get total unread chat messages for an employee"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM chats c
            JOIN messages m ON c.message_id = m.id
            WHERE m.user_id = %s AND c.sender_type = 'admin' AND c.is_read = FALSE
        """, (user_id,))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"ok": True, "unread": count})
    except Exception as e:
        return jsonify({"ok": False, "unread": 0})


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
    ptb_app.add_handler(MessageHandler(filters.CONTACT, contact_received))
    ptb_app.add_handler(CallbackQueryHandler(delete_all_callback, pattern=r"^delall:"))

    loop.run_until_complete(ptb_app.initialize())
    loop.run_until_complete(setup_webhook())

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    logger.info(f"✅ Flask server port {PORT} da ishga tushdi!")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    run()

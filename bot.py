import os
import json
import string
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import firebase_admin
from firebase_admin import credentials, firestore


# ============================================================
# HEALTH CHECK SERVER (Koyeb/Render uchun)
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.getenv("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# .env fayldan o'qish
load_dotenv()

# ============================================================
# SOZLAMALAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ADMIN_IDS = [6768934631]

PROMO_COIN_AMOUNT = 5


# ============================================================
# FIREBASE INIT
# ============================================================

firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")
if firebase_creds_json:
    cred_dict = json.loads(firebase_creds_json)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("service_account.json")

firebase_admin.initialize_app(cred)
db = firestore.client()


# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

def generate_promo_code(length=8):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        doc = db.collection('promo_codes').document(code).get()
        if not doc.exists:
            return code


def get_channels():
    try:
        doc = db.collection('bot_config').document('channels').get()
        if doc.exists:
            return doc.to_dict().get('list', [])
    except Exception as e:
        print(f"Kanallarni olishda xato: {e}")
    return []


def get_task_version():
    try:
        doc = db.collection('bot_config').document('settings').get()
        if doc.exists:
            return doc.to_dict().get('task_version', 1)
    except Exception as e:
        print(f"Versiya olishda xato: {e}")
    return 1


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ============================================================
# USER HANDLERLARI
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        print(f"[START] User: {user.id} - {user.first_name} - Admin: {is_admin(user.id)}")

        # Admin bo'lsa - admin panel ko'rsatish
        if is_admin(user.id):
            await show_admin_panel(update, context)
            return

        # Oddiy user - vazifalarni ko'rsatish
        await show_tasks(update, context)
    except Exception as e:
        print(f"[START ERROR] {e}")
        try:
            await update.message.reply_text("Xatolik yuz berdi. Qayta /start bosing.")
        except:
            pass


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oddiy userga vazifalarni ko'rsatish"""
    try:
        user = update.effective_user
        channels = get_channels()
        task_version = get_task_version()

        print(f"[TASKS] User: {user.id}, Channels: {len(channels)}, Version: {task_version}")

        # Foydalanuvchi allaqachon bajarganmi
        try:
            user_doc = db.collection('bot_users').document(str(user.id)).get()
            if user_doc.exists:
                data = user_doc.to_dict()
                if data.get('completed_version') == task_version:
                    await update.message.reply_text(
                        f"Siz barcha vazifalarni bajargansiz!\n\n"
                        f"Promo kodingiz: `{data.get('last_code', 'N/A')}`\n\n"
                        f"Bu kodni TDM Training ilovasiga kiriting va coin oling!",
                        parse_mode='Markdown'
                    )
                    return
        except Exception as e:
            print(f"[TASKS] User doc xato: {e}")

        if not channels:
            await update.message.reply_text(
                f"Salom, {user.first_name}!\n\n"
                f"Hozircha vazifalar yo'q.\n"
                f"Keyinroq qaytib keling!"
            )
            return

        text = (
            f"Salom, {user.first_name}!\n\n"
            f"Quyidagi kanallarga obuna bo'ling va\n"
            f"promo kod oling!\n\n"
            f"Kanallar soni: {len(channels)}\n"
            f"Mukofot: {PROMO_COIN_AMOUNT} coin"
        )

        keyboard = []
        for i, ch in enumerate(channels, 1):
            url = ch.get('url', '').strip()
            # URL ni tekshirish - faqat http/https bilan boshlanishi kerak
            if not url.startswith('http'):
                url = 'https://' + url
            ch_type = ch.get('type', 'channel')
            if ch_type == 'request':
                label = f"{i}. {ch['name']} (so'rov)"
            elif ch_type == 'link':
                label = f"{i}. {ch['name']}"
            else:
                label = f"{i}. {ch['name']}"
            try:
                keyboard.append([InlineKeyboardButton(label, url=url)])
            except Exception as e:
                print(f"[TASKS] Tugma yaratishda xato ({url}): {e}")

        keyboard.append([InlineKeyboardButton("Tekshirish", callback_data="check_subs")])

        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"[TASKS ERROR] {e}")
        try:
            await update.message.reply_text(
                f"Salom! Hozircha vazifalar yo'q.\nKeyinroq qaytib keling!"
            )
        except:
            pass


async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    channels = get_channels()
    task_version = get_task_version()

    # Allaqachon bajarganmi
    try:
        user_doc = db.collection('bot_users').document(str(user.id)).get()
        if user_doc.exists and user_doc.to_dict().get('completed_version') == task_version:
            code = user_doc.to_dict().get('last_code')
            await query.message.reply_text(
                f"Siz allaqachon bajargansiz!\n\n"
                f"Promo kodingiz: `{code}`",
                parse_mode='Markdown'
            )
            return
    except Exception as e:
        print(f"Tekshirishda xato: {e}")

    if not channels:
        await query.message.reply_text("Hozircha vazifalar yo'q.")
        return

    # Har bir kanalni tekshirish
    not_subscribed = []
    for ch in channels:
        ch_type = ch.get('type', 'channel')

        # link va request turini tekshirmaymiz
        if ch_type in ['link', 'request']:
            continue

        try:
            member = await context.bot.get_chat_member(ch['id'], user.id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch['name'])
        except Exception as e:
            print(f"Kanal tekshirishda xato ({ch['id']}): {e}")
            not_subscribed.append(ch['name'])

    if not_subscribed:
        text = "Siz quyidagi kanallarga obuna emassiz:\n\n"
        for name in not_subscribed:
            text += f"  - {name}\n"
        text += "\nObuna bo'ling va qayta tekshiring!"

        keyboard = []
        for i, ch in enumerate(channels, 1):
            url = ch.get('url', '').strip()
            if not url.startswith('http'):
                url = 'https://' + url
            ch_type = ch.get('type', 'channel')
            if ch_type == 'request':
                label = f"{i}. {ch['name']} (so'rov)"
            elif ch_type == 'link':
                label = f"{i}. {ch['name']}"
            else:
                label = f"{i}. {ch['name']}"
            try:
                keyboard.append([InlineKeyboardButton(label, url=url)])
            except Exception:
                pass
        keyboard.append([InlineKeyboardButton("Tekshirish", callback_data="check_subs")])

        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Hammasi OK - promo kod berish
    try:
        code = generate_promo_code()

        db.collection('promo_codes').document(code).set({
            'code': code,
            'telegram_uid': str(user.id),
            'telegram_name': user.full_name,
            'used': False,
            'used_by': None,
            'coins': PROMO_COIN_AMOUNT,
            'created_at': firestore.SERVER_TIMESTAMP,
            'task_version': task_version,
        })

        db.collection('bot_users').document(str(user.id)).set({
            'telegram_uid': str(user.id),
            'telegram_name': user.full_name,
            'completed_version': task_version,
            'last_code': code,
            'updated_at': firestore.SERVER_TIMESTAMP,
        }, merge=True)

        await query.message.reply_text(
            f"Tabriklaymiz! Barcha vazifalar bajarildi!\n\n"
            f"Promo kodingiz: `{code}`\n\n"
            f"Bu kodni TDM Training ilovasiga kiriting va {PROMO_COIN_AMOUNT} coin oling!",
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"Promo kod yaratishda xato: {e}")
        await query.message.reply_text("Xatolik yuz berdi. Qayta urinib ko'ring: /start")


# ============================================================
# ADMIN PANEL
# ============================================================

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun tugmali panel"""
    text = "Admin Panel\n\nQuyidagi tugmalardan birini tanlang:"

    keyboard = [
        [InlineKeyboardButton("Statistika", callback_data="admin_stats"),
         InlineKeyboardButton("Userlar", callback_data="admin_users")],
        [InlineKeyboardButton("Promo kodlar", callback_data="admin_codes"),
         InlineKeyboardButton("Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("Coin sozlash", callback_data="admin_coins"),
         InlineKeyboardButton("Yangi versiya", callback_data="admin_new_ver")],
        [InlineKeyboardButton("Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("Kanal o'chirish", callback_data="admin_remove_ch")],
        [InlineKeyboardButton("Vazifalarni ko'rish (user ko'rinishi)", callback_data="admin_view_tasks")],
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel tugmalarini boshqarish"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.reply_text("Sizda ruxsat yo'q.")
        return

    data = query.data

    if data == "admin_stats":
        await handle_stats(query)
    elif data == "admin_users":
        await handle_users(query)
    elif data == "admin_codes":
        await handle_codes(query)
    elif data == "admin_channels":
        await handle_channels(query)
    elif data == "admin_coins":
        await handle_coins_info(query)
    elif data == "admin_new_ver":
        await handle_new_version(query)
    elif data == "admin_broadcast":
        await handle_broadcast_info(query)
    elif data == "admin_add_ch":
        await handle_add_channel_info(query)
    elif data == "admin_remove_ch":
        await handle_remove_channel_info(query)
    elif data == "admin_view_tasks":
        await handle_view_tasks(query)
    elif data == "admin_back":
        await handle_back_to_panel(query)
    elif data == "admin_codes_used":
        await handle_codes_filtered(query, 'used')
    elif data == "admin_codes_unused":
        await handle_codes_filtered(query, 'unused')


def back_button():
    return [InlineKeyboardButton("Orqaga", callback_data="admin_back")]


async def handle_back_to_panel(query):
    text = "Admin Panel\n\nQuyidagi tugmalardan birini tanlang:"

    keyboard = [
        [InlineKeyboardButton("Statistika", callback_data="admin_stats"),
         InlineKeyboardButton("Userlar", callback_data="admin_users")],
        [InlineKeyboardButton("Promo kodlar", callback_data="admin_codes"),
         InlineKeyboardButton("Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("Coin sozlash", callback_data="admin_coins"),
         InlineKeyboardButton("Yangi versiya", callback_data="admin_new_ver")],
        [InlineKeyboardButton("Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("Kanal o'chirish", callback_data="admin_remove_ch")],
        [InlineKeyboardButton("Vazifalarni ko'rish (user ko'rinishi)", callback_data="admin_view_tasks")],
    ]

    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_stats(query):
    try:
        total_codes = len(list(db.collection('promo_codes').stream()))
        used_codes = len(list(db.collection('promo_codes').where('used', '==', True).stream()))
        unused_codes = total_codes - used_codes
        total_users = len(list(db.collection('bot_users').stream()))
        channels = get_channels()

        text = (
            f"Statistika\n\n"
            f"Foydalanuvchilar: {total_users}\n"
            f"Promo kodlar: {total_codes}\n"
            f"  - Ishlatilgan: {used_codes}\n"
            f"  - Ishlatilmagan: {unused_codes}\n"
            f"Kanallar: {len(channels)}\n"
            f"Vazifa versiyasi: {get_task_version()}\n"
            f"Coin miqdori: {PROMO_COIN_AMOUNT}"
        )
    except Exception as e:
        text = f"Statistika olishda xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_users(query):
    try:
        users = list(db.collection('bot_users').order_by(
            'updated_at', direction=firestore.Query.DESCENDING
        ).limit(20).stream())

        if not users:
            text = "Foydalanuvchilar yo'q."
        else:
            text = f"Oxirgi {len(users)} ta foydalanuvchi:\n\n"
            for i, u in enumerate(users, 1):
                data = u.to_dict()
                name = data.get('telegram_name', '?')
                uid = data.get('telegram_uid', '?')
                ver = data.get('completed_version', 0)
                text += f"{i}. {name} | ID: {uid} | V{ver}\n"
    except Exception as e:
        text = f"Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_codes(query):
    try:
        total = len(list(db.collection('promo_codes').stream()))
        used = len(list(db.collection('promo_codes').where('used', '==', True).stream()))
        unused = total - used

        text = (
            f"Promo kodlar\n\n"
            f"Jami: {total}\n"
            f"Ishlatilgan: {used}\n"
            f"Ishlatilmagan: {unused}\n\n"
            f"Qaysilarni ko'rmoqchisiz?"
        )
    except Exception as e:
        text = f"Xato: {e}"

    keyboard = [
        [InlineKeyboardButton("Ishlatilganlar", callback_data="admin_codes_used"),
         InlineKeyboardButton("Ishlatilmaganlar", callback_data="admin_codes_unused")],
        back_button()
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_codes_filtered(query, filter_type):
    try:
        if filter_type == 'used':
            codes = list(db.collection('promo_codes').where('used', '==', True).limit(20).stream())
            title = "Ishlatilgan kodlar"
        else:
            codes = list(db.collection('promo_codes').where('used', '==', False).limit(20).stream())
            title = "Ishlatilmagan kodlar"

        if not codes:
            text = f"{title}\n\nKodlar yo'q."
        else:
            text = f"{title} ({len(codes)} ta):\n\n"
            for c in codes:
                data = c.to_dict()
                code = data.get('code', c.id)
                tg_name = data.get('telegram_name', '?')
                text += f"`{code}` - {tg_name}\n"
    except Exception as e:
        text = f"Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def handle_channels(query):
    channels = get_channels()

    if not channels:
        text = "Kanallar ro'yxati bo'sh.\n\nKanal qo'shish uchun pastdagi tugmani bosing."
    else:
        text = f"Kanallar ({len(channels)} ta):\n\n"
        for i, ch in enumerate(channels, 1):
            text += f"{i}. {ch['name']}\n"
            text += f"   Tur: {ch.get('type', 'channel')}\n"
            text += f"   ID: {ch['id']}\n\n"
        text += f"Vazifa versiyasi: {get_task_version()}"

    keyboard = [
        [InlineKeyboardButton("Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("Kanal o'chirish", callback_data="admin_remove_ch")],
        back_button()
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_coins_info(query):
    text = (
        f"Hozirgi coin miqdori: {PROMO_COIN_AMOUNT}\n\n"
        f"O'zgartirish uchun yozing:\n"
        f"/set_coins 10"
    )
    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_new_version(query):
    try:
        version = get_task_version() + 1
        db.collection('bot_config').document('settings').set(
            {'task_version': version}, merge=True
        )
        text = (
            f"Yangi versiya yaratildi: V{version}\n\n"
            f"Endi barcha foydalanuvchilar qayta vazifa bajarib,\n"
            f"yangi promo kod olishlari mumkin."
        )
    except Exception as e:
        text = f"Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_broadcast_info(query):
    text = (
        "Barcha foydalanuvchilarga xabar yuborish:\n\n"
        "Quyidagi formatda yozing:\n"
        "/broadcast Xabar matni shu yerda"
    )
    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_add_channel_info(query):
    text = (
        "Kanal qo'shish:\n\n"
        "Quyidagi formatda yozing:\n\n"
        "Ochiq kanal:\n"
        "/add_channel channel @username Nomi https://t.me/username\n\n"
        "Yopiq guruh:\n"
        "/add_channel request -100xxx Nomi https://t.me/+invite\n\n"
        "Tashqi havola:\n"
        "/add_channel link id Nomi https://link.com"
    )
    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_remove_channel_info(query):
    channels = get_channels()

    if not channels:
        text = "Kanallar ro'yxati bo'sh."
    else:
        text = "Kanalni o'chirish:\n\n"
        for i, ch in enumerate(channels, 1):
            text += f"{i}. {ch['name']} - ID: {ch['id']}\n"
        text += "\nQuyidagi formatda yozing:\n"
        text += "/remove_channel <kanal_id>"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_view_tasks(query):
    """Admin user ko'rinishida vazifalarni ko'radi"""
    channels = get_channels()

    if not channels:
        text = "Hozircha vazifalar yo'q (kanallar qo'shilmagan)."
    else:
        text = (
            f"User ko'rinishi:\n\n"
            f"Kanallar soni: {len(channels)}\n"
            f"Mukofot: {PROMO_COIN_AMOUNT} coin\n\n"
        )
        for i, ch in enumerate(channels, 1):
            text += f"{i}. {ch['name']} ({ch.get('type', 'channel')})\n"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ============================================================
# ADMIN COMMAND HANDLERLARI (buyruqlar orqali)
# ============================================================

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args or len(args) < 4:
        await update.message.reply_text(
            "Format:\n"
            "/add_channel <type> <id> <name> <url>\n\n"
            "Misollar:\n"
            "/add_channel channel @kanal Kanal_Nomi https://t.me/kanal\n"
            "/add_channel request -100123 Guruh https://t.me/+invite\n"
            "/add_channel link inst Instagram https://instagram.com/page"
        )
        return

    ch_type = args[0]
    ch_id = args[1]
    ch_name = args[2].replace('_', ' ')
    ch_url = args[3]

    channels = get_channels()
    channels.append({
        'id': ch_id,
        'name': ch_name,
        'url': ch_url,
        'type': ch_type,
    })

    db.collection('bot_config').document('channels').set({'list': channels})

    version = get_task_version() + 1
    db.collection('bot_config').document('settings').set(
        {'task_version': version}, merge=True
    )

    await update.message.reply_text(
        f"Kanal qo'shildi!\n\n"
        f"Nomi: {ch_name}\n"
        f"Turi: {ch_type}\n"
        f"ID: {ch_id}\n"
        f"Vazifa versiyasi: V{version}\n\n"
        f"Agar Telegram kanal bo'lsa, botni kanalga admin qiling!"
    )


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        channels = get_channels()
        if not channels:
            await update.message.reply_text("Kanallar ro'yxati bo'sh.")
            return
        text = "Kanalni o'chirish:\n\n"
        for i, ch in enumerate(channels, 1):
            text += f"{i}. {ch['name']} - ID: {ch['id']}\n"
        text += "\nFormat: /remove_channel <kanal_id>"
        await update.message.reply_text(text)
        return

    channel_id = args[0]
    channels = get_channels()
    new_channels = [ch for ch in channels if ch['id'] != channel_id]

    if len(new_channels) == len(channels):
        await update.message.reply_text(f"{channel_id} topilmadi.")
        return

    db.collection('bot_config').document('channels').set({'list': new_channels})
    await update.message.reply_text(f"Kanal o'chirildi: {channel_id}")


async def set_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PROMO_COIN_AMOUNT

    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"Hozirgi: {PROMO_COIN_AMOUNT} coin\n"
            f"Format: /set_coins <son>"
        )
        return

    PROMO_COIN_AMOUNT = int(context.args[0])
    db.collection('bot_config').document('settings').set(
        {'promo_coins': PROMO_COIN_AMOUNT}, merge=True
    )

    await update.message.reply_text(f"Coin miqdori: {PROMO_COIN_AMOUNT}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Format: /broadcast <xabar matni>")
        return

    message_text = ' '.join(context.args)
    users = list(db.collection('bot_users').stream())

    sent = 0
    failed = 0
    for u in users:
        data = u.to_dict()
        tg_id = data.get('telegram_uid')
        if tg_id:
            try:
                await context.bot.send_message(
                    chat_id=int(tg_id),
                    text=f"Admin xabari:\n\n{message_text}"
                )
                sent += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"Broadcast tugadi!\n\n"
        f"Yuborildi: {sent}\n"
        f"Xatolik: {failed}"
    )


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Format: /user_info <telegram_id>")
        return

    tg_id = context.args[0]
    try:
        user_doc = db.collection('bot_users').document(tg_id).get()
        if not user_doc.exists:
            await update.message.reply_text(f"Foydalanuvchi topilmadi: {tg_id}")
            return

        data = user_doc.to_dict()
        name = data.get('telegram_name', '?')
        ver = data.get('completed_version', 0)
        code = data.get('last_code', '-')

        user_codes = list(
            db.collection('promo_codes')
            .where('telegram_uid', '==', tg_id)
            .stream()
        )

        text = (
            f"Foydalanuvchi:\n\n"
            f"Ism: {name}\n"
            f"ID: {tg_id}\n"
            f"Versiya: V{ver}\n"
            f"Oxirgi kod: `{code}`\n"
            f"Jami kodlari: {len(user_codes)}\n"
        )

        for c in user_codes:
            cd = c.to_dict()
            used = "+" if cd.get('used') else "-"
            text += f"  {used} `{cd.get('code', c.id)}` (V{cd.get('task_version', '?')})\n"

        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Xato: {e}")


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin /panel buyrug'i"""
    if is_admin(update.effective_user.id):
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text("Sizda ruxsat yo'q.")


# ============================================================
# MAIN
# ============================================================

async def error_handler(update, context):
    """Xatolarni ushlash va log qilish"""
    print(f"[ERROR] {context.error}")


def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"Health server ishga tushdi (port {os.getenv('PORT', 8000)})")

    app = Application.builder().token(BOT_TOKEN).build()

    # Error handler
    app.add_error_handler(error_handler)

    # User buyruqlari
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CallbackQueryHandler(check_subscriptions, pattern="^check_subs$"))

    # Admin panel tugmalari
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    # Admin buyruqlari
    app.add_handler(CommandHandler("add_channel", add_channel))
    app.add_handler(CommandHandler("remove_channel", remove_channel))
    app.add_handler(CommandHandler("set_coins", set_coins))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("user_info", user_info))

    print("Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

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

PROMO_COIN_AMOUNT = 20


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


def is_valid_url(url):
    """URL to'g'ri formatda ekanligini tekshirish"""
    if not url:
        return False
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    if '<' in url or '>' in url or ' ' in url:
        return False
    if '.' not in url and '+' not in url:
        return False
    return True


def fix_url(url):
    """URL ni to'g'rilash"""
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    return url


def save_user_request(user_id, channel_id, task_version):
    """Userning so'rov yuborgan kanalini saqlash"""
    try:
        db.collection('user_requests').document(f"{user_id}_{channel_id}_{task_version}").set({
            'user_id': str(user_id),
            'channel_id': channel_id,
            'task_version': task_version,
            'requested_at': firestore.SERVER_TIMESTAMP,
        })
        return True
    except Exception as e:
        print(f"So'rovni saqlashda xato: {e}")
        return False


def check_user_request(user_id, channel_id, task_version):
    """User oldin so'rov yuborgan yoki yubormaganligini tekshirish"""
    try:
        doc = db.collection('user_requests').document(f"{user_id}_{channel_id}_{task_version}").get()
        return doc.exists
    except Exception as e:
        print(f"So'rovni tekshirishda xato: {e}")
        return False


def get_user_remaining_requests(user_id, task_version):
    """Userning qaysi kanallarga so'rov yuborishi kerakligini aniqlash"""
    channels = get_channels()
    request_channels = [ch for ch in channels if ch.get('type') == 'request']
    
    remaining = []
    for ch in request_channels:
        if not check_user_request(user_id, ch['id'], task_version):
            remaining.append(ch)
    
    return remaining


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
    user = update.effective_user
    channels = get_channels()
    task_version = get_task_version()

    print(f"[TASKS] User: {user.id}, Channels: {len(channels)}, Version: {task_version}")

    # Foydalanuvchi allaqachon bajarganmi tekshirish
    try:
        user_doc = db.collection('bot_users').document(str(user.id)).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            if data.get('completed_version') == task_version:
                await update.message.reply_text(
                    f"✅ Siz barcha vazifalarni bajargansiz!\n\n"
                    f"🎁 Promo kodingiz: `{data.get('last_code', 'N/A')}`\n\n"
                    f"Bu kodni TDM Training ilovasiga kiriting va {PROMO_COIN_AMOUNT} coin oling!",
                    parse_mode='Markdown'
                )
                return
    except Exception as e:
        print(f"[TASKS] User doc xato: {e}")

    if not channels:
        await update.message.reply_text(
            "⏳ Hozircha vazifalar yo'q.\nKeyinroq qaytib keling!"
        )
        return

    # Kanallarni turlarga ajratish
    regular_channels = [ch for ch in channels if ch.get('type') in ['channel', 'link', None]]
    request_channels = [ch for ch in channels if ch.get('type') == 'request']
    
    # So'rov yuborilmagan kanallarni topish
    remaining_requests = get_user_remaining_requests(user.id, task_version)

    text = "📢 Vazifalarni bajaring va mukofot oling!\n\n"
    
    keyboard = []
    
    # Oddiy kanallar va havolalar
    if regular_channels:
        text += "1️⃣ Quyidagi kanallarga obuna bo'ling:\n\n"
        for ch in regular_channels:
            url = ch.get('url', '')
            if not is_valid_url(url):
                print(f"[TASKS] Noto'g'ri URL o'tkazib yuborildi: {url}")
                continue
            url = fix_url(url)
            keyboard.append([InlineKeyboardButton(f"📱 {ch['name']}", url=url)])
    
    # So'rov yuborish kerak bo'lgan kanallar
    if request_channels:
        text += "\n2️⃣ Quyidagi yopiq kanallarga so'rov yuboring:\n\n"
        for ch in request_channels:
            url = ch.get('url', '')
            if not is_valid_url(url):
                continue
            url = fix_url(url)
            
            # Agar user so'rov yuborgan bo'lsa, belgi qo'shish
            if check_user_request(user.id, ch['id'], task_version):
                keyboard.append([InlineKeyboardButton(f"✅ {ch['name']} (So'rov yuborildi)", url=url)])
            else:
                keyboard.append([InlineKeyboardButton(f"🔐 {ch['name']} (So'rov yuboring)", url=url)])

    text += f"\n\n💰 Mukofot: {PROMO_COIN_AMOUNT} coin"
    
    # So'rov yuborish tugmasi
    if remaining_requests:
        text += f"\n\n⚠️ Hali {len(remaining_requests)} ta yopiq kanalga so'rov yuborishingiz kerak!"
        keyboard.append([InlineKeyboardButton("📤 So'rov yubordim", callback_data="mark_requested")])
    
    # Tekshirish tugmasi
    keyboard.append([InlineKeyboardButton("✅ Bajarildi, tekshiring!", callback_data="check_subs")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def mark_requested(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User so'rov yubordi deb belgilash"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    task_version = get_task_version()
    
    # So'rov yuborilmagan kanallarni topish
    remaining_requests = get_user_remaining_requests(user.id, task_version)
    
    if not remaining_requests:
        await query.message.reply_text(
            "✅ Siz allaqachon barcha yopiq kanallarga so'rov yuborgansiz!\n\n"
            "Endi 'Bajarildi, tekshiring!' tugmasini bosing."
        )
        return
    
    # Birinchi so'rov yuborilmagan kanalga belgilash
    first_channel = remaining_requests[0]
    save_user_request(user.id, first_channel['id'], task_version)
    
    # Qayta vazifalarni ko'rsatish
    channels = get_channels()
    request_channels = [ch for ch in channels if ch.get('type') == 'request']
    regular_channels = [ch for ch in channels if ch.get('type') in ['channel', 'link', None]]
    
    # Qolgan so'rovlarni hisoblash
    new_remaining = get_user_remaining_requests(user.id, task_version)
    
    text = f"✅ So'rov qabul qilindi!\n\n"
    
    keyboard = []
    
    # Oddiy kanallar
    if regular_channels:
        text += "1️⃣ Quyidagi kanallarga obuna bo'ling:\n\n"
        for ch in regular_channels:
            url = ch.get('url', '')
            if not is_valid_url(url):
                continue
            url = fix_url(url)
            keyboard.append([InlineKeyboardButton(f"📱 {ch['name']}", url=url)])
    
    # Yopiq kanallar
    if request_channels:
        text += "\n2️⃣ Yopiq kanallar:\n\n"
        for ch in request_channels:
            url = ch.get('url', '')
            if not is_valid_url(url):
                continue
            url = fix_url(url)
            
            if check_user_request(user.id, ch['id'], task_version):
                keyboard.append([InlineKeyboardButton(f"✅ {ch['name']} (So'rov yuborildi)", url=url)])
            else:
                keyboard.append([InlineKeyboardButton(f"🔐 {ch['name']} (So'rov yuboring)", url=url)])
    
    text += f"\n\n💰 Mukofot: {PROMO_COIN_AMOUNT} coin"
    
    if new_remaining:
        text += f"\n\n⚠️ Hali {len(new_remaining)} ta yopiq kanalga so'rov yuborishingiz kerak!"
        keyboard.append([InlineKeyboardButton("📤 So'rov yubordim", callback_data="mark_requested")])
    else:
        text += "\n\n✅ Barcha yopiq kanallarga so'rov yuborildi!"
    
    keyboard.append([InlineKeyboardButton("✅ Bajarildi, tekshiring!", callback_data="check_subs")])
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


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
                f"✅ Siz allaqachon bajargansiz!\n\n"
                f"🎁 Promo kodingiz: `{code}`\n\n"
                f"Bu kodni TDM Training ilovasiga kiriting!",
                parse_mode='Markdown'
            )
            return
    except Exception as e:
        print(f"Tekshirishda xato: {e}")

    if not channels:
        await query.message.reply_text("⏳ Hozircha vazifalar yo'q.")
        return

    not_completed = []
    
    # Oddiy kanallarni tekshirish
    for ch in channels:
        ch_type = ch.get('type', 'channel')
        
        # Link va request turlarini tekshirmaymiz
        if ch_type == 'link':
            continue
        
        # Request turidagi kanallar uchun - faqat so'rov yuborgan yoki yubormaganini tekshirish
        if ch_type == 'request':
            if not check_user_request(user.id, ch['id'], task_version):
                not_completed.append(f"🔐 {ch['name']} (So'rov yuborishingiz kerak)")
            continue
        
        # Channel turidagi oddiy kanallarni tekshirish
        try:
            member = await context.bot.get_chat_member(ch['id'], user.id)
            if member.status in ['left', 'kicked']:
                not_completed.append(f"📱 {ch['name']}")
        except Exception as e:
            print(f"Kanal tekshirishda xato ({ch['id']}): {e}")
            not_completed.append(f"📱 {ch['name']}")

    if not_completed:
        text = "❌ Barcha vazifalar bajarilmagan!\n\n"
        text += "Quyidagilarni bajaring:\n\n"
        for item in not_completed:
            text += f"• {item}\n"
        
        keyboard = []
        
        # Oddiy kanallar
        regular_channels = [ch for ch in channels if ch.get('type') in ['channel', 'link', None]]
        if regular_channels:
            for ch in regular_channels:
                url = ch.get('url', '')
                if not is_valid_url(url):
                    continue
                url = fix_url(url)
                keyboard.append([InlineKeyboardButton(f"📱 {ch['name']}", url=url)])
        
        # Yopiq kanallar
        request_channels = [ch for ch in channels if ch.get('type') == 'request']
        if request_channels:
            for ch in request_channels:
                url = ch.get('url', '')
                if not is_valid_url(url):
                    continue
                url = fix_url(url)
                
                if check_user_request(user.id, ch['id'], task_version):
                    keyboard.append([InlineKeyboardButton(f"✅ {ch['name']} (So'rov yuborildi)", url=url)])
                else:
                    keyboard.append([InlineKeyboardButton(f"🔐 {ch['name']} (So'rov yuboring)", url=url)])
        
        # So'rov yuborish tugmasi
        remaining_requests = get_user_remaining_requests(user.id, task_version)
        if remaining_requests:
            keyboard.append([InlineKeyboardButton("📤 So'rov yubordim", callback_data="mark_requested")])
        
        keyboard.append([InlineKeyboardButton("✅ Bajarildi, tekshiring!", callback_data="check_subs")])
        
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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

        await query.message.edit_text(
            f"🎉 Tabriklaymiz! Barcha vazifalar bajarildi!\n\n"
            f"🎁 Sizning promo kodingiz:\n\n"
            f"`{code}`\n\n"
            f"💰 Bu kodni TDM Training ilovasiga kiriting va {PROMO_COIN_AMOUNT} coin oling!\n\n"
            f"✅ Kod ilovada faqat 1 marta ishlatilishi mumkin.",
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"Promo kod yaratishda xato: {e}")
        await query.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring: /start")


# ============================================================
# ADMIN PANEL
# ============================================================

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun tugmali panel"""
    text = "🔧 Admin Panel\n\nQuyidagi tugmalardan birini tanlang:"

    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Userlar", callback_data="admin_users")],
        [InlineKeyboardButton("🎫 Promo kodlar", callback_data="admin_codes"),
         InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("💰 Coin sozlash", callback_data="admin_coins"),
         InlineKeyboardButton("🔄 Yangi versiya", callback_data="admin_new_ver")],
        [InlineKeyboardButton("📤 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("➖ Kanal o'chirish", callback_data="admin_remove_ch")],
        [InlineKeyboardButton("👁 Vazifalarni ko'rish", callback_data="admin_view_tasks")],
        [InlineKeyboardButton("📋 So'rovlar statistikasi", callback_data="admin_requests_stats")],
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel tugmalarini boshqarish"""
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.reply_text("❌ Sizda ruxsat yo'q.")
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
    elif data == "admin_requests_stats":
        await handle_requests_stats(query)
    elif data == "admin_back":
        await handle_back_to_panel(query)
    elif data == "admin_codes_used":
        await handle_codes_filtered(query, 'used')
    elif data == "admin_codes_unused":
        await handle_codes_filtered(query, 'unused')


def back_button():
    return [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_back")]


async def handle_back_to_panel(query):
    text = "🔧 Admin Panel\n\nQuyidagi tugmalardan birini tanlang:"

    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Userlar", callback_data="admin_users")],
        [InlineKeyboardButton("🎫 Promo kodlar", callback_data="admin_codes"),
         InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("💰 Coin sozlash", callback_data="admin_coins"),
         InlineKeyboardButton("🔄 Yangi versiya", callback_data="admin_new_ver")],
        [InlineKeyboardButton("📤 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("➖ Kanal o'chirish", callback_data="admin_remove_ch")],
        [InlineKeyboardButton("👁 Vazifalarni ko'rish", callback_data="admin_view_tasks")],
        [InlineKeyboardButton("📋 So'rovlar statistikasi", callback_data="admin_requests_stats")],
    ]

    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_stats(query):
    try:
        total_codes = len(list(db.collection('promo_codes').stream()))
        used_codes = len(list(db.collection('promo_codes').where('used', '==', True).stream()))
        unused_codes = total_codes - used_codes
        total_users = len(list(db.collection('bot_users').stream()))
        total_requests = len(list(db.collection('user_requests').stream()))
        channels = get_channels()
        
        regular_ch = len([ch for ch in channels if ch.get('type') in ['channel', 'link', None]])
        request_ch = len([ch for ch in channels if ch.get('type') == 'request'])

        text = (
            f"📊 Statistika\n\n"
            f"👥 Foydalanuvchilar: {total_users}\n"
            f"🎫 Promo kodlar: {total_codes}\n"
            f"  ✅ Ishlatilgan: {used_codes}\n"
            f"  ⏳ Ishlatilmagan: {unused_codes}\n\n"
            f"📢 Kanallar: {len(channels)}\n"
            f"  📱 Oddiy: {regular_ch}\n"
            f"  🔐 Yopiq: {request_ch}\n\n"
            f"📤 Jami so'rovlar: {total_requests}\n"
            f"🔄 Vazifa versiyasi: V{get_task_version()}\n"
            f"💰 Coin miqdori: {PROMO_COIN_AMOUNT}"
        )
    except Exception as e:
        text = f"❌ Statistika olishda xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_users(query):
    try:
        users = list(db.collection('bot_users').order_by(
            'updated_at', direction=firestore.Query.DESCENDING
        ).limit(20).stream())

        if not users:
            text = "👥 Foydalanuvchilar yo'q."
        else:
            text = f"👥 Oxirgi {len(users)} ta foydalanuvchi:\n\n"
            for i, u in enumerate(users, 1):
                data = u.to_dict()
                name = data.get('telegram_name', '?')
                uid = data.get('telegram_uid', '?')
                ver = data.get('completed_version', 0)
                text += f"{i}. {name}\n   ID: {uid} | V{ver}\n"
    except Exception as e:
        text = f"❌ Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_codes(query):
    try:
        total = len(list(db.collection('promo_codes').stream()))
        used = len(list(db.collection('promo_codes').where('used', '==', True).stream()))
        unused = total - used

        text = (
            f"🎫 Promo kodlar\n\n"
            f"📊 Jami: {total}\n"
            f"✅ Ishlatilgan: {used}\n"
            f"⏳ Ishlatilmagan: {unused}\n\n"
            f"Qaysilarni ko'rmoqchisiz?"
        )
    except Exception as e:
        text = f"❌ Xato: {e}"

    keyboard = [
        [InlineKeyboardButton("✅ Ishlatilganlar", callback_data="admin_codes_used"),
         InlineKeyboardButton("⏳ Ishlatilmaganlar", callback_data="admin_codes_unused")],
        back_button()
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_codes_filtered(query, filter_type):
    try:
        if filter_type == 'used':
            codes = list(db.collection('promo_codes').where('used', '==', True).limit(20).stream())
            title = "✅ Ishlatilgan kodlar"
        else:
            codes = list(db.collection('promo_codes').where('used', '==', False).limit(20).stream())
            title = "⏳ Ishlatilmagan kodlar"

        if not codes:
            text = f"{title}\n\n❌ Kodlar yo'q."
        else:
            text = f"{title} ({len(codes)} ta):\n\n"
            for c in codes:
                data = c.to_dict()
                code = data.get('code', c.id)
                tg_name = data.get('telegram_name', '?')
                ver = data.get('task_version', '?')
                text += f"`{code}` - {tg_name} (V{ver})\n"
    except Exception as e:
        text = f"❌ Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def handle_channels(query):
    channels = get_channels()

    if not channels:
        text = "📢 Kanallar ro'yxati bo'sh.\n\nKanal qo'shish uchun pastdagi tugmani bosing."
    else:
        text = f"📢 Kanallar ({len(channels)} ta):\n\n"
        for i, ch in enumerate(channels, 1):
            ch_type = ch.get('type', 'channel')
            if ch_type == 'channel':
                emoji = "📱"
            elif ch_type == 'request':
                emoji = "🔐"
            else:
                emoji = "🔗"
            
            text += f"{i}. {emoji} {ch['name']}\n"
            text += f"   Tur: {ch_type}\n"
            text += f"   ID: {ch['id']}\n\n"
        text += f"🔄 Vazifa versiyasi: V{get_task_version()}"

    keyboard = [
        [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("➖ Kanal o'chirish", callback_data="admin_remove_ch")],
        back_button()
    ]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_coins_info(query):
    text = (
        f"💰 Coin sozlamalari\n\n"
        f"Hozirgi miqdor: {PROMO_COIN_AMOUNT} coin\n\n"
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
            f"🔄 Yangi versiya yaratildi: V{version}\n\n"
            f"✅ Endi barcha foydalanuvchilar qayta vazifa bajarib,\n"
            f"yangi promo kod olishlari mumkin.\n\n"
            f"⚠️ Eski versiya kodlari bekor bo'lmaydi."
        )
    except Exception as e:
        text = f"❌ Xato: {e}"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_broadcast_info(query):
    text = (
        "📤 Barcha foydalanuvchilarga xabar yuborish\n\n"
        "Quyidagi formatda yozing:\n\n"
        "/broadcast Xabar matni shu yerda"
    )
    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_add_channel_info(query):
    text = (
        "➕ Kanal qo'shish\n\n"
        "Format:\n"
        "/add_channel <type> <id> <name> <url>\n\n"
        "📱 Ochiq kanal:\n"
        "/add_channel channel @username Kanal_Nomi https://t.me/username\n\n"
        "🔐 Yopiq kanal/guruh (so'rov yuboriladi):\n"
        "/add_channel request -100xxx Guruh_Nomi https://t.me/+invite\n\n"
        "🔗 Tashqi havola:\n"
        "/add_channel link id Link_Nomi https://link.com\n\n"
        "⚠️ Telegram kanal bo'lsa, botni admin qiling!"
    )
    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_remove_channel_info(query):
    channels = get_channels()

    if not channels:
        text = "❌ Kanallar ro'yxati bo'sh."
    else:
        text = "➖ Kanalni o'chirish\n\n"
        for i, ch in enumerate(channels, 1):
            ch_type = ch.get('type', 'channel')
            if ch_type == 'channel':
                emoji = "📱"
            elif ch_type == 'request':
                emoji = "🔐"
            else:
                emoji = "🔗"
            text += f"{i}. {emoji} {ch['name']} - ID: {ch['id']}\n"
        text += "\nFormat:\n/remove_channel <kanal_id>"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_view_tasks(query):
    """Admin user ko'rinishida vazifalarni ko'radi"""
    channels = get_channels()

    if not channels:
        text = "❌ Hozircha vazifalar yo'q (kanallar qo'shilmagan)."
    else:
        regular_ch = [ch for ch in channels if ch.get('type') in ['channel', 'link', None]]
        request_ch = [ch for ch in channels if ch.get('type') == 'request']
        
        text = (
            f"👁 User ko'rinishi:\n\n"
            f"📊 Kanallar soni: {len(channels)}\n"
            f"  📱 Oddiy: {len(regular_ch)}\n"
            f"  🔐 Yopiq: {len(request_ch)}\n"
            f"💰 Mukofot: {PROMO_COIN_AMOUNT} coin\n\n"
        )
        
        if regular_ch:
            text += "📱 Oddiy kanallar:\n"
            for i, ch in enumerate(regular_ch, 1):
                text += f"{i}. {ch['name']}\n"
            text += "\n"
        
        if request_ch:
            text += "🔐 Yopiq kanallar (so'rov yuboriladi):\n"
            for i, ch in enumerate(request_ch, 1):
                text += f"{i}. {ch['name']}\n"

    keyboard = [back_button()]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_requests_stats(query):
    """So'rovlar statistikasini ko'rsatish"""
    try:
        task_version = get_task_version()
        all_requests = list(db.collection('user_requests').stream())
        current_version_requests = [r for r in all_requests if r.to_dict().get('task_version') == task_version]
        
        channels = get_channels()
        request_channels = [ch for ch in channels if ch.get('type') == 'request']
        
        text = f"📋 So'rovlar statistikasi (V{task_version}):\n\n"
        text += f"📤 Jami so'rovlar: {len(current_version_requests)}\n"
        text += f"🔐 Yopiq kanallar: {len(request_channels)}\n\n"
        
        if request_channels:
            text += "Kanallar bo'yicha:\n"
            for ch in request_channels:
                ch_requests = [r for r in current_version_requests if r.to_dict().get('channel_id') == ch['id']]
                text += f"• {ch['name']}: {len(ch_requests)} ta so'rov\n"
        else:
            text += "❌ Yopiq kanallar yo'q."
    except Exception as e:
        text = f"❌ Xato: {e}"
    
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
            "📱 /add_channel channel @kanal Kanal_Nomi https://t.me/kanal\n"
            "🔐 /add_channel request -100123 Guruh_Nomi https://t.me/+invite\n"
            "🔗 /add_channel link inst Instagram https://instagram.com/page"
        )
        return

    ch_type = args[0]
    ch_id = args[1]
    ch_name = args[2].replace('_', ' ')
    ch_url = args[3]

    # URL tekshirish
    if not ch_url.startswith('http'):
        ch_url = 'https://' + ch_url

    if ch_type not in ['channel', 'request', 'link']:
        await update.message.reply_text(
            "❌ Tur noto'g'ri! Faqat: channel, request, link"
        )
        return

    channels = get_channels()
    
    # Kanal allaqachon mavjudligini tekshirish
    if any(ch['id'] == ch_id for ch in channels):
        await update.message.reply_text(
            f"⚠️ Bu kanal allaqachon ro'yxatda mavjud!\n"
            f"ID: {ch_id}"
        )
        return
    
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

    type_emoji = "📱" if ch_type == 'channel' else "🔐" if ch_type == 'request' else "🔗"
    
    await update.message.reply_text(
        f"✅ Kanal qo'shildi!\n\n"
        f"{type_emoji} Nomi: {ch_name}\n"
        f"Turi: {ch_type}\n"
        f"ID: {ch_id}\n"
        f"🔄 Yangi vazifa versiyasi: V{version}\n\n"
        f"⚠️ Agar Telegram kanal bo'lsa, botni kanalga admin qiling!"
    )


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        channels = get_channels()
        if not channels:
            await update.message.reply_text("❌ Kanallar ro'yxati bo'sh.")
            return
        text = "➖ Kanalni o'chirish:\n\n"
        for i, ch in enumerate(channels, 1):
            ch_type = ch.get('type', 'channel')
            emoji = "📱" if ch_type == 'channel' else "🔐" if ch_type == 'request' else "🔗"
            text += f"{i}. {emoji} {ch['name']} - ID: {ch['id']}\n"
        text += "\nFormat: /remove_channel <kanal_id>"
        await update.message.reply_text(text)
        return

    channel_id = args[0]
    channels = get_channels()
    
    # O'chiriladigan kanalni topish
    channel_to_remove = next((ch for ch in channels if ch['id'] == channel_id), None)
    
    if not channel_to_remove:
        await update.message.reply_text(f"❌ {channel_id} topilmadi.")
        return
    
    new_channels = [ch for ch in channels if ch['id'] != channel_id]

    db.collection('bot_config').document('channels').set({'list': new_channels})
    
    await update.message.reply_text(
        f"✅ Kanal o'chirildi!\n\n"
        f"Nomi: {channel_to_remove['name']}\n"
        f"ID: {channel_id}"
    )


async def set_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PROMO_COIN_AMOUNT

    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"💰 Hozirgi: {PROMO_COIN_AMOUNT} coin\n\n"
            f"Format: /set_coins <son>\n"
            f"Misol: /set_coins 50"
        )
        return

    PROMO_COIN_AMOUNT = int(context.args[0])
    db.collection('bot_config').document('settings').set(
        {'promo_coins': PROMO_COIN_AMOUNT}, merge=True
    )

    await update.message.reply_text(
        f"✅ Coin miqdori o'zgardi!\n\n"
        f"💰 Yangi qiymat: {PROMO_COIN_AMOUNT} coin"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "📤 Format: /broadcast <xabar matni>\n\n"
            "Misol:\n"
            "/broadcast Yangi vazifalar qo'shildi!"
        )
        return

    message_text = ' '.join(context.args)
    users = list(db.collection('bot_users').stream())

    await update.message.reply_text(
        f"📤 Xabar yuborilmoqda...\n"
        f"👥 Jami foydalanuvchilar: {len(users)}"
    )

    sent = 0
    failed = 0
    for u in users:
        data = u.to_dict()
        tg_id = data.get('telegram_uid')
        if tg_id:
            try:
                await context.bot.send_message(
                    chat_id=int(tg_id),
                    text=f"📢 Admin xabari:\n\n{message_text}"
                )
                sent += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"✅ Broadcast tugadi!\n\n"
        f"📤 Yuborildi: {sent}\n"
        f"❌ Xatolik: {failed}"
    )


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "ℹ️ Format: /user_info <telegram_id>\n\n"
            "Misol: /user_info 123456789"
        )
        return

    tg_id = context.args[0]
    try:
        user_doc = db.collection('bot_users').document(tg_id).get()
        if not user_doc.exists:
            await update.message.reply_text(f"❌ Foydalanuvchi topilmadi: {tg_id}")
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
        
        # So'rovlar
        user_requests = list(
            db.collection('user_requests')
            .where('user_id', '==', tg_id)
            .stream()
        )

        text = (
            f"👤 Foydalanuvchi ma'lumotlari:\n\n"
            f"📝 Ism: {name}\n"
            f"🆔 ID: {tg_id}\n"
            f"🔄 Versiya: V{ver}\n"
            f"🎁 Oxirgi kod: `{code}`\n"
            f"🎫 Jami kodlari: {len(user_codes)}\n"
            f"📤 Jami so'rovlari: {len(user_requests)}\n\n"
        )

        if user_codes:
            text += "🎫 Kodlar:\n"
            for c in user_codes:
                cd = c.to_dict()
                used = "✅" if cd.get('used') else "⏳"
                text += f"  {used} `{cd.get('code', c.id)}` (V{cd.get('task_version', '?')})\n"

        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Xato: {e}")


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin /panel buyrug'i"""
    if is_admin(update.effective_user.id):
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text("❌ Sizda ruxsat yo'q.")


# ============================================================
# MAIN
# ============================================================

async def error_handler(update, context):
    """Xatolarni ushlash va log qilish"""
    print(f"[ERROR] {context.error}")


def main():
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"✅ Health server ishga tushdi (port {os.getenv('PORT', 8000)})")

    app = Application.builder().token(BOT_TOKEN).build()

    # Error handler
    app.add_error_handler(error_handler)

    # User buyruqlari
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CallbackQueryHandler(check_subscriptions, pattern="^check_subs$"))
    app.add_handler(CallbackQueryHandler(mark_requested, pattern="^mark_requested$"))

    # Admin panel tugmalari
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    # Admin buyruqlari
    app.add_handler(CommandHandler("add_channel", add_channel))
    app.add_handler(CommandHandler("remove_channel", remove_channel))
    app.add_handler(CommandHandler("set_coins", set_coins))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("user_info", user_info))

    print("🚀 Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

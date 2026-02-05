import os
import json
import string
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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
        pass  # Loglarni yashirish

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

# Admin Telegram ID'lari (botga /admin buyruqlar berish uchun)
# @userinfobot ga /start bosib ID'ingizni bilib oling
ADMIN_IDS = [
6768934631
]

# Har bir promo kodda beriladigan coin miqdori
PROMO_COIN_AMOUNT = 5

# ============================================================
# FIREBASE INIT
# ============================================================

# Hosting uchun: FIREBASE_CREDENTIALS env var dan JSON o'qish
# Lokal uchun: service_account.json fayldan o'qish
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
    """Noyob promo kod generatsiya qilish"""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        # Firestore'da mavjud emasligini tekshirish
        doc = db.collection('promo_codes').document(code).get()
        if not doc.exists:
            return code


def get_channels():
    """Firestore'dan kanallar ro'yxatini olish"""
    doc = db.collection('bot_config').document('channels').get()
    if doc.exists:
        return doc.to_dict().get('list', [])
    return []


def get_task_version():
    """Joriy vazifa versiyasini olish"""
    doc = db.collection('bot_config').document('settings').get()
    if doc.exists:
        return doc.to_dict().get('task_version', 1)
    return 1


# ============================================================
# BOT HANDLERLARI
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot /start buyrug'i"""
    user = update.effective_user
    channels = get_channels()
    task_version = get_task_version()

    # Foydalanuvchi allaqachon bajarganmi tekshirish
    user_doc = db.collection('bot_users').document(str(user.id)).get()
    if user_doc.exists:
        data = user_doc.to_dict()
        if data.get('completed_version') == task_version:
            await update.message.reply_text(
                f"✅ Siz barcha vazifalarni bajargansiz!\n\n"
                f"Promo kodingiz: `{data.get('last_code', 'N/A')}`\n\n"
                f"Bu kodni TDM Training ilovasiga kiriting!",
                parse_mode='Markdown'
            )
            return

    if not channels:
        await update.message.reply_text("Hozircha vazifalar yo'q. Keyinroq qaytib keling!")
        return

    text = "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling yoki so'rov yuboring ❗"

    keyboard = []
    for i, ch in enumerate(channels, 1):
        ch_type = ch.get('type', 'channel')
        if ch_type == 'request':
            label = f"📨 So'rov yuborish ({i})"
        elif ch_type == 'link':
            label = f"🌐 {ch['name']}"
        else:
            label = f"📢 {ch['name']}"

        keyboard.append([InlineKeyboardButton(label, url=ch['url'])])

    keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)


async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obunalarni tekshirish"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    channels = get_channels()
    task_version = get_task_version()

    # Allaqachon bajarganmi
    user_doc = db.collection('bot_users').document(str(user.id)).get()
    if user_doc.exists and user_doc.to_dict().get('completed_version') == task_version:
        code = user_doc.to_dict().get('last_code')
        await query.message.reply_text(
            f"✅ Siz allaqachon barcha vazifalarni bajargansiz!\n\n"
            f"Promo kodingiz: `{code}`",
            parse_mode='Markdown'
        )
        return

    # Har bir kanalni tekshirish
    not_subscribed = []
    for ch in channels:
        ch_type = ch.get('type', 'channel')

        # Faqat Telegram kanallarini tekshirish mumkin
        if ch_type == 'link':
            continue

        try:
            member = await context.bot.get_chat_member(ch['id'], user.id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch['name'])
        except Exception as e:
            # Bot kanal adminiga qo'shilmagan yoki xatolik
            not_subscribed.append(ch['name'])

    if not_subscribed:
        text = "❌ Siz quyidagi kanallarga obuna bo'lmagansiz:\n\n"
        for name in not_subscribed:
            text += f"  • {name}\n"
        text += "\nIltimos, barcha kanallarga obuna bo'ling va qayta tekshiring!"

        # Qayta tugmalarni ko'rsatish
        keyboard = []
        for i, ch in enumerate(channels, 1):
            ch_type = ch.get('type', 'channel')
            if ch_type == 'request':
                label = f"📨 So'rov yuborish ({i})"
            elif ch_type == 'link':
                label = f"🌐 {ch['name']}"
            else:
                label = f"📢 {ch['name']}"
            keyboard.append([InlineKeyboardButton(label, url=ch['url'])])
        keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs")])

        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Hammasi OK - promo kod berish
    code = generate_promo_code()

    # Firestore'ga saqlash
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
        f"🎉 Tabriklaymiz! Barcha vazifalar bajarildi!\n\n"
        f"Promo kodingiz: `{code}`\n\n"
        f"Bu kodni TDM Training ilovasiga kiriting va {PROMO_COIN_AMOUNT} coin oling!",
        parse_mode='Markdown'
    )


# ============================================================
# ADMIN BUYRUQLARI
# ============================================================

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Kanal qo'shish.
    Format: /add_channel <type> <channel_id> <name> <url>
    type: channel, request, link
    Misol: /add_channel channel @my_channel MyChannel https://t.me/my_channel
    Misol: /add_channel request -100123456 PrivateGroup https://t.me/+invite_link
    Misol: /add_channel link instagram Instagram https://instagram.com/mypage
    """
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Sizda ruxsat yo'q.")
        return

    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "📝 Format:\n"
            "/add_channel <type> <channel_id> <name> <url>\n\n"
            "type: channel, request, link\n\n"
            "Misollar:\n"
            "/add_channel channel @kanal_nomi Kanal_Nomi https://t.me/kanal_nomi\n"
            "/add_channel request -100123 Guruh_Nomi https://t.me/+invite\n"
            "/add_channel link inst Instagram https://instagram.com/page"
        )
        return

    ch_type = args[0]  # channel, request, link
    ch_id = args[1]  # @username yoki -100xxx
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

    # Vazifa versiyasini oshirish
    version = get_task_version() + 1
    db.collection('bot_config').document('settings').set(
        {'task_version': version}, merge=True
    )

    await update.message.reply_text(
        f"✅ Kanal qo'shildi!\n\n"
        f"Nomi: {ch_name}\n"
        f"Turi: {ch_type}\n"
        f"ID: {ch_id}\n"
        f"Yangi vazifa versiyasi: {version}\n\n"
        f"⚠️ Agar bu Telegram kanal bo'lsa, botni o'sha kanalga admin qilib qo'shing!"
    )


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Kanalni o'chirish.
    Format: /remove_channel <channel_id>
    """
    if update.effective_user.id not in ADMIN_IDS:
        return

    args = context.args
    if not args:
        await update.message.reply_text("Format: /remove_channel <channel_id>")
        return

    channel_id = args[0]
    channels = get_channels()
    new_channels = [ch for ch in channels if ch['id'] != channel_id]

    if len(new_channels) == len(channels):
        await update.message.reply_text(f"❌ {channel_id} topilmadi.")
        return

    db.collection('bot_config').document('channels').set({'list': new_channels})

    await update.message.reply_text(f"✅ Kanal o'chirildi: {channel_id}")


async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanallar ro'yxatini ko'rsatish"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    channels = get_channels()
    if not channels:
        await update.message.reply_text("📋 Kanallar ro'yxati bo'sh.")
        return

    text = "📋 Kanallar ro'yxati:\n\n"
    for i, ch in enumerate(channels, 1):
        text += f"{i}. {ch['name']} ({ch.get('type', 'channel')})\n"
        text += f"   ID: {ch['id']}\n"
        text += f"   URL: {ch['url']}\n\n"

    text += f"\n📊 Vazifa versiyasi: {get_task_version()}"
    await update.message.reply_text(text)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot statistikasi"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    # Promo kodlar soni
    total_codes = len(list(db.collection('promo_codes').stream()))
    used_codes = len(list(db.collection('promo_codes').where('used', '==', True).stream()))
    unused_codes = total_codes - used_codes

    # Foydalanuvchilar soni
    total_users = len(list(db.collection('bot_users').stream()))

    text = (
        f"📊 Bot Statistikasi:\n\n"
        f"👥 Jami foydalanuvchilar: {total_users}\n"
        f"🎟️ Jami promo kodlar: {total_codes}\n"
        f"✅ Ishlatilgan: {used_codes}\n"
        f"⏳ Ishlatilmagan: {unused_codes}\n"
        f"📋 Kanallar: {len(get_channels())}\n"
        f"🔄 Vazifa versiyasi: {get_task_version()}"
    )
    await update.message.reply_text(text)


async def new_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Yangi vazifa versiyasini yaratish.
    Bu barcha foydalanuvchilarga qayta vazifa bajarish imkonini beradi.
    """
    if update.effective_user.id not in ADMIN_IDS:
        return

    version = get_task_version() + 1
    db.collection('bot_config').document('settings').set(
        {'task_version': version}, merge=True
    )

    await update.message.reply_text(
        f"🔄 Yangi vazifa versiyasi yaratildi: {version}\n\n"
        f"Endi barcha foydalanuvchilar qayta vazifa bajarib, yangi promo kod olishlari mumkin."
    )


async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oxirgi foydalanuvchilar ro'yxati"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    users = list(db.collection('bot_users').order_by(
        'updated_at', direction=firestore.Query.DESCENDING
    ).limit(20).stream())

    if not users:
        await update.message.reply_text("👥 Foydalanuvchilar yo'q.")
        return

    text = "👥 Oxirgi 20 ta foydalanuvchi:\n\n"
    for i, u in enumerate(users, 1):
        data = u.to_dict()
        name = data.get('telegram_name', 'Noma\'lum')
        uid = data.get('telegram_uid', '?')
        ver = data.get('completed_version', 0)
        code = data.get('last_code', '-')
        text += f"{i}. {name}\n   ID: {uid} | V{ver} | Kod: {code}\n\n"

    await update.message.reply_text(text)


async def codes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Promo kodlar ro'yxati"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    # Parametr: all, used, unused
    filter_type = context.args[0] if context.args else 'all'

    query = db.collection('promo_codes').order_by(
        'created_at', direction=firestore.Query.DESCENDING
    ).limit(30)

    if filter_type == 'used':
        query = db.collection('promo_codes').where('used', '==', True).limit(30)
    elif filter_type == 'unused':
        query = db.collection('promo_codes').where('used', '==', False).limit(30)

    codes = list(query.stream())

    if not codes:
        await update.message.reply_text("🎟 Promo kodlar yo'q.")
        return

    text = f"🎟 Promo kodlar ({filter_type}):\n\n"
    for c in codes:
        data = c.to_dict()
        code = data.get('code', c.id)
        used = "✅" if data.get('used') else "⏳"
        tg_name = data.get('telegram_name', '?')
        used_by = data.get('used_by', '-')
        coins = data.get('coins', PROMO_COIN_AMOUNT)
        text += f"{used} `{code}` | {tg_name} | {coins} coin"
        if data.get('used') and used_by:
            text += f" | App: {used_by[:8]}..."
        text += "\n"

    text += f"\n📝 /codes all | /codes used | /codes unused"
    await update.message.reply_text(text, parse_mode='Markdown')


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bitta foydalanuvchi haqida to'liq ma'lumot"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("Format: /user_info <telegram_id>")
        return

    tg_id = context.args[0]
    user_doc = db.collection('bot_users').document(tg_id).get()

    if not user_doc.exists:
        await update.message.reply_text(f"❌ Foydalanuvchi topilmadi: {tg_id}")
        return

    data = user_doc.to_dict()
    name = data.get('telegram_name', 'Noma\'lum')
    ver = data.get('completed_version', 0)
    code = data.get('last_code', '-')

    # Bu foydalanuvchining barcha promo kodlari
    user_codes = list(
        db.collection('promo_codes')
        .where('telegram_uid', '==', tg_id)
        .stream()
    )

    text = (
        f"👤 Foydalanuvchi ma'lumotlari:\n\n"
        f"Ism: {name}\n"
        f"Telegram ID: {tg_id}\n"
        f"Vazifa versiyasi: {ver}\n"
        f"Oxirgi kod: `{code}`\n\n"
        f"🎟 Jami kodlari: {len(user_codes)}\n"
    )

    for c in user_codes:
        cd = c.to_dict()
        used = "✅" if cd.get('used') else "⏳"
        text += f"  {used} `{cd.get('code', c.id)}` (V{cd.get('task_version', '?')})\n"

    await update.message.reply_text(text, parse_mode='Markdown')


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha foydalanuvchilarga xabar yuborish"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text(
            "Format: /broadcast <xabar matni>\n"
            "Misol: /broadcast Yangi vazifalar qo'shildi! /start bosing"
        )
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
                    text=f"📢 Admin xabari:\n\n{message_text}"
                )
                sent += 1
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"📢 Broadcast tugadi!\n\n"
        f"✅ Yuborildi: {sent}\n"
        f"❌ Xatolik: {failed}"
    )


async def set_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Promo kod coin miqdorini o'zgartirish"""
    global PROMO_COIN_AMOUNT

    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            f"Hozirgi: {PROMO_COIN_AMOUNT} coin\n"
            f"Format: /set_coins <son>\n"
            f"Misol: /set_coins 10"
        )
        return
    PROMO_COIN_AMOUNT = int(context.args[0])

    db.collection('bot_config').document('settings').set(
        {'promo_coins': PROMO_COIN_AMOUNT}, merge=True
    )

    await update.message.reply_text(f"✅ Promo kod coin miqdori: {PROMO_COIN_AMOUNT}")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin buyruqlar ro'yxati"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        "🛠 Admin buyruqlar:\n\n"
        "📊 ANALITIKA:\n"
        "/stats - Umumiy statistika\n"
        "/users - Oxirgi foydalanuvchilar\n"
        "/codes [all|used|unused] - Promo kodlar\n"
        "/user_info <tg_id> - Foydalanuvchi ma'lumoti\n\n"
        "📋 KANAL BOSHQARUVI:\n"
        "/add_channel <type> <id> <name> <url>\n"
        "/remove_channel <id>\n"
        "/channels - Ro'yxat\n\n"
        "⚙️ SOZLAMALAR:\n"
        "/set_coins <son> - Promo kod coin miqdori\n"
        "/new_version - Yangi vazifa versiyasi\n"
        "/broadcast <xabar> - Hammaga xabar\n\n"
        "/admin - Shu yordam"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    # Health check serverni alohida threadda ishga tushirish
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"🌐 Health server ishga tushdi (port {os.getenv('PORT', 8000)})")

    app = Application.builder().token(BOT_TOKEN).build()

    # Foydalanuvchi buyruqlari
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_subscriptions, pattern="^check_subs$"))

    # Admin buyruqlari
    app.add_handler(CommandHandler("add_channel", add_channel))
    app.add_handler(CommandHandler("remove_channel", remove_channel))
    app.add_handler(CommandHandler("channels", list_channels))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("users", users_list))
    app.add_handler(CommandHandler("codes", codes_list))
    app.add_handler(CommandHandler("user_info", user_info))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("set_coins", set_coins))
    app.add_handler(CommandHandler("new_version", new_version))
    app.add_handler(CommandHandler("admin", admin_help))

    print("🤖 Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

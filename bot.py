import os
import re
import asyncio
import logging
import threading
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWaitError, PeerIdInvalid, ChannelPrivate
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto
from flask import Flask

# ========== الإعدادات ==========
API_ID = int(os.getenv("API_ID", "35909411"))
API_HASH = os.getenv("API_HASH", "d2e7f09b5aaeaf64904b8afd6b8057c7")
SESSION_STRING = os.getenv("SESSION_STRING")
PORT = int(os.getenv("PORT", 8080))

# ========== Logging خفيف ==========
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== Flask للـ Keep-Alive ==========
flask_app = Flask(__name__)

@flask_app.route("/")
def keep_alive():
    return "Bot is running ✅", 200

# ========== Pyrogram Client ==========
client = Client(
    name="session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workers=1,
    in_memory=True
)

# ========== حفظ حالة المستخدم ==========
user_state = {}

# ========== الأوامر الأساسية ==========

@client.on_message(filters.me & filters.command("start"))
async def start_cmd(client, message):
    """أمر البداية"""
    await message.reply_text(
        "🤖 **بوت حفظ المحتوى المقيد**\n\n"
        "📌 **الاستخدام:**\n"
        "1️⃣ أرسل رابط المقطع:\n"
        "`https://t.me/channel_name/123`\n\n"
        "2️⃣ أو ارسل معرّف القناة:\n"
        "`@channel_name`\n\n"
        "✅ يدعم: فيديو، وثائق، صور، ألبومات"
    )

@client.on_message(filters.me & filters.command("help"))
async def help_cmd(client, message):
    """أمر المساعدة"""
    await message.reply_text(
        "📖 **طريقة الاستخدام:**\n\n"
        "**حفظ مقطع واحد:**\n"
        "`https://t.me/c/123456789/100`\n\n"
        "**حفظ مجموعة:**\n"
        "ارسل الرابط ثم أرسل:\n"
        "`100 120` (من 100 إلى 120)\n\n"
        "**حفظ مقطع واحد فقط:**\n"
        "`https://t.me/c/123456789/100` ثم أرسل `تم`"
    )

# ========== معالج الروابط ==========

@client.on_message(filters.me & filters.regex(r"^https://t\.me/"))
async def handle_link(client, message):
    """معالج رابط Telegram"""
    link = message.text.strip()
    
    # محاولة استخراج المعلومات من الرابط
    match = re.search(r"https://t\.me/(c/)?(.+?)/(\d+)", link)
    
    if not match:
        await message.reply_text("❌ صيغة الرابط غير صحيحة")
        return
    
    is_private = match.group(1)
    chat_identifier = match.group(2)
    msg_id = int(match.group(3))
    
    # تحويل معرّف القناة
    if is_private:
        chat_id = int(f"-100{chat_identifier}")
    else:
        chat_id = chat_identifier
    
    user_state[message.chat.id] = {
        "chat_id": chat_id,
        "start_id": msg_id,
        "status": "waiting"
    }
    
    await message.reply_text(
        f"✅ تم حفظ الرابط\n\n"
        f"الآن اختر:\n"
        f"• أرسل `تم` لحفظ هذا المقطع فقط\n"
        f"• أرسل رقم المقطع الأخير مثل `120`"
    )

# ========== معالج النصوص (الأرقام والتأكيد) ==========

@client.on_message(filters.me & filters.text)
async def handle_text(client, message):
    """معالج النصوص والأرقام"""
    user_id = message.chat.id
    text = message.text.strip().lower()
    
    if user_id not in user_state:
        return
    
    state = user_state[user_id]
    
    # التحقق من "تم"
    if text == "تم":
        end_id = state["start_id"]
    else:
        # محاولة تحويل النص إلى رقم
        try:
            end_id = int(message.text.strip())
        except ValueError:
            return
    
    state["end_id"] = end_id
    state["status"] = "downloading"
    
    # بدء التحميل
    await start_downloading(client, message, user_id)

# ========== دالة التحميل الرئيسية ==========

async def start_downloading(client, message, user_id):
    """تحميل المقاطع وحفظها"""
    state = user_state[user_id]
    chat_id = state["chat_id"]
    start_id = state["start_id"]
    end_id = state["end_id"]
    
    notification = await message.reply_text("⏳ جاري التحميل...")
    
    success_count = 0
    error_count = 0
    total = end_id - start_id + 1
    
    for msg_id in range(start_id, end_id + 1):
        try:
            # جلب الرسالة
            try:
                msg = await client.get_messages(chat_id, msg_id)
            except PeerIdInvalid:
                # محاولة البحث في القنوات
                async for dialog in client.get_dialogs():
                    if dialog.chat.id == chat_id or str(dialog.chat.id).endswith(str(chat_id)[4:]):
                        msg = await client.get_messages(dialog.chat.id, msg_id)
                        break
                else:
                    error_count += 1
                    continue
            
            if not msg:
                error_count += 1
                continue
            
            # فحص نوع الوسيط
            if msg.media:
                await send_media(client, msg)
                success_count += 1
            else:
                error_count += 1
            
            # انتظار لتجنب FloodWait
            await asyncio.sleep(1)
            
        except FloodWaitError as e:
            await asyncio.sleep(e.value + 1)
        except ChannelPrivate:
            error_count += 1
            continue
        except Exception as e:
            logger.warning(f"Error at message {msg_id}: {e}")
            error_count += 1
            continue
        
        # تحديث الإشعار كل 5 مقاطع
        if msg_id % 5 == 0 or msg_id == end_id:
            progress_percent = ((msg_id - start_id + 1) / total) * 100
            await notification.edit_text(
                f"⏳ جاري التحميل...\n\n"
                f"📊 التقدم: {msg_id - start_id + 1}/{total} ({progress_percent:.1f}%)\n"
                f"✅ نجح: {success_count}\n"
                f"❌ فشل: {error_count}"
            )
    
    # الرسالة النهائية
    await notification.edit_text(
        f"✅ **تم التحميل بنجاح!**\n\n"
        f"📊 الملخص:\n"
        f"📥 الإجمالي: {total}\n"
        f"✅ نجح: {success_count}\n"
        f"❌ فشل: {error_count}\n\n"
        f"⏰ الوقت: {datetime.now().strftime('%H:%M:%S')}"
    )
    
    # حذف الحالة
    if user_id in user_state:
        del user_state[user_id]

# ========== دالة إرسال الوسيط ==========

async def send_media(client, msg):
    """إرسال الوسيط (فيديو، وثيقة، صورة) إلى Saved Messages"""
    try:
        if msg.video:
            await client.send_video(
                "me",
                msg.video.file_id,
                caption=msg.caption or "📹 Video"
            )
        elif msg.document:
            await client.send_document(
                "me",
                msg.document.file_id,
                caption=msg.caption or "📄 Document"
            )
        elif msg.photo:
            await client.send_photo(
                "me",
                msg.photo.file_id,
                caption=msg.caption or "🖼️ Photo"
            )
        elif msg.animation:
            await client.send_animation(
                "me",
                msg.animation.file_id,
                caption=msg.caption or "🎬 Animation"
            )
        elif msg.audio:
            await client.send_audio(
                "me",
                msg.audio.file_id,
                caption=msg.caption or "🎵 Audio"
            )
        elif msg.media_group_id:
            # معالجة الألبومات
            await handle_media_group(client, msg)
    except Exception as e:
        logger.warning(f"Error sending media: {e}")
        raise

# ========== معالج الألبومات ==========

async def handle_media_group(client, msg):
    """معالجة ألبومات الصور والفيديوهات"""
    try:
        # الحصول على جميع الرسائل بنفس media_group_id
        media_group_id = msg.media_group_id
        chat_id = msg.chat.id
        
        # محاولة الحصول على الرسائل
        messages = []
        for i in range(msg.message_id - 10, msg.message_id + 10):
            try:
                m = await client.get_messages(chat_id, i)
                if m and m.media_group_id == media_group_id:
                    messages.append(m)
            except:
                pass
        
        # إرسال كمجموعة
        if messages:
            media_list = []
            for m in messages:
                if m.video:
                    media_list.append(InputMediaVideo(m.video.file_id, caption=m.caption))
                elif m.photo:
                    media_list.append(InputMediaPhoto(m.photo.file_id, caption=m.caption))
                elif m.document:
                    media_list.append(InputMediaDocument(m.document.file_id, caption=m.caption))
            
            if media_list:
                await client.send_media_group("me", media_list)
    except Exception as e:
        logger.warning(f"Error handling media group: {e}")

# ========== البداية ==========

def run_flask():
    """تشغيل Flask في thread منفصل"""
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def main():
    """البداية الرئيسية"""
    logger.info("🚀 جاري تشغيل البوت...")
    
    # تشغيل Flask في thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Flask شغال على البورت {PORT}")
    
    # تشغيل البوت
    async with client:
        logger.info("✅ البوت متصل بنجاح")
        await client.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("❌ تم إيقاف البوت")

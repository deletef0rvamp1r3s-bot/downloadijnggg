import os
import re
import asyncio
import logging
import threading
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, PeerIdInvalid, ChannelPrivate
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto
from flask import Flask

# ========== الإعدادات ==========
API_ID = int(os.getenv("API_ID", "35909411"))
API_HASH = os.getenv("API_HASH", "d2e7f09b5aaeaf64904b8afd6b8057c7")
SESSION_STRING = os.getenv("SESSION_STRING")
PORT = int(os.getenv("PORT", 8080))

# ========== Logging ==========
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== Flask ==========
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
    in_memory=True,
    no_updates=False
)

user_state = {}

# ========== الأوامر ==========

@client.on_message(filters.me & filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text(
        "🤖 **بوت حفظ المحتوى**\n\n"
        "📌 **الاستخدام:**\n"
        "`https://t.me/channel_name/123`"
    )

@client.on_message(filters.me & filters.regex(r"^https://t\.me/"))
async def handle_link(client, message):
    link = message.text.strip()
    match = re.search(r"https://t\.me/(c/)?(.+?)/(\d+)", link)
    
    if not match:
        await message.reply_text("❌ صيغة خاطئة")
        return
    
    is_private = match.group(1)
    chat_identifier = match.group(2)
    msg_id = int(match.group(3))
    
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
        f"أرسل `تم` أو رقم المقطع الأخير"
    )

@client.on_message(filters.me & filters.text)
async def handle_text(client, message):
    user_id = message.chat.id
    text = message.text.strip().lower()
    
    if user_id not in user_state:
        return
    
    state = user_state[user_id]
    
    if text == "تم":
        end_id = state["start_id"]
    else:
        try:
            end_id = int(message.text.strip())
        except ValueError:
            return
    
    state["end_id"] = end_id
    
    asyncio.create_task(start_downloading(client, message, user_id))

async def start_downloading(client, message, user_id):
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
            try:
                msg = await client.get_messages(chat_id, msg_id)
            except PeerIdInvalid:
                error_count += 1
                await asyncio.sleep(1)
                continue
            
            if not msg or not msg.media:
                error_count += 1
                await asyncio.sleep(1)
                continue
            
            try:
                if msg.video:
                    await client.send_video("me", msg.video.file_id, caption=msg.caption or "📹")
                elif msg.document:
                    await client.send_document("me", msg.document.file_id, caption=msg.caption or "📄")
                elif msg.photo:
                    await client.send_photo("me", msg.photo.file_id, caption=msg.caption or "🖼️")
                elif msg.audio:
                    await client.send_audio("me", msg.audio.file_id, caption=msg.caption or "🎵")
                
                success_count += 1
            except Exception as e:
                logger.warning(f"Error sending: {e}")
                error_count += 1
            
            await asyncio.sleep(1)
            
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"Error: {e}")
            error_count += 1
        
        if msg_id % 3 == 0 or msg_id == end_id:
            progress = ((msg_id - start_id + 1) / total) * 100
            await notification.edit_text(
                f"⏳ جاري التحميل...\n\n"
                f"📊 {msg_id - start_id + 1}/{total} ({progress:.0f}%)\n"
                f"✅ {success_count} | ❌ {error_count}"
            )
    
    await notification.edit_text(
        f"✅ **انتهى التحميل!**\n\n"
        f"✅ نجح: {success_count}\n"
        f"❌ فشل: {error_count}"
    )
    
    if user_id in user_state:
        del user_state[user_id]

async def idle_loop():
    while True:
        await asyncio.sleep(1)

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Flask على البورت {PORT}")
    
    async with client:
        logger.info("✅ البوت متصل")
        await idle_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ خطأ: {e}")

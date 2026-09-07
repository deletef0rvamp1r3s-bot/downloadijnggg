import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto

web_server = Flask(__name__)

@web_server.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_server.run(host="0.0.0.0", port=port)

# بياناتك 
API_ID = 35909411
API_HASH = "d2e7f09b5aaeaf64904b8afd6b8057c7"
SESSION_STRING = os.environ.get("SESSION_STRING", "")

app = Client("my_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# قاموس لحفظ حالة المستخدم عند إرسال الرابط
user_state = {}

@app.on_message(filters.me & filters.regex(r"^https://t\.me/(c/)?(.+)/(\d+)$"))
async def fetch_restricted_video(client, message):
    link = message.text
    match = re.search(r"^https://t\.me/(c/)?(.+)/(\d+)$", link)
    if not match:
        return
        
    is_private = match.group(1)
    chat_identifier = match.group(2)
    msg_id = int(match.group(3))
    
    if is_private:
        target_chat_id = int(f"-100{chat_identifier}")
    else:
        target_chat_id = chat_identifier
        
    # حفظ الرابط في الذاكرة لانتظار ردك
    user_state[message.chat.id] = {
        "chat_id": target_chat_id,
        "start_id": msg_id
    }
    
    await message.reply_text(
        "خيارات الحفظ:\n\n"
        "1️⃣ لحفظ هذا المقطع فقط، أرسل كلمة: `تم`\n"
        "2️⃣ لحفظ مجموعة مقاطع، أرسل رقم المقطع الأخير (مثلاً `3500`)"
    )

@app.on_message(filters.me & filters.text & ~filters.regex(r"^https://t\.me/"))
async def process_choice(client, message):
    chat_id = message.chat.id
    if chat_id not in user_state:
        return
        
    text = message.text.strip()
    
    # التأكد من الإجابة
    if text == "تم":
        state = user_state.pop(chat_id)
        start_id = state["start_id"]
        end_id = start_id
    elif text.isdigit():
        state = user_state.pop(chat_id)
        start_id = state["start_id"]
        end_id = int(text)
        if end_id < start_id:
            await message.reply_text("❌ رقم المقطع الأخير يجب أن يكون أكبر من أو يساوي المقطع الأول.")
            return
    else:
        # إذا كان الكلام لا علاقة له بالأمر، يتجاهل
        return
        
    await start_downloading(client, message, state["chat_id"], start_id, end_id)

async def start_downloading(client, message, chat_id, start_id, end_id):
    notification = await message.reply_text(f"⏳ جاري تجهيز التحميل من المعرف {start_id} إلى {end_id}...")
    
    # فحص القدرة على الوصول للقناة أولاً (نفس كودك السابق)
    try:
        await client.get_messages(chat_id, start_id)
    except Exception as e:
        error_msg = str(e).lower()
        if "peer_id_invalid" in error_msg or "peer id invalid" in error_msg:
            await notification.edit_text("⏳ هذه القناة جديدة على ذاكرة البوت.\n🔍 جاري البحث عنها في محادثاتك للتعرف عليها...")
            
            found = False
            count = 0
            async for dialog in client.get_dialogs():
                count += 1
                if count % 50 == 0:
                    await notification.edit_text(f"🔍 مستمر في البحث... (تم فحص {count} محادثة)")
                
                if dialog.chat.id == chat_id:
                    found = True
                    break
            
            if not found:
                await notification.edit_text("❌ بحثت في جميع محادثاتك ولم أجد هذه القناة! تأكد أنك منضم إليها.")
                return
            
            await notification.edit_text("✅ تم التعرف على القناة بنجاح! جاري استكمال السحب...")
        else:
            await notification.edit_text(f"❌ حدث خطأ غير متوقع: {e}")
            return

    processed_groups = set()
    success_count = 0
    
    for current_id in range(start_id, end_id + 1):
        try:
            # تحديث الرسالة كل 3 مقاطع لتجنب حظر التعديل المستمر
            if current_id % 3 == 0 or current_id == start_id:
                await notification.edit_text(f"⏳ جاري معالجة المقطع رقم {current_id} من أصل {end_id}...\n✅ تم سحب {success_count} بنجاح حتى الآن.")
            
            msg = await client.get_messages(chat_id, current_id)
            if msg.empty or (not msg.video and not msg.document and not msg.photo and not msg.media_group_id):
                continue
                
            # التعامل مع الميديا قروب (الألبومات)
            if msg.media_group_id:
                if msg.media_group_id in processed_groups:
                    continue
                processed_groups.add(msg.media_group_id)
                
                media_group = await client.get_media_group(chat_id, current_id)
                media_files = []
                downloaded_paths = []
                
                for m in media_group:
                    if m.video or m.document or m.photo:
                        file_path = await m.download()
                        downloaded_paths.append(file_path)
                        
                        if m.video:
                            media_files.append(InputMediaVideo(file_path, caption=m.caption or ""))
                        elif m.document:
                            media_files.append(InputMediaDocument(file_path, caption=m.caption or ""))
                        elif m.photo:
                            media_files.append(InputMediaPhoto(file_path, caption=m.caption or ""))
                
                if media_files:
                    await client.send_media_group("me", media_files)
                    success_count += len(media_files)
                    
                # تنظيف الملفات بعد إرسال القروب
                for path in downloaded_paths:
                    if os.path.exists(path):
                        os.remove(path)
                        
            # التعامل مع المقطع الفردي
            else:
                if msg.video:
                    file_path = await msg.download()
                    thumb_path = None
                    if msg.video.thumbs:
                        thumb_path = await client.download_media(msg.video.thumbs[0].file_id)
                        
                    await client.send_video(
                        chat_id="me", 
                        video=file_path, 
                        caption=msg.caption or "✅ تم السحب بنجاح!",
                        duration=msg.video.duration or 0,
                        width=msg.video.width or 0,
                        height=msg.video.height or 0,
                        thumb=thumb_path
                    )
                    success_count += 1
                    if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
                    if os.path.exists(file_path): os.remove(file_path)
                    
                elif msg.document:
                    file_path = await msg.download()
                    thumb_path = None
                    if msg.document.thumbs:
                        thumb_path = await client.download_media(msg.document.thumbs[0].file_id)
                        
                    await client.send_document(
                        chat_id="me", 
                        document=file_path, 
                        caption=msg.caption or "✅ تم السحب بنجاح!",
                        thumb=thumb_path
                    )
                    success_count += 1
                    if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
                    if os.path.exists(file_path): os.remove(file_path)
                    
                elif msg.photo:
                    file_path = await msg.download()
                    await client.send_photo(
                        chat_id="me",
                        photo=file_path,
                        caption=msg.caption or "✅ تم السحب بنجاح!"
                    )
                    success_count += 1
                    if os.path.exists(file_path): os.remove(file_path)

            # وقت راحة بين المقاطع لمنع الحظر (FloodWait)
            await asyncio.sleep(2)
            
        except Exception as e:
            # إذا فشل مقطع معين (مثلاً محذوف)، يتجاهله ويكمل اللي بعده
            print(f"Error downloading {current_id}: {e}")
            continue

    await notification.edit_text(f"🎉 اكتملت العملية بنجاح!\n✅ إجمالي ما تم سحبه: {success_count}")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

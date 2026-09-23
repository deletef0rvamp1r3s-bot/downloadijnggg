import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto
from pyrogram.errors import FloodWait, MessageIdInvalid

# إعداد خادم الويب الخاص بـ Railway
web_server = Flask(__name__)

@web_server.route('/')
def home():
    return "Bot is alive and running super fast!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_server.run(host="0.0.0.0", port=port)

# إعدادات البوت
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# تفعيل البوت (sleep_threshold يمنع تخطي المقاطع عند طلبات الانتظار القصيرة تلقائياً)
app = Client("my_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, sleep_threshold=60)

user_states = {}

@app.on_message(filters.me & filters.regex(r"^https://t\.me/(c/)?(.+)/(\d+)$"))
async def fetch_restricted_video(client, message):
    link = message.text
    match = re.search(r"^https://t\.me/(c/)?(.+)/(\d+)$", link)
    if not match: return
        
    is_private = match.group(1)
    chat_identifier = match.group(2)
    msg_id = int(match.group(3))
    
    chat_id = int(f"-100{chat_identifier}") if is_private else chat_identifier
        
    user_states[message.from_user.id] = {"chat_id": chat_id, "start_id": msg_id}
    
    await message.reply_text(
        f"🔗 تم التقاط الرابط للرسالة رقم `{msg_id}`.\n\n"
        "💡 **كيف تريد السحب؟**\n"
        "1️⃣ لسحب هذا المقطع **فقط** ⬅️ أرسل كلمة: `فقط`\n"
        "2️⃣ لسحب **مجموعة مقاطع** ⬅️ أرسل رقم الرسالة النهائية (مثال: `6850`)"
    )

@app.on_message(filters.me & filters.text & ~filters.regex(r"^https://t\.me/"))
async def handle_download_choice(client, message):
    user_id = message.from_user.id
    if user_id not in user_states: return
        
    text = message.text.strip()
    state = user_states[user_id]
    chat_id = state["chat_id"]
    start_id = state["start_id"]
    
    if text == "فقط":
        end_id = start_id
    elif text.isdigit():
        end_id = int(text)
        if end_id <= start_id:
            return await message.reply_text("❌ يجب أن يكون الرقم النهائي أكبر من رقم البداية.")
    else:
        return
        
    del user_states[user_id]
    notification = await message.reply_text("⏳ جاري التحقق من القناة...")
    
    # التعرف على القناة بأمان
    try:
        await client.get_chat(chat_id)
    except Exception as e:
        if "peer" in str(e).lower():
            await notification.edit_text("⏳ جاري البحث عن القناة للتعرف عليها (تحديث البيانات)...")
            async for _ in client.get_dialogs(): pass # تحديث الكاش فقط
        else:
            return await notification.edit_text(f"❌ خطأ في القناة: {e}")
            
    await notification.edit_text(f"✅ تم بدء السحب من {start_id} إلى {end_id} (النسخة السريعة 🚀)...")
    
    processed_groups = set()
    success_count = 0
    
    # تقسيم الرسائل إلى دفعات (Chunks) لزيادة السرعة وتقليل حظر الـ API
    msg_ids = list(range(start_id, end_id + 1))
    chunk_size = 50 # جلب 50 رسالة دفعة واحدة بدلاً من واحدة تلو الأخرى
    
    for i in range(0, len(msg_ids), chunk_size):
        chunk = msg_ids[i:i + chunk_size]
        try:
            messages = await client.get_messages(chat_id, chunk)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            messages = await client.get_messages(chat_id, chunk)

        for msg in messages:
            if msg.empty: continue
            
            # تحديث شاشة العرض كل 5 رسائل
            if msg.id % 5 == 0:
                try: await notification.edit_text(f"🚀 جاري المعالجة... (وصلنا للرسالة {msg.id} من أصل {end_id})")
                except: pass # تجاهل خطأ عدم تغير النص
                
            retry_count = 0
            while retry_count < 3: # محاولة 3 مرات في حال الفشل
                try:
                    files_to_delete = []
                    
                    # --- 1. معالجة الألبومات (Media Group) ---
                    if msg.media_group_id:
                        if msg.media_group_id in processed_groups:
                            break # تم سحبه مسبقاً
                        processed_groups.add(msg.media_group_id)
                        
                        media_group = await client.get_media_group(chat_id, msg.id)
                        media_to_send = []
                        
                        for m in media_group:
                            if m.video or m.document or m.photo:
                                file_path = await m.download()
                                files_to_delete.append(file_path)
                                
                                thumb_path = None
                                thumb_obj = getattr(m.video or m.document, "thumbs", None)
                                if thumb_obj:
                                    thumb_path = await client.download_media(thumb_obj[0].file_id)
                                    files_to_delete.append(thumb_path)
                                    
                                if m.video:
                                    media_to_send.append(InputMediaVideo(media=file_path, duration=m.video.duration or 0, width=m.video.width or 0, height=m.video.height or 0, thumb=thumb_path))
                                elif m.document:
                                    media_to_send.append(InputMediaDocument(media=file_path, thumb=thumb_path))
                                elif m.photo:
                                    media_to_send.append(InputMediaPhoto(media=file_path))
                                    
                        if media_to_send:
                            await client.send_media_group("me", media_to_send)
                            success_count += len(media_to_send)
                            
                    # --- 2. معالجة الرسائل الفردية ---
                    elif msg.video or msg.document or msg.photo:
                        file_path = await msg.download()
                        files_to_delete.append(file_path)
                        
                        thumb_path = None
                        thumb_obj = getattr(msg.video or msg.document, "thumbs", None)
                        if thumb_obj:
                            thumb_path = await client.download_media(thumb_obj[0].file_id)
                            files_to_delete.append(thumb_path)
                            
                        if msg.video:
                            await client.send_video("me", file_path, duration=msg.video.duration or 0, width=msg.video.width or 0, height=msg.video.height or 0, thumb=thumb_path)
                        elif msg.document:
                            await client.send_document("me", file_path, thumb=thumb_path)
                        elif msg.photo:
                            await client.send_photo("me", file_path)
                            
                        success_count += 1

                    # تنظيف الملفات فوراً لتوفير مساحة Railway
                    for f in files_to_delete:
                        if f and os.path.exists(f): os.remove(f)
                    
                    break # نجحت العملية، اخرج من حلقة الإعادة (while)

                except FloodWait as e:
                    print(f"⚠️ حظر مؤقت لـ {e.value} ثانية... جاري الانتظار.")
                    await asyncio.sleep(e.value + 2)
                    retry_count += 1
                except Exception as inner_e:
                    print(f"❌ خطأ في الرسالة {msg.id}: {inner_e}")
                    # تنظيف الملفات في حال حدث خطأ أثناء الرفع
                    for f in files_to_delete:
                        if f and os.path.exists(f): os.remove(f)
                    retry_count += 1
                    await asyncio.sleep(2)
            
    await notification.edit_text(f"🎉 تمت العملية بنجاح!\n✅ تم سحب وإرسال {success_count} ملف بأقصى سرعة ممكنة.")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

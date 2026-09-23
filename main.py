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

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

app = Client("my_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# قاموس لتخزين حالة المستخدم مؤقتاً عند إرسال رابط
user_states = {}

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
        chat_id = int(f"-100{chat_identifier}")
    else:
        chat_id = chat_identifier
        
    # حفظ الرابط في ذاكرة البوت بانتظار أمرك القادم
    user_states[message.from_user.id] = {
        "chat_id": chat_id,
        "start_id": msg_id
    }
    
    await message.reply_text(
        f"🔗 تم التقاط الرابط للرسالة رقم `{msg_id}`.\n\n"
        "💡 **كيف تريد السحب؟**\n"
        "1️⃣ لسحب هذا المقطع **فقط** ⬅️ أرسل كلمة: `فقط`\n"
        "2️⃣ لسحب **مجموعة مقاطع** ⬅️ أرسل رقم الرسالة النهائية (مثال: `6850`)"
    )

# التقاط الرد (كلمة "فقط" أو أرقام)
@app.on_message(filters.me & filters.text & ~filters.regex(r"^https://t\.me/"))
async def handle_download_choice(client, message):
    user_id = message.from_user.id
    
    # إذا لم يكن هناك رابط محفوظ مسبقاً، تجاهل الرسالة
    if user_id not in user_states:
        return
        
    text = message.text.strip()
    state = user_states[user_id]
    chat_id = state["chat_id"]
    start_id = state["start_id"]
    
    if text == "فقط":
        end_id = start_id
    elif text.isdigit():
        end_id = int(text)
        if end_id <= start_id:
            await message.reply_text("❌ يجب أن يكون الرقم النهائي أكبر من رقم البداية. أعد إرسال الرقم بشكل صحيح.")
            return
    else:
        # رسالة عادية لا علاقة لها بالبوت
        return
        
    # مسح الحالة لبدء عملية جديدة لاحقاً
    del user_states[user_id]
    
    notification = await message.reply_text("⏳ جاري التحقق من القناة...")
    
    # --- التعرف على القناة ---
    try:
        await client.get_messages(chat_id, start_id)
    except Exception as e:
        if "peer_id_invalid" in str(e).lower() or "peer id invalid" in str(e).lower():
            await notification.edit_text("⏳ جاري البحث عن القناة للتعرف عليها...")
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
                return await notification.edit_text("❌ بحثت في محادثاتك ولم أجد القناة! تأكد أنك منضم إليها.")
        else:
            return await notification.edit_text(f"❌ حدث خطأ غير متوقع: {e}")
            
    # --- بدء عملية السحب ---
    await notification.edit_text(f"✅ تم بدء السحب من {start_id} إلى {end_id}...")
    processed_groups = set()
    success_count = 0
    
    for current_id in range(start_id, end_id + 1):
        try:
            # تحديث شاشة العرض كل 5 رسائل لمعرفة التقدم
            if current_id % 5 == 0 and current_id != start_id:
                await notification.edit_text(f"⏳ جاري المعالجة... (وصلنا للرسالة {current_id} من أصل {end_id})")
                
            msg = await client.get_messages(chat_id, current_id)
            if msg.empty:
                continue
                
            # --- 1. معالجة Group Media (الألبومات) ---
            if msg.media_group_id:
                # التأكد من عدم سحب نفس الألبوم مرتين
                if msg.media_group_id in processed_groups:
                    continue 
                processed_groups.add(msg.media_group_id)
                
                media_group = await client.get_media_group(chat_id, current_id)
                media_to_send = []
                files_to_delete = []
                
                for m in media_group:
                    if m.video or m.document or m.photo:
                        file_path = await m.download()
                        files_to_delete.append(file_path)
                        
                        # سحب الصورة المصغرة إن وجدت
                        thumb_path = None
                        if m.video and getattr(m.video, "thumbs", None):
                            thumb_path = await client.download_media(m.video.thumbs[0].file_id)
                        elif m.document and getattr(m.document, "thumbs", None):
                            thumb_path = await client.download_media(m.document.thumbs[0].file_id)
                            
                        if thumb_path:
                            files_to_delete.append(thumb_path)
                            
                        # تجهيز المقطع ليتم إرساله داخل الألبوم
                        if m.video:
                            media_to_send.append(
                                InputMediaVideo(
                                    media=file_path,
                                    duration=m.video.duration or 0,
                                    width=m.video.width or 0,
                                    height=m.video.height or 0,
                                    thumb=thumb_path
                                )
                            )
                        elif m.document:
                            media_to_send.append(InputMediaDocument(media=file_path, thumb=thumb_path))
                        elif m.photo:
                            media_to_send.append(InputMediaPhoto(media=file_path))
                            
                # إرسال الألبوم دفعة واحدة
                if media_to_send:
                    await client.send_media_group("me", media_to_send)
                    success_count += len(media_to_send)
                    
                # تنظيف خادم Railway من الملفات
                for f in files_to_delete:
                    if f and os.path.exists(f):
                        os.remove(f)
                        
            # --- 2. معالجة الرسائل الفردية ---
            else:
                if msg.video or msg.document or msg.photo:
                    file_path = await msg.download()
                    
                    thumb_path = None
                    if msg.video and getattr(msg.video, "thumbs", None):
                        thumb_path = await client.download_media(msg.video.thumbs[0].file_id)
                    elif msg.document and getattr(msg.document, "thumbs", None):
                        thumb_path = await client.download_media(msg.document.thumbs[0].file_id)
                        
                    if msg.video:
                        await client.send_video(
                            "me", file_path, 
                            duration=msg.video.duration or 0,
                            width=msg.video.width or 0,
                            height=msg.video.height or 0,
                            thumb=thumb_path
                        )
                    elif msg.document:
                        await client.send_document("me", file_path, thumb=thumb_path)
                    elif msg.photo:
                        await client.send_photo("me", file_path)
                        
                    success_count += 1
                    
                    # تنظيف
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    if thumb_path and os.path.exists(thumb_path):
                        os.remove(thumb_path)
                        
            # تأخير بسيط لمنع حظر تيليجرام (FloodWait) بسبب السحب السريع للكميات
            await asyncio.sleep(1.5)
            
        except Exception as inner_e:
            print(f"Error on message {current_id}: {inner_e}")
            continue
            
    await notification.edit_text(f"🎉 تمت العملية بنجاح!\n✅ تم سحب وإرسال {success_count} ملف.")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

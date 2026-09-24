import os
import re
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto, InputMediaAnimation, InputMediaAudio
from pyrogram.errors import FloodWait

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

app = Client("my_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, sleep_threshold=60)

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
        
    user_states[message.from_user.id] = {
        "chat_id": chat_id,
        "start_id": msg_id
    }
    
    await message.reply_text(
        f"🔗 تم التقاط الرابط للرسالة رقم `{msg_id}`.\n\n"
        "💡 أرسل `فقط` لسحب هذا المقطع، أو أرسل **رقم الرسالة النهائية** لسحب مجموعة."
    )

@app.on_message(filters.me & filters.text & ~filters.regex(r"^https://t\.me/"))
async def handle_download_choice(client, message):
    user_id = message.from_user.id
    
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
            await message.reply_text("❌ الرقم النهائي يجب أن يكون أكبر من رقم البداية.")
            return
    else:
        return
        
    del user_states[user_id]
    
    notification = await message.reply_text("⏳ جاري التحقق من القناة...")
    
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
            
    await notification.edit_text(f"✅ تم بدء السحب من {start_id} إلى {end_id}...")
    processed_groups = set()
    success_count = 0
    
    for current_id in range(start_id, end_id + 1):
        retry_count = 0
        
        while retry_count < 3: 
            files_to_delete = []
            file_path = None
            thumb_path = None
            
            try:
                if current_id % 2 == 0: # تحديث الرسالة بشكل أسرع لتعرف أين وصل
                    try: await notification.edit_text(f"⏳ جاري المعالجة... (وصلنا للرسالة {current_id} من أصل {end_id})")
                    except: pass
                    
                msg = await client.get_messages(chat_id, current_id)
                
                # إذا كانت الرسالة محذوفة أو نصية فقط (لا تحتوي على ميديا)، نتخطاها
                if msg.empty or not msg.media:
                    break 
                    
                # --- 1. معالجة الألبومات (Media Group) ---
                if msg.media_group_id:
                    if msg.media_group_id in processed_groups:
                        break 
                    processed_groups.add(msg.media_group_id)
                    
                    media_group = await client.get_media_group(chat_id, current_id)
                    media_to_send = []
                    
                    for m in media_group:
                        if m.media:
                            file_path = await m.download()
                            if not file_path: continue
                            files_to_delete.append(file_path)
                            
                            thumb_path = None
                            if m.video and getattr(m.video, "thumbs", None):
                                thumb_path = await client.download_media(m.video.thumbs[0].file_id)
                            elif m.document and getattr(m.document, "thumbs", None):
                                thumb_path = await client.download_media(m.document.thumbs[0].file_id)
                                
                            if thumb_path: files_to_delete.append(thumb_path)
                                
                            # تصنيف الميديا داخل الألبوم (بما فيها الـ GIF)
                            if m.video:
                                media_to_send.append(InputMediaVideo(media=file_path, duration=m.video.duration or 0, width=m.video.width or 0, height=m.video.height or 0, thumb=thumb_path))
                            elif m.animation:
                                media_to_send.append(InputMediaAnimation(media=file_path))
                            elif m.document:
                                media_to_send.append(InputMediaDocument(media=file_path, thumb=thumb_path))
                            elif m.photo:
                                media_to_send.append(InputMediaPhoto(media=file_path))
                            elif m.audio:
                                media_to_send.append(InputMediaAudio(media=file_path))
                                
                    if media_to_send:
                        await client.send_media_group("me", media_to_send)
                        success_count += len(media_to_send)
                        
                    for f in files_to_delete:
                        if f and os.path.exists(f): os.remove(f)
                        
                # --- 2. معالجة الرسائل الفردية (فيديو، صورة، GIF، صوت، ملف، ملصق) ---
                else:
                    if msg.media:
                        file_path = await msg.download()
                        if not file_path: break
                        
                        thumb_path = None
                        if msg.video and getattr(msg.video, "thumbs", None):
                            thumb_path = await client.download_media(msg.video.thumbs[0].file_id)
                        elif msg.document and getattr(msg.document, "thumbs", None):
                            thumb_path = await client.download_media(msg.document.thumbs[0].file_id)
                            
                        # إرسال الميديا بناءً على نوعها
                        if msg.video:
                            await client.send_video("me", file_path, duration=msg.video.duration or 0, width=msg.video.width or 0, height=msg.video.height or 0, thumb=thumb_path)
                        elif msg.animation:
                            await client.send_animation("me", file_path)
                        elif msg.sticker:
                            await client.send_sticker("me", file_path)
                        elif msg.voice:
                            await client.send_voice("me", file_path)
                        elif msg.audio:
                            await client.send_audio("me", file_path)
                        elif msg.document:
                            await client.send_document("me", file_path, thumb=thumb_path)
                        elif msg.photo:
                            await client.send_photo("me", file_path)
                            
                        success_count += 1
                        
                        if file_path and os.path.exists(file_path): os.remove(file_path)
                        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
                        
                break
                
            except FloodWait as e:
                print(f"⚠️ حظر مؤقت لـ {e.value} ثانية...")
                await asyncio.sleep(e.value + 2)
                retry_count += 1
            except Exception as inner_e:
                print(f"❌ خطأ في الرسالة {current_id}: {inner_e}")
                for f in files_to_delete:
                    if f and os.path.exists(f): os.remove(f)
                if file_path and os.path.exists(file_path): os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
                
                retry_count += 1
                await asyncio.sleep(2)
                
    await notification.edit_text(f"🎉 تمت العملية بنجاح!\n✅ تم سحب وإرسال {success_count} ملف.")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

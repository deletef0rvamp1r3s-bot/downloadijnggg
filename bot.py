import os
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters

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
        
    notification = await message.reply_text("⏳ جاري جلب المقطع...")
    
    try:
        target_msg = await client.get_messages(chat_id, msg_id)
    except Exception as e:
        error_msg = str(e).lower()
        if "peer_id_invalid" in error_msg or "peer id invalid" in error_msg:
            await notification.edit_text("⏳ هذه القناة جديدة على ذاكرة البوت.\n🔍 جاري البحث عنها في محادثاتك للتعرف عليها...")
            
            found = False
            count = 0
            # فحص المحادثات للتعرف على القناة
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
            
            try:
                target_msg = await client.get_messages(chat_id, msg_id)
            except Exception as inner_e:
                await notification.edit_text(f"❌ حدث خطأ بعد التعرف على القناة: {inner_e}")
                return
        else:
            await notification.edit_text(f"❌ حدث خطأ غير متوقع: {e}")
            return

    try:
        if target_msg.video or target_msg.document:
            await notification.edit_text("⏳ جاري تحميل المقطع من القناة... (قد يستغرق وقتاً حسب الحجم)")
            file_path = await target_msg.download()
            
            await notification.edit_text("⏳ جاري الإرسال إليك...")
            
            if target_msg.video:
                await client.send_video(chat_id="me", video=file_path, caption="✅ تم السحب بنجاح!")
            else:
                await client.send_document(chat_id="me", document=file_path, caption="✅ تم السحب بنجاح!")
            
            os.remove(file_path)
            await notification.delete()
        else:
            await notification.edit_text("❌ الرابط لا يحتوي على مقطع فيديو أو ملف مدعوم.")
    except Exception as e:
        await notification.edit_text(f"❌ حدث خطأ أثناء التحميل: {e}")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

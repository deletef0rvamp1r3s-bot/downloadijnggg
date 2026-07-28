import os
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters

# خادم ويب بسيط
web_server = Flask(__name__)

@web_server.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_server.run(host="0.0.0.0", port=port)

# سحب البيانات من متغيرات البيئة
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# تشغيل العميل باستخدام StringSession
app = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# الاستجابة للروابط في "الرسائل المحفوظة"
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
        
    try:
        notification = await message.reply_text("⏳ جاري جلب المقطع...")
        target_msg = await client.get_messages(chat_id, msg_id)
        
        if target_msg.video or target_msg.document:
            await notification.edit_text("⏳ جاري تحميل المقطع من القناة...")
            file_path = await target_msg.download()
            
            await notification.edit_text("⏳ جاري إرساله إليك...")
            
            if target_msg.video:
                await client.send_video(chat_id="me", video=file_path, caption="✅ تم السحب بنجاح!")
            else:
                await client.send_document(chat_id="me", document=file_path, caption="✅ تم السحب بنجاح!")
            
            os.remove(file_path)
            await notification.delete()
        else:
            await notification.edit_text("❌ الرابط لا يحتوي على مقطع فيديو أو ملف مدعوم.")
            
    except Exception as e:
        await message.reply_text(f"❌ حدث خطأ: {e}")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

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

# بياناتك (ثابتة في الكود)
API_ID = 35909411
API_HASH = "d2e7f09b5aaeaf64904b8afd6b8057c7"
# جلب نص الجلسة من متغيرات Railway
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# تشغيل العميل
app = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# الاستجابة للروابط في الرسائل المحفوظة
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
    
    # محاولة جلب الرسالة، وإذا لم يتعرف على القناة يبحث عنها تلقائياً
    try:
        target_msg = await client.get_messages(chat_id, msg_id)
    except Exception as e:
        if "PEER_ID_INVALID" in str(e) or "Peer id invalid" in str(e):
            await notification.edit_text("⏳ هذه القناة جديدة على ذاكرة البوت.. جاري البحث عنها للتعرف عليها...")
            found = False
            async for dialog in client.get_dialogs():
                if dialog.chat.id == chat_id:
                    found = True
                    break
            
            if not found:
                await notification.edit_text("❌ لم أتمكن من العثور على القناة! تأكد أنك منضم إليها بحسابك.")
                return
            
            try:
                # المحاولة مرة أخرى بعد التعرف التلقائي
                target_msg = await client.get_messages(chat_id, msg_id)
            except Exception as inner_e:
                await notification.edit_text(f"❌ حدث خطأ أثناء الوصول للمقطع: {inner_e}")
                return
        else:
            await notification.edit_text(f"❌ حدث خطأ: {e}")
            return

    # مرحلة التحميل والإرسال
    try:
        if target_msg.video or target_msg.document:
            await notification.edit_text("⏳ جاري التحميل... (قد يستغرق وقتاً حسب حجم المقطع)")
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

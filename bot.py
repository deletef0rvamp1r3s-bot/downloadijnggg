import os
import re
import asyncio
import logging
from threading import Thread

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import (
    InputMediaVideo,
    InputMediaDocument,
    InputMediaPhoto
)


# =========================
# إعداد Logs
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================
# فحص متغيرات Railway
# =========================

API_ID_VALUE = os.getenv("API_ID", "").strip()
API_HASH_VALUE = os.getenv("API_HASH", "").strip()
SESSION_VALUE = os.getenv("SESSION_STRING", "").strip()

print("API_ID موجود:", bool(API_ID_VALUE))
print("API_HASH موجود:", bool(API_HASH_VALUE))
print("SESSION موجود:", bool(SESSION_VALUE))
print("طول SESSION:", len(SESSION_VALUE))

if not API_ID_VALUE:
    raise RuntimeError("المتغير API_ID غير موجود في Railway")

if not API_HASH_VALUE:
    raise RuntimeError("المتغير API_HASH غير موجود في Railway")

if not SESSION_VALUE:
    raise RuntimeError("المتغير SESSION_STRING غير موجود في Railway")

try:
    API_ID = int(API_ID_VALUE)
except ValueError:
    raise RuntimeError("قيمة API_ID يجب أن تكون رقمًا فقط")


# =========================
# Web Server الخاص بـ Railway
# =========================

web_server = Flask(__name__)


@web_server.route("/")
def home():
    return "Userbot is alive and running!"


def run_web_server():
    port = int(os.getenv("PORT", "8080"))

    web_server.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# =========================
# Pyrogram Client
# =========================

app = Client(
    name="my_account",
    api_id=API_ID,
    api_hash=API_HASH_VALUE,
    session_string=SESSION_VALUE,
    in_memory=True
)


# حفظ حالة الطلبات
user_state = {}


# روابط عامة:
# https://t.me/channel/123
#
# روابط خاصة:
# https://t.me/c/123456789/123
LINK_PATTERN = r"^https://t\.me/(c/)?([^/\s]+)/(\d+)$"


# =========================
# عند بدء تشغيل الحساب
# =========================

@app.on_message(
    filters.chat("me") &
    filters.command("status")
)
async def status_command(client, message):
    me = await client.get_me()

    await message.reply_text(
        "✅ Userbot يعمل\n\n"
        f"👤 الاسم: {me.first_name}\n"
        f"🆔 ID: {me.id}\n"
        f"📛 Username: @{me.username or 'لا يوجد'}"
    )


# اختبار استقبال الرسائل من Saved Messages
@app.on_message(filters.chat("me"))
async def debug_saved_messages(client, message):
    logger.info(
        "رسالة وصلت إلى Saved Messages | id=%s | text=%r",
        message.id,
        message.text
    )

    if message.text and message.text.strip() == "/test":
        await message.reply_text("✅ الجلسة تعمل بشكل صحيح")


# =========================
# استقبال رابط تيليجرام
# =========================

@app.on_message(
    filters.chat("me") &
    filters.text &
    filters.regex(LINK_PATTERN)
)
async def receive_link(client, message):
    try:
        link = message.text.strip()

        logger.info("تم استقبال الرابط: %s", link)

        match = re.match(LINK_PATTERN, link)

        if not match:
            await message.reply_text("❌ الرابط غير صحيح")
            return

        is_private = match.group(1)
        chat_identifier = match.group(2)
        start_id = int(match.group(3))

        if is_private:
            target_chat_id = int(f"-100{chat_identifier}")
        else:
            target_chat_id = chat_identifier

        user_state[message.chat.id] = {
            "chat_id": target_chat_id,
            "start_id": start_id
        }

        await message.reply_text(
            "خيارات الحفظ:\n\n"
            "1️⃣ لحفظ هذا المقطع فقط أرسل:\n"
            "تم\n\n"
            "2️⃣ لحفظ مجموعة مقاطع أرسل رقم المقطع الأخير، مثال:\n"
            "3500"
        )

    except Exception as error:
        logger.exception("خطأ أثناء قراءة الرابط")

        await message.reply_text(
            f"❌ حدث خطأ أثناء قراءة الرابط:\n{error}"
        )


# =========================
# استقبال كلمة تم أو رقم النهاية
# =========================

@app.on_message(
    filters.chat("me") &
    filters.text &
    ~filters.regex(r"^https://t\.me/")
)
async def process_choice(client, message):
    try:
        chat_id = message.chat.id

        if chat_id not in user_state:
            return

        text = message.text.strip()
        state = user_state[chat_id]

        if text == "تم":
            start_id = state["start_id"]
            end_id = start_id

        elif text.isdigit():
            start_id = state["start_id"]
            end_id = int(text)

            if end_id < start_id:
                await message.reply_text(
                    "❌ رقم المقطع الأخير يجب أن يكون أكبر من "
                    "أو يساوي رقم المقطع الأول."
                )
                return

        else:
            return

        user_state.pop(chat_id, None)

        await download_messages(
            client=client,
            message=message,
            chat_id=state["chat_id"],
            start_id=start_id,
            end_id=end_id
        )

    except Exception as error:
        logger.exception("خطأ أثناء معالجة الاختيار")

        await message.reply_text(
            f"❌ حدث خطأ:\n{error}"
        )


# =========================
# تحميل الرسائل
# =========================

async def download_messages(
    client,
    message,
    chat_id,
    start_id,
    end_id
):
    notification = await message.reply_text(
        f"⏳ جاري التحميل من {start_id} إلى {end_id}..."
    )

    success_count = 0
    failed_count = 0
    processed_groups = set()

    # التأكد من الوصول للمحادثة
    try:
        await client.get_messages(chat_id, start_id)

    except Exception as error:
        logger.exception("تعذر الوصول إلى المحادثة")

        error_text = str(error).lower()

        if (
            "peer_id_invalid" in error_text or
            "peer id invalid" in error_text
        ):
            await notification.edit_text(
                "⏳ القناة غير معروفة للحساب.\n"
                "🔍 جاري البحث عنها في المحادثات..."
            )

            found = False
            checked = 0

            try:
                async for dialog in client.get_dialogs():
                    checked += 1

                    if checked % 50 == 0:
                        try:
                            await notification.edit_text(
                                f"🔍 جاري البحث...\n"
                                f"تم فحص {checked} محادثة"
                            )
                        except Exception:
                            pass

                    if str(dialog.chat.id) == str(chat_id):
                        found = True
                        break

            except Exception as search_error:
                await notification.edit_text(
                    f"❌ حدث خطأ أثناء البحث:\n{search_error}"
                )
                return

            if not found:
                await notification.edit_text(
                    "❌ لم أجد هذه القناة في محادثات الحساب.\n"
                    "تأكد أنك منضم إليها."
                )
                return

            await notification.edit_text(
                "✅ تم التعرف على القناة.\n"
                "⏳ جاري بدء التحميل..."
            )

        else:
            await notification.edit_text(
                f"❌ لا يمكن الوصول إلى القناة:\n{error}"
            )
            return

    # بدء معالجة الرسائل
    for current_id in range(start_id, end_id + 1):
        try:
            if current_id == start_id or current_id % 3 == 0:
                try:
                    await notification.edit_text(
                        f"⏳ جاري معالجة الرسالة {current_id} "
                        f"من أصل {end_id}\n\n"
                        f"✅ تم حفظ: {success_count}\n"
                        f"❌ فشل: {failed_count}"
                    )
                except Exception:
                    pass

            msg = await client.get_messages(
                chat_id,
                current_id
            )

            if not msg or msg.empty:
                continue

            if not (
                msg.video or
                msg.document or
                msg.photo
            ):
                continue

            # =========================
            # ألبوم
            # =========================

            if msg.media_group_id:
                group_id = msg.media_group_id

                if group_id in processed_groups:
                    continue

                processed_groups.add(group_id)

                media_group = await client.get_media_group(
                    chat_id,
                    current_id
                )

                media_list = []
                downloaded_files = []

                for item in media_group:
                    if not (
                        item.video or
                        item.document or
                        item.photo
                    ):
                        continue

                    file_path = await item.download()

                    if not file_path:
                        continue

                    downloaded_files.append(file_path)

                    caption = item.caption or ""

                    if item.video:
                        media_list.append(
                            InputMediaVideo(
                                media=file_path,
                                caption=caption
                            )
                        )

                    elif item.document:
                        media_list.append(
                            InputMediaDocument(
                                media=file_path,
                                caption=caption
                            )
                        )

                    elif item.photo:
                        media_list.append(
                            InputMediaPhoto(
                                media=file_path,
                                caption=caption
                            )
                        )

                if media_list:
                    # الحد الأقصى للمجموعة الواحدة 10 ملفات
                    for position in range(0, len(media_list), 10):
                        part = media_list[position:position + 10]

                        await client.send_media_group(
                            chat_id="me",
                            media=part
                        )

                        success_count += len(part)

                for file_path in downloaded_files:
                    delete_file(file_path)

            # =========================
            # فيديو منفرد
            # =========================

            elif msg.video:
                file_path = await msg.download()

                if not file_path:
                    continue

                thumb_path = None

                try:
                    if msg.video.thumbs:
                        thumb_path = await client.download_media(
                            msg.video.thumbs[0].file_id
                        )

                    await client.send_video(
                        chat_id="me",
                        video=file_path,
                        caption=msg.caption or "✅ تم الحفظ بنجاح",
                        duration=msg.video.duration or 0,
                        width=msg.video.width or 0,
                        height=msg.video.height or 0,
                        thumb=thumb_path
                    )

                    success_count += 1

                finally:
                    delete_file(file_path)
                    delete_file(thumb_path)

            # =========================
            # ملف
            # =========================

            elif msg.document:
                file_path = await msg.download()

                if not file_path:
                    continue

                thumb_path = None

                try:
                    if msg.document.thumbs:
                        thumb_path = await client.download_media(
                            msg.document.thumbs[0].file_id
                        )

                    await client.send_document(
                        chat_id="me",
                        document=file_path,
                        caption=msg.caption or "✅ تم الحفظ بنجاح",
                        thumb=thumb_path
                    )

                    success_count += 1

                finally:
                    delete_file(file_path)
                    delete_file(thumb_path)

            # =========================
            # صورة
            # =========================

            elif msg.photo:
                file_path = await msg.download()

                if not file_path:
                    continue

                try:
                    await client.send_photo(
                        chat_id="me",
                        photo=file_path,
                        caption=msg.caption or "✅ تم الحفظ بنجاح"
                    )

                    success_count += 1

                finally:
                    delete_file(file_path)

            await asyncio.sleep(2)

        except Exception as error:
            failed_count += 1

            logger.exception(
                "فشل التعامل مع الرسالة رقم %s",
                current_id
            )

            continue

    try:
        await notification.edit_text(
            "🎉 اكتملت العملية\n\n"
            f"✅ تم حفظ: {success_count}\n"
            f"❌ فشل: {failed_count}"
        )
    except Exception:
        pass


# =========================
# حذف الملفات المؤقتة
# =========================

def delete_file(file_path):
    if not file_path:
        return

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as error:
        logger.warning(
            "تعذر حذف الملف %s: %s",
            file_path,
            error
        )


# =========================
# تشغيل البرنامج
# =========================

if __name__ == "__main__":
    logger.info("تشغيل Web Server...")

    Thread(
        target=run_web_server,
        daemon=True
    ).start()

    logger.info("تشغيل Pyrogram...")

    try:
        app.run()

    except Exception as error:
        logger.exception(
            "توقف Pyrogram بسبب الخطأ التالي: %s",
            error
        )
        raise

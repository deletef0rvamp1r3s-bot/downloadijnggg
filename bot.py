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
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================
# Railway Web Server
# =========================

web_server = Flask(__name__)


@web_server.route("/")
def home():
    return "Userbot is alive and running!"


def run_web_server():
    port = int(os.environ.get("PORT", "8080"))
    web_server.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# =========================
# Environment Variables
# =========================

try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"].strip()
    SESSION_STRING = os.environ["SESSION_STRING"].strip()
except KeyError as error:
    raise RuntimeError(
        f"المتغير التالي غير موجود في Railway Variables: {error}"
    )


if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING فارغ. أضفه في Railway Variables."
    )


# =========================
# Pyrogram Client
# =========================

app = Client(
    name="my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True
)


# حالة المستخدم أثناء انتظار رقم النهاية
user_state = {}


# رابط عام:
# https://t.me/channel/123
#
# رابط خاص:
# https://t.me/c/123456789/123
LINK_PATTERN = r"^https://t\.me/(c/)?([^/\s]+)/(\d+)$"


# =========================
# Startup
# =========================

@app.on_message(filters.chat("me") & filters.command("status"))
async def status_command(client, message):
    me = await client.get_me()

    await message.reply_text(
        "✅ Userbot يعمل\n"
        f"👤 الحساب: {me.first_name}\n"
        f"🆔 ID: {me.id}"
    )


# =========================
# استقبال الرابط في Saved Messages
# =========================

@app.on_message(
    filters.chat("me") &
    filters.text &
    filters.regex(LINK_PATTERN)
)
async def fetch_restricted_video(client, message):
    try:
        link = message.text.strip()

        logger.info("تم استقبال الرابط: %s", link)

        match = re.match(LINK_PATTERN, link)

        if not match:
            await message.reply_text("❌ الرابط غير صحيح.")
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
            "1️⃣ لحفظ هذا المقطع فقط، أرسل:\n"
            "`تم`\n\n"
            "2️⃣ لحفظ مجموعة مقاطع، أرسل رقم المقطع الأخير، مثال:\n"
            "`3500`"
        )

    except Exception as error:
        logger.exception("حدث خطأ أثناء قراءة الرابط")
        await message.reply_text(
            f"❌ حدث خطأ أثناء قراءة الرابط:\n{error}"
        )


# =========================
# استقبال اختيار المستخدم
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

        await start_downloading(
            client=client,
            message=message,
            chat_id=state["chat_id"],
            start_id=start_id,
            end_id=end_id
        )

    except Exception as error:
        logger.exception("حدث خطأ في اختيار المستخدم")
        await message.reply_text(
            f"❌ حدث خطأ:\n{error}"
        )


# =========================
# تحميل وإرسال الملفات
# =========================

async def start_downloading(
    client,
    message,
    chat_id,
    start_id,
    end_id
):
    notification = await message.reply_text(
        f"⏳ جاري تجهيز التحميل من {start_id} إلى {end_id}..."
    )

    # اختبار الوصول إلى المحادثة
    try:
        test_message = await client.get_messages(
            chat_id,
            start_id
        )

        if not test_message or test_message.empty:
            logger.warning(
                "الرسالة الأولى غير موجودة: %s",
                start_id
            )

    except Exception as error:
        logger.exception("تعذر الوصول إلى المحادثة")

        error_text = str(error).lower()

        if (
            "peer_id_invalid" in error_text or
            "peer id invalid" in error_text
        ):
            await notification.edit_text(
                "⏳ القناة غير معروفة للحساب حاليًا.\n"
                "🔍 جاري البحث عنها في محادثات الحساب..."
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

            except Exception as dialog_error:
                await notification.edit_text(
                    f"❌ حدث خطأ أثناء البحث:\n{dialog_error}"
                )
                return

            if not found:
                await notification.edit_text(
                    "❌ لم أجد هذه القناة في محادثات الحساب.\n"
                    "تأكد أنك منضم للقناة وأن الرابط صحيح."
                )
                return

            await notification.edit_text(
                "✅ تم التعرف على القناة.\n"
                "⏳ جاري بدء التحميل..."
            )

        else:
            await notification.edit_text(
                f"❌ تعذر الوصول إلى المحادثة:\n{error}"
            )
            return

    processed_groups = set()
    success_count = 0
    failed_count = 0

    for current_id in range(start_id, end_id + 1):
        try:
            # تحديث رسالة الحالة كل 3 رسائل تقريبًا
            if (
                current_id == start_id or
                current_id % 3 == 0
            ):
                try:
                    await notification.edit_text(
                        f"⏳ جاري معالجة المقطع {current_id} "
                        f"من أصل {end_id}\n"
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

            has_media = (
                msg.video or
                msg.document or
                msg.photo
            )

            if not has_media:
                continue

            # =========================
            # ألبوم / Media Group
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

                media_files = []
                downloaded_paths = []

                for group_message in media_group:
                    if not (
                        group_message.video or
                        group_message.document or
                        group_message.photo
                    ):
                        continue

                    file_path = await group_message.download()

                    if not file_path:
                        continue

                    downloaded_paths.append(file_path)
                    caption = group_message.caption or ""

                    if group_message.video:
                        media_files.append(
                            InputMediaVideo(
                                media=file_path,
                                caption=caption
                            )
                        )

                    elif group_message.document:
                        media_files.append(
                            InputMediaDocument(
                                media=file_path,
                                caption=caption
                            )
                        )

                    elif group_message.photo:
                        media_files.append(
                            InputMediaPhoto(
                                media=file_path,
                                caption=caption
                            )
                        )

                if media_files:
                    # Telegram يسمح بحد أقصى 10 عناصر في المجموعة
                    for i in range(0, len(media_files), 10):
                        chunk = media_files[i:i + 10]

                        await client.send_media_group(
                            chat_id="me",
                            media=chunk
                        )

                        success_count += len(chunk)

                # حذف الملفات المؤقتة
                for file_path in downloaded_paths:
                    remove_file(file_path)

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
                    remove_file(file_path)
                    remove_file(thumb_path)

            # =========================
            # ملف / Document
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
                    remove_file(file_path)
                    remove_file(thumb_path)

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
                    remove_file(file_path)

            # راحة بسيطة لتقليل FloodWait
            await asyncio.sleep(2)

        except Exception as error:
            failed_count += 1

            logger.exception(
                "فشل تحميل الرسالة رقم %s",
                current_id
            )

            # يكمل بقية الرسائل حتى لو فشلت رسالة
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

def remove_file(file_path):
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

    web_thread = Thread(
        target=run_web_server,
        daemon=True
    )
    web_thread.start()

    logger.info("تشغيل Pyrogram...")

    try:
        app.run()
    except Exception:
        logger.exception("توقف البرنامج بسبب خطأ")
        raise

import logging
import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InputMediaVideo, InputMediaDocument, InputMediaPhoto
from pyrogram.errors import FloodWait
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
PORT = int(os.getenv("PORT", 8080))

app = Flask(__name__)
client = Client(
    "bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workers=1,
    in_memory=True
)

user_state = {}

@app.route("/health")
def health():
    return "OK", 200

@client.on_message(filters.private)
async def handle_message(_, message):
    try:
        user_id = message.chat.id
        text = message.text.strip() if message.text else ""
        
        logger.info(f"Message from {user_id}: {text}")
        
        if text.startswith("https://t.me/"):
            parts = text.split("/")
            try:
                start_id = int(parts[-1])
                chat_id_str = parts[-2]
                
                if chat_id_str.startswith("c"):
                    chat_id = -100 * int(chat_id_str[1:])
                else:
                    chat_id = int(chat_id_str)
                
                user_state[user_id] = {"chat_id": chat_id, "start_id": start_id}
                await message.reply("Link saved. Send 'done' for single item or end message ID.")
            except (ValueError, IndexError):
                await message.reply("Invalid link")
        
        elif text.lower() in ["تم", "done"]:
            if user_id not in user_state:
                await message.reply("Send link first")
                return
            
            state = user_state[user_id]
            await download_and_save(state["chat_id"], state["start_id"], state["start_id"], user_id, message)
        
        elif text.isdigit():
            if user_id not in user_state:
                await message.reply("Send link first")
                return
            
            state = user_state[user_id]
            end_id = int(text)
            await download_and_save(state["chat_id"], state["start_id"], end_id, user_id, message)
    
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await message.reply(f"Error: {str(e)[:100]}")

async def download_and_save(chat_id, start_id, end_id, user_id, message):
    try:
        media_list = []
        count = 0
        
        async for msg in client.get_chat_history(chat_id, limit=end_id - start_id + 1):
            if msg.message_id < start_id or msg.message_id > end_id:
                continue
            
            if msg.video:
                media_list.append(InputMediaVideo(media=msg.video.file_id, caption=msg.caption or ""))
                count += 1
            elif msg.document:
                media_list.append(InputMediaDocument(media=msg.document.file_id, caption=msg.caption or ""))
                count += 1
            elif msg.photo:
                media_list.append(InputMediaPhoto(media=msg.photo.file_id, caption=msg.caption or ""))
                count += 1
            
            if len(media_list) >= 10:
                await client.send_media_group("me", media_list)
                media_list = []
                await asyncio.sleep(2)
        
        if media_list:
            await client.send_media_group("me", media_list)
        
        await message.reply(f"Saved {count} media to Saved Messages")
        logger.info(f"Saved {count} media from chat {chat_id}")
    
    except FloodWait as fw:
        logger.warning(f"FloodWait: {fw.value}s")
        await asyncio.sleep(fw.value)
        await download_and_save(chat_id, start_id, end_id, user_id, message)
    except Exception as e:
        logger.error(f"Save error: {e}", exc_info=True)
        await message.reply(f"Error: {str(e)[:100]}")

async def idle_loop():
    while True:
        await asyncio.sleep(60)

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    async with client:
        logger.info("Bot started")
        await idle_loop()

if __name__ == "__main__":
    asyncio.run(main())

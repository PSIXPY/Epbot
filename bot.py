import os
import time
import logging
from flask import Flask, request
from telebot import TeleBot, types

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")

bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

message_links = {}

def get_sender_name(user):
    if not user:
        return "Неизвестный"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    if user.username:
        return f"{name} (@{user.username})"
    return name

def send_message_with_reply(target_chat_id, message, thread_id=None):
    reply_to_id = None
    reply_to_name = None
    
    if message.reply_to_message:
        original_msg_id = message.reply_to_message.message_id
        original_chat_id = message.reply_to_message.chat.id
        link_key = f"{original_chat_id}:{original_msg_id}"
        if link_key in message_links:
            reply_to_id = message_links[link_key]
            if message.reply_to_message.from_user:
                reply_to_name = get_sender_name(message.reply_to_message.from_user)
    
    sender_name = get_sender_name(message.from_user)
    
    # Формируем текст БЕЗ Markdown (обычный текст)
    if reply_to_name:
        header = f"📨 {sender_name} ответил(а) {reply_to_name}:\n\n"
    else:
        header = f"📨 От: {sender_name}\n\n"
    
    if message.caption:
        content = message.caption
    elif message.text:
        content = message.text
    else:
        content = "📎 Медиафайл"
    
    full_text = header + content
    
    try:
        if message.photo:
            sent = bot.send_photo(
                target_chat_id, message.photo[-1].file_id,
                caption=full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        elif message.video:
            sent = bot.send_video(
                target_chat_id, message.video.file_id,
                caption=full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        elif message.document:
            sent = bot.send_document(
                target_chat_id, message.document.file_id,
                caption=full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        elif message.audio:
            sent = bot.send_audio(
                target_chat_id, message.audio.file_id,
                caption=full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        elif message.voice:
            sent = bot.send_voice(
                target_chat_id, message.voice.file_id,
                caption=full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        elif message.sticker:
            sent = bot.send_sticker(
                target_chat_id, message.sticker.file_id,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
            time.sleep(0.3)
            bot.send_message(
                target_chat_id, 
                f"📨 От: {sender_name}",
                message_thread_id=thread_id,
                reply_to_message_id=sent.message_id
            )
        else:
            sent = bot.send_message(
                target_chat_id, full_text,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_id
            )
        
        source_key = f"{message.chat.id}:{message.message_id}"
        target_key = f"{target_chat_id}:{sent.message_id}"
        message_links[source_key] = sent.message_id
        message_links[target_key] = message.message_id
        
        if len(message_links) > 2000:
            keys_to_remove = list(message_links.keys())[:1000]
            for key in keys_to_remove:
                del message_links[key]
        
        time.sleep(0.5)
        
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

# === ОБРАБОТЧИКИ ===

@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    send_message_with_reply(CHAT_B, message, CHAT_B_THREAD)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def handle_chat_b(message):
    send_message_with_reply(CHAT_A, message)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id != CHAT_B_THREAD)
def ignore_other_threads(message):
    pass

# === ВЕБХУК ===

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Bad request", 400

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200

# === ЗАПУСК ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    
    logger.info(f"🤖 Бот запущен (без Markdown)")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Канал B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    
    app.run(host="0.0.0.0", port=port)

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

processed_ids = set()

def get_sender_name(message):
    user = message.from_user
    if not user:
        return "Неизвестный"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    if user.username:
        return f"{name} (@{user.username})"
    return name

def escape_md(text):
    special = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in special else c for c in text)

def send_to_target(target_chat_id, message, full_text, thread_id=None):
    try:
        if message.photo:
            bot.send_photo(target_chat_id, message.photo[-1].file_id, 
                          caption=full_text, parse_mode="MarkdownV2",
                          message_thread_id=thread_id)
        elif message.video:
            bot.send_video(target_chat_id, message.video.file_id, 
                          caption=full_text, parse_mode="MarkdownV2",
                          message_thread_id=thread_id)
        elif message.document:
            bot.send_document(target_chat_id, message.document.file_id, 
                            caption=full_text, parse_mode="MarkdownV2",
                            message_thread_id=thread_id)
        elif message.audio:
            bot.send_audio(target_chat_id, message.audio.file_id, 
                          caption=full_text, parse_mode="MarkdownV2",
                          message_thread_id=thread_id)
        elif message.voice:
            bot.send_voice(target_chat_id, message.voice.file_id, 
                          caption=full_text, parse_mode="MarkdownV2",
                          message_thread_id=thread_id)
        elif message.sticker:
            # ПЕРЕСЫЛАЕМ стикер (это работает!)
            bot.forward_message(
                chat_id=target_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=thread_id
            )
            # Отправляем подпись отдельно
            if full_text and "📎 *Медиафайл*" not in full_text:
                time.sleep(0.3)
                bot.send_message(target_chat_id, full_text, 
                               parse_mode="MarkdownV2",
                               message_thread_id=thread_id)
        elif message.video_note:
            bot.send_video_note(target_chat_id, message.video_note.file_id,
                              message_thread_id=thread_id)
            if full_text and "📎 *Медиафайл*" not in full_text:
                bot.send_message(target_chat_id, full_text,
                               parse_mode="MarkdownV2",
                               message_thread_id=thread_id)
        elif message.animation:
            bot.send_animation(target_chat_id, message.animation.file_id,
                             caption=full_text, parse_mode="MarkdownV2",
                             message_thread_id=thread_id)
        else:
            bot.send_message(target_chat_id, full_text, 
                           parse_mode="MarkdownV2",
                           message_thread_id=thread_id)
        time.sleep(1)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def forward_message(message, target_chat_id, thread_id=None):
    if message.message_id in processed_ids:
        return
    processed_ids.add(message.message_id)
    if len(processed_ids) > 1000:
        processed_ids.clear()
    
    try:
        sender = get_sender_name(message)
        header = f"📨 **От:** {escape_md(sender)}\n\n"
        
        if message.caption:
            content = message.caption
        elif message.text:
            content = message.text
        else:
            content = "📎 *Медиафайл*"
        
        full_text = header + escape_md(content)
        send_to_target(target_chat_id, message, full_text, thread_id)
        
    except Exception as e:
        logger.error(f"Ошибка пересылки: {e}")

# === ОБРАБОТЧИКИ ===

@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    forward_message(message, CHAT_B, CHAT_B_THREAD)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def handle_chat_b_thread(message):
    forward_message(message, CHAT_A)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id != CHAT_B_THREAD)
def ignore_other_threads(message):
    pass

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    bot.remove_webhook()
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    bot.set_webhook(url=webhook_url)
    
    logger.info(f"🤖 Бот запущен")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Канал B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    
    app.run(host="0.0.0.0", port=port)

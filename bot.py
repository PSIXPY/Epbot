import os
import time
import logging
from flask import Flask, request
from telebot import TeleBot, types

# === НАСТРОЙКИ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")

# === ИНИЦИАЛИЗАЦИЯ ===
bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

processed_ids = set()
DELAY = 1

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

def forward_message(message, target_chat_id):
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
        
        if message.photo:
            bot.send_photo(target_chat_id, message.photo[-1].file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.video:
            bot.send_video(target_chat_id, message.video.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.document:
            bot.send_document(target_chat_id, message.document.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.audio:
            bot.send_audio(target_chat_id, message.audio.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.voice:
            bot.send_voice(target_chat_id, message.voice.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.sticker:
            bot.send_sticker(target_chat_id, message.sticker.file_id)
            if full_text:
                bot.send_message(target_chat_id, full_text, parse_mode="MarkdownV2")
        else:
            bot.send_message(target_chat_id, full_text, parse_mode="MarkdownV2")
            
        time.sleep(DELAY)
    except Exception as e:
        logger.error(f"Ошибка: {e}")

@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    forward_message(message, CHAT_B)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B)
def handle_chat_b(message):
    forward_message(message, CHAT_A)

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
    
    # Удаляем старый вебхук и устанавливаем новый
    try:
        bot.remove_webhook()
        if RENDER_URL:
            bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
            logger.info(f"Вебхук установлен: {RENDER_URL}/{BOT_TOKEN}")
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")
    
    logger.info(f"🤖 Бот запущен | Чат A: {CHAT_A} | Чат B: {CHAT_B}")
    logger.info(f"Сервер слушает порт: {port}")
    
    # Запускаем Flask
    app.run(host="0.0.0.0", port=port, debug=False)

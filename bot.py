import os
import logging
import requests
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

def send_photo_direct(chat_id, photo_url, caption=None, thread_id=None):
    """Отправляет фото через прямой запрос к API Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption or "",
        "message_thread_id": thread_id if thread_id else None
    }
    # Убираем None значения
    data = {k: v for k, v in data.items() if v is not None}
    response = requests.post(url, data=data)
    return response.json()

@bot.message_handler(func=lambda m: True)
def forward_all(message):
    logger.info(f"📥 Получено: {message.content_type}")
    
    # Определяем получателя
    if message.chat.id == CHAT_A:
        target = CHAT_B
        thread = CHAT_B_THREAD
        logger.info(f"→ В чат B, тема {thread}")
    elif message.chat.id == CHAT_B and message.message_thread_id == CHAT_B_THREAD:
        target = CHAT_A
        thread = None
        logger.info(f"→ В чат A")
    else:
        logger.info(f"⏭ Игнорируем")
        return
    
    try:
        # Фото
        if message.photo:
            # Получаем file_id самого большого фото
            file_id = message.photo[-1].file_id
            logger.info(f"📸 Фото, file_id: {file_id}")
            
            # Отправляем фото
            bot.send_photo(target, file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Фото отправлено!")
        
        # Видео
        elif message.video:
            bot.send_video(target, message.video.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Видео отправлено!")
        
        # Голосовые
        elif message.voice:
            bot.send_voice(target, message.voice.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Голосовое отправлено!")
        
        # Документы
        elif message.document:
            bot.send_document(target, message.document.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Документ отправлен!")
        
        # Стикеры
        elif message.sticker:
            bot.send_sticker(target, message.sticker.file_id, message_thread_id=thread)
            logger.info(f"✅ Стикер отправлен!")
        
        # Текст
        elif message.text:
            bot.send_message(target, message.text, message_thread_id=thread)
            logger.info(f"✅ Текст отправлен!")
        
        # Всё остальное
        else:
            bot.forward_message(target, message.chat.id, message.message_id, message_thread_id=thread)
            logger.info(f"✅ Переслано!")
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Чат B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    
    app.run(host="0.0.0.0", port=port)

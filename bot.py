import os
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

@bot.message_handler(func=lambda m: True)
def forward_all(message):
    logger.info(f"📥 СООБЩЕНИЕ | Чат: {message.chat.id} | Тип: {message.content_type} | Тред: {message.message_thread_id}")
    
    # Определяем куда пересылать
    if message.chat.id == CHAT_A:
        target = CHAT_B
        thread = CHAT_B_THREAD
        logger.info(f"→ Отправляем в чат B (тема {thread})")
    elif message.chat.id == CHAT_B and message.message_thread_id == CHAT_B_THREAD:
        target = CHAT_A
        thread = None
        logger.info(f"→ Отправляем в чат A")
    else:
        logger.info(f"⏭ Игнорируем (не тот чат или тема)")
        return
    
    try:
        # Фото
        if message.photo:
            file_id = message.photo[-1].file_id
            bot.send_photo(target, file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Фото отправлено")
        
        # Видео
        elif message.video:
            bot.send_video(target, message.video.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Видео отправлено")
        
        # Голосовые
        elif message.voice:
            bot.send_voice(target, message.voice.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Голосовое отправлено")
        
        # Документы
        elif message.document:
            bot.send_document(target, message.document.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Документ отправлен")
        
        # Аудио
        elif message.audio:
            bot.send_audio(target, message.audio.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ Аудио отправлено")
        
        # Стикеры
        elif message.sticker:
            bot.send_sticker(target, message.sticker.file_id, message_thread_id=thread)
            logger.info(f"✅ Стикер отправлен")
            # Если есть подпись, отправляем отдельно
            if message.caption:
                bot.send_message(target, message.caption, message_thread_id=thread)
        
        # GIF
        elif message.animation:
            bot.send_animation(target, message.animation.file_id, caption=message.caption, message_thread_id=thread)
            logger.info(f"✅ GIF отправлен")
        
        # Видеосообщения (кружочки)
        elif message.video_note:
            bot.send_video_note(target, message.video_note.file_id, message_thread_id=thread)
            logger.info(f"✅ Видеосообщение отправлено")
        
        # Текст
        elif message.text:
            bot.send_message(target, message.text, message_thread_id=thread)
            logger.info(f"✅ Текст отправлен")
        
        # Всё остальное
        else:
            bot.forward_message(target, message.chat.id, message.message_id, message_thread_id=thread)
            logger.info(f"✅ Переслано (другой тип)")
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

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
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Чат B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    
    app.run(host="0.0.0.0", port=port)

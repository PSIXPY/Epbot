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

# ГЛОБАЛЬНЫЙ ОБРАБОТЧИК - ПЕРЕСЫЛАЕТ ВСЁ
@bot.message_handler(func=lambda m: True)
def forward_everything(message):
    try:
        logger.info(f"Сообщение: {message.content_type}")
        
        # Определяем получателя
        if message.chat.id == CHAT_A:
            target = CHAT_B
            thread = CHAT_B_THREAD
        elif message.chat.id == CHAT_B and message.message_thread_id == CHAT_B_THREAD:
            target = CHAT_A
            thread = None
        else:
            return
        
        # ПЕРЕСЫЛАЕМ ОРИГИНАЛ (работает для всего)
        bot.forward_message(
            chat_id=target,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=thread
        )
        logger.info(f"Переслано!")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")

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
    logger.info("БОТ ЗАПУЩЕН")
    app.run(host="0.0.0.0", port=port)

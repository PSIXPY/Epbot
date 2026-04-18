import os
import logging
import json
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Базовый URL Telegram API
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(chat_id, text, reply_to=None, thread_id=None):
    """Отправляет текстовое сообщение"""
    url = f"{API_URL}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "message_thread_id": thread_id
    }
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        logger.info(f"Текст отправлен: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка отправки текста: {e}")
        return None

def send_photo(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    """Отправляет фото"""
    url = f"{API_URL}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "photo": file_id,
        "message_thread_id": thread_id
    }
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        logger.info(f"Фото отправлено: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        return None

def send_voice(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    """Отправляет голосовое сообщение"""
    url = f"{API_URL}/sendVoice"
    data = {
        "chat_id": chat_id,
        "voice": file_id,
        "message_thread_id": thread_id
    }
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        logger.info(f"Голосовое отправлено: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка отправки голосового: {e}")
        return None

def forward_message(from_chat, to_chat, message_id, thread_id=None):
    """Пересылает сообщение"""
    url = f"{API_URL}/forwardMessage"
    data = {
        "chat_id": to_chat,
        "from_chat_id": from_chat,
        "message_id": message_id,
        "message_thread_id": thread_id
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        logger.info(f"Переслано: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка пересылки: {e}")
        return None

def process_update(update):
    """Обрабатывает входящее обновление"""
    if "message" not in update:
        return
    
    message = update["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    thread_id = message.get("message_thread_id")
    
    logger.info(f"📥 Получено | Чат: {chat_id} | Тред: {thread_id}")
    
    # Определяем получателя
    if chat_id == CHAT_A:
        target = CHAT_B
        target_thread = CHAT_B_THREAD
        logger.info(f"→ Отправляем в чат B")
    elif chat_id == CHAT_B and thread_id == CHAT_B_THREAD:
        target = CHAT_A
        target_thread = None
        logger.info(f"→ Отправляем в чат A")
    else:
        logger.info(f"⏭ Игнорируем")
        return
    
    # Получаем отправителя
    sender = message.get("from", {})
    sender_name = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()
    if not sender_name:
        sender_name = sender.get("username", "Пользователь")
    
    caption_text = f"📨 От: {sender_name}"
    
    # Обрабатываем разные типы сообщений
    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]
        caption = message.get("caption", "")
        full_caption = f"{caption_text}\n\n{caption}" if caption else caption_text
        send_photo(target, file_id, full_caption, thread_id=target_thread)
    
    elif "voice" in message:
        file_id = message["voice"]["file_id"]
        caption = message.get("caption", "")
        full_caption = f"{caption_text}\n\n{caption}" if caption else caption_text
        send_voice(target, file_id, full_caption, thread_id=target_thread)
    
    elif "video" in message:
        file_id = message["video"]["file_id"]
        caption = message.get("caption", "")
        full_caption = f"{caption_text}\n\n{caption}" if caption else caption_text
        send_photo(target, file_id, full_caption, thread_id=target_thread)
    
    elif "text" in message:
        text = f"{caption_text}\n\n{message['text']}"
        send_message(target, text, thread_id=target_thread)
    
    else:
        # Пересылаем остальные типы (стикеры, документы и т.д.)
        forward_message(chat_id, target, message_id, target_thread)

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        logger.info(f"Webhook вызван")
        process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    """Временный эндпоинт для установки вебхука"""
    url = f"{API_URL}/setWebhook?url={RENDER_URL}/{BOT_TOKEN}"
    response = requests.get(url)
    return response.json()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Устанавливаем вебхук
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    requests.get(f"{API_URL}/setWebhook?url={webhook_url}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН (чистый API)")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Чат B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    logger.info(f"   Вебхук: {webhook_url}")
    
    app.run(host="0.0.0.0", port=port)

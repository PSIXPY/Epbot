import os
import logging
import json
import requests
import urllib.parse
import wikipediaapi
import random
from flask import Flask, request
from datetime import datetime, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0))  # ID канала, чьи сообщения игнорируем
RENDER_URL = os.environ.get("RENDER_URL", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
message_links = {}


# === УМНАЯ ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    """Умный поиск: Википедия → поиск по содержимому → ссылки на поисковики"""
    try:
        wiki_wiki = wikipediaapi.Wikipedia(
            language='ru',
            user_agent='TelegramRelayBot/1.0 (https://t.me/your_bot)'
        )
        
        # 1. Поиск по точному заголовку
        page = wiki_wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # 2. Исправление регистра (каждое слово с заглавной)
        words = query.split()
        corrected_words = []
        for w in words:
            if len(w) > 1:
                corrected_words.append(w[0].upper() + w[1:].lower())
            else:
                corrected_words.append(w.upper())
        corrected = " ".join(corrected_words)
        
        if corrected != query:
            page = wiki_wiki.page(corrected)
            if page.exists():
                summary = page.summary[:500]
                if len(page.summary) > 500:
                    summary += "..."
                return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # 3. Поиск через API Википедии (по содержимому статей)
        search_url = "https://ru.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "utf8": 1,
            "srlimit": 3,
            "srwhat": "text"
        }
        
        response = requests.get(search_url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("query", {}).get("search"):
                results = data["query"]["search"]
                best_match = results[0]["title"]
                page = wiki_wiki.page(best_match)
                if page.exists():
                    summary = page.summary[:500]
                    if len(page.summary) > 500:
                        summary += "..."
                    return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # 4. Поиск в английской Википедии
        wiki_en = wikipediaapi.Wikipedia(
            language='en',
            user_agent='TelegramRelayBot/1.0 (https://t.me/your_bot)'
        )
        page = wiki_en.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}* (англ.)\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # 5. Если ничего не найдено — даём ссылки на поиск в интернете
        encoded_query = urllib.parse.quote(query)
        google_link = f"https://www.google.com/search?q={encoded_query}"
        yandex_link = f"https://yandex.ru/search/?text={encoded_query}"
        duck_link = f"https://duckduckgo.com/?q={encoded_query}"
        
        return f"""❌ В Википедии ничего не найдено по запросу '{query}'.

💡 *Результаты поиска в интернете:*

🔍 [Яндекс]({yandex_link})
🌐 [Google]({google_link})
🦆 [DuckDuckGo]({duck_link})

💬 *Совет:* Попробуйте переформулировать запрос или написать на английском."""
            
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        return "❌ Произошла ошибка при поиске. Попробуйте позже."


# === ОСНОВНЫЕ ФУНКЦИИ БОТА ===

def get_sender_name(sender):
    if not sender:
        return "Неизвестный"
    name = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()
    if not name:
        name = sender.get('username', 'Пользователь')
    if sender.get('username'):
        return f"{name} (@{sender['username']})"
    return name

def send_message(chat_id, text, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки текста: {e}")
        return None

def send_photo(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendPhoto"
    data = {"chat_id": chat_id, "photo": file_id, "message_thread_id": thread_id}
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        return None

def send_voice(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendVoice"
    data = {"chat_id": chat_id, "voice": file_id, "message_thread_id": thread_id}
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки голосового: {e}")
        return None

def send_video(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendVideo"
    data = {"chat_id": chat_id, "video": file_id, "message_thread_id": thread_id}
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки видео: {e}")
        return None

def send_sticker(chat_id, file_id, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendSticker"
    data = {"chat_id": chat_id, "sticker": file_id, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка отправки стикера: {e}")
        return None

def forward_message(from_chat, to_chat, message_id, thread_id=None):
    url = f"{API_URL}/forwardMessage"
    data = {
        "chat_id": to_chat,
        "from_chat_id": from_chat,
        "message_id": message_id,
        "message_thread_id": thread_id
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except Exception as e:
        logger.error(f"Ошибка пересылки: {e}")
        return None


# === ОСНОВНОЙ ОБРАБОТЧИК ===

def process_update(update):
    if "message" not in update:
        return
    
    message = update["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    thread_id = message.get("message_thread_id")
    
    logger.info(f"📥 Получено | Чат: {chat_id} | Тред: {thread_id}")
    
    # Определяем получателя
    if chat_id == CHAT_A:
        # === ФИЛЬТР: сообщения от канала SOURCE_CHANNEL не пересылаем ===
        if message.get("from") and message["from"].get("id") == SOURCE_CHANNEL:
            logger.info(f"⏭ Игнорируем сообщение от канала {SOURCE_CHANNEL} в чате A")
            return
        target = CHAT_B
        target_thread = CHAT_B_THREAD
    elif chat_id == CHAT_B and thread_id == CHAT_B_THREAD:
        target = CHAT_A
        target_thread = None
    else:
        logger.info(f"⏭ Игнорируем")
        return
    
    # Получаем отправителя
    sender = message.get("from", {})
    sender_name = get_sender_name(sender)
    
    # === ОБРАБОТКА КОМАНД ===
    if "text" in message:
        text = message["text"]
        
        # КОМАНДА /wiki
        if text.lower().startswith("/wiki"):
            search_query = text[5:].strip()
            if not search_query:
                help_text = """ℹ️ *Как использовать команду /wiki*

`/wiki Python` — поиск статьи о Python
`/wiki Илон маск` — найдёт статью об Илоне Маске
`/wiki почему Хорус предал Императора` — поиск по содержимому

🌐 Бот ищет в Википедии, а если не находит — даёт ссылки на Яндекс и Google!"""
                send_message(chat_id, help_text, thread_id=thread_id)
                return
            
            send_message(chat_id, f"🔍 Ищу *{search_query}* в Википедии...\n⏳ Пожалуйста, подождите.", thread_id=thread_id)
            result = search_wikipedia(search_query)
            send_message(chat_id, result, thread_id=thread_id)
            return
        
        # КОМАНДА /help
        if text.lower() in ["/help", "/start"]:
            help_text = """📖 *Доступные команды*

/wiki [запрос] — умный поиск в Википедии
/help — показать эту справку
/roll — случайное число от 1 до 100
/coin — орёл или решка
/time — текущее время
/date — сегодняшняя дата

📱 *Примеры:*
/wiki Python
/wiki Илон маск
/wiki почему Хорус предал Императора

⚡ Бот пересылает все сообщения между чатами и поддерживает ответы (реплаи).
🔍 Поиск в Википедии работает даже с маленькой буквы и по содержимому статей!"""
            send_message(chat_id, help_text, thread_id=thread_id)
            return
        
        # ПРОСТЫЕ КОМАНДЫ
        if text.lower() == "/roll":
            result = random.randint(1, 100)
            send_message(chat_id, f"🎲 Вам выпало: **{result}**", thread_id=thread_id)
            return
        
        if text.lower() == "/coin":
            result = random.choice(["Орёл", "Решка"])
            send_message(chat_id, f"🪙 {result}!", thread_id=thread_id)
            return
        
        if text.lower() == "/time":
            now = datetime.now().strftime("%H:%M:%S")
            send_message(chat_id, f"🕐 Текущее время: **{now}**", thread_id=thread_id)
            return
        
        if text.lower() == "/date":
            today = datetime.now().strftime("%d.%m.%Y")
            send_message(chat_id, f"📅 Сегодня: **{today}**", thread_id=thread_id)
            return
    
    # === ПРОВЕРКА ОТВЕТОВ (РЕПЛАЕВ) ===
    reply_to_id = None
    reply_to_name = None
    
    if "reply_to_message" in message:
        reply_msg = message["reply_to_message"]
        original_msg_id = reply_msg["message_id"]
        original_chat_id = reply_msg["chat"]["id"]
        
        link_key = f"{original_chat_id}:{original_msg_id}"
        if link_key in message_links:
            reply_to_id = message_links[link_key]
            if "from" in reply_msg:
                reply_to_name = get_sender_name(reply_msg["from"])
    
    # Формируем подпись
    if reply_to_name:
        caption_text = f"📨 {sender_name} ответил(а) {reply_to_name}"
    else:
        caption_text = f"📨 От: {sender_name}"
    
    # Получаем содержимое
    content_text = ""
    if "caption" in message:
        content_text = message["caption"]
    elif "text" in message:
        content_text = message["text"]
    
    full_caption = f"{caption_text}\n\n{content_text}" if content_text else caption_text
    
    # === ОТПРАВКА В ЗАВИСИМОСТИ ОТ ТИПА ===
    sent_msg_id = None
    
    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]
        sent_msg_id = send_photo(target, file_id, full_caption, reply_to_id, target_thread)
    
    elif "voice" in message:
        file_id = message["voice"]["file_id"]
        sent_msg_id = send_voice(target, file_id, full_caption, reply_to_id, target_thread)
    
    elif "video" in message:
        file_id = message["video"]["file_id"]
        sent_msg_id = send_video(target, file_id, full_caption, reply_to_id, target_thread)
    
    elif "sticker" in message:
        file_id = message["sticker"]["file_id"]
        sent_msg_id = send_sticker(target, file_id, reply_to_id, target_thread)
        if caption_text:
            send_message(target, caption_text, sent_msg_id, target_thread)
    
    elif "text" in message:
        sent_msg_id = send_message(target, full_caption, reply_to_id, target_thread)
    
    else:
        sent_msg_id = forward_message(chat_id, target, message_id, target_thread)
    
    # Сохраняем связь для ответов
    if sent_msg_id:
        source_key = f"{chat_id}:{message_id}"
        target_key = f"{target}:{sent_msg_id}"
        message_links[source_key] = sent_msg_id
        message_links[target_key] = message_id
        
        if len(message_links) > 2000:
            keys = list(message_links.keys())[:1000]
            for key in keys:
                del message_links[key]
    
    logger.info(f"✅ Обработано")


# === ВЕБХУК ===

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200


# === ЗАПУСК ===

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Устанавливаем вебхук
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    requests.get(f"{API_URL}/setWebhook?url={webhook_url}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Чат B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    if SOURCE_CHANNEL:
        logger.info(f"   Фильтр: сообщения от канала {SOURCE_CHANNEL} в чате A не пересылаются")
    logger.info("📖 Умный поиск в Википедии: по заголовкам, содержимому и интернету")
    
    app.run(host="0.0.0.0", port=port)

import os
import logging
import requests
import urllib.parse
import wikipediaapi
import random
from flask import Flask, request
from datetime import datetime

# === КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ СРЕДЫ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))                 # обычный чат A
CHAT_B = int(os.environ.get("CHAT_B", 0))                 # чат-форум B
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))   # топик B1 для связи с чатом A
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0)) # канал-источник
CHANNEL_THREAD = int(os.environ.get("CHANNEL_THREAD", 0)) # топик B2 для постов из канала
RENDER_URL = os.environ.get("RENDER_URL", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
message_links = {}  # для ответов (реплаев)

# === ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ (умная) ===
def search_wikipedia(query):
    try:
        wiki = wikipediaapi.Wikipedia(
            language='ru',
            user_agent='TelegramRelayBot/1.0'
        )
        page = wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # исправление регистра
        corrected = query[0].upper() + query[1:].lower()
        if corrected != query:
            page = wiki.page(corrected)
            if page.exists():
                summary = page.summary[:500]
                if len(page.summary) > 500:
                    summary += "..."
                return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # поиск через API
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
        resp = requests.get(search_url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("query", {}).get("search"):
                best = data["query"]["search"][0]["title"]
                page = wiki.page(best)
                if page.exists():
                    summary = page.summary[:500]
                    if len(page.summary) > 500:
                        summary += "..."
                    return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # английская википедия
        wiki_en = wikipediaapi.Wikipedia(
            language='en',
            user_agent='TelegramRelayBot/1.0'
        )
        page = wiki_en.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}* (англ.)\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        # ссылки на поиск
        encoded = urllib.parse.quote(query)
        return f"""❌ В Википедии ничего не найдено по запросу '{query}'.
💡 *Поиск в интернете:* [Google](https://www.google.com/search?q={encoded}) | [Яндекс](https://yandex.ru/search/?text={encoded})"""
    except Exception as e:
        logger.error(f"Ошибка Википедии: {e}")
        return "❌ Ошибка при поиске. Попробуйте позже."

# === БАЗОВЫЕ ФУНКЦИИ ОТПРАВКИ ===
def send_message(chat_id, text, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "text": text, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(f"{API_URL}/sendMessage", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"send_message error: {e}")
        return None

def send_photo(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "photo": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(f"{API_URL}/sendPhoto", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"send_photo error: {e}")
        return None

def send_voice(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "voice": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(f"{API_URL}/sendVoice", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"send_voice error: {e}")
        return None

def send_video(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "video": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(f"{API_URL}/sendVideo", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"send_video error: {e}")
        return None

def send_sticker(chat_id, file_id, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "sticker": file_id, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(f"{API_URL}/sendSticker", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"send_sticker error: {e}")
        return None

def forward_message(from_chat, to_chat, message_id, thread_id=None):
    data = {"chat_id": to_chat, "from_chat_id": from_chat, "message_id": message_id, "message_thread_id": thread_id}
    try:
        r = requests.post(f"{API_URL}/forwardMessage", data=data, timeout=10)
        return r.json().get("result", {}).get("message_id") if r.ok else None
    except Exception as e:
        logger.error(f"forward_message error: {e}")
        return None

def get_sender_name(sender):
    if not sender:
        return "Неизвестный"
    name = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()
    if not name:
        name = sender.get('username', 'Пользователь')
    if sender.get('username'):
        return f"{name} (@{sender['username']})"
    return name

# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===

# 1. Пересылка из канала → топик B2
@bot.message_handler(func=lambda m: m.chat.id == SOURCE_CHANNEL)
def handle_channel_post(message):
    logger.info(f"📢 Канал -> топик {CHANNEL_THREAD}")
    try:
        if message.photo:
            send_photo(CHAT_B, message.photo[-1].file_id, message.caption, thread_id=CHANNEL_THREAD)
        elif message.video:
            send_video(CHAT_B, message.video.file_id, message.caption, thread_id=CHANNEL_THREAD)
        elif message.document:
            # для документов используем forward, так как sendDocument с caption работает
            forward_message(message.chat.id, CHAT_B, message.message_id, CHANNEL_THREAD)
        elif message.voice:
            send_voice(CHAT_B, message.voice.file_id, message.caption, thread_id=CHANNEL_THREAD)
        elif message.sticker:
            send_sticker(CHAT_B, message.sticker.file_id, thread_id=CHANNEL_THREAD)
            if message.caption:
                send_message(CHAT_B, message.caption, thread_id=CHANNEL_THREAD)
        elif message.text:
            send_message(CHAT_B, message.text, thread_id=CHANNEL_THREAD)
        else:
            forward_message(message.chat.id, CHAT_B, message.message_id, CHANNEL_THREAD)
        logger.info("✅ Переслано из канала")
    except Exception as e:
        logger.error(f"Ошибка канал->топик: {e}")

# 2. Пересылка из чата A → топик B1 (с фильтром сообщений от канала)
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    # Не пересылаем сообщения, отправленные самим каналом-источником
    if message.from_user and message.from_user.id == SOURCE_CHANNEL:
        logger.info(f"⏭ Сообщение от канала {SOURCE_CHANNEL} проигнорировано в чате A")
        return
    forward_message(message, CHAT_B, CHAT_B_THREAD)

# 3. Пересылка из топика B1 → чат A
@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def handle_b1_thread(message):
    forward_message(message, CHAT_A, None)

# === ОБРАБОТКА КОМАНД (WIKI, HELP, ROLL...) ===
def process_update(update):
    if "message" not in update:
        return
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    thread_id = msg.get("message_thread_id")
    if "text" not in msg:
        return
    text = msg["text"]

    # /wiki
    if text.lower().startswith("/wiki"):
        query = text[5:].strip()
        if not query:
            send_message(chat_id, "ℹ️ Использование: `/wiki запрос`", thread_id=thread_id)
            return
        send_message(chat_id, f"🔍 Ищу *{query}* в Википедии...", thread_id=thread_id)
        result = search_wikipedia(query)
        send_message(chat_id, result, thread_id=thread_id)
        return

    # /help
    if text.lower() in ["/help", "/start"]:
        help_txt = """📖 *Команды бота*
/wiki [запрос] – поиск в Википедии
/roll – случайное число (1-100)
/coin – орёл/решка
/time – текущее время
/date – сегодняшняя дата
/help – эта справка

🔄 *Режим пересылки:*
• Чат A ↔ Топик B1
• Канал → Топик B2 (отдельный)
• Сообщения от канала в чате A игнорируются"""
        send_message(chat_id, help_txt, thread_id=thread_id)
        return

    # /roll
    if text.lower() == "/roll":
        send_message(chat_id, f"🎲 {random.randint(1, 100)}", thread_id=thread_id)
        return
    # /coin
    if text.lower() == "/coin":
        send_message(chat_id, f"🪙 {random.choice(['Орёл', 'Решка'])}", thread_id=thread_id)
        return
    # /time
    if text.lower() == "/time":
        send_message(chat_id, f"🕐 {datetime.now().strftime('%H:%M:%S')}", thread_id=thread_id)
        return
    # /date
    if text.lower() == "/date":
        send_message(chat_id, f"📅 {datetime.now().strftime('%d.%m.%Y')}", thread_id=thread_id)
        return

# === WEBHOOK ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    requests.get(f"{API_URL}/setWebhook?url={webhook_url}")
    logger.info("🚀 Бот запущен")
    logger.info(f"Чат A: {CHAT_A}")
    logger.info(f"Чат B: {CHAT_B}, топик B1: {CHAT_B_THREAD}, топик B2: {CHANNEL_THREAD}")
    logger.info(f"Канал-источник: {SOURCE_CHANNEL}")
    app.run(host="0.0.0.0", port=port)

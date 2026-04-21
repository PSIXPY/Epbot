import os
import logging
import json
import requests
import urllib.parse
import wikipediaapi
import random
from flask import Flask, request
from datetime import datetime, timedelta
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
message_links = {}
bot = TeleBot(BOT_TOKEN)

# Временное хранилище для скрытых сообщений
pending_secret = {}

# === УМНАЯ ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    try:
        wiki_wiki = wikipediaapi.Wikipedia(
            language='ru',
            user_agent='TelegramRelayBot/1.0'
        )
        page = wiki_wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
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
                best_match = data["query"]["search"][0]["title"]
                page = wiki_wiki.page(best_match)
                if page.exists():
                    summary = page.summary[:500]
                    if len(page.summary) > 500:
                        summary += "..."
                    return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
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
        encoded_query = urllib.parse.quote(query)
        google_link = f"https://www.google.com/search?q={encoded_query}"
        yandex_link = f"https://yandex.ru/search/?text={encoded_query}"
        duck_link = f"https://duckduckgo.com/?q={encoded_query}"
        return f"""❌ В Википедии ничего не найдено.

💡 [Google]({google_link}) | [Яндекс]({yandex_link}) | [DuckDuckGo]({duck_link})"""
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        return "❌ Ошибка при поиске."

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
        logger.error(f"send_message: {e}")
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
        logger.error(f"send_photo: {e}")
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
        logger.error(f"send_voice: {e}")
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
        logger.error(f"send_video: {e}")
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
        logger.error(f"send_sticker: {e}")
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
        logger.error(f"forward_message: {e}")
        return None

# === СКРЫТЫЕ СООБЩЕНИЯ ===
@bot.message_handler(commands=['msg'])
def start_secret_message(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "ℹ️ *Как отправить скрытое сообщение:*\n`/msg @username`\n\nПосле этого напишите текст сообщения.",
            parse_mode="Markdown"
        )
        return
    
    target_username = parts[1].lstrip("@")
    chat_id = message.chat.id
    sender_id = message.from_user.id
    sender_name = message.from_user.first_name
    
    pending_secret[chat_id] = {
        "target": target_username,
        "sender_id": sender_id,
        "sender_name": sender_name
    }
    
    markup = types.ForceReply(selective=True)
    bot.send_message(
        chat_id,
        f"🔐 *Скрытое сообщение для @{target_username}*\n\nНапишите текст ниже:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.reply_to_message and "Скрытое сообщение для @" in m.reply_to_message.text)
def handle_secret_text(message):
    chat_id = message.chat.id
    secret_text = message.text
    
    if chat_id not in pending_secret:
        bot.reply_to(message, "❌ Ошибка: не найден получатель. Попробуйте снова `/msg @username`")
        return
    
    target_username = pending_secret[chat_id]["target"]
    sender_id = pending_secret[chat_id]["sender_id"]
    sender_name = pending_secret[chat_id]["sender_name"]
    del pending_secret[chat_id]
    
    markup = InlineKeyboardMarkup()
    button = InlineKeyboardButton(
        text=f"📩 Скрытое сообщение от {sender_name}",
        callback_data=f"show_msg:{target_username}:{secret_text[:50]}:{sender_id}"
    )
    markup.add(button)
    
    bot.send_message(
        chat_id,
        f"🔔 *Новое скрытое сообщение* от {sender_name} для @{target_username}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    
    try:
        bot.delete_message(chat_id, message.reply_to_message.message_id)
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_msg"))
def handle_secret_callback(call):
    try:
        _, target_username, secret_text, sender_id = call.data.split(":", 3)
    except:
        bot.answer_callback_query(call.id, "❌ Ошибка формата сообщения.", show_alert=True)
        return
    
    if call.from_user.username != target_username:
        bot.answer_callback_query(call.id, "❌ Это сообщение не для вас!", show_alert=True)
        return
    
    bot.answer_callback_query(
        call.id,
        f"📩 Сообщение: {secret_text}",
        show_alert=True
    )
    
    try:
        bot.send_message(
            call.from_user.id,
            f"🔒 *Скрытое сообщение* от *ID {sender_id}*:\n\n{secret_text}",
            parse_mode="Markdown"
        )
    except:
        pass

# === ОСНОВНОЙ ОБРАБОТЧИК ===
def process_update(update):
    # Обработка постов в каналах (реакция 🔥)
    if "channel_post" in update:
        post = update["channel_post"]
        channel_id = post["chat"]["id"]
        if channel_id == -1001317416582 or channel_id == -1002185590715:
            try:
                url = f"{API_URL}/setMessageReaction"
                data = {
                    "chat_id": channel_id,
                    "message_id": post["message_id"],
                    "reaction": [{"type": "emoji", "emoji": "🔥"}]
                }
                requests.post(url, json=data, timeout=5)
            except:
                pass
        return

    if "message" not in update:
        return
    
    message = update["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    thread_id = message.get("message_thread_id")
    
    logger.info(f"📥 Получено | Чат: {chat_id}")
    
    # Игнорируем команду /msg (не пересылаем)
    if "text" in message and message["text"].lower().startswith("/msg"):
        logger.info(f"⏭ Команда /msg не пересылается")
        return
    
    # Определяем получателя
    if chat_id == CHAT_A:
        if message.get("from") and message["from"].get("id") == SOURCE_CHANNEL:
            logger.info(f"⏭ Игнорируем сообщение от канала {SOURCE_CHANNEL}")
            return
        target = CHAT_B
        target_thread = CHAT_B_THREAD
    elif chat_id == CHAT_B and thread_id == CHAT_B_THREAD:
        target = CHAT_A
        target_thread = None
    else:
        logger.info(f"⏭ Игнорируем")
        return
    
    sender = message.get("from", {})
    sender_name = get_sender_name(sender)
    
    # Обработка команд
    if "text" in message:
        text = message["text"]
        
        if text.lower().startswith("/wiki"):
            query = text[5:].strip()
            if not query:
                send_message(chat_id, "ℹ️ Использование: `/wiki запрос`", thread_id=thread_id)
                return
            send_message(chat_id, f"🔍 Ищу *{query}* в Википедии...", thread_id=thread_id)
            result = search_wikipedia(query)
            send_message(chat_id, result, thread_id=thread_id)
            return
        
        if text.lower() in ["/help", "/start"]:
            help_text = """📖 *Команды бота*

/wiki [запрос] — поиск в Википедии
/msg @username — скрытое сообщение
/roll — случайное число (1-100)
/coin — орёл/решка
/time — текущее время
/date — сегодняшняя дата
/help — эта справка"""
            send_message(chat_id, help_text, thread_id=thread_id)
            return
        
        if text.lower() == "/roll":
            send_message(chat_id, f"🎲 {random.randint(1, 100)}", thread_id=thread_id)
            return
        if text.lower() == "/coin":
            send_message(chat_id, f"🪙 {random.choice(['Орёл', 'Решка'])}", thread_id=thread_id)
            return
        if text.lower() == "/time":
            send_message(chat_id, f"🕐 {datetime.now().strftime('%H:%M:%S')}", thread_id=thread_id)
            return
        if text.lower() == "/date":
            send_message(chat_id, f"📅 {datetime.now().strftime('%d.%m.%Y')}", thread_id=thread_id)
            return
    
    # Пересылка сообщений
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
        bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200

# === ЗАПУСК ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    requests.get(f"{API_URL}/setWebhook?url={webhook_url}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info("📩 Команда /msg для скрытых сообщений (ForceReply)")
    
    app.run(host="0.0.0.0", port=port)

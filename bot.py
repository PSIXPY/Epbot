import os
import logging
import json
import requests
import urllib.parse
import wikipediaapi
import random
import time
import threading
import re
from flask import Flask, request
from datetime import datetime
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
message_links = {}
bot = TeleBot(BOT_TOKEN)

# Хранилище для скрытых сообщений
secret_messages = {}


# === ФУНКЦИЯ ДЛЯ РАБОТЫ С GROQ ===
def ask_groq(prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "qwen/qwen3-32b",
        "messages": [
            {"role": "system", "content": "Отвечай кратко, по существу и без лишних рассуждений."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL)
            answer = re.sub(r'/think.*?/think', '', answer, flags=re.DOTALL)
            answer = re.sub(r'<think/?>|</think>|/think', '', answer)
            answer = re.sub(r'\n\s*\n', '\n', answer).strip()
            return answer if answer else "🤔 Не удалось сформулировать ответ."
        elif response.status_code == 429:
            return "⚠️ Лимит запросов к ИИ исчерпан! Подождите 1 минуту."
        else:
            return f"❌ Ошибка Groq: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


# === ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    try:
        wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 {page.fullurl}]"
        
        search_url = "https://ru.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1,
            "srwhat": "text"
        }
        response = requests.get(search_url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("query", {}).get("search"):
                best = data["query"]["search"][0]["title"]
                page = wiki.page(best)
                if page.exists():
                    summary = page.summary[:500]
                    if len(page.summary) > 500:
                        summary += "..."
                    return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 {page.fullurl}]"
        
        return f"❌ В Википедии ничего не найдено по запросу '{query}'."
    except Exception as e:
        return f"❌ Ошибка: {e}"


# === ОСНОВНЫЕ ФУНКЦИИ ===
def get_sender_name(sender):
    if not sender:
        return "Неизвестный"
    name = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()
    if not name:
        name = sender.get('username', 'Пользователь')
    return f"{name} (@{sender['username']})" if sender.get('username') else name

def send_message(chat_id, text, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "text": text, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API_URL}/sendMessage", data=data, timeout=10)
    except Exception as e:
        logger.error(f"send_message error: {e}")

def send_photo(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "photo": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API_URL}/sendPhoto", data=data, timeout=10)
    except Exception as e:
        logger.error(f"send_photo error: {e}")

def send_voice(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "voice": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API_URL}/sendVoice", data=data, timeout=10)
    except Exception as e:
        logger.error(f"send_voice error: {e}")

def send_video(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "video": file_id, "caption": caption, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API_URL}/sendVideo", data=data, timeout=10)
    except Exception as e:
        logger.error(f"send_video error: {e}")

def send_sticker(chat_id, file_id, reply_to=None, thread_id=None):
    data = {"chat_id": chat_id, "sticker": file_id, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API_URL}/sendSticker", data=data, timeout=10)
    except Exception as e:
        logger.error(f"send_sticker error: {e}")

def forward_message(from_chat, to_chat, message_id, thread_id=None):
    data = {"chat_id": to_chat, "from_chat_id": from_chat, "message_id": message_id, "message_thread_id": thread_id}
    try:
        requests.post(f"{API_URL}/forwardMessage", data=data, timeout=10)
    except Exception as e:
        logger.error(f"forward_message error: {e}")


# === КОМАНДЫ ===
@bot.message_handler(commands=['ai'])
def ai_command(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ Пример: `/ai Как дела?`", parse_mode="Markdown")
        return
    msg = bot.reply_to(message, "🤖 Думаю...", parse_mode="Markdown")
    answer = ask_groq(prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['wiki'])
def wiki_command(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ Пример: `/wiki Python`", parse_mode="Markdown")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown")

@bot.message_handler(commands=['roll'])
def roll_command(message):
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")

@bot.message_handler(commands=['coin'])
def coin_command(message):
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")

@bot.message_handler(commands=['help', 'start'])
def help_command(message):
    help_text = """📖 *Команды бота*

/wiki [запрос] — поиск в Википедии
/ai [запрос] — общение с ИИ
/roll — случайное число (1-100)
/coin — орёл/решка
/help — эта справка

📩 *Скрытые сообщения:*
`@бот @получатель текст`"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


# === INLINE-РЕЖИМ ДЛЯ СКРЫТЫХ СООБЩЕНИЙ ===
@bot.inline_handler(func=lambda query: True)
def inline_query(query):
    try:
        text = query.query.strip()
        if not text:
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return
        target_username = parts[0].lstrip("@")
        content = parts[1]
        sender_id = query.from_user.id
        sender_name = query.from_user.first_name
        
        content_type = "text"
        file_id = None
        if content.startswith(("http://", "https://")):
            lower = content.lower()
            if any(ext in lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                content_type = "photo"
                file_id = content
            elif any(ext in lower for ext in ['.mp4', '.mov', '.avi', '.mkv']):
                content_type = "video"
                file_id = content
            elif any(ext in lower for ext in ['.mp3', '.ogg', '.wav', '.m4a']):
                content_type = "audio"
                file_id = content
        
        msg_id = f"sec_{int(datetime.now().timestamp() * 1000)}_{sender_id}"
        secret_messages[msg_id] = {
            "content": content, "content_type": content_type, "file_id": file_id,
            "sender_id": sender_id, "sender_name": sender_name,
            "target_username": target_username, "expires": datetime.now().timestamp() + 300
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"Отправить сообщение для @{target_username}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔔 *Новое скрытое сообщение* от {sender_name} для @{target_username}",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(query.id, [result], cache_time=0)
    except Exception as e:
        logger.error(f"Inline error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("read_"))
def handle_read_callback(call):
    try:
        msg_id = call.data[5:]
        if msg_id not in secret_messages:
            bot.answer_callback_query(call.id, "❌ Сообщение устарело.", show_alert=True)
            return
        data = secret_messages[msg_id]
        if call.from_user.username != data["target_username"]:
            bot.answer_callback_query(call.id, "❌ Не для вас!", show_alert=True)
            return
        if datetime.now().timestamp() > data["expires"]:
            bot.answer_callback_query(call.id, "❌ Сообщение устарело.", show_alert=True)
            del secret_messages[msg_id]
            return
        del secret_messages[msg_id]
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, f"📩 {data['content']}", show_alert=True)
    except Exception as e:
        logger.error(f"Callback error: {e}")

def clean_expired_messages():
    while True:
        time.sleep(86400)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]
        if expired:
            logger.info(f"🧹 Удалено {len(expired)} устаревших сообщений")

threading.Thread(target=clean_expired_messages, daemon=True).start()


# === ПЕРЕСЫЛКА СООБЩЕНИЙ (НЕ ТРОГАЕТ КОМАНДЫ) ===
def process_update(update):
    if "channel_post" in update:
        post = update["channel_post"]
        channel_id = post["chat"]["id"]
        if channel_id == -1001317416582 or channel_id == -1002185590715:
            try:
                requests.post(f"{API_URL}/setMessageReaction", json={
                    "chat_id": channel_id,
                    "message_id": post["message_id"],
                    "reaction": [{"type": "emoji", "emoji": "🔥"}]
                }, timeout=5)
            except:
                pass
        return

    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    thread_id = message.get("message_thread_id")
    message_text = message.get("text", "")

    # КОМАНДЫ НЕ ПЕРЕСЫЛАЕМ - ПРОСТО ВЫХОДИМ
    if message_text.startswith("/"):
        return

    # Определяем получателя
    if chat_id == CHAT_A:
        target = CHAT_B
        target_thread = CHAT_B_THREAD
    elif chat_id == CHAT_B and thread_id == CHAT_B_THREAD:
        target = CHAT_A
        target_thread = None
    else:
        return

    sender = message.get("from", {})
    sender_name = get_sender_name(sender)

    # Проверка ответов
    reply_to_id = None
    reply_to_name = None
    if "reply_to_message" in message:
        reply_msg = message["reply_to_message"]
        link_key = f"{reply_msg['chat']['id']}:{reply_msg['message_id']}"
        if link_key in message_links:
            reply_to_id = message_links[link_key]
            if "from" in reply_msg:
                reply_to_name = get_sender_name(reply_msg["from"])

    caption_text = f"📨 {sender_name} ответил(а) {reply_to_name}" if reply_to_name else f"📨 От: {sender_name}"
    content_text = message.get("caption") or message.get("text", "")
    full_caption = f"{caption_text}\n\n{content_text}" if content_text else caption_text

    if "photo" in message:
        send_photo(target, message["photo"][-1]["file_id"], full_caption, reply_to_id, target_thread)
    elif "voice" in message:
        send_voice(target, message["voice"]["file_id"], full_caption, reply_to_id, target_thread)
    elif "video" in message:
        send_video(target, message["video"]["file_id"], full_caption, reply_to_id, target_thread)
    elif "sticker" in message:
        send_sticker(target, message["sticker"]["file_id"], reply_to_id, target_thread)
        if caption_text:
            send_message(target, caption_text, reply_to_id, target_thread)
    elif "text" in message:
        send_message(target, full_caption, reply_to_id, target_thread)
    else:
        forward_message(chat_id, target, message_id, target_thread)

    # Сохраняем связь для ответов
    if "result" in locals() and result:
        message_links[f"{chat_id}:{message_id}"] = result
        message_links[f"{target}:{result}"] = message_id


# === ВЕБХУК ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        bot.process_new_updates([types.Update.de_json(update)])
        process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


# === ЗАПУСК ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    requests.get(f"{API_URL}/setWebhook?url={webhook_url}")
    
    logger.info("🤖 БОТ ЗАПУЩЕН (вебхук)")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info("Команды: /ai, /wiki, /roll, /coin, /help")
    
    app.run(host="0.0.0.0", port=port)

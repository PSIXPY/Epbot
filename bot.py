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
from datetime import datetime, timedelta
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
SOURCE_CHANNEL = int(os.environ.get("SOURCE_CHANNEL", 0))
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


# === ФУНКЦИЯ ДЛЯ РАБОТЫ С GROQ (с полной очисткой тегов /think) ===
def ask_groq(prompt):
    """Отправляет запрос к Groq — чёткие ответы без лишних рассуждений"""
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен. Добавьте GROQ_API_KEY в переменные окружения."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "qwen/qwen3-32b",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — полезный, дружелюбный ассистент. "
                    "Отвечай кратко, по существу и без лишних рассуждений. "
                    "Не используй теги /think, не описывай свой процесс мышления. "
                    "Просто давай чёткий ответ на вопрос пользователя."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            answer = result["choices"][0]["message"]["content"]
            
            # Полная очистка от тегов /think
            # Удаляем /think ... /think (включая пустые)
            answer = re.sub(r'/think\s*\n?\s*/think', '', answer, flags=re.DOTALL)
            # Удаляем /think с содержимым
            answer = re.sub(r'/think.*?/think', '', answer, flags=re.DOTALL)
            # Удаляем одиночные /think
            answer = re.sub(r'/think\s*\n?', '', answer)
            # Убираем лишние пустые строки
            answer = re.sub(r'\n\s*\n', '\n', answer).strip()
            
            return answer if answer else "🤔 Не удалось сформулировать ответ."
        
        elif response.status_code == 429:
            return "⚠️ *Лимит запросов к ИИ исчерпан!*\n\nПожалуйста, подождите 1 минуту и попробуйте снова."
        
        elif response.status_code == 413:
            return "❌ *Сообщение слишком длинное!*\n\nПожалуйста, задайте вопрос короче."
        
        else:
            error_msg = response.json().get("error", {}).get("message", "Неизвестная ошибка")
            return f"❌ Ошибка Groq: {error_msg}"
            
    except requests.exceptions.Timeout:
        return "❌ Превышено время ожидания ответа от Groq. Попробуйте позже."
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return f"❌ Ошибка при запросе к Groq: {str(e)[:100]}"


# === УМНАЯ ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    try:
        wiki_wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
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
        
        wiki_en = wikipediaapi.Wikipedia(language='en', user_agent='TelegramRelayBot/1.0')
        page = wiki_en.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}* (англ.)\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        encoded_query = urllib.parse.quote(query)
        return f"❌ В Википедии ничего не найдено.\n\n💡 [Google](https://www.google.com/search?q={encoded_query}) | [Яндекс](https://yandex.ru/search/?text={encoded_query})"
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
        return result["result"]["message_id"] if result.get("ok") else None
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
        return result["result"]["message_id"] if result.get("ok") else None
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
        return result["result"]["message_id"] if result.get("ok") else None
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
        return result["result"]["message_id"] if result.get("ok") else None
    except Exception as e:
        logger.error(f"send_video: {e}")
        return None


def send_audio(chat_id, file_id, caption=None, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendAudio"
    data = {"chat_id": chat_id, "audio": file_id, "message_thread_id": thread_id}
    if caption:
        data["caption"] = caption
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        return result["result"]["message_id"] if result.get("ok") else None
    except Exception as e:
        logger.error(f"send_audio: {e}")
        return None


def send_sticker(chat_id, file_id, reply_to=None, thread_id=None):
    url = f"{API_URL}/sendSticker"
    data = {"chat_id": chat_id, "sticker": file_id, "message_thread_id": thread_id}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        response = requests.post(url, data=data, timeout=10)
        result = response.json()
        return result["result"]["message_id"] if result.get("ok") else None
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
        return result["result"]["message_id"] if result.get("ok") else None
    except Exception as e:
        logger.error(f"forward_message: {e}")
        return None


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
            lower_content = content.lower()
            if any(ext in lower_content for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                content_type = "photo"
                file_id = content
            elif any(ext in lower_content for ext in ['.mp4', '.mov', '.avi', '.mkv']):
                content_type = "video"
                file_id = content
            elif any(ext in lower_content for ext in ['.mp3', '.ogg', '.wav', '.m4a']):
                content_type = "audio"
                file_id = content
        
        msg_id = f"sec_{int(datetime.now().timestamp() * 1000)}_{sender_id}"
        
        secret_messages[msg_id] = {
            "content": content,
            "content_type": content_type,
            "file_id": file_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "target_username": target_username,
            "timestamp": datetime.now().timestamp(),
            "expires": datetime.now().timestamp() + 300
        }
        
        markup = InlineKeyboardMarkup()
        button = InlineKeyboardButton(
            text="📩 Прочитать сообщение",
            callback_data=f"read_{msg_id}"
        )
        markup.add(button)
        
        description = f"{content[:50]}..." if content_type == "text" else f"📎 Медиафайл для @{target_username}"
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"Отправить сообщение для @{target_username}",
            description=description,
            input_message_content=types.InputTextMessageContent(
                f"🔔 *Новое скрытое сообщение* от {sender_name} для @{target_username}\n\nНажмите на кнопку, чтобы прочитать:",
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
            bot.answer_callback_query(call.id, "❌ Сообщение устарело или уже прочитано.", show_alert=True)
            return
        
        data = secret_messages[msg_id]
        content = data["content"]
        content_type = data["content_type"]
        file_id = data["file_id"]
        sender_id = data["sender_id"]
        sender_name = data["sender_name"]
        target_username = data["target_username"]
        
        if call.from_user.username != target_username:
            bot.answer_callback_query(call.id, "❌ Это сообщение не для вас!", show_alert=True)
            return
        
        if datetime.now().timestamp() > data.get("expires", datetime.now().timestamp() + 300):
            bot.answer_callback_query(call.id, "❌ Сообщение устарело.", show_alert=True)
            del secret_messages[msg_id]
            return
        
        del secret_messages[msg_id]
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        
        if content_type == "text":
            bot.answer_callback_query(
                call.id,
                f"📩 Сообщение от {sender_name}: {content}",
                show_alert=True
            )
        else:
            bot.answer_callback_query(call.id, f"📩 Сообщение от {sender_name}", show_alert=False)
        
        try:
            if content_type == "text":
                bot.send_message(
                    call.from_user.id,
                    f"🔒 *Скрытое сообщение* от *{sender_name}*:\n\n{content}",
                    parse_mode="Markdown"
                )
            elif content_type == "photo":
                bot.send_photo(
                    call.from_user.id,
                    file_id if file_id else content,
                    caption=f"🔒 *Скрытое сообщение* от *{sender_name}*",
                    parse_mode="Markdown"
                )
            elif content_type == "video":
                bot.send_video(
                    call.from_user.id,
                    file_id if file_id else content,
                    caption=f"🔒 *Скрытое сообщение* от *{sender_name}*",
                    parse_mode="Markdown"
                )
            elif content_type == "audio":
                bot.send_audio(
                    call.from_user.id,
                    file_id if file_id else content,
                    caption=f"🔒 *Скрытое сообщение* от *{sender_name}*",
                    parse_mode="Markdown"
                )
            else:
                bot.send_message(
                    call.from_user.id,
                    f"🔒 *Скрытое сообщение* от *{sender_name}*:\n\n{content}",
                    parse_mode="Markdown"
                )
            
            bot.send_message(
                sender_id,
                f"✅ @{target_username} прочитал(а) ваше скрытое сообщение",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Send to DM error: {e}")
            bot.send_message(
                call.from_user.id,
                f"🔒 *Скрытое сообщение* от *{sender_name}*:\n\n{content}",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Read callback error: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при открытии сообщения.", show_alert=True)


# === ОЧИСТКА УСТАРЕВШИХ СООБЩЕНИЙ ===
def clean_expired_messages():
    while True:
        time.sleep(86400)
        now = datetime.now().timestamp()
        expired = [msg_id for msg_id, data in secret_messages.items() 
                   if data.get("expires", now + 300) < now]
        for msg_id in expired:
            del secret_messages[msg_id]
        if expired:
            logger.info(f"🧹 Удалено {len(expired)} устаревших скрытых сообщений")

threading.Thread(target=clean_expired_messages, daemon=True).start()


# === ОБЫЧНЫЕ КОМАНДЫ ===
@bot.message_handler(commands=['ai'])
def ai_command(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ Напишите запрос после команды. Пример: `/ai Как дела?`", parse_mode="Markdown")
        return
    
    msg = bot.reply_to(message, "🤖 Думаю...", parse_mode="Markdown")
    answer = ask_groq(prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id, parse_mode="Markdown")


@bot.message_handler(commands=['wiki'])
def wiki_command(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ Использование: `/wiki запрос`", parse_mode="Markdown")
        return
    bot.reply_to(message, f"🔍 Ищу *{query}* в Википедии...", parse_mode="Markdown")
    result = search_wikipedia(query)
    bot.send_message(message.chat.id, result, parse_mode="Markdown", message_thread_id=message.message_thread_id)


@bot.message_handler(commands=['help', 'start'])
def help_command(message):
    help_text = """📖 *Команды бота*

/wiki [запрос] — поиск в Википедии
/ai [запрос] — общение с ИИ (Groq / qwen3-32b)
/roll — случайное число (1-100)
/coin — орёл/решка
/time — текущее время
/date — сегодняшняя дата
/help — эта справка

📩 *Скрытые сообщения:*
`@имя_бота @получатель текст`

🔄 Обычные сообщения пересылаются между чатами"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=['roll'])
def roll_command(message):
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")


@bot.message_handler(commands=['coin'])
def coin_command(message):
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")


@bot.message_handler(commands=['time'])
def time_command(message):
    bot.reply_to(message, f"🕐 {datetime.now().strftime('%H:%M:%S')}")


@bot.message_handler(commands=['date'])
def date_command(message):
    bot.reply_to(message, f"📅 {datetime.now().strftime('%d.%m.%Y')}")


# === ОСНОВНОЙ ОБРАБОТЧИК ===
def process_update(update):
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

    message_text = message.get("text", "")

    if message_text.startswith("/"):
        logger.info(f"⏭ Команда не пересылается")
        return

    if chat_id == CHAT_A:
        if message.get("from") and message["from"].get("id") == SOURCE_CHANNEL:
            logger.info(f"⏭ Игнорируем сообщение от канала")
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

    if reply_to_name:
        caption_text = f"📨 {sender_name} ответил(а) {reply_to_name}"
    else:
        caption_text = f"📨 От: {sender_name}"

    content_text = ""
    if "caption" in message:
        content_text = message["caption"]
    elif "text" in message:
        content_text = message["text"]

    full_caption = f"{caption_text}\n\n{content_text}" if content_text else caption_text

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
        bot.process_new_updates([types.Update.de_json(update)])
        process_update(update)
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
    logger.info("📩 Скрытые сообщения: @имя_бота @получатель текст")
    logger.info("🤖 Команда /ai для общения с ИИ (Groq / qwen3-32b) — с полной очисткой тегов /think")
    logger.info("🔄 Обычные сообщения пересылаются между чатами")

    app.run(host="0.0.0.0", port=port)

import os
import logging
import requests
import wikipediaapi
import random
import time
import threading
import re
import urllib.parse
import hashlib
from datetime import datetime, timedelta
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import PyPDF2
import docx
from io import BytesIO
from bs4 import BeautifulSoup

# === ПЕРЕМЕННЫЕ ===
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
bot = TeleBot(BOT_TOKEN)
message_links = {}
secret_messages = {}

# === КЭШ И ИСТОРИЯ ===
ai_cache = {}
CACHE_TTL = 3600
user_histories = {}
MAX_HISTORY = 10

# === КЕШ УЧАСТНИКОВ ДЛЯ МАССОВОГО УПОМИНАНИЯ ===
user_cache = {}
last_call_time = {}
CALL_COOLDOWN = 60


# === ФУНКЦИИ МАССОВОГО УПОМИНАНИЯ ===
def save_user_from_message(message):
    """Сохраняет пользователя в кеш из сообщения"""
    user = message.from_user
    if not user or user.is_bot:
        return
    chat_id = message.chat.id
    if chat_id not in user_cache:
        user_cache[chat_id] = {}
    user_cache[chat_id][user.id] = {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "last_seen": time.time()
    }


@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'voice', 'sticker', 'document'])
def catch_all_messages(message):
    save_user_from_message(message)


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(("калл ", "калл", "call ", "call")))
def call_all_no_slash(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    if chat_id in last_call_time and time.time() - last_call_time[chat_id] < CALL_COOLDOWN:
        remaining = int(CALL_COOLDOWN - (time.time() - last_call_time[chat_id]))
        bot.reply_to(message, f"⏳ Подождите {remaining} сек.")
        return
    
    text = message.text
    if text.lower().startswith("калл "):
        custom_text = text[5:].strip()
    elif text.lower().startswith("калл"):
        custom_text = text[4:].strip()
    elif text.lower().startswith("call "):
        custom_text = text[5:].strip()
    else:
        custom_text = text[4:].strip()
    
    if not custom_text:
        custom_text = "ВНИМАНИЕ!"
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    members = user_cache.get(chat_id, {})
    
    if not members:
        bot.send_message(chat_id, "❌ Список участников пуст. Напишите что-нибудь в чат, чтобы бот вас запомнил.", message_thread_id=thread_id)
        return
    
    mentions = []
    for uid, data in members.items():
        if uid == user_id:
            continue
        username = data.get("username")
        if username:
            mentions.append(f"@{username}")
        else:
            name = data.get("first_name", "Пользователь")
            mentions.append(f"[{name}](tg://user?id={uid})")
    
    if not mentions:
        bot.send_message(chat_id, "🤷‍♂️ Некого упоминать.", message_thread_id=thread_id)
        return
    
    chunk_size = 50
    for i in range(0, len(mentions), chunk_size):
        chunk = mentions[i:i+chunk_size]
        bot.send_message(chat_id, f"📢 {custom_text}\n\n{' '.join(chunk)}", parse_mode="Markdown", message_thread_id=thread_id)
    
    last_call_time[chat_id] = time.time()
    logger.info(f"калл в {chat_id}, упомянуто {len(mentions)}")


@bot.message_handler(commands=['all', 'call'])
def call_all_members(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    if chat_id in last_call_time and time.time() - last_call_time[chat_id] < CALL_COOLDOWN:
        remaining = int(CALL_COOLDOWN - (time.time() - last_call_time[chat_id]))
        bot.reply_to(message, f"⏳ Подождите {remaining} сек.")
        return
    
    parts = message.text.split(maxsplit=1)
    custom_text = parts[1] if len(parts) > 1 else "ВНИМАНИЕ!"
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    members = user_cache.get(chat_id, {})
    
    if not members:
        bot.send_message(chat_id, "❌ Список участников пуст. Напишите что-нибудь в чат, чтобы бот вас запомнил.", message_thread_id=thread_id)
        return
    
    mentions = []
    for uid, data in members.items():
        if uid == user_id:
            continue
        username = data.get("username")
        if username:
            mentions.append(f"@{username}")
        else:
            name = data.get("first_name", "Пользователь")
            mentions.append(f"[{name}](tg://user?id={uid})")
    
    if not mentions:
        bot.send_message(chat_id, "🤷‍♂️ Некого упоминать.", message_thread_id=thread_id)
        return
    
    chunk_size = 50
    for i in range(0, len(mentions), chunk_size):
        chunk = mentions[i:i+chunk_size]
        bot.send_message(chat_id, f"📢 {custom_text}\n\n{' '.join(chunk)}", parse_mode="Markdown", message_thread_id=thread_id)
    
    last_call_time[chat_id] = time.time()
    logger.info(f"/all в {chat_id}, упомянуто {len(mentions)}")


# === ФУНКЦИИ ДЛЯ GROQ ===
def ask_groq(user_id, prompt, system_prompt=None):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    
    cache_key = hashlib.md5(prompt.lower().encode()).hexdigest()
    if cache_key in ai_cache:
        cached_time, cached_answer = ai_cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_answer
    
    now = time.time()
    
    if user_id not in user_histories:
        user_histories[user_id] = {"messages": [], "created": now}
    
    user_histories[user_id]["messages"].append({"role": "user", "content": prompt, "timestamp": now})
    
    if len(user_histories[user_id]["messages"]) > MAX_HISTORY:
        user_histories[user_id]["messages"] = user_histories[user_id]["messages"][-MAX_HISTORY:]
    
    default_system = "Ты — полезный, дружелюбный ассистент. Отвечай кратко, по существу, учитывая контекст."
    messages = [
        {"role": "system", "content": system_prompt or default_system}
    ] + [{"role": msg["role"], "content": msg["content"]} for msg in user_histories[user_id]["messages"]]
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "qwen/qwen3-32b",
        "messages": messages,
        "max_tokens": 800,
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>|/think.*?/think|<think/?>|</think>|/think', '', answer, flags=re.DOTALL)
            answer = answer.strip()
            
            user_histories[user_id]["messages"].append({"role": "assistant", "content": answer, "timestamp": now})
            ai_cache[cache_key] = (time.time(), answer)
            
            return answer if answer else "🤔 Не удалось сформулировать ответ."
        elif response.status_code == 429:
            return "⚠️ Лимит запросов к ИИ исчерпан! Подождите 1 минуту."
        return f"❌ Ошибка Groq: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


def clean_expired_histories():
    while True:
        time.sleep(3600)
        now = time.time()
        expired_users = []
        for user_id, data in user_histories.items():
            if now - data.get("created", now) > 172800:
                expired_users.append(user_id)
        for user_id in expired_users:
            del user_histories[user_id]
        if expired_users:
            logger.info(f"🧹 Удалена история {len(expired_users)} пользователей")

threading.Thread(target=clean_expired_histories, daemon=True).start()


# === ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    try:
        wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        resp = requests.get("https://ru.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": 1, "srwhat": "text"
        }, timeout=10)
        
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
        
        encoded_query = urllib.parse.quote(query)
        google_link = f"https://www.google.com/search?q={encoded_query}"
        yandex_link = f"https://yandex.ru/search/?text={encoded_query}"
        
        return f"""❌ *Ничего не найдено в Википедии* по запросу: "{query}"

💡 *Попробуйте поискать в интернете:*

🔍 [Google]({google_link})
🌐 [Яндекс]({yandex_link})"""
        
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


# === ФУНКЦИЯ ВЕБ-ПОИСКА ===
def web_search(query):
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        results = soup.find_all('a', class_='result__a', limit=5)
        
        if not results:
            return None
        
        search_results = []
        for result in results:
            title = result.get_text()
            link = result.get('href')
            if link and not link.startswith('/'):
                search_results.append(f"• [{title}]({link})")
        
        if search_results:
            return "🔍 *Результаты поиска:*\n\n" + "\n".join(search_results)
        return None
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return None


# === АНАЛИЗ ИЗОБРАЖЕНИЙ ===
def analyze_image(image_url, prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен для анализа изображений."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    data = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Опиши это изображение подробно"},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }
        ],
        "max_tokens": 500
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        return f"❌ Ошибка анализа: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


# === ЧТЕНИЕ ФАЙЛОВ ===
def extract_text_from_file(file_bytes, filename):
    try:
        if filename.endswith('.pdf'):
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text[:3000]
        elif filename.endswith('.docx'):
            doc = docx.Document(BytesIO(file_bytes))
            text = "\n".join([para.text for para in doc.paragraphs])
            return text[:3000]
        elif filename.endswith('.txt'):
            return file_bytes.decode('utf-8')[:3000]
        else:
            return None
    except Exception as e:
        logger.error(f"File extraction error: {e}")
        return None


# === КОМАНДЫ ===
@bot.message_handler(commands=['ai'])
def ai_cmd(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ `/ai Как дела?`", parse_mode="Markdown")
        return
    
    if "найди" in prompt.lower() or "поищи" in prompt.lower() or "google" in prompt.lower():
        search_results = web_search(prompt)
        if search_results:
            bot.reply_to(message, search_results, parse_mode="Markdown", disable_web_page_preview=True)
            return
    
    user_id = message.from_user.id
    msg = bot.reply_to(message, "🤖 Думаю...", parse_mode="Markdown")
    answer = ask_groq(user_id, prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id, parse_mode="Markdown")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if not message.caption or not message.caption.lower().startswith('/ai'):
        return
    
    prompt = message.caption[4:].strip()
    if not prompt:
        prompt = "Опиши это изображение"
    
    msg = bot.reply_to(message, "🖼️ Анализирую изображение...")
    
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    
    result = analyze_image(file_url, prompt)
    bot.edit_message_text(result, message.chat.id, msg.message_id, parse_mode="Markdown")


@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.caption or not message.caption.lower().startswith('/ai'):
        return
    
    file_name = message.document.file_name
    if not (file_name.endswith('.pdf') or file_name.endswith('.docx') or file_name.endswith('.txt')):
        bot.reply_to(message, "❌ Поддерживаются только PDF, DOCX и TXT файлы.")
        return
    
    prompt = message.caption[4:].strip() or "Извлеки и кратко опиши содержимое файла"
    
    msg = bot.reply_to(message, "📄 Читаю файл...")
    
    file_info = bot.get_file(message.document.file_id)
    file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}").content
    
    text = extract_text_from_file(file_bytes, file_name)
    if text:
        user_id = message.from_user.id
        answer = ask_groq(user_id, f"{prompt}\n\nСодержимое файла:\n{text}")
        bot.edit_message_text(answer, message.chat.id, msg.message_id, parse_mode="Markdown")
    else:
        bot.edit_message_text("❌ Не удалось извлечь текст из файла.", message.chat.id, msg.message_id)


@bot.message_handler(commands=['wiki'])
def wiki_cmd(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ `/wiki Python`", parse_mode="Markdown")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown")


@bot.message_handler(commands=['roll'])
def roll_cmd(message):
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")


@bot.message_handler(commands=['coin'])
def coin_cmd(message):
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")


@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
        bot.reply_to(message, "🗑️ История ваших диалогов очищена!")
    else:
        bot.reply_to(message, "📭 У вас нет сохранённой истории.")


@bot.message_handler(commands=['help', 'start'])
def help_cmd(message):
    help_text = """📖 *Команды бота*

🤖 *ИИ и поиск:*
/wiki [запрос] — поиск в Википедии
/ai [вопрос] — общение с ИИ
/ai найди [запрос] — поиск в интернете
/clear_history — очистить историю диалога

📢 *Массовые уведомления:*
/all текст — созвать всех участников
калл текст — созвать всех (без слеша)

🎲 *Развлечения:*
/roll — случайное число (1-100)
/coin — орёл/решка

📩 *Скрытые сообщения:* `@бот @получатель текст`

🔄 *Автоматически:* пересылка сообщений между чатами и 🔥 на новые посты в каналах"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


# === ПЕРЕСЫЛКА СООБЩЕНИЙ ===
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def forward_to_b(message):
    bot.forward_message(CHAT_B, message.chat.id, message.message_id, message_thread_id=CHAT_B_THREAD)
    logger.info(f"Переслано из A в B")


@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def forward_to_a(message):
    bot.forward_message(CHAT_A, message.chat.id, message.message_id)
    logger.info(f"Переслано из B в A")


# === ПОСТЫ В КАНАЛАХ ===
@bot.channel_post_handler(func=lambda m: m.chat.id in [-1001317416582, -1002185590715])
def channel_reaction(message):
    try:
        bot.set_message_reaction(message.chat.id, message.message_id, reaction=[types.ReactionTypeEmoji(emoji="🔥")])
    except:
        pass


# === СКРЫТЫЕ СООБЩЕНИЯ ===
@bot.inline_handler(func=lambda query: True)
def inline_query(query):
    try:
        text = query.query.strip()
        if not text:
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return
        target = parts[0].lstrip("@")
        content = parts[1]
        msg_id = f"sec_{int(datetime.now().timestamp() * 1000)}"
        
        secret_messages[msg_id] = {
            "target": target, "content": content, "sender": query.from_user.first_name,
            "expires": datetime.now().timestamp() + 300
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id, title=f"Отправить @{target}", description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔔 *Скрытое сообщение* от {query.from_user.first_name} для @{target}",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(query.id, [result], cache_time=0)
    except Exception as e:
        logger.error(f"Inline error: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith("read_"))
def read_secret(call):
    msg_id = call.data[5:]
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение устарело", show_alert=True)
        return
    data = secret_messages[msg_id]
    if call.from_user.username != data["target"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    if datetime.now().timestamp() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Сообщение устарело", show_alert=True)
        del secret_messages[msg_id]
        return
    del secret_messages[msg_id]
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, f"📩 {data['content']}", show_alert=True)


def clean_expired_secrets():
    while True:
        time.sleep(86400)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]

threading.Thread(target=clean_expired_secrets, daemon=True).start()


# === ПРОГРЕВ БОТА ===
def warmup():
    logger.info("🔥 Прогреваю бота...")
    if GROQ_API_KEY:
        try:
            ask_groq(0, "ok")
            logger.info("✅ Groq API прогрето")
        except:
            pass
    try:
        bot.get_me()
        logger.info("✅ Telegram API прогрето")
    except:
        pass
    logger.info("🔥 Бот готов к работе!")


# === ВЕБХУК ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


# === ЗАПУСК ===
if __name__ == "__main__":
    warmup()
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info("Команды: /ai, /wiki, /roll, /coin, /all, /help")
    
    app.run(host="0.0.0.0", port=port)

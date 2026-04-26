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
import sqlite3
from datetime import datetime
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import PyPDF2
import docx
from io import BytesIO
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

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
secret_messages = {}

# === КЭШ И ИСТОРИЯ ДЛЯ ИИ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

# === БАЗА ДАННЫХ ПЕРЕВОДЧИКА ===
DB_PATH = "translator.db"

def init_translator_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS chat_translator
                 (chat_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()
    logger.info("📁 База данных переводчика инициализирована")

def is_translator_enabled(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT enabled FROM chat_translator WHERE chat_id=?", (chat_id,))
    result = c.fetchone()
    conn.close()
    return result[0] == 1 if result else False

def set_translator_enabled(chat_id, enabled):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO chat_translator (chat_id, enabled) VALUES (?, ?)", (chat_id, enabled))
    conn.commit()
    conn.close()

init_translator_db()


# === ОСНОВНЫЕ ФУНКЦИИ ===
def get_sender_name(user):
    if not user:
        return "Неизвестный"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    if user.username:
        return f"{name} (@{user.username})"
    return name


def ask_groq(user_id, prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    
    cache_key = hashlib.md5(prompt.lower().encode()).hexdigest()
    if cache_key in ai_cache:
        cached_time, cached_answer = ai_cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            return cached_answer
    
    if user_id not in user_histories:
        user_histories[user_id] = []
    
    user_histories[user_id].append({"role": "user", "content": prompt})
    
    if len(user_histories[user_id]) > MAX_HISTORY:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY:]
    
    messages = [
        {"role": "system", "content": "Отвечай кратко, по существу, учитывая контекст."},
        *user_histories[user_id]
    ]
    
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
            answer = re.sub(r'<think>.*?</think>|/think', '', answer, flags=re.DOTALL)
            answer = answer.strip()
            user_histories[user_id].append({"role": "assistant", "content": answer})
            ai_cache[cache_key] = (time.time(), answer)
            return answer
        elif response.status_code == 429:
            return "⚠️ Лимит запросов к ИИ исчерпан! Подождите 1 минуту."
        return f"❌ Ошибка Groq: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


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
                    {"type": "text", "text": prompt or "Опиши это изображение"},
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
        return None
    except Exception as e:
        logger.error(f"File extraction error: {e}")
        return None


def search_wikipedia(query):
    try:
        wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 {page.fullurl}]"
        
        encoded_query = urllib.parse.quote(query)
        return f"❌ Ничего не найдено в Википедии.\n\n🔍 [Google](https://www.google.com/search?q={encoded_query}) | [Яндекс](https://yandex.ru/search/?text={encoded_query})"
    except Exception as e:
        return f"❌ Ошибка: {e}"


# === КОМАНДЫ ===
@bot.message_handler(commands=['start', 'help'])
def help_command(message):
    help_text = """📖 *Команды бота*

🤖 *ИИ и поиск:*
/wiki [запрос] — поиск в Википедии
/ai [вопрос] — общение с ИИ
/ai найди [запрос] — поиск в интернете
/clear_history — очистить историю диалога

🖼️ *Анализ изображений:* фото + `/ai Опиши`
📄 *Чтение файлов:* файл + `/ai Прочитай`

🌐 *Переводчик:* (работает в этом чате)
/т — показать статус
/т on — включить перевод RU↔EN
/т off — выключить перевод

🎲 *Развлечения:*
/roll — случайное число (1-100)
/coin — орёл/решка

📩 *Скрытые сообщения:* `@бот @получатель текст`

🔄 *Автоматически:* пересылка сообщений между чатами и 🔥 на новые посты в каналах"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=['ai'])
def ai_command(message):
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
        bot.reply_to(message, "❌ Поддерживаются только PDF, DOCX и TXT")
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
def wiki_command(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ `/wiki Python`", parse_mode="Markdown")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown")


@bot.message_handler(commands=['roll'])
def roll_command(message):
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")


@bot.message_handler(commands=['coin'])
def coin_command(message):
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")


@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
        bot.reply_to(message, "🗑️ История ваших диалогов очищена!")
    else:
        bot.reply_to(message, "📭 У вас нет сохранённой истории.")


# === ПЕРЕВОДЧИК ===
@bot.message_handler(commands=['т'])
def translate_command(message):
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) < 2:
        status = "✅ Включён" if is_translator_enabled(chat_id) else "❌ Выключен"
        bot.reply_to(message, f"🌐 *Переводчик RU↔EN*\nСтатус: {status}\n\n"
                           f"`/т on` — включить\n`/т off` — выключить", parse_mode="Markdown")
        return
    
    action = parts[1].lower()
    
    if action == "on":
        set_translator_enabled(chat_id, True)
        bot.reply_to(message, "✅ *Переводчик RU↔EN включён!*\n\n"
                           "📌 *Как работает:*\n"
                           "• Русский текст → перевод на английский\n"
                           "• Английский текст → перевод на русский", parse_mode="Markdown")
    
    elif action == "off":
        set_translator_enabled(chat_id, False)
        bot.reply_to(message, "❌ *Переводчик выключен*", parse_mode="Markdown")


# === АВТОМАТИЧЕСКИЙ ПЕРЕВОД СООБЩЕНИЙ ===
@bot.message_handler(func=lambda m: True, content_types=['text'])
def auto_translate(message):
    chat_id = message.chat.id
    
    if not is_translator_enabled(chat_id):
        return
    
    if message.from_user.id == bot.get_me().id:
        return
    
    if message.text.startswith('/'):
        return
    
    text = message.text.strip()
    if not text:
        return
    
    try:
        has_cyrillic = any(ord(c) > 1024 for c in text)
        
        if has_cyrillic:
            translated = GoogleTranslator(source='ru', target='en').translate(text)
            lang_flag = "🇷🇺 → 🇬🇧"
        else:
            translated = GoogleTranslator(source='en', target='ru').translate(text)
            lang_flag = "🇬🇧 → 🇷🇺"
        
        if translated and translated != text:
            bot.reply_to(message, f"{lang_flag} *Перевод:*\n{translated}", parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")


# === ПЕРЕСЫЛКА СООБЩЕНИЙ ===
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def forward_to_b(message):
    try:
        sender_name = get_sender_name(message.from_user)
        caption = f"📨 От: {sender_name}"
        
        if message.text:
            bot.send_message(CHAT_B, f"{caption}\n\n{message.text}", message_thread_id=CHAT_B_THREAD)
        elif message.photo:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_photo(CHAT_B, message.photo[-1].file_id, caption=text, message_thread_id=CHAT_B_THREAD)
        elif message.video:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_video(CHAT_B, message.video.file_id, caption=text, message_thread_id=CHAT_B_THREAD)
        elif message.voice:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_voice(CHAT_B, message.voice.file_id, caption=text, message_thread_id=CHAT_B_THREAD)
        elif message.audio:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_audio(CHAT_B, message.audio.file_id, caption=text, message_thread_id=CHAT_B_THREAD)
        elif message.document:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_document(CHAT_B, message.document.file_id, caption=text, message_thread_id=CHAT_B_THREAD)
        elif message.sticker:
            bot.send_sticker(CHAT_B, message.sticker.file_id, message_thread_id=CHAT_B_THREAD)
            bot.send_message(CHAT_B, caption, message_thread_id=CHAT_B_THREAD)
        else:
            bot.send_message(CHAT_B, caption, message_thread_id=CHAT_B_THREAD)
        logger.info(f"Переслано из A в B")
    except Exception as e:
        logger.error(f"Ошибка A->B: {e}")


@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def forward_to_a(message):
    try:
        sender_name = get_sender_name(message.from_user)
        caption = f"📨 От: {sender_name}"
        
        if message.text:
            bot.send_message(CHAT_A, f"{caption}\n\n{message.text}")
        elif message.photo:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_photo(CHAT_A, message.photo[-1].file_id, caption=text)
        elif message.video:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_video(CHAT_A, message.video.file_id, caption=text)
        elif message.voice:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_voice(CHAT_A, message.voice.file_id, caption=text)
        elif message.audio:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_audio(CHAT_A, message.audio.file_id, caption=text)
        elif message.document:
            text = f"{caption}\n\n{message.caption}" if message.caption else caption
            bot.send_document(CHAT_A, message.document.file_id, caption=text)
        elif message.sticker:
            bot.send_sticker(CHAT_A, message.sticker.file_id)
            bot.send_message(CHAT_A, caption)
        else:
            bot.send_message(CHAT_A, caption)
        logger.info(f"Переслано из B в A")
    except Exception as e:
        logger.error(f"Ошибка B->A: {e}")


# === ПОСТЫ В КАНАЛАХ (РЕАКЦИЯ 🔥) ===
@bot.channel_post_handler(func=lambda m: m.chat.id in [-1001317416582, -1002185590715])
def channel_reaction(message):
    try:
        bot.set_message_reaction(message.chat.id, message.message_id, reaction=[types.ReactionTypeEmoji(emoji="🔥")])
        logger.info(f"🔥 Реакция на пост {message.message_id} в канале {message.chat.id}")
    except Exception as e:
        logger.error(f"Ошибка set_message_reaction: {e}")
        try:
            url = f"{API_URL}/setMessageReaction"
            data = {
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "reaction": [{"type": "emoji", "emoji": "🔥"}]
            }
            requests.post(url, json=data, timeout=5)
            logger.info(f"🔥 Реакция (API) на пост {message.message_id} в канале {message.chat.id}")
        except Exception as e2:
            logger.error(f"Ошибка API реакции: {e2}")


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
        msg_id = f"sec_{int(datetime.now().timestamp() * 1000)}_{query.from_user.id}"
        
        secret_messages[msg_id] = {
            "target": target,
            "content": content,
            "sender": query.from_user.first_name,
            "sender_id": query.from_user.id,
            "expires": datetime.now().timestamp() + 10800
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать сообщение", callback_data=f"read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"Отправить @{target}",
            description=content[:50],
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
        bot.answer_callback_query(call.id, "❌ Сообщение устарело или уже прочитано.", show_alert=True)
        return
    
    data = secret_messages[msg_id]
    target_username = data["target"]
    content = data["content"]
    sender_name = data["sender"]
    expires = data["expires"]
    
    if call.from_user.username != target_username:
        bot.answer_callback_query(call.id, "❌ Это сообщение не для вас!", show_alert=True)
        return
    
    if datetime.now().timestamp() > expires:
        bot.answer_callback_query(call.id, "❌ Сообщение устарело (хранится 3 часа).", show_alert=True)
        del secret_messages[msg_id]
        return
    
    bot.answer_callback_query(
        call.id,
        f"📩 Сообщение от {sender_name}:\n\n{content}",
        show_alert=True
    )
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    del secret_messages[msg_id]
    logger.info(f"📩 Скрытое сообщение {msg_id} прочитано и удалено")


def clean_expired_secrets():
    while True:
        time.sleep(3600)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]
        if expired:
            logger.info(f"🧹 Удалено {len(expired)} устаревших скрытых сообщений")

threading.Thread(target=clean_expired_secrets, daemon=True).start()


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
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info("Команды: /ai, /wiki, /roll, /coin, /т, /help")
    logger.info("🔥 Реакции на каналы: включены")
    logger.info("🎵 Пересылка аудиофайлов: включена")
    logger.info("🌐 Переводчик RU↔EN: включён (включается командой /т on)")
    
    app.run(host="0.0.0.0", port=port)

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
import json
import tempfile
from datetime import datetime, timedelta
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

# === ДИАГНОСТИКА ПЕРЕСЫЛКИ ===
logger.info(f"🔧 ДИАГНОСТИКА: CHAT_A={CHAT_A}, CHAT_B={CHAT_B}, CHAT_B_THREAD={CHAT_B_THREAD}")

@bot.message_handler(func=lambda m: True)
def debug_all_messages(message):
    logger.info(f"🔔 ПОЛУЧЕНО СООБЩЕНИЕ: chat={message.chat.id}, user={message.from_user.id}, thread={message.message_thread_id}, text={message.text[:50] if message.text else 'None'}")
    # Не отвечаем, чтобы не мешать работе

# === КЭШ И ИСТОРИЯ ДЛЯ ИИ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

# === НАСТРОЙКИ ПЕРЕВОДЧИКА ===
TRANSLATOR_SETTINGS_FILE = os.path.join(tempfile.gettempdir(), "translator_settings.json")

def load_translator_settings():
    if os.path.exists(TRANSLATOR_SETTINGS_FILE):
        try:
            with open(TRANSLATOR_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_translator_settings(settings):
    try:
        with open(TRANSLATOR_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Настройки переводчика сохранены")
    except Exception as e:
        logger.error(f"Ошибка сохранения настроек: {e}")

translator_settings = load_translator_settings()

def is_translator_enabled(chat_id):
    return translator_settings.get(str(chat_id), False)

def set_translator_enabled(chat_id, enabled):
    translator_settings[str(chat_id)] = enabled
    save_translator_settings(translator_settings)

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = os.path.join(tempfile.gettempdir(), "reminders.json")

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_reminders(reminders):
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(reminders, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Напоминания сохранены")
    except Exception as e:
        logger.error(f"Ошибка сохранения напоминаний: {e}")

reminders = load_reminders()
reminder_counter = max([r.get("id", 0) for r in reminders]) if reminders else 0

def parse_time_with_day(time_str):
    days_map = {
        "пн": 0, "понедельник": 0,
        "вт": 1, "вторник": 1,
        "ср": 2, "среда": 2,
        "чт": 3, "четверг": 3,
        "пт": 4, "пятница": 4,
        "сб": 5, "суббота": 5,
        "вс": 6, "воскресенье": 6
    }
    
    parts = time_str.lower().split()
    time_part = parts[0]
    daily = False
    weekly_day = None
    
    for part in parts[1:]:
        if part in ["ежедневно", "каждый", "daily", "каждый день"]:
            daily = True
        elif part in days_map:
            weekly_day = days_map[part]
    
    try:
        if ":" in time_part:
            hours, minutes = map(int, time_part.split(":"))
        else:
            hours = int(time_part)
            minutes = 0
        return hours, minutes, weekly_day, daily
    except:
        return None, None, None, None

def get_next_trigger_time(hours, minutes, weekly_day=None, daily=False):
    now = datetime.now()
    target = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    
    if daily:
        if target <= now:
            target = target + timedelta(days=1)
        return target
    
    if weekly_day is not None:
        days_ahead = (weekly_day - now.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        target = now + timedelta(days=days_ahead)
        return target.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    
    if target <= now:
        target = target + timedelta(days=1)
    return target

def send_reminder(reminder):
    try:
        bot.send_message(
            reminder["chat_id"], 
            f"⏰ *НАПОМИНАНИЕ!*\n\n{reminder['text']}", 
            parse_mode="Markdown", 
            message_thread_id=reminder.get("thread_id")
        )
        logger.info(f"✅ Отправлено напоминание {reminder['id']}")
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания: {e}")

def schedule_reminder(reminder):
    next_time = get_next_trigger_time(
        reminder["hours"], 
        reminder["minutes"], 
        reminder.get("weekly_day"), 
        reminder.get("daily", False)
    )
    delay = (next_time - datetime.now()).total_seconds()
    if delay <= 0:
        return
    
    timer = threading.Timer(delay, execute_reminder, args=[reminder])
    timer.daemon = True
    timer.start()
    reminder["timer"] = timer

def execute_reminder(reminder):
    send_reminder(reminder)
    schedule_reminder(reminder)

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

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
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt or "Опиши это изображение"},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }],
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

⏰ *Напоминания:*
/remind 15:30 ежедневно Текст — каждый день
/remind 15:30 пн Текст — каждый понедельник
/reminds — список напоминаний
/delremind ID — удалить напоминание

🤖 *ИИ и поиск:*
/wiki [запрос] — поиск в Википедии
/ai [вопрос] — общение с ИИ
/ai найди [запрос] — поиск в интернете
/clear_history — очистить историю

🖼️ *Анализ изображений:* фото + `/ai Опиши`
📄 *Чтение файлов:* файл + `/ai Прочитай`
🌐 *Переводчик:* `/т on` / `/т off`

🎲 *Развлечения:* /roll, /coin

📩 *Скрытые сообщения:* `@бот @получатель текст`"""
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
        bot.reply_to(message, "🗑️ История очищена!")
    else:
        bot.reply_to(message, "📭 Нет сохранённой истории")

# === НАПОМИНАНИЯ ===
@bot.message_handler(commands=['remind'])
def add_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message, "ℹ️ `/remind 15:30 ежедневно Текст`", parse_mode="Markdown")
        return
    
    time_str = parts[1]
    reminder_text = parts[2]
    
    hours, minutes, weekly_day, daily = parse_time_with_day(time_str)
    if hours is None:
        bot.reply_to(message, "❌ Неправильный формат.\nПример: `/remind 15:30 ежедневно Текст`", parse_mode="Markdown")
        return
    
    global reminder_counter
    reminder_counter += 1
    
    reminder = {
        "id": reminder_counter,
        "chat_id": chat_id,
        "user_id": user_id,
        "thread_id": thread_id,
        "text": reminder_text,
        "hours": hours,
        "minutes": minutes,
        "weekly_day": weekly_day,
        "daily": daily
    }
    
    reminders.append(reminder)
    save_reminders(reminders)
    schedule_reminder(reminder)
    
    if daily:
        period = "каждый день"
    elif weekly_day is not None:
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        period = f"каждый {days[weekly_day]}"
    else:
        period = "сегодня"
    
    bot.reply_to(message, f"✅ *Напоминание добавлено!*\n\n⏰ {period} в {hours:02d}:{minutes:02d}\n📝 {reminder_text}\n🆔 ID: {reminder_counter}", parse_mode="Markdown")

@bot.message_handler(commands=['reminds'])
def list_reminders(message):
    if not reminders:
        bot.reply_to(message, "📭 Нет активных напоминаний.")
        return
    
    response = "📋 *Активные напоминания:*\n\n"
    for r in reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        elif r.get("weekly_day") is not None:
            days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            period = f"каждый {days[r['weekly_day']]} в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"в {r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 `{r['id']}` — {period}\n   📝 {r['text'][:40]}\n\n"
    bot.reply_to(message, response, parse_mode="Markdown")

@bot.message_handler(commands=['delremind'])
def delete_reminder(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ `/delremind ID`", parse_mode="Markdown")
        return
    
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if "timer" in r:
                    r["timer"].cancel()
                reminders.pop(i)
                save_reminders(reminders)
                bot.reply_to(message, f"✅ Напоминание `{rid}` удалено.", parse_mode="Markdown")
                return
        bot.reply_to(message, f"❌ Напоминание с ID `{rid}` не найдено.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Неверный ID.")

# === ПЕРЕВОДЧИК ===
@bot.message_handler(commands=['т'])
def translate_command(message):
    chat_id = message.chat.id
    parts = message.text.split()
    if len(parts) < 2:
        status = "✅ Включён" if is_translator_enabled(chat_id) else "❌ Выключен"
        bot.reply_to(message, f"🌐 *Переводчик RU↔EN*\nСтатус: {status}\n\n`/т on` — включить\n`/т off` — выключить", parse_mode="Markdown")
        return
    action = parts[1].lower()
    if action == "on":
        set_translator_enabled(chat_id, True)
        bot.reply_to(message, "✅ *Переводчик включён!*", parse_mode="Markdown")
    elif action == "off":
        set_translator_enabled(chat_id, False)
        bot.reply_to(message, "❌ *Переводчик выключен*", parse_mode="Markdown")

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
        else:
            translated = GoogleTranslator(source='en', target='ru').translate(text)
        if translated and translated != text:
            bot.reply_to(message, translated)
    except Exception as e:
        logger.error(f"Ошибка перевода: {e}")

# === ПЕРЕСЫЛКА СООБЩЕНИЙ (С ДИАГНОСТИКОЙ) ===
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def forward_to_b(message):
    logger.info(f"📤 ПЕРЕСЫЛКА A->B: chat={message.chat.id}, text={message.text[:50] if message.text else 'None'}")
    try:
        sender_name = get_sender_name(message.from_user)
        prefix = f"📨 От: {sender_name}\n\n"
        
        if message.text:
            bot.send_message(CHAT_B, prefix + message.text, message_thread_id=CHAT_B_THREAD)
        elif message.photo:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_photo(CHAT_B, message.photo[-1].file_id, caption=caption, message_thread_id=CHAT_B_THREAD)
        elif message.video:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_video(CHAT_B, message.video.file_id, caption=caption, message_thread_id=CHAT_B_THREAD)
        elif message.audio:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_audio(CHAT_B, message.audio.file_id, caption=caption, message_thread_id=CHAT_B_THREAD)
        elif message.voice:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_voice(CHAT_B, message.voice.file_id, caption=caption, message_thread_id=CHAT_B_THREAD)
        elif message.document:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_document(CHAT_B, message.document.file_id, caption=caption, message_thread_id=CHAT_B_THREAD)
        elif message.sticker:
            bot.send_sticker(CHAT_B, message.sticker.file_id, message_thread_id=CHAT_B_THREAD)
            bot.send_message(CHAT_B, prefix, message_thread_id=CHAT_B_THREAD)
        else:
            bot.send_message(CHAT_B, prefix, message_thread_id=CHAT_B_THREAD)
        
        logger.info(f"✅ ПЕРЕСЫЛКА A->B: Успешно")
    except Exception as e:
        logger.error(f"❌ ПЕРЕСЫЛКА A->B: Ошибка {e}")

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def forward_to_a(message):
    logger.info(f"📤 ПЕРЕСЫЛКА B->A: chat={message.chat.id}, thread={message.message_thread_id}, text={message.text[:50] if message.text else 'None'}")
    try:
        sender_name = get_sender_name(message.from_user)
        prefix = f"📨 От: {sender_name}\n\n"
        
        if message.text:
            bot.send_message(CHAT_A, prefix + message.text)
        elif message.photo:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_photo(CHAT_A, message.photo[-1].file_id, caption=caption)
        elif message.video:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_video(CHAT_A, message.video.file_id, caption=caption)
        elif message.audio:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_audio(CHAT_A, message.audio.file_id, caption=caption)
        elif message.voice:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_voice(CHAT_A, message.voice.file_id, caption=caption)
        elif message.document:
            caption = prefix + (message.caption if message.caption else "")
            bot.send_document(CHAT_A, message.document.file_id, caption=caption)
        elif message.sticker:
            bot.send_sticker(CHAT_A, message.sticker.file_id)
            bot.send_message(CHAT_A, prefix)
        else:
            bot.send_message(CHAT_A, prefix)
        
        logger.info(f"✅ ПЕРЕСЫЛКА B->A: Успешно")
    except Exception as e:
        logger.error(f"❌ ПЕРЕСЫЛКА B->A: Ошибка {e}")

# === ПОСТЫ В КАНАЛАХ (РЕАКЦИЯ 🔥) ===
@bot.channel_post_handler(func=lambda m: m.chat.id in [-1001317416582, -1002185590715])
def channel_reaction(message):
    chat_id = message.chat.id
    message_id = message.message_id
    logger.info(f"🔥 Попытка реакции на пост {message_id} в канале {chat_id}")
    
    url = f"{API_URL}/setMessageReaction"
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": "🔥"}]
    }
    
    try:
        response = requests.post(url, json=data, timeout=5)
        result = response.json()
        if result.get("ok"):
            logger.info(f"✅ Реакция 🔥 на пост {message_id}")
        else:
            logger.error(f"❌ Ошибка API: {result}")
    except Exception as e:
        logger.error(f"❌ Исключение: {e}")

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
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"read_{msg_id}"))
        
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
        time.sleep(3600)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]

threading.Thread(target=clean_expired_secrets, daemon=True).start()

# === ЗАПУСК НАПОМИНАНИЙ ===
start_all_reminders()

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
    logger.info("✅ Пересылка сообщений включена (с диагностикой)")
    logger.info("🔥 Реакции на каналы включены")
    logger.info("💾 Данные сохраняются в /tmp")
    
    app.run(host="0.0.0.0", port=port)

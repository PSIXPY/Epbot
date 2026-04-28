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
import pytz

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

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# === ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ УДАЛЕНИЯ СООБЩЕНИЙ ===
def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === ФУНКЦИЯ ПОЛУЧЕНИЯ ИМЕНИ ОТПРАВИТЕЛЯ ===
def get_sender_name(user):
    if not user:
        return "Неизвестный"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    if user.username:
        return f"{name} (@{user.username})"
    return name

# === ОБЪЕДИНЕНИЕ ЧАТОВ С ПОДДЕРЖКОЙ ОТВЕТОВ ===
@bot.message_handler(func=lambda m: m.chat.id in [CHAT_A, CHAT_B] and not (m.text and m.text.startswith('/')))
def relay_messages(message):
    if message.from_user.id == bot.get_me().id:
        return
    
    chat_id = message.chat.id
    sender_name = get_sender_name(message.from_user)
    
    message_text = message.text or message.caption or ""
    
    reply_info = ""
    if message.reply_to_message:
        original = message.reply_to_message
        original_sender = get_sender_name(original.from_user)
        original_text = (original.text or original.caption or "сообщение")[:150]
        reply_info = f"💬 *В ответ {original_sender}:*\n{original_text}\n\n"
    
    final_text = f"📩 *{sender_name}*\n\n{reply_info}{message_text}"
    
    if chat_id == CHAT_A:
        try:
            if message.text:
                bot.send_message(CHAT_B, final_text, parse_mode="Markdown", 
                               message_thread_id=CHAT_B_THREAD)
            elif message.photo:
                bot.send_photo(CHAT_B, message.photo[-1].file_id, caption=final_text[:1024],
                             parse_mode="Markdown", message_thread_id=CHAT_B_THREAD)
            logger.info(f"✅ Переслано из A в B")
        except Exception as e:
            logger.error(f"Ошибка A→B: {e}")
    
    elif chat_id == CHAT_B and message.message_thread_id == CHAT_B_THREAD:
        try:
            if message.text:
                bot.send_message(CHAT_A, final_text, parse_mode="Markdown")
            elif message.photo:
                bot.send_photo(CHAT_A, message.photo[-1].file_id, caption=final_text[:1024],
                             parse_mode="Markdown")
            logger.info(f"✅ Переслано из B в A")
        except Exception as e:
            logger.error(f"Ошибка B→A: {e}")

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

# === НАПОМИНАНИЯ С МОСКОВСКИМ ВРЕМЕНЕМ (СОХРАНЕНИЕ В КОРНЕ) ===
REMINDERS_FILE = "reminders.json"  # ← Сохраняем в корне проекта

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_reminders(reminders):
    reminders_to_save = []
    for r in reminders:
        r_copy = {}
        for k, v in r.items():
            if k not in ["timer", "_timer"]:
                r_copy[k] = v
        reminders_to_save.append(r_copy)
    
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(reminders_to_save, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Напоминания сохранены в {REMINDERS_FILE}")
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

def get_next_trigger_time_moscow(hours, minutes, weekly_day=None, daily=False):
    now_moscow = datetime.now(MOSCOW_TZ)
    target = now_moscow.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    
    if daily:
        if target <= now_moscow:
            target = target + timedelta(days=1)
        target = target + timedelta(seconds=2)
        return target
    
    if weekly_day is not None:
        days_ahead = (weekly_day - now_moscow.weekday()) % 7
        if days_ahead == 0 and target <= now_moscow:
            days_ahead = 7
        target = now_moscow + timedelta(days=days_ahead)
        target = target.replace(hour=hours, minute=minutes, second=2, microsecond=0)
        return target
    
    if target <= now_moscow:
        target = target + timedelta(days=1)
    target = target + timedelta(seconds=2)
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
    next_time = get_next_trigger_time_moscow(
        reminder["hours"], 
        reminder["minutes"], 
        reminder.get("weekly_day"), 
        reminder.get("daily", False)
    )
    
    next_time_utc = next_time.astimezone(pytz.UTC)
    delay = (next_time_utc - datetime.now(pytz.UTC)).total_seconds()
    
    if delay < 1:
        delay = 1
    
    timer = threading.Timer(delay, execute_reminder, args=[reminder])
    timer.daemon = True
    timer.start()
    reminder["_timer"] = timer
    logger.info(f"⏰ Напоминание {reminder['id']} запланировано на {next_time.strftime('%Y-%m-%d %H:%M:%S')} МСК (через {delay:.1f} сек)")

def execute_reminder(reminder):
    send_reminder(reminder)
    schedule_reminder(reminder)

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

# === ОСНОВНЫЕ ФУНКЦИИ ===
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

⏰ *Напоминания (МСК):*
/remind 15:30 ежедневно Текст
/reminds — список
/delremind ID — удалить

🤖 *ИИ:*
/ai вопрос
/ai найди запрос
/wiki запрос

🖼️ *Фото:* + `/ai Опиши`

🔐 *Тайные:* @бот @username текст

🌐 *Перевод:* `/т on` / `/т off`

🎲 *Игры:* /roll | /coin"""
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

# === НАПОМИНАНИЯ КОМАНДЫ ===
@bot.message_handler(commands=['remind'])
def add_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        msg = bot.send_message(chat_id, "ℹ️ *Как создать напоминание:*\n\n"
                           "`/remind 15:30 ежедневно Текст` — каждый день\n"
                           "`/remind 15:30 пн Текст` — каждый понедельник\n"
                           "`/remind 18:00 Текст` — сегодня/завтра", 
                           parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
        return
    
    time_str = parts[1]
    reminder_text = parts[2]
    
    hours, minutes, weekly_day, daily = parse_time_with_day(time_str)
    if hours is None:
        msg = bot.send_message(chat_id, "❌ Неправильный формат времени.\n"
                           "Пример: `/remind 15:30 ежедневно Текст`", 
                           parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
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
    
    now_moscow = datetime.now(MOSCOW_TZ)
    target_today = now_moscow.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    
    if daily:
        period = "каждый день"
    elif weekly_day is not None:
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        period = f"каждый {days[weekly_day]}"
    else:
        if target_today > now_moscow:
            period = "сегодня"
        else:
            period = "завтра"
    
    location = "в этот же топик" if thread_id else "в этот чат"
    
    msg = bot.send_message(
        chat_id,
        f"✅ *Напоминание добавлено!*\n\n"
        f"⏰ {period} в {hours:02d}:{minutes:02d} МСК\n"
        f"📍 Придёт {location}\n"
        f"📝 {reminder_text}\n"
        f"🆔 ID: `{reminder_counter}`", 
        parse_mode="Markdown",
        message_thread_id=thread_id
    )
    
    delete_after_delay(chat_id, msg.message_id)

@bot.message_handler(commands=['reminds'])
def list_reminders(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    user_reminders = []
    for r in reminders:
        if r.get("chat_id") != chat_id:
            continue
        if thread_id and r.get("thread_id") and r.get("thread_id") != thread_id:
            continue
        user_reminders.append(r)
    
    if not user_reminders:
        msg = bot.send_message(chat_id, "📭 В этом чате нет активных напоминаний.\n\n"
                           "Создайте: `/remind 15:30 ежедневно Текст`", 
                           parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    
    response = "📋 *Активные напоминания в этом чате:*\n\n"
    for r in user_reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        elif r.get("weekly_day") is not None:
            days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            period = f"каждый {days[r['weekly_day']]} в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"в {r['hours']:02d}:{r['minutes']:02d}"
        
        topic_info = f" (топик {r['thread_id']})" if r.get('thread_id') else ""
        response += f"🆔 `{r['id']}` — {period}{topic_info}\n   📝 {r['text'][:50]}\n\n"
    
    msg = bot.send_message(chat_id, response, parse_mode="Markdown", message_thread_id=thread_id)
    delete_after_delay(chat_id, msg.message_id, 30)

@bot.message_handler(commands=['delremind'])
def delete_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    parts = message.text.split()
    if len(parts) < 2:
        msg = bot.send_message(chat_id, "ℹ️ `/delremind ID`\n\nПосмотреть ID можно через `/reminds`", 
                           parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
        return
    
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if r.get("chat_id") != chat_id:
                    msg = bot.send_message(chat_id, f"❌ Напоминание `{rid}` не найдено в этом чате.", 
                                          parse_mode="Markdown", message_thread_id=thread_id)
                    delete_after_delay(chat_id, msg.message_id, 15)
                    return
                
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders(reminders)
                msg = bot.send_message(chat_id, f"✅ Напоминание `{rid}` удалено.", 
                                      parse_mode="Markdown", message_thread_id=thread_id)
                delete_after_delay(chat_id, msg.message_id)
                return
        msg = bot.send_message(chat_id, f"❌ Напоминание с ID `{rid}` не найдено.", 
                           parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
    except:
        msg = bot.send_message(chat_id, "❌ Неверный ID. Используйте цифры, например: `/delremind 5`", 
                          parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)

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
    if message.text.startswith('📩'):
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

# === ПОСТЫ В КАНАЛАХ (РЕАКЦИЯ 🔥) ===
@bot.channel_post_handler(func=lambda m: True)
def channel_reaction(message):
    # ID ваших каналов (замените на свои)
    allowed_channels = [-1001317416582, -1002185590715]
    
    if message.chat.id not in allowed_channels:
        return
    
    logger.info(f"🔥 Канал {message.chat.id}, пост {message.message_id}")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    data = {
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "reaction": [{"type": "emoji", "emoji": "🔥"}]
    }
    
    try:
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        if result.get("ok"):
            logger.info(f"✅ Реакция 🔥 на пост {message.message_id}")
        else:
            logger.error(f"❌ Ошибка API: {result}")
    except Exception as e:
        logger.error(f"❌ Ошибка реакции: {e}")

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
        
        target_raw = parts[0].lstrip("@")
        content = parts[1]
        
        target_id = None
        target_name = target_raw
        found = False
        
        possible_usernames = [target_raw, target_raw.lower(), target_raw.capitalize(), target_raw.title()]
        
        for username in possible_usernames:
            try:
                target_info = bot.get_chat(f"@{username}")
                target_id = target_info.id
                target_name = target_info.first_name or target_info.username or target_raw
                found = True
                break
            except:
                continue
        
        if not found and target_raw.isdigit():
            try:
                target_info = bot.get_chat(int(target_raw))
                target_id = int(target_raw)
                target_name = target_info.first_name or f"ID:{target_raw}"
                found = True
            except:
                pass
        
        if not found:
            result = types.InlineQueryResultArticle(
                id="error",
                title=f"❌ Пользователь не найден",
                description=f"@{target_raw} - проверьте правильность",
                input_message_content=types.InputTextMessageContent(
                    f"❌ Пользователь `{target_raw}` не найден"
                )
            )
            bot.answer_inline_query(query.id, [result], cache_time=0)
            return
        
        msg_id = f"sec_{int(time.time() * 1000)}_{query.from_user.id}_{random.randint(1000,9999)}"
        
        secret_messages[msg_id] = {
            "target_id": target_id,
            "target_name": target_name,
            "content": content,
            "sender": query.from_user.first_name,
            "sender_id": query.from_user.id,
            "expires": time.time() + 86400
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Для {target_name}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔐 *Скрытое сообщение* от {query.from_user.first_name}\n👤 {target_name}",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        
        bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)
        
    except Exception as e:
        logger.error(f"Inline error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("read_"))
def read_secret(call):
    msg_id = call.data[5:]
    
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение не найдено", show_alert=True)
        return
    
    data = secret_messages[msg_id]
    
    if call.from_user.id != data["target_id"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Истекло 24 часа", show_alert=True)
        del secret_messages[msg_id]
        return
    
    content = data['content']
    sender = data['sender']
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    bot.answer_callback_query(call.id, f"📩 От {sender}:\n{content}", show_alert=True)
    del secret_messages[msg_id]

def clean_old_secrets():
    now = time.time()
    expired = [msg_id for msg_id, data in secret_messages.items() if data.get("expires", now) < now]
    for msg_id in expired:
        del secret_messages[msg_id]

def periodic_cleanup():
    while True:
        time.sleep(3600)
        clean_old_secrets()

threading.Thread(target=periodic_cleanup, daemon=True).start()

# === ЗАПУСК ===
start_all_reminders()

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info("✅ Пересылка с ответами")
    logger.info("✅ Напоминания сохраняются в корне проекта")
    
    app.run(host="0.0.0.0", port=port)

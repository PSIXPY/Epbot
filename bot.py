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
secret_messages = {}

# === НАПОМИНАНИЯ ===
reminders = {}
reminder_counter = 0

# === КЭШ УЧАСТНИКОВ ДЛЯ УПОМИНАНИЙ ===
user_cache = {}
last_call_time = {}
CALL_COOLDOWN = 60

# === КЭШ И ИСТОРИЯ ДЛЯ ИИ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600


# === ФУНКЦИИ ДЛЯ СБОРА УЧАСТНИКОВ ===
def save_user_from_message(message):
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


def get_all_mentions(chat_id, exclude_user_id=None):
    members = user_cache.get(chat_id, {})
    mentions = []
    for uid, data in members.items():
        if uid == exclude_user_id:
            continue
        username = data.get("username")
        if username:
            mentions.append(f"@{username}")
        else:
            name = data.get("first_name", "Пользователь")
            mentions.append(f"[{name}](tg://user?id={uid})")
    return " ".join(mentions)


# === ФУНКЦИИ ДЛЯ НАПОМИНАНИЙ ===
def parse_time_with_day(time_str):
    days_map = {
        "пн": 0, "пон": 0, "понедельник": 0,
        "вт": 1, "втор": 1, "вторник": 1,
        "ср": 2, "сред": 2, "среда": 2,
        "чт": 3, "чет": 3, "четверг": 3,
        "пт": 4, "пятн": 4, "пятница": 4,
        "сб": 5, "суб": 5, "суббота": 5,
        "вс": 6, "воск": 6, "воскресенье": 6
    }
    
    parts = time_str.lower().split()
    time_part = parts[0]
    daily = False
    weekly_day = None
    thread_id = None
    
    for part in parts[1:]:
        if part in ["ежедневно", "каждый", "daily", "ежедневная", "каждый день"]:
            daily = True
        elif part in days_map:
            weekly_day = days_map[part]
        elif part.startswith("#"):
            try:
                thread_id = int(part[1:])
            except:
                pass
    
    try:
        if ":" in time_part:
            hours, minutes = map(int, time_part.split(":"))
        else:
            hours = int(time_part)
            minutes = 0
        return hours, minutes, weekly_day, daily, thread_id
    except:
        return None, None, None, None, None


def add_reminder(user_id, chat_id, reminder_time, text, thread_id=None, ping_all=False, daily=False, weekly_day=None, target_thread_id=None):
    global reminder_counter
    reminder_counter += 1
    reminder_id = reminder_counter
    
    reminders[reminder_id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "time": reminder_time,
        "text": text,
        "thread_id": thread_id,
        "target_thread_id": target_thread_id,
        "ping_all": ping_all,
        "daily": daily,
        "weekly_day": weekly_day
    }
    return reminder_id


def check_reminders():
    """Проверяет и отправляет напоминания"""
    logger.info("✅ ПОТОК НАПОМИНАНИЙ ЗАПУЩЕН")
    while True:
        now = time.time()
        logger.info(f"🔍 Проверка напоминаний: {datetime.now()}, активных: {len(reminders)}")
        
        to_remove = []
        to_repeat = []
        
        for rid, reminder in reminders.items():
            if reminder["time"] <= now:
                logger.info(f"⏰ Срабатывает напоминание {rid}")
                try:
                    chat_id = reminder["chat_id"]
                    text = reminder["text"]
                    source_thread_id = reminder.get("thread_id")
                    target_thread_id = reminder.get("target_thread_id")
                    send_thread_id = target_thread_id if target_thread_id else source_thread_id
                    
                    msg = f"⏰ *НАПОМИНАНИЕ!*\n\n{text}"
                    
                    if reminder.get("ping_all"):
                        mentions = get_all_mentions(chat_id)
                        if mentions:
                            msg = f"⏰ *НАПОМИНАНИЕ!*\n\n{text}\n\n{mentions}"
                        else:
                            logger.warning(f"Нет участников для упоминания в чате {chat_id}")
                    
                    bot.send_message(chat_id, msg, parse_mode="Markdown", message_thread_id=send_thread_id)
                    logger.info(f"✅ Отправлено напоминание {rid}")
                    
                    if reminder.get("daily"):
                        new_time = reminder["time"] + 86400
                        to_repeat.append((rid, new_time))
                        logger.info(f"🔄 Ежедневное напоминание {rid} перенесено на {datetime.fromtimestamp(new_time)}")
                    elif reminder.get("weekly_day") is not None:
                        new_time = reminder["time"] + 604800
                        to_repeat.append((rid, new_time))
                    else:
                        to_remove.append(rid)
                except Exception as e:
                    logger.error(f"Ошибка отправки напоминания {rid}: {e}")
                    to_remove.append(rid)
        
        for rid, new_time in to_repeat:
            reminders[rid]["time"] = new_time
        for rid in to_remove:
            del reminders[rid]
            logger.info(f"🗑️ Удалено напоминание {rid}")
        
        time.sleep(10)


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
    
    now = time.time()
    
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
    save_user_from_message(message)
    help_text = """📖 *Команды бота*

🤖 *ИИ и поиск:*
/wiki [запрос] — поиск в Википедии
/ai [вопрос] — общение с ИИ
/ai найди [запрос] — поиск в интернете
/clear_history — очистить историю диалога

⏰ *Напоминания:*
/remind 15:30 Текст — одноразовое
/remind 15:30 пн Текст — каждый понедельник
/remind 15:30 ежедневно Текст — каждый день
/remind 15:30 #123 Текст — в тему 123
/remind 15:30 калл Текст — с упоминанием всех
/reminds — список напоминаний
/delremind ID — удалить напоминание

📢 *Массовые уведомления:*
/all текст — упомянуть всех участников
калл текст — упомянуть всех (без слеша)

🖼️ *Анализ изображений:* фото + `/ai Опиши`
📄 *Чтение файлов:* файл + `/ai Прочитай`

🎲 *Развлечения:* /roll, /coin

📩 *Скрытые сообщения:* `@бот @получатель текст`"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


# === МАССОВЫЕ УПОМИНАНИЯ ===
@bot.message_handler(commands=['all'])
def all_command(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    if chat_id in last_call_time and time.time() - last_call_time[chat_id] < CALL_COOLDOWN:
        remaining = int(CALL_COOLDOWN - (time.time() - last_call_time[chat_id]))
        bot.reply_to(message, f"⏳ Подождите {remaining} секунд перед следующим вызовом.")
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
    
    all_mentions = " ".join(mentions)
    bot.send_message(chat_id, f"📢 {custom_text}\n\n{all_mentions}", parse_mode="Markdown", message_thread_id=thread_id)
    
    last_call_time[chat_id] = time.time()
    logger.info(f"Вызван /all в чате {chat_id}, упомянуто {len(mentions)} участников")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip().startswith(("калл", "call")))
def call_all_no_slash(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    
    if chat_id in last_call_time and time.time() - last_call_time[chat_id] < CALL_COOLDOWN:
        remaining = int(CALL_COOLDOWN - (time.time() - last_call_time[chat_id]))
        bot.reply_to(message, f"⏳ Подождите {remaining} секунд перед следующим вызовом.")
        return
    
    text = message.text.strip()
    if text.lower().startswith("калл"):
        custom_text = text[4:].strip()
    elif text.lower().startswith("call"):
        custom_text = text[4:].strip()
    else:
        custom_text = text
    
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
    
    all_mentions = " ".join(mentions)
    bot.send_message(chat_id, f"📢 {custom_text}\n\n{all_mentions}", parse_mode="Markdown", message_thread_id=thread_id)
    
    last_call_time[chat_id] = time.time()
    logger.info(f"Вызван калл в чате {chat_id}, упомянуто {len(mentions)} участников")


@bot.message_handler(commands=['ai'])
def ai_command(message):
    save_user_from_message(message)
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
    save_user_from_message(message)
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
    save_user_from_message(message)
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
def wiki_command(message):
    save_user_from_message(message)
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ `/wiki Python`", parse_mode="Markdown")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown")


@bot.message_handler(commands=['roll'])
def roll_command(message):
    save_user_from_message(message)
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")


@bot.message_handler(commands=['coin'])
def coin_command(message):
    save_user_from_message(message)
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")


@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    save_user_from_message(message)
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
        bot.reply_to(message, "🗑️ История ваших диалогов очищена!")
    else:
        bot.reply_to(message, "📭 У вас нет сохранённой истории.")


# === НАПОМИНАНИЯ ===
@bot.message_handler(commands=['remind', 'whisper'])
def set_reminder(message):
    save_user_from_message(message)
    chat_id = message.chat.id
    user_id = message.from_user.id
    thread_id = message.message_thread_id
    text = message.text
    
    ping_all = "калл" in text.lower() or "call" in text.lower()
    
    if text.startswith("/remind"):
        parts = text[7:].strip().split(maxsplit=1)
    else:
        parts = text[8:].strip().split(maxsplit=1)
    
    if len(parts) < 2:
        bot.reply_to(message, """ℹ️ *Как установить напоминание:*\n\n`/remind 15:30 Текст` — сегодня\n`/remind 15:30 пн Текст` — каждый понедельник\n`/remind 15:30 #123 Текст` — в тему 123\n`/remind 15:30 ежедневно Текст` — каждый день\n`/remind 15:30 калл Текст` — с упоминанием всех""", parse_mode="Markdown")
        return
    
    time_str = parts[0]
    reminder_text = parts[1]
    
    if ping_all:
        reminder_text = reminder_text.replace("калл", "").replace("call", "").strip()
    
    hours, minutes, weekly_day, daily, target_thread_id = parse_time_with_day(time_str)
    if hours is None:
        bot.reply_to(message, "❌ Неправильный формат времени.", parse_mode="Markdown")
        return
    
    now = datetime.now()
    
    if daily:
        reminder_time = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if reminder_time <= now:
            reminder_time = reminder_time + timedelta(days=1)
        response_note = "ежедневно"
    elif weekly_day is not None:
        days_ahead = (weekly_day - now.weekday()) % 7
        if days_ahead == 0 and now.hour > hours or (now.hour == hours and now.minute >= minutes):
            days_ahead = 7
        reminder_time = now + timedelta(days=days_ahead)
        reminder_time = reminder_time.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        days_names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        response_note = f"каждый {days_names[weekly_day]}"
    else:
        reminder_time = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if reminder_time <= now:
            reminder_time = reminder_time + timedelta(days=1)
        response_note = "одноразовое"
    
    timestamp = reminder_time.timestamp()
    
    reminder_id = add_reminder(user_id, chat_id, timestamp, reminder_text, thread_id, ping_all, daily, weekly_day, target_thread_id)
    
    time_str_formatted = reminder_time.strftime("%d.%m.%Y в %H:%M")
    response = f"✅ *Напоминание установлено!*\n\n⏰ Когда: {time_str_formatted}\n📝 Текст: {reminder_text}\n🔄 Тип: {response_note}"
    
    if target_thread_id:
        response += f"\n📌 *Тема:* #{target_thread_id}"
    if ping_all:
        response += "\n\n📢 *При срабатывании будут упомянуты ВСЕ участники чата!*"
    
    bot.reply_to(message, response, parse_mode="Markdown")


@bot.message_handler(commands=['reminds', 'whispers'])
def list_reminders(message):
    save_user_from_message(message)
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    user_reminders = []
    for rid, rem in reminders.items():
        if rem["user_id"] == user_id or rem["chat_id"] == chat_id:
            time_str = datetime.fromtimestamp(rem["time"]).strftime("%d.%m %H:%M")
            text_preview = rem["text"][:30] + "..." if len(rem["text"]) > 30 else rem["text"]
            ping_info = "🔔📢" if rem.get("ping_all") else "🔔"
            user_reminders.append(f"`{rid}` {ping_info} {time_str} — {text_preview}")
    
    if not user_reminders:
        bot.reply_to(message, "📭 У вас нет активных напоминаний.")
        return
    
    reminders_list = "\n".join(user_reminders)
    bot.reply_to(message, f"📋 *Активные напоминания:*\n\n{reminders_list}\n\n_Удалить: /delremind ID_", parse_mode="Markdown")


@bot.message_handler(commands=['delremind', 'delwhisper'])
def delete_reminder(message):
    save_user_from_message(message)
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ Использование: `/delremind ID`", parse_mode="Markdown")
        return
    
    try:
        rid = int(parts[1])
        if rid in reminders:
            del reminders[rid]
            bot.reply_to(message, f"✅ Напоминание `{rid}` удалено.", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"❌ Напоминание с ID `{rid}` не найдено.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Неверный ID.")


# === ПЕРЕСЫЛКА СООБЩЕНИЙ ===
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def forward_to_b(message):
    save_user_from_message(message)
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
    save_user_from_message(message)
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
threading.Thread(target=check_reminders, daemon=True).start()


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
    logger.info("Команды: /ai, /wiki, /roll, /coin, /remind, /all, /help")
    logger.info("📢 калл текст — упоминание всех участников")
    logger.info("✅ ПОТОК НАПОМИНАНИЙ ЗАПУЩЕН")
    
    app.run(host="0.0.0.0", port=port)

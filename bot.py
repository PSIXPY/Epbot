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
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
bot = TeleBot(BOT_TOKEN)
secret_messages = {}

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

def get_sender_name(user):
    if not user:
        return "Неизвестный"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    if user.username:
        return f"{name} (@{user.username})"
    return name

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === ПЕРЕСЫЛКА МЕЖДУ ЧАТАМИ ОТКЛЮЧЕНА ===

# === КЭШ И ИСТОРИЯ ===
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
    except:
        pass

translator_settings = load_translator_settings()

def is_translator_enabled(chat_id):
    return translator_settings.get(str(chat_id), False)

def set_translator_enabled(chat_id, enabled):
    translator_settings[str(chat_id)] = enabled
    save_translator_settings(translator_settings)

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = "reminders.json"

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"📂 Загружено {len(data)} напоминаний")
                return data
        except:
            return []
    return []

def save_reminders(reminders):
    to_save = []
    for r in reminders:
        copy = {}
        for k, v in r.items():
            if k not in ["timer", "_timer"]:
                copy[k] = v
        to_save.append(copy)
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Сохранено {len(to_save)} напоминаний")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

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
        return target
    if weekly_day is not None:
        days_ahead = (weekly_day - now_moscow.weekday()) % 7
        if days_ahead == 0 and target <= now_moscow:
            days_ahead = 7
        target = now_moscow + timedelta(days=days_ahead)
        return target.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if target <= now_moscow:
        target = target + timedelta(days=1)
    return target

def send_reminder(reminder):
    try:
        bot.send_message(
            reminder["chat_id"], 
            f"⏰ НАПОМИНАНИЕ!\n\n{reminder['text']}", 
            parse_mode=None,
            message_thread_id=reminder.get("thread_id")
        )
        logger.info(f"✅ Отправлено напоминание {reminder['id']}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def schedule_reminder(reminder):
    next_time = get_next_trigger_time_moscow(
        reminder["hours"], 
        reminder["minutes"], 
        reminder.get("weekly_day"), 
        reminder.get("daily", False)
    )
    next_time_utc = next_time.astimezone(pytz.UTC)
    delay = (next_time_utc - datetime.now(pytz.UTC)).total_seconds()
    if delay < 0:
        delay = 0
    timer = threading.Timer(delay, execute_reminder, args=[reminder])
    timer.daemon = True
    timer.start()
    reminder["_timer"] = timer
    logger.info(f"⏰ Напоминание {reminder['id']} на {next_time.strftime('%Y-%m-%d %H:%M:%S')} МСК")

def execute_reminder(reminder):
    send_reminder(reminder)
    if reminder.get("daily"):
        schedule_reminder(reminder)

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

# === ИИ ===
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
        {"role": "system", "content": "Отвечай кратко, по существу."},
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
            return "⚠️ Лимит запросов. Подождите."
        return f"❌ Ошибка: {response.status_code}"
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

def search_wikipedia(query):
    try:
        # Сначала ищем на русском
        wiki_ru = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page_ru = wiki_ru.page(query)
        if page_ru.exists():
            summary = page_ru.summary[:500]
            if len(page_ru.summary) > 500:
                summary += "..."
            return f"📖 *{page_ru.title}*\n\n{summary}\n\n[🔗 Читать на Википедии]({page_ru.fullurl})"
        
        # Если не найдено, ищем на английском
        wiki_en = wikipediaapi.Wikipedia(language='en', user_agent='TelegramRelayBot/1.0')
        page_en = wiki_en.page(query)
        if page_en.exists():
            summary = page_en.summary[:500]
            if len(page_en.summary) > 500:
                summary += "..."
            return f"📖 *{page_en.title}*\n\n{summary}\n\n[🔗 Читать на Wikipedia]({page_en.fullurl})\n\n_На русском не найдено, показан английский вариант_"
        
        # Если ничего не найдено - поисковики
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded_query}"
        yandex_url = f"https://yandex.ru/search/?text={encoded_query}"
        wiki_search = f"https://ru.wikipedia.org/w/index.php?search={encoded_query}"
        
        return (f"❌ *Ничего не найдено в Википедии* по запросу: `{query}`\n\n"
                f"🔍 *Поиск в интернете:*\n"
                f"• [Google]({google_url})\n"
                f"• [Яндекс]({yandex_url})\n"
                f"• [Поиск в Википедии]({wiki_search})")
        
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return f"❌ Ошибка при поиске: {str(e)[:100]}"

# === КОМАНДЫ ===
@bot.message_handler(commands=['start', 'help'])
def help_command(message):
    help_text = """📖 *Команды бота*

⏰ *Напоминания (МСК):*
/remind 15:30 ежедневно текст
/reminds — список
/delremind ID — удалить

🤖 *ИИ:* /ai вопрос

📖 *Википедия:* /wiki запрос

🌐 *Перевод:* `/т on` / `/т off`

🎲 *Игры:* /roll | /coin

👑 *Админ:* /backup"""
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['ai'])
def ai_command(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ /ai Как дела?")
        return
    msg = bot.reply_to(message, "🤖 Думаю...")
    answer = ask_groq(message.from_user.id, prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id)

@bot.message_handler(commands=['wiki'])
def wiki_command(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ /wiki запрос\n\nПример: /wiki Python")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown", disable_web_page_preview=True)

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
        bot.reply_to(message, "📭 Нет истории")

# === БЕКАП ===
@bot.message_handler(commands=['backup'])
def backup_reminders(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав")
        return
    try:
        backup_data = []
        for r in reminders:
            r_copy = {}
            for k, v in r.items():
                if k not in ["timer", "_timer"]:
                    r_copy[k] = v
            backup_data.append(r_copy)
        backup_file = f"reminders_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        with open(backup_file, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"📦 Бекап напоминаний\n\nДата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\nВсего: {len(backup_data)}")
        os.remove(backup_file)
        logger.info(f"📦 Бекап создан ({len(backup_data)})")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['restore'])
def restore_reminders(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав")
        return
    bot.send_message(message.chat.id, "📥 Отправьте файл бекапа")

@bot.message_handler(content_types=['document'])
def handle_backup_file(message):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.document.file_name.startswith("reminders_backup_"):
        return
    
    status = bot.send_message(message.chat.id, "🔄 Восстанавливаю...")
    try:
        file_info = bot.get_file(message.document.file_id)
        file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}").content
        backup_data = json.loads(file_bytes.decode('utf-8'))
        
        for r in reminders:
            if "_timer" in r:
                try:
                    r["_timer"].cancel()
                except:
                    pass
        
        reminders.clear()
        global reminder_counter
        reminder_counter = 0
        for r in backup_data:
            reminders.append(r)
            if r.get("id", 0) > reminder_counter:
                reminder_counter = r.get("id", 0)
        
        save_reminders(reminders)
        start_all_reminders()
        
        bot.delete_message(message.chat.id, status.message_id)
        bot.send_message(message.chat.id, f"✅ Восстановлено {len(backup_data)} напоминаний!")
    except Exception as e:
        bot.delete_message(message.chat.id, status.message_id)
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

# === НАПОМИНАНИЯ ===
@bot.message_handler(commands=['remind'])
def add_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        msg = bot.send_message(chat_id, "ℹ️ /remind 15:30 ежедневно текст", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
        return
    
    time_str = parts[1]
    reminder_text = parts[2]
    
    reminder_text_clean = reminder_text
    for kw in ["ежедневно", "каждый", "daily"]:
        if reminder_text_clean.lower().startswith(kw):
            reminder_text_clean = reminder_text_clean[len(kw):].lstrip()
            break
    
    hours, minutes, weekly_day, daily = parse_time_with_day(time_str)
    if hours is None:
        msg = bot.send_message(chat_id, "❌ Неправильный формат. Пример: /remind 15:30 текст", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
        return
    
    global reminder_counter
    reminder_counter += 1
    reminder = {
        "id": reminder_counter,
        "chat_id": chat_id,
        "user_id": message.from_user.id,
        "thread_id": thread_id,
        "text": reminder_text_clean,
        "hours": hours,
        "minutes": minutes,
        "weekly_day": weekly_day,
        "daily": daily
    }
    
    reminders.append(reminder)
    save_reminders(reminders)
    schedule_reminder(reminder)
    
    now_moscow = datetime.now(MOSCOW_TZ)
    target_today = now_moscow.replace(hour=hours, minute=minutes)
    if daily:
        period = "каждый день"
    elif weekly_day is not None:
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        period = f"каждый {days[weekly_day]}"
    else:
        period = "сегодня" if target_today > now_moscow else "завтра"
    
    location = "в этот же топик" if thread_id else "в этот чат"
    msg = bot.send_message(chat_id, 
        f"✅ *Напоминание добавлено!*\n\n"
        f"⏰ {period} в {hours:02d}:{minutes:02d} МСК\n"
        f"📍 Придёт {location}\n"
        f"📝 {reminder_text_clean}\n"
        f"🆔 ID: `{reminder_counter}`", 
        parse_mode="Markdown", message_thread_id=thread_id)
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
        if thread_id and r.get("thread_id") != thread_id:
            continue
        user_reminders.append(r)
    
    if not user_reminders:
        msg = bot.send_message(chat_id, "📭 Нет активных напоминаний", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    
    response = "📋 *Активные напоминания:*\n\n"
    for r in user_reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        elif r.get("weekly_day") is not None:
            days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            period = f"каждый {days[r['weekly_day']]} в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"в {r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 `{r['id']}` - {period}\n   📝 {r['text'][:50]}\n\n"
    
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
        msg = bot.send_message(chat_id, "ℹ️ /delremind ID", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)
        return
    
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if r.get("chat_id") != chat_id:
                    msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
                    delete_after_delay(chat_id, msg.message_id, 15)
                    return
                if thread_id and r.get("thread_id") != thread_id:
                    msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не в этом топике", message_thread_id=thread_id)
                    delete_after_delay(chat_id, msg.message_id, 15)
                    return
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders(reminders)
                msg = bot.send_message(chat_id, f"✅ Напоминание {rid} удалено", message_thread_id=thread_id)
                delete_after_delay(chat_id, msg.message_id)
                return
        msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
    except:
        msg = bot.send_message(chat_id, "❌ Неверный ID", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id)

# === ПЕРЕВОДЧИК ===
@bot.message_handler(commands=['т'])
def translate_command(message):
    chat_id = message.chat.id
    parts = message.text.split()
    if len(parts) < 2:
        status = "✅ Включён" if is_translator_enabled(chat_id) else "❌ Выключен"
        bot.reply_to(message, f"🌐 *Переводчик*\nСтатус: {status}\n\n/т on - вкл\n/т off - выкл", parse_mode="Markdown")
        return
    action = parts[1].lower()
    if action == "on":
        set_translator_enabled(chat_id, True)
        bot.reply_to(message, "✅ Включён!")
    elif action == "off":
        set_translator_enabled(chat_id, False)
        bot.reply_to(message, "❌ Выключен")

@bot.message_handler(func=lambda m: True, content_types=['text'])
def auto_translate(message):
    chat_id = message.chat.id
    if not is_translator_enabled(chat_id):
        return
    if message.from_user.id == bot.get_me().id or message.text.startswith('/') or message.text.startswith('📩') or message.text.startswith('📨'):
        return
    text = message.text.strip()
    if not text or len(text) < 3:
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
        logger.error(f"Ошибка: {e}")

# === СКРЫТЫЕ СООБЩЕНИЯ ===
@bot.inline_handler(func=lambda query: True)
def inline_query(query):
    try:
        text = query.query.strip()
        if not text or len(text.split(maxsplit=1)) < 2:
            return
        target_raw, content = text.split(maxsplit=1)
        target_raw = target_raw.lstrip("@")
        
        target_id = None
        target_name = target_raw
        try:
            info = bot.get_chat(f"@{target_raw}")
            target_id = info.id
            target_name = info.first_name or target_raw
        except:
            if target_raw.isdigit():
                target_id = int(target_raw)
                target_name = f"Пользователь {target_raw}"
            else:
                result = types.InlineQueryResultArticle(
                    id="err",
                    title="❌ Не найден",
                    description=f"{target_raw} не найден",
                    input_message_content=types.InputTextMessageContent(f"❌ Пользователь {target_raw} не найден")
                )
                bot.answer_inline_query(query.id, [result], cache_time=0)
                return
        
        msg_id = f"sec_{int(time.time())}_{query.from_user.id}_{random.randint(1000,9999)}"
        secret_messages[msg_id] = {
            "target_id": target_id,
            "target_name": target_name,
            "content": content,
            "sender": query.from_user.first_name,
            "expires": time.time() + 86400
        }
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"sec_{msg_id}"))
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Для {target_name}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔐 *Скрытое сообщение*\nОт: {query.from_user.first_name}"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)
    except Exception as e:
        logger.error(f"Inline: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("sec_"))
def handle_secret_read(call):
    msg_id = call.data[4:]
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Не найдено", show_alert=True)
        return
    data = secret_messages[msg_id]
    if call.from_user.id != data["target_id"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Истекло", show_alert=True)
        del secret_messages[msg_id]
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.answer_callback_query(call.id, f"📩 От {data['sender']}:\n\n{data['content']}", show_alert=True)
    del secret_messages[msg_id]

def clean_old():
    now = time.time()
    to_del = [mid for mid, d in secret_messages.items() if d.get("expires", 0) < now]
    for mid in to_del:
        del secret_messages[mid]

threading.Thread(target=lambda: (time.sleep(3600), clean_old()), daemon=True).start()

# === ПОСТЫ В КАНАЛАХ ===
@bot.channel_post_handler(func=lambda m: True)
def channel_reaction(message):
    allowed = [-1001317416582, -1002185590715]
    if message.chat.id not in allowed:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
        data = {"chat_id": message.chat.id, "message_id": message.message_id, "reaction": [{"type": "emoji", "emoji": "🔥"}]}
        requests.post(url, json=data, timeout=5)
        logger.info(f"🔥 Реакция на пост {message.message_id}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")

# === ЗАПУСК ===
start_all_reminders()

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook: {e}")
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
    logger.info("✅ Пересылка между чатами ОТКЛЮЧЕНА")
    logger.info("✅ Напоминания работают")
    logger.info("✅ Википедия работает")
    logger.info("✅ ИИ работает")
    logger.info("✅ Бекап работает")
    
    app.run(host="0.0.0.0", port=port)

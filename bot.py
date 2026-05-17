import os
import json
import time
import threading
import requests
import hashlib
import re
import random
from datetime import datetime, timedelta
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import pytz
import user_cache

# === ПЕРЕМЕННЫЕ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)
secret_messages = {}
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

print("🤖 БОТ ЗАПУЩЕН")
print(f"🔑 TOKEN: {BOT_TOKEN[:10]}...")
print(f"👑 ADMIN: {ADMIN_ID}")

# === ФУНКЦИЯ ЭКРАНИРОВАНИЯ MARKDOWN ===
def escape_markdown(text):
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in escape_chars else c for c in str(text))

# === ЗАГРУЗКА КЭША ПОЛЬЗОВАТЕЛЕЙ ===
chat_users = user_cache.load_users()
print(f"👥 Загружено {len(chat_users)} пользователей")

# === ДЛЯ ЦИТАТ ===
QUOTES_CACHE_FILE = "daily_quotes.json"
daily_messages = []
active_chats = set()

# === НАСТРОЙКИ ЦИТАТ ===
quotes_settings = {}
QUOTES_SETTINGS_FILE = "quotes_settings.json"

def load_quotes_settings():
    global quotes_settings
    if os.path.exists(QUOTES_SETTINGS_FILE):
        try:
            with open(QUOTES_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                quotes_settings = json.load(f)
                print(f"📜 Загружено {len(quotes_settings)} настроек цитат")
        except:
            pass

def save_quotes_settings():
    try:
        with open(QUOTES_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(quotes_settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка: {e}")

def get_chat_quotes_settings(chat_id):
    chat_id_str = str(chat_id)
    if chat_id_str not in quotes_settings:
        quotes_settings[chat_id_str] = {"enabled": True, "interval_hours": 2}
        save_quotes_settings()
    return quotes_settings[chat_id_str]

def update_chat_quotes_settings(chat_id, key, value):
    chat_id_str = str(chat_id)
    if chat_id_str not in quotes_settings:
        quotes_settings[chat_id_str] = {"enabled": True, "interval_hours": 2}
    quotes_settings[chat_id_str][key] = value
    save_quotes_settings()

# === Глобальные переменные для таймеров ===
quote_timers = {}
summary_timers = {}

def get_today_date():
    return datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d')

def load_daily_quotes():
    global daily_messages
    if os.path.exists(QUOTES_CACHE_FILE):
        try:
            with open(QUOTES_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == get_today_date():
                    daily_messages = data.get('messages', [])
                    print(f"📚 Загружено {len(daily_messages)} сообщений за сегодня")
                else:
                    clear_daily_quotes()
        except Exception as e:
            print(f"❌ Ошибка: {e}")

def save_daily_quotes():
    try:
        data = {
            'date': get_today_date(),
            'messages': daily_messages
        }
        with open(QUOTES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка: {e}")

def clear_daily_quotes():
    global daily_messages
    daily_messages = []
    save_daily_quotes()
    print("🔄 Кэш цитат очищен")

def add_message_to_quotes(message):
    global daily_messages
    if message.from_user and message.from_user.is_bot:
        return
    if not message.text:
        return
    if message.text.startswith('/'):
        return
    if len(message.text) < 2:
        return
    if len(message.text) > 500:
        return
    user = message.from_user
    if not user:
        return
    thread_id = message.message_thread_id if message.message_thread_id else 0
    unique_id = f"{message.chat.id}_{thread_id}"
    daily_messages.append({
        'text': message.text.strip(),
        'author': user.id,
        'author_name': user.first_name or user.username or "Участник",
        'author_username': user.username,
        'time': datetime.now(MOSCOW_TZ).strftime('%H:%M:%S'),
        'chat_id': message.chat.id,
        'thread_id': thread_id,
        'unique_id': unique_id,
        'date': get_today_date()
    })
    if len(daily_messages) > 1000:
        daily_messages = daily_messages[-1000:]
    save_daily_quotes()

def get_today_messages_for_chat(chat_id, thread_id=0):
    unique_id = f"{chat_id}_{thread_id}"
    return [m for m in daily_messages if m.get('unique_id') == unique_id]

def add_chat_to_active(message):
    if message.text and message.text.startswith('/'):
        return
    thread_id = message.message_thread_id if message.message_thread_id else 0
    unique_id = f"{message.chat.id}_{thread_id}"
    if unique_id not in active_chats:
        active_chats.add(unique_id)

def get_chat_messages_count(chat_id, thread_id=0):
    return len(get_today_messages_for_chat(chat_id, thread_id))

def get_random_quote(chat_id, thread_id=0):
    chat_messages = get_today_messages_for_chat(chat_id, thread_id)
    if len(chat_messages) < 2:
        return None
    quote = random.choice(chat_messages)
    return f"📜 *Цитата дня*\n\n« {escape_markdown(quote['text'])} »\n\n— {escape_markdown(quote.get('author_name', 'Участник'))}"

def clean_inactive_chats():
    global active_chats
    to_remove = []
    for unique_id in active_chats:
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        chat_messages = get_today_messages_for_chat(chat_id)
        if len(chat_messages) < 2:
            to_remove.append(unique_id)
    for item in to_remove:
        active_chats.discard(item)

def cancel_quote_timer(chat_id):
    if chat_id in quote_timers:
        try:
            quote_timers[chat_id].cancel()
        except:
            pass
        del quote_timers[chat_id]

def schedule_quote_for_chat(chat_id, thread_id=0):
    settings = get_chat_quotes_settings(chat_id)
    if not settings.get("enabled", True):
        return
    
    interval_hours = settings.get("interval_hours", 2)
    delay_seconds = interval_hours * 3600 + random.randint(-1800, 1800)
    if delay_seconds < 1800:
        delay_seconds = 1800
    
    timer = threading.Timer(delay_seconds, lambda: send_quote_to_chat(chat_id, thread_id))
    timer.daemon = True
    timer.start()
    quote_timers[chat_id] = timer
    print(f"📜 Цитата для чата {chat_id} через {delay_seconds/3600:.1f}ч")

def send_quote_to_chat(chat_id, thread_id=0):
    settings = get_chat_quotes_settings(chat_id)
    if not settings.get("enabled", True):
        cancel_quote_timer(chat_id)
        return
    
    chat_messages = get_today_messages_for_chat(chat_id, thread_id)
    
    if len(chat_messages) < 2:
        schedule_quote_for_chat(chat_id, thread_id)
        return
    
    quote = random.choice(chat_messages)
    author_name = quote.get('author_name', 'Участник')
    quote_text = quote.get('text', '')
    
    text = f"📜 *Цитата дня*\n\n« {escape_markdown(quote_text)} »\n\n— {escape_markdown(author_name)}"
    
    try:
        bot.send_message(chat_id, text, parse_mode="Markdown", message_thread_id=thread_id if thread_id else None)
        print(f"✅ Цитата отправлена в чат {chat_id}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    
    schedule_quote_for_chat(chat_id, thread_id)

def schedule_all_chat_quotes():
    for unique_id in list(active_chats):
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        thread_id = int(parts[1]) if len(parts) > 1 else 0
        schedule_quote_for_chat(chat_id, thread_id)

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = "reminders.json"
reminders = []
reminder_counter = 0

def load_reminders():
    global reminders, reminder_counter
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                reminders = json.load(f)
                reminder_counter = max([r.get("id", 0) for r in reminders]) if reminders else 0
                print(f"⏰ Загружено {len(reminders)} напоминаний")
        except:
            reminders = []

def save_reminders():
    to_save = []
    for r in reminders:
        copy = {k: v for k, v in r.items() if k not in ["timer", "_timer"]}
        to_save.append(copy)
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка: {e}")

def send_reminder(reminder):
    try:
        bot.send_message(reminder["chat_id"], f"⏰ НАПОМИНАНИЕ!\n\n{escape_markdown(reminder['text'])}", message_thread_id=reminder.get("thread_id"))
        print(f"✅ Напоминание {reminder['id']} отправлено")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

def schedule_reminder(reminder):
    now_moscow = datetime.now(MOSCOW_TZ)
    target = now_moscow.replace(hour=reminder["hours"], minute=reminder["minutes"], second=0, microsecond=0)
    if target <= now_moscow:
        target += timedelta(days=1)
    delay = (target - now_moscow).total_seconds()
    timer = threading.Timer(delay, lambda: execute_reminder(reminder))
    timer.daemon = True
    timer.start()
    reminder["_timer"] = timer
    print(f"⏰ Напоминание {reminder['id']} на {target.strftime('%H:%M')}")

def execute_reminder(reminder):
    send_reminder(reminder)
    if reminder.get("daily"):
        schedule_reminder(reminder)

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

# === ИИ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

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
    messages = [{"role": "system", "content": "Отвечай кратко. НЕ используй теги <think>."}, *user_histories[user_id]]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3-32b", "messages": messages, "max_tokens": 800, "temperature": 0.2}
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>|\[think\].*?\[/think\]', '', answer, flags=re.DOTALL | re.IGNORECASE).strip()
            if not answer:
                answer = "⚠️ Не удалось сгенерировать ответ."
            user_histories[user_id].append({"role": "assistant", "content": answer})
            ai_cache[cache_key] = (time.time(), answer)
            return answer
        elif response.status_code == 429:
            return "⚠️ Лимит. Подождите."
        return f"❌ Ошибка: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"

def ask_groq_for_summary(prompt):
    if not GROQ_API_KEY:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3-32b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000, "temperature": 0.8}
    try:
        response = requests.post(url, headers=headers, json=data, timeout=45)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            return re.sub(r'<think>.*?</think>|\[think\].*?\[/think\]', '', answer, flags=re.DOTALL | re.IGNORECASE).strip()
    except:
        return None

def set_reaction(chat_id, message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    data = {"chat_id": chat_id, "message_id": message_id, "reaction": [{"type": "emoji", "emoji": "🔥"}]}
    try:
        requests.post(url, json=data, timeout=5)
    except:
        pass

# ========== НАСТРОЙКИ СВОДКИ ==========
summary_settings = {}
SUMMARY_FILE = "summary_settings.json"

def load_summary_settings():
    global summary_settings
    if os.path.exists(SUMMARY_FILE):
        try:
            with open(SUMMARY_FILE, 'r', encoding='utf-8') as f:
                summary_settings = json.load(f)
                print(f"📋 Загружено {len(summary_settings)} настроек")
        except:
            pass

def save_summary_settings():
    try:
        with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
            json.dump(summary_settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка: {e}")

def get_chat_summary_settings(chat_id):
    chat_id_str = str(chat_id)
    if chat_id_str not in summary_settings:
        summary_settings[chat_id_str] = {
            "enabled": True,
            "time": "22:00",
            "mode": "normal",
            "ai_style": "troll",
            "quote_enabled": False
        }
        save_summary_settings()
    return summary_settings[chat_id_str]

def update_chat_summary_settings(chat_id, key, value):
    chat_id_str = str(chat_id)
    if chat_id_str not in summary_settings:
        summary_settings[chat_id_str] = {
            "enabled": True,
            "time": "22:00",
            "mode": "normal",
            "ai_style": "troll",
            "quote_enabled": False
        }
    summary_settings[chat_id_str][key] = value
    save_summary_settings()
    reschedule_summary_for_chat(chat_id)

def cancel_summary_timer(chat_id):
    if chat_id in summary_timers:
        try:
            summary_timers[chat_id].cancel()
        except:
            pass
        del summary_timers[chat_id]

def schedule_summary_for_chat(chat_id, thread_id=0):
    settings = get_chat_summary_settings(chat_id)
    if not settings.get("enabled", True):
        return
    
    now_moscow = datetime.now(MOSCOW_TZ)
    time_str = settings["time"]
    try:
        target_hour, target_minute = map(int, time_str.split(":"))
        target = now_moscow.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if target <= now_moscow:
            target += timedelta(days=1)
        delay = (target - now_moscow).total_seconds()
        cancel_summary_timer(chat_id)
        timer = threading.Timer(delay, lambda: send_scheduled_summary(chat_id, thread_id))
        timer.daemon = True
        timer.start()
        summary_timers[chat_id] = timer
        print(f"📋 Сводка для чата {chat_id} на {target.strftime('%H:%M')}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

def reschedule_summary_for_chat(chat_id):
    thread_id = 0
    for unique_id in active_chats:
        parts = unique_id.split("_")
        if int(parts[0]) == chat_id:
            thread_id = int(parts[1]) if len(parts) > 1 else 0
            break
    schedule_summary_for_chat(chat_id, thread_id)

def generate_regular_summary(chat_id):
    today_messages = get_today_messages_for_chat(chat_id)
    if len(today_messages) < 3:
        return None
    total_msgs = len(today_messages)
    users_count = len(set(m.get('author') for m in today_messages))
    user_activity = {}
    for msg in today_messages:
        author = msg.get('author_name', 'Участник')
        user_activity[author] = user_activity.get(author, 0) + 1
    top_users = sorted(user_activity.items(), key=lambda x: x[1], reverse=True)[:3]
    text = f"📊 *Сводка дня*\n\n📊 *Статистика:*\n• Сообщений: {total_msgs}\n• Участников: {users_count}\n\n"
    if top_users:
        text += f"🏆 *Самые активные:*\n"
        for name, count in top_users:
            text += f"• {escape_markdown(name)} — {count}\n"
    return text

def generate_ai_summary(chat_id, style="troll"):
    today_messages = get_today_messages_for_chat(chat_id)
    if len(today_messages) < 5:
        return None
    recent_messages = today_messages[-40:]
    chat_log = []
    for msg in recent_messages:
        author = msg.get('author_name', 'Участник')
        text = msg.get('text', '')
        if text and len(text) < 200:
            chat_log.append(f"{escape_markdown(author)}: {escape_markdown(text)}")
    if not chat_log:
        return None
    conversation = "\n".join(chat_log)
    style_text = "юмористическую" if style == "troll" else "информативную"
    prompt = f"""Ты — журналист. Напиши {style_text} сводку по этому чату.
Разбей на 4-6 тем с эмодзи. НЕ ИСПОЛЬЗУЙ теги <think>.
Вот диалог за сегодня:
{conversation}"""
    response = ask_groq_for_summary(prompt)
    if response:
        return f"📝 *Что обсуждали участники чата сегодня?*\n\n{response}"
    return None

def send_scheduled_summary(chat_id, thread_id=0):
    settings = get_chat_summary_settings(chat_id)
    if not settings.get("enabled", True):
        return
    
    today_messages = get_today_messages_for_chat(chat_id, thread_id)
    if len(today_messages) < 3:
        print(f"⏩ В чате {chat_id} недостаточно сообщений за сегодня для сводки")
        schedule_summary_for_chat(chat_id, thread_id)
        return
    
    if settings.get("mode") == "ai":
        summary = generate_ai_summary(chat_id, settings.get("ai_style", "troll"))
    else:
        summary = generate_regular_summary(chat_id)
    
    if not summary:
        schedule_summary_for_chat(chat_id, thread_id)
        return
    
    quote_enabled = settings.get("quote_enabled", False)
    header = "📊 *Сводка дня*"
    
    if quote_enabled:
        summary_html = summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        summary_html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', summary_html)
        summary_html = re.sub(r'\*(.+?)\*', r'<i>\1</i>', summary_html)
        summary_html = summary_html.replace('\n', '<br/>')
        
        text = f"{header}\n\n<blockquote expandable>{summary_html}</blockquote>"
        
        try:
            bot.send_message(chat_id, text, parse_mode="HTML", 
                            message_thread_id=thread_id if thread_id else None)
            print(f"📋 Отправлена сводка (цитата) в чат {chat_id}")
        except Exception as e:
            print(f"❌ Ошибка HTML: {e}")
            bot.send_message(chat_id, f"{header}\n\n{summary}", parse_mode="Markdown", 
                            message_thread_id=thread_id if thread_id else None)
    else:
        try:
            bot.send_message(chat_id, f"{header}\n\n{summary}", parse_mode="Markdown", 
                            message_thread_id=thread_id if thread_id else None)
            print(f"📋 Отправлена сводка в чат {chat_id}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    schedule_summary_for_chat(chat_id, thread_id)

def schedule_all_chat_summaries():
    for unique_chat in list(active_chats):
        parts = unique_chat.split("_")
        chat_id = int(parts[0])
        thread_id = int(parts[1]) if len(parts) > 1 else 0
        schedule_summary_for_chat(chat_id, thread_id)

# ========== ФУНКЦИЯ ПРОВЕРКИ ПРАВ АДМИНА ==========

def is_chat_admin(chat_id, user_id):
    try:
        chat_member = bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['administrator', 'creator']
    except:
        return False

# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    if message.chat.id != user_id:
        bot.send_message(user_id, "⚙️ Отправляю меню...")
        bot.delete_message(message.chat.id, message.message_id)
        send_main_menu(user_id)
    else:
        send_main_menu(user_id)

@bot.message_handler(commands=['help'])
def help_command(message):
    text = "✅ *Бот работает!*\n\n🤖 *ИИ:* `/ai вопрос`\n\n⏰ *Напоминания:* `/remind 15:30 текст`\n📜 *Цитаты:* `/quote`\n📋 *Сводка:* `/summary`\n\n📜 *Управление цитатами:*\n• `/quotes_on` — включить\n• `/quotes_off` — выключить\n\n⚙️ *Меню:* `/start`"
    if message.from_user.id == ADMIN_ID:
        text += "\n\n👑 *Админ-команды:* `/users` `/adduser` `/deluser` `/backup` `/restore` `/check_reminders`"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['ai'])
def ai_command(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ /ai вопрос")
        return
    msg = bot.reply_to(message, "🤖 Думаю...")
    answer = ask_groq(message.from_user.id, prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id)

@bot.message_handler(commands=['quote'])
def quote_command(message):
    thread_id = message.message_thread_id or 0
    count = get_chat_messages_count(message.chat.id, thread_id)
    if count < 2:
        bot.reply_to(message, "📭 Нет сообщений для цитаты")
        return
    quote_text = get_random_quote(message.chat.id, thread_id)
    if quote_text:
        bot.reply_to(message, quote_text, parse_mode="Markdown")

@bot.message_handler(commands=['quotes_on'])
def quotes_on_command(message):
    chat_id = message.chat.id
    if not is_chat_admin(chat_id, message.from_user.id):
        bot.reply_to(message, "❌ Только админы могут включать цитаты!")
        return
    update_chat_quotes_settings(chat_id, "enabled", True)
    thread_id = 0
    for unique_id in active_chats:
        parts = unique_id.split("_")
        if int(parts[0]) == chat_id:
            thread_id = int(parts[1]) if len(parts) > 1 else 0
            break
    schedule_quote_for_chat(chat_id, thread_id)
    bot.reply_to(message, "✅ Цитаты включены!")

@bot.message_handler(commands=['quotes_off'])
def quotes_off_command(message):
    chat_id = message.chat.id
    if not is_chat_admin(chat_id, message.from_user.id):
        bot.reply_to(message, "❌ Только админы могут выключать цитаты!")
        return
    update_chat_quotes_settings(chat_id, "enabled", False)
    cancel_quote_timer(chat_id)
    bot.reply_to(message, "❌ Цитаты выключены!")

@bot.message_handler(commands=['summary'])
def summary_command(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        subcmd = parts[1].lower()
        if subcmd == "on":
            if not is_chat_admin(message.chat.id, message.from_user.id):
                bot.reply_to(message, "❌ Только админы могут включать сводку!")
                return
            update_chat_summary_settings(message.chat.id, "enabled", True)
            bot.reply_to(message, "✅ Сводка включена!")
            return
        elif subcmd == "off":
            if not is_chat_admin(message.chat.id, message.from_user.id):
                bot.reply_to(message, "❌ Только админы могут выключать сводку!")
                return
            update_chat_summary_settings(message.chat.id, "enabled", False)
            bot.reply_to(message, "❌ Сводка выключена!")
            return
        elif subcmd in ["тролль", "troll"]:
            if not is_chat_admin(message.chat.id, message.from_user.id):
                bot.reply_to(message, "❌ Только админы могут смотреть сводку!")
                return
            summary = generate_ai_summary(message.chat.id, "troll")
            bot.reply_to(message, summary if summary else "📭 Недостаточно сообщений за сегодня")
            return
        elif subcmd in ["обычный", "normal"]:
            if not is_chat_admin(message.chat.id, message.from_user.id):
                bot.reply_to(message, "❌ Только админы могут смотреть сводку!")
                return
            summary = generate_regular_summary(message.chat.id)
            bot.reply_to(message, summary if summary else "📭 Недостаточно сообщений за сегодня")
            return
    if not is_chat_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Только админы могут смотреть сводку!")
        return
    settings = get_chat_summary_settings(message.chat.id)
    if settings.get("mode") == "ai":
        summary = generate_ai_summary(message.chat.id, settings.get("ai_style", "troll"))
    else:
        summary = generate_regular_summary(message.chat.id)
    bot.reply_to(message, summary if summary else "📭 Недостаточно сообщений за сегодня")

@bot.message_handler(commands=['remind'])
def add_reminder(message):
    global reminder_counter
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        msg = bot.send_message(chat_id, "ℹ️ /remind 15:30 текст", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
        return
    time_str = parts[1]
    reminder_text = parts[2]
    try:
        if ":" in time_str:
            hours, minutes = map(int, time_str.split(":"))
        else:
            hours = int(time_str)
            minutes = 0
    except:
        msg = bot.send_message(chat_id, "❌ Неверный формат", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
        return
    daily = reminder_text.lower().startswith("ежедневно")
    if daily:
        reminder_text = reminder_text[len("ежедневно"):].lstrip()
    reminder_counter += 1
    reminder = {
        "id": reminder_counter,
        "chat_id": chat_id,
        "user_id": message.from_user.id,
        "thread_id": thread_id,
        "text": reminder_text,
        "hours": hours,
        "minutes": minutes,
        "daily": daily,
        "created_at": datetime.now(MOSCOW_TZ).isoformat()
    }
    reminders.append(reminder)
    save_reminders()
    schedule_reminder(reminder)
    msg = bot.send_message(chat_id, f"✅ Напоминание #{reminder_counter} создано!\n⏰ {hours:02d}:{minutes:02d}", message_thread_id=thread_id)
    delete_after_delay(chat_id, msg.message_id, 10)

@bot.message_handler(commands=['reminds'])
def list_reminders(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    user_reminders = [r for r in reminders if r.get("chat_id") == chat_id and r.get("user_id") == message.from_user.id]
    if thread_id:
        user_reminders = [r for r in user_reminders if r.get("thread_id") == thread_id]
    if not user_reminders:
        msg = bot.send_message(chat_id, "📭 Нет напоминаний", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    response = "📋 *Ваши напоминания:*\n\n"
    for r in user_reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"{r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 {r['id']} - {period}\n   📝 {escape_markdown(r['text'][:40])}\n\n"
    msg = bot.send_message(chat_id, response, parse_mode="Markdown", message_thread_id=thread_id)
    delete_after_delay(chat_id, msg.message_id, 30)

@bot.message_handler(commands=['delremind'])
def delete_reminder(message):
    global reminders
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    parts = message.text.split()
    if len(parts) < 2:
        msg = bot.send_message(chat_id, "ℹ️ /delremind ID", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
        return
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if r.get("chat_id") != chat_id or r.get("thread_id") != thread_id:
                    return
                if r.get("user_id") != message.from_user.id:
                    bot.reply_to(message, "❌ Не ваше напоминание!")
                    return
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders()
                msg = bot.send_message(chat_id, f"✅ Напоминание {rid} удалено", message_thread_id=thread_id)
                delete_after_delay(chat_id, msg.message_id, 10)
                return
        msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
    except:
        msg = bot.send_message(chat_id, "❌ Неверный ID", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)

# ========== АДМИН-КОМАНДЫ ==========

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    
    if not chat_users:
        bot.send_message(message.chat.id, "📭 Нет пользователей")
        return
    
    total = len(chat_users)
    bot.send_message(message.chat.id, f"📊 *Всего пользователей:* {total}", parse_mode="Markdown")
    
    user_lines = []
    for uid, user in chat_users.items():
        username = user.get('username')
        first_name = user.get('first_name', 'Без имени')
        
        safe_name = escape_markdown(first_name)
        
        if username:
            safe_username = escape_markdown(username)
            user_lines.append(f"• `{uid}` | @{safe_username} | {safe_name}")
        else:
            user_lines.append(f"• `{uid}` | {safe_name}")
    
    chunk_size = 50
    total_chunks = (len(user_lines) + chunk_size - 1) // chunk_size
    
    for i in range(0, len(user_lines), chunk_size):
        chunk = user_lines[i:i+chunk_size]
        chunk_num = (i // chunk_size) + 1
        text = f"📋 *Список пользователей* (часть {chunk_num}/{total_chunks}):\n\n"
        text += "\n".join(chunk)
        bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['adduser'])
def add_user_manually(message):
    global chat_users
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ /adduser @username")
        return
    username = parts[1].strip().lstrip("@")
    user_id = f"manual_{int(time.time())}"
    chat_users[user_id] = {
        "id": user_id,
        "username": username,
        "first_name": username,
        "last_name": "",
        "full_name": username,
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    user_cache.save_users(chat_users)
    bot.reply_to(message, f"✅ @{escape_markdown(username)} добавлен!")

@bot.message_handler(commands=['deluser'])
def delete_user(message):
    global chat_users
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ /deluser @username")
        return
    target = parts[1].strip().lstrip("@")
    found = None
    for uid, user in chat_users.items():
        if user.get('username') == target:
            found = uid
            break
    if found:
        del chat_users[found]
        user_cache.save_users(chat_users)
        bot.reply_to(message, f"✅ @{escape_markdown(target)} удалён!")
    else:
        bot.reply_to(message, f"❌ @{escape_markdown(target)} не найден")

@bot.message_handler(commands=['backup'])
def backup_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    status = bot.reply_to(message, "🔄 Создаю бекап...")
    try:
        clean_reminders = [{k: v for k, v in r.items() if k not in ["timer", "_timer"]} for r in reminders]
        data = {
            "version": "2.1",
            "date": str(datetime.now()),
            "reminders": clean_reminders,
            "chat_users": chat_users,
            "summary_settings": summary_settings,
            "quotes_settings": quotes_settings
        }
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"✅ Бекап создан!\n👥 {len(chat_users)} пользователей\n⏰ {len(clean_reminders)} напоминаний")
        os.remove(filename)
        bot.delete_message(message.chat.id, status.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", message.chat.id, status.message_id)

@bot.message_handler(commands=['restore'])
def restore_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    bot.send_message(message.chat.id, "📥 Отправьте JSON файл бекапа")

@bot.message_handler(commands=['check_reminders'])
def check_all_reminders(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для главного администратора!")
        return
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    
    if not reminders:
        bot.send_message(message.chat.id, "📭 Нет ни одного напоминания в боте")
        return
    
    total = len(reminders)
    daily_count = len([r for r in reminders if r.get("daily")])
    regular_count = total - daily_count
    
    chats_stats = {}
    for r in reminders:
        chat_id = r.get("chat_id")
        if chat_id not in chats_stats:
            try:
                chat = bot.get_chat(chat_id)
                chat_title = chat.title or f"Чат {chat_id}"
            except:
                chat_title = f"Чат {chat_id}"
            chats_stats[chat_id] = {"title": chat_title, "count": 0}
        chats_stats[chat_id]["count"] += 1
    
    text = f"📊 *Статистика напоминаний*\n\n"
    text += f"📝 Всего: {total}\n"
    text += f"🔄 Ежедневных: {daily_count}\n"
    text += f"⏰ Обычных: {regular_count}\n"
    text += f"💬 Чатов с напоминаниями: {len(chats_stats)}\n\n"
    text += f"*Список чатов:*\n"
    
    for chat_id, data in chats_stats.items():
        text += f"• {escape_markdown(data['title'][:30])} — {data['count']} нап.\n"
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(content_types=['document'])
def handle_restore_file(message):
    global chat_users, reminder_counter, reminders, summary_settings, quotes_settings
    if message.from_user.id != ADMIN_ID:
        return
    if message.chat.type != 'private':
        return
    status = bot.reply_to(message, "🔄 Восстанавливаю...")
    try:
        file_info = bot.get_file(message.document.file_id)
        content = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}").content
        data = json.loads(content.decode('utf-8'))
        for r in reminders:
            if "_timer" in r:
                try:
                    r["_timer"].cancel()
                except:
                    pass
        if "reminders" in data:
            reminders.clear()
            reminder_counter = 0
            for r in data["reminders"]:
                reminders.append(r)
                if r.get("id", 0) > reminder_counter:
                    reminder_counter = r.get("id", 0)
            save_reminders()
            start_all_reminders()
        if "chat_users" in data:
            chat_users = data["chat_users"]
            user_cache.save_users(chat_users)
        if "summary_settings" in data:
            summary_settings = data["summary_settings"]
            save_summary_settings()
            schedule_all_chat_summaries()
        if "quotes_settings" in data:
            quotes_settings = data["quotes_settings"]
            save_quotes_settings()
            schedule_all_chat_quotes()
        bot.edit_message_text(f"✅ Восстановлено!\n👥 {len(chat_users)} пользователей\n⏰ {len(reminders)} напоминаний", message.chat.id, status.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", message.chat.id, status.message_id)

# ========== РП ДЕЙСТВИЯ ==========

def decline_name(name, preposition=""):
    preposition = preposition.lower()
    if name in ["кому-то", "пользователь", "участник", "кто-то", "кого-то"]:
        return name
    parts = name.split()
    declined_parts = []
    for part in parts:
        declined_parts.append(decline_single_name(part, preposition))
    return " ".join(declined_parts)

def decline_single_name(name, preposition=""):
    name_lower = name.lower()
    
    if preposition in ["у", "без", "до", "из", "от", "para"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'ы'
        elif name_lower.endswith('я'):
            return name[:-1] + 'и'
        else:
            return name + 'а'
    
    if preposition in ["с", "над", "под", "перед"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'ой'
        elif name_lower.endswith('я'):
            return name[:-1] + 'ей'
        elif name_lower.endswith('й'):
            return name[:-1] + 'ем'
        else:
            return name + 'ом'
    
    if preposition in ["о", "об", "при"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'е'
        elif name_lower.endswith('я'):
            return name[:-1] + 'е'
        else:
            return name + 'е'
    
    if name_lower.endswith('а'):
        return name[:-1] + 'е'
    elif name_lower.endswith('я'):
        return name[:-1] + 'е'
    elif name_lower.endswith('й'):
        return name[:-1] + 'ю'
    elif name_lower.endswith('ь'):
        return name[:-1] + 'ю'
    else:
        return name + 'у'

def get_gender(user):
    name = (user.first_name or "").lower()
    female_endings = ('а', 'я', 'ия', 'ья')
    male_exceptions = ('никита', 'дима', 'влад', 'лева', 'саша', 'женя', 'валя', 'илья')
    if name.endswith(female_endings) and name not in male_exceptions:
        return 'female'
    return 'male'

def handle_actions(message):
    full_text = message.text.strip().lower()
    
    # Команды на всех (без ответа)
    global_actions = {
        "кончить на всех": ("💦", "кончил на всех", "кончила на всех"),
        "сквиртануть на всех": ("💦💦", "сквиртанул на всех", "сквиртанула на всех"),
        "кончить всем в лицо": ("💦", "кончил всем в лицо", "кончила всем в лицо"),
    }
    
    if full_text in global_actions:
        emoji, male_action, female_action = global_actions[full_text]
        sender = message.from_user
        sender_name = sender.first_name or sender.username or "Кто-то"
        sender_gender = get_gender(sender)
        action = male_action if sender_gender == 'male' else female_action
        
        response = f"{emoji} {escape_markdown(sender_name)} {action}"
        thread_id = message.message_thread_id if message.message_thread_id else None
        try:
            bot.send_message(message.chat.id, response, message_thread_id=thread_id)
            return True
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return False
    
    # Команды с ответом на сообщение
    if not message.reply_to_message:
        return False
    
    # Многословные команды
    multiline_commands = ["сесть на лицо", "дать пять", "пожать руку", "послать нахуй"]
    search_key = None
    
    for cmd in multiline_commands:
        if full_text == cmd:
            search_key = cmd
            break
    
    if not search_key:
        search_key = full_text.replace(" ", "")
    
    actions_map = {
        "обнять": ("🤗", "обнял", "обняла", ""),
        "обнимаю": ("🤗", "обнял", "обняла", ""),
        "обниму": ("🤗", "обнял", "обняла", ""),
        "поцеловать": ("😘", "поцеловал", "поцеловала", ""),
        "целую": ("😘", "поцеловал", "поцеловала", ""),
        "поцелую": ("😘", "поцеловал", "поцеловала", ""),
        "прижать": ("🫂", "прижал", "прижала", ""),
        "погладить": ("🫳", "погладил", "погладила", ""),
        "потрогать": ("✋", "потрогал", "потрогала", ""),
        "кусь": ("🦷", "куснул", "куснула", ""),
        "укусить": ("🦷", "укусил", "укусила", ""),
        "лизнуть": ("👅", "лизнул", "лизнула", ""),
        "облизать": ("👅", "облизал", "облизала", ""),
        "похвалить": ("👍", "похвалил", "похвалила", ""),
        "поздравить": ("🎉", "поздравил", "поздравила", "с"),
        "извиниться": ("🙏", "извинился", "извинилась", "перед"),
        "пожать руку": ("🤝", "пожал руку", "пожала руку", ""),
        "шлепнуть": ("🖐️", "шлепнул", "шлепнула", ""),
        "ущипнуть": ("🤏", "ущипнул", "ущипнула", ""),
        "покормить": ("🍕", "покормил", "покормила", ""),
        "дать пять": ("🙏", "дал пять", "дала пять", ""),
        "понюхать": ("👃", "понюхал", "понюхала", ""),
        "испугать": ("😱", "испугал", "испугала", ""),
        "рассмешить": ("😂", "рассмешил", "рассмешила", ""),
        "предложить": ("💍", "предложил", "предложила", ""),
        "помочь": ("🫶", "помог", "помогла", ""),
        "ударить": ("👊", "ударил", "ударила", ""),
        "пнуть": ("🦶", "пнул", "пнула", ""),
        "убить": ("💀", "убил", "убила", ""),
        "сжечь": ("🔥", "сжёг", "сожгла", ""),
        "взорвать": ("💣", "взорвал", "взорвала", ""),
        "расстрелять": ("🔫", "расстрелял", "расстреляла", ""),
        "шмальнуть": ("🔫", "шмальнул", "шмальнула", "в"),
        "задушить": ("🪢", "задушил", "задушила", ""),
        "послать нахуй": ("🖕", "послал нахуй", "послала нахуй", ""),
        "наорать": ("📢", "наорал", "наорала", "на"),
        "унизить": ("😢", "унизил", "унизила", ""),
        "арестовать": ("🚔", "арестовал", "арестовала", ""),
        "ушатать": ("⚰️", "ушатал", "ушатала", ""),
        "отрубить": ("⚡", "отрубил", "отрубила", ""),
        "выпороть": ("😨", "выпорол", "выпорола", ""),
        "закопать": ("🪦", "закопал", "закопала", ""),
        "связать": ("🪢", "связал", "связала", ""),
        "заставить": ("😤", "заставил", "заставила", ""),
        "повесить": ("🪢", "повесил", "повесила", "на"),
        "уничтожить": ("💥", "уничтожил", "уничтожила", ""),
        "продать": ("💰", "продал", "продала", ""),
        "кастрировать": ("✂️", "кастрировал", "кастрировала", ""),
        "отстрелить": ("🔫", "отстрелил", "отстрелила", ""),
        "выкопать": ("⛏️", "выкопал", "выкопала", ""),
        "выпить": ("🍺", "выпил", "выпила", ""),
        "наказать": ("😈", "наказал", "наказала", ""),
        "щекотать": ("😂", "пощекотал", "пощекотала", ""),
        "дрочить": ("✊", "дрочил", "дрочила", ""),
        "подрочить": ("✊", "подрочил", "подрочила", ""),
        "отдрочить": ("✊", "отдрочил", "отдрочила", ""),
        "лизать": ("👅", "лизал", "лизала", ""),
        "лижет": ("👅", "лизал", "лизала", ""),
        "отлизать": ("👅", "отлизал", "отлизала", ""),
        "выебать": ("🔞", "выебал", "выебала", ""),
        "оттрахать": ("🔞", "оттрахал", "оттрахала", ""),
        "трахнуть": ("🔞", "трахнул", "трахнула", ""),
        "изнасиловать": ("🔞", "изнасиловал", "изнасиловала", ""),
        "отсосать": ("👅", "отсосал", "отсосала", ""),
        "кончить": ("💦", "кончил", "кончила", "в"),
        "сквиртануть": ("💦💦", "сквиртанул", "сквиртанула", "на"),
        "сесть на лицо": ("🍑", "сел на лицо", "села на лицо", "на"),
        "сосать": ("👅", "сосал", "сосала", ""),
        "лечь": ("😴", "лёг", "леглá", "на"),
        "спать": ("😴", "лёг спать", "леглá спать", ""),
        "уснуть": ("😴", "уснул", "уснула", ""),
        "пить": ("🍺", "выпил", "выпила", ""),
        "зарезать": ("🔪", "зарезал", "зарезала", ""),
        "харкнуть": ("💨", "харкнул", "харкнула", "на"),
    }
    
    emoji = None
    past_action_male = None
    past_action_female = None
    preposition = ""
    reply_text = ""
    
    if search_key in actions_map:
        emoji, past_action_male, past_action_female, preposition = actions_map[search_key]
    else:
        parts = full_text.split(maxsplit=1)
        first_word = parts[0]
        reply_text = parts[1] if len(parts) > 1 else ""
        if first_word in actions_map:
            emoji, past_action_male, past_action_female, preposition = actions_map[first_word]
        else:
            return False
    
    sender = message.from_user
    target_user = message.reply_to_message.from_user
    sender_name = sender.first_name or sender.username or "Кто-то"
    target_name = target_user.first_name or target_user.username or "кому-то"
    
    sender_gender = get_gender(sender)
    past_action = past_action_male if sender_gender == 'male' else past_action_female
    
    declined_target = decline_name(target_name, preposition) if preposition else decline_name(target_name, "")
    
    if preposition:
        target_with_preposition = f"{preposition} {declined_target}"
    else:
        target_with_preposition = declined_target
    
    safe_sender = escape_markdown(sender_name)
    safe_target = escape_markdown(target_with_preposition)
    safe_reply = escape_markdown(reply_text)
    
    if reply_text:
        response = f"{emoji} {safe_sender} {past_action} {safe_target}: {safe_reply}"
    else:
        response = f"{emoji} {safe_sender} {past_action} {safe_target}"
    
    thread_id = message.message_thread_id if message.message_thread_id else None
    try:
        bot.send_message(message.chat.id, response, message_thread_id=thread_id)
        return True
    except Exception as e:
        print(f"❌ Ошибка РП: {e}")
        return False

# ========== ОСНОВНОЙ ОБРАБОТЧИК ==========
@bot.message_handler(func=lambda message: True)
def main_handler(message):
    if message.text and not message.text.startswith('/') and not message.reply_to_message:
        text_lower = message.text.lower().strip()
        user_name = message.from_user.first_name or message.from_user.username or "Пользователь"
        
        greetings = ["привет", "здарова", "ку", "хай"]
        thanks = ["спасибо", "благодарю", "thanks"]
        goodbyes = ["пока", "до свидания", "bye"]
        howare = ["как дела", "как ты"]
        morning = ["доброе утро", "доброго утра"]
        night = ["спокойной ночи", "доброй ночи"]
        
        if text_lower in greetings:
            bot.reply_to(message, f"👋 Привет, {escape_markdown(user_name)}!")
            return
        if text_lower in thanks:
            bot.reply_to(message, f"🙏 Пожалуйста, {escape_markdown(user_name)}!")
            return
        if text_lower in goodbyes:
            bot.reply_to(message, f"👋 Пока, {escape_markdown(user_name)}!")
            return
        if text_lower in howare:
            bot.reply_to(message, f"😊 У меня всё отлично, {escape_markdown(user_name)}!")
            return
        if text_lower in morning:
            bot.reply_to(message, f"🌅 Доброе утро, {escape_markdown(user_name)}!")
            return
        if text_lower in night:
            bot.reply_to(message, f"🌙 Спокойной ночи, {escape_markdown(user_name)}!")
            return
    
    if message.text and not message.text.startswith('/'):
        if handle_actions(message):
            return
    
    if message.chat.type in ['group', 'supergroup']:
        global chat_users
        old_count = len(chat_users)
        chat_users = user_cache.save_user_from_message(message, chat_users)
        if len(chat_users) != old_count:
            print(f"✨ Новый пользователь! Всего: {len(chat_users)}")
        add_chat_to_active(message)
        get_chat_summary_settings(message.chat.id)
        add_message_to_quotes(message)

# ========== ЛС-МЕНЮ ==========

def send_main_menu(user_id):
    text = "⚙️ *Главное меню бота*\n\nВыберите раздел:"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 Сводка", callback_data="menu_summary"),
        InlineKeyboardButton("⏰ Напоминания", callback_data="menu_reminders"),
        InlineKeyboardButton("📜 Цитаты", callback_data="menu_quotes"),
        InlineKeyboardButton("➕ Добавить бота", callback_data="menu_add_bot"),
        InlineKeyboardButton("❌ Закрыть", callback_data="close_menu")
    )
    bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=markup)

def get_user_chats_list(user_id, check_admin=False):
    user_chats = []
    seen = set()
    for unique_id in active_chats:
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        if chat_id in seen:
            continue
        try:
            chat = bot.get_chat(chat_id)
            if check_admin:
                member = bot.get_chat_member(chat_id, user_id)
                if member.status not in ['administrator', 'creator']:
                    continue
            else:
                try:
                    member = bot.get_chat_member(chat_id, user_id)
                    if member.status not in ['member', 'administrator', 'creator', 'restricted']:
                        continue
                except:
                    pass
            seen.add(chat_id)
            user_chats.append({"id": chat_id, "title": chat.title or f"Чат {chat_id}"})
        except:
            continue
    return user_chats

# ========== CALLBACKИ ==========

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data
    
    if data == "menu_summary":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        chats = get_user_chats_list(user_id, check_admin=True)
        if not chats:
            bot.send_message(user_id, "📭 Нет чатов, где вы администратор. Добавьте бота и сделайте админом.")
            return
        markup = InlineKeyboardMarkup()
        for chat in chats:
            markup.add(InlineKeyboardButton(f"📊 {escape_markdown(chat['title'][:35])}", callback_data=f"summary_{chat['id']}"))
        markup.add(InlineKeyboardButton("◀ Назад", callback_data="back_main"))
        bot.send_message(user_id, "📊 *Выберите чат* (только где вы админ)", parse_mode="Markdown", reply_markup=markup)
        
    elif data == "menu_reminders":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        chats = get_user_chats_list(user_id, check_admin=False)
        if not chats:
            bot.send_message(user_id, "📭 Нет доступных чатов.")
            return
        markup = InlineKeyboardMarkup()
        for chat in chats:
            count = len([r for r in reminders if r.get('chat_id') == chat['id'] and r.get('user_id') == user_id])
            markup.add(InlineKeyboardButton(f"⏰ {escape_markdown(chat['title'][:30])} ({count})", callback_data=f"remind_{chat['id']}"))
        markup.add(InlineKeyboardButton("◀ Назад", callback_data="back_main"))
        bot.send_message(user_id, "⏰ *Выберите чат*", parse_mode="Markdown", reply_markup=markup)
        
    elif data == "menu_quotes":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        chats = get_user_chats_list(user_id, check_admin=True)
        if not chats:
            bot.send_message(user_id, "📭 Нет чатов, где вы администратор.")
            return
        markup = InlineKeyboardMarkup()
        for chat in chats:
            settings = get_chat_quotes_settings(chat['id'])
            status = "✅" if settings.get("enabled", True) else "❌"
            markup.add(InlineKeyboardButton(f"📜 {status} {escape_markdown(chat['title'][:33])}", callback_data=f"quotes_{chat['id']}"))
        markup.add(InlineKeyboardButton("◀ Назад", callback_data="back_main"))
        bot.send_message(user_id, "📜 *Выберите чат* (только где вы админ)", parse_mode="Markdown", reply_markup=markup)
        
    elif data == "menu_add_bot":
        bot.answer_callback_query(call.id)
        bot_username = bot.get_me().username
        text = f"🤖 *Добавить бота:*\n\n1. Откройте группу\n2. Добавьте @{bot_username}\n3. Выдайте права админа\n\n[🔗 Ссылка](https://t.me/{bot_username}?startgroup=start)"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔗 Пригласить", url=f"https://t.me/{bot_username}?startgroup=start"))
        markup.add(InlineKeyboardButton("◀ Назад", callback_data="back_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
        
    elif data == "back_main":
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_main_menu(user_id)
        
    elif data == "close_menu":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)
        
    elif data.startswith("summary_"):
        chat_id = int(data.split("_")[1])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Вы не админ этого чата!", show_alert=True)
            return
        settings = get_chat_summary_settings(chat_id)
        status = "✅ Вкл" if settings.get("enabled") else "❌ Выкл"
        mode = "🤖 ИИ" if settings["mode"] == "ai" else "📊 Обычный"
        quote_status = "💬 Вкл" if settings.get("quote_enabled", False) else "📝 Выкл"
        text = f"📊 *Настройки*\n\n🟢 Статус: {status}\n🤖 Режим: {mode}\n🕐 Время: {settings['time']}\n💬 Цитирование: {quote_status}"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Вкл", callback_data=f"summ_enable_{chat_id}"),
            InlineKeyboardButton("❌ Выкл", callback_data=f"summ_disable_{chat_id}"),
            InlineKeyboardButton("🤖 ИИ", callback_data=f"summ_ai_{chat_id}"),
            InlineKeyboardButton("📊 Обычный", callback_data=f"summ_normal_{chat_id}"),
            InlineKeyboardButton("💬 Вкл цит.", callback_data=f"summ_quote_on_{chat_id}"),
            InlineKeyboardButton("📝 Выкл цит.", callback_data=f"summ_quote_off_{chat_id}"),
            InlineKeyboardButton("📋 Показать", callback_data=f"summ_show_{chat_id}"),
            InlineKeyboardButton("◀ Назад", callback_data="menu_summary")
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        
    elif data.startswith("remind_"):
        chat_id = int(data.split("_")[1])
        user_reminders = [r for r in reminders if r.get("chat_id") == chat_id and r.get("user_id") == user_id]
        try:
            chat = bot.get_chat(chat_id)
            title = chat.title or f"Чат {chat_id}"
        except:
            title = f"Чат {chat_id}"
        if not user_reminders:
            text = f"⏰ *{escape_markdown(title)}*\n\n📭 Нет напоминаний"
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("➕ Создать", callback_data=f"create_{chat_id}"))
            markup.add(InlineKeyboardButton("◀ Назад", callback_data="menu_reminders"))
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
            return
        text = f"⏰ *Ваши напоминания*\n`{escape_markdown(title)}`\n\n"
        for r in user_reminders[:10]:
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}" if r.get("daily") else f"{r['hours']:02d}:{r['minutes']:02d}"
            text += f"🆔 `{r['id']}` • {period}\n   📝 {escape_markdown(r['text'][:40])}\n\n"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Создать", callback_data=f"create_{chat_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{chat_id}"),
            InlineKeyboardButton("◀ Назад", callback_data="menu_reminders")
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        
    elif data.startswith("quotes_"):
        chat_id = int(data.split("_")[1])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Вы не админ этого чата!", show_alert=True)
            return
        settings = get_chat_quotes_settings(chat_id)
        try:
            chat = bot.get_chat(chat_id)
            title = chat.title or f"Чат {chat_id}"
        except:
            title = f"Чат {chat_id}"
        status = "✅ Включены" if settings.get("enabled", True) else "❌ Выключены"
        interval = settings.get("interval_hours", 2)
        text = f"📜 *Настройки цитат*\n`{escape_markdown(title)}`\n\n🟢 Статус: {status}\n⏱️ Интервал: {interval}ч"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ Вкл", callback_data=f"quote_enable_{chat_id}"),
            InlineKeyboardButton("❌ Выкл", callback_data=f"quote_disable_{chat_id}"),
            InlineKeyboardButton("⏱️ 1ч", callback_data=f"quote_interval_1_{chat_id}"),
            InlineKeyboardButton("⏱️ 2ч", callback_data=f"quote_interval_2_{chat_id}"),
            InlineKeyboardButton("⏱️ 3ч", callback_data=f"quote_interval_3_{chat_id}"),
            InlineKeyboardButton("◀ Назад", callback_data="menu_quotes")
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        
    elif data.startswith("quote_enable_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_quotes_settings(chat_id, "enabled", True)
        for unique_id in active_chats:
            parts = unique_id.split("_")
            if int(parts[0]) == chat_id:
                thread_id = int(parts[1]) if len(parts) > 1 else 0
                schedule_quote_for_chat(chat_id, thread_id)
                break
        bot.answer_callback_query(call.id, "✅ Цитаты включены!")
        
    elif data.startswith("quote_disable_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_quotes_settings(chat_id, "enabled", False)
        cancel_quote_timer(chat_id)
        bot.answer_callback_query(call.id, "❌ Цитаты выключены!")
        
    elif data.startswith("quote_interval_"):
        parts = data.split("_")
        interval = int(parts[2])
        chat_id = int(parts[3])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_quotes_settings(chat_id, "interval_hours", interval)
        if get_chat_quotes_settings(chat_id).get("enabled", True):
            cancel_quote_timer(chat_id)
            for unique_id in active_chats:
                parts = unique_id.split("_")
                if int(parts[0]) == chat_id:
                    thread_id = int(parts[1]) if len(parts) > 1 else 0
                    schedule_quote_for_chat(chat_id, thread_id)
                    break
        bot.answer_callback_query(call.id, f"⏱️ Интервал {interval} часа")
        
    elif data.startswith("summ_enable_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "enabled", True)
        bot.answer_callback_query(call.id, "✅ Сводка включена!")
        
    elif data.startswith("summ_disable_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "enabled", False)
        bot.answer_callback_query(call.id, "❌ Сводка выключена!")
        
    elif data.startswith("summ_ai_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "mode", "ai")
        bot.answer_callback_query(call.id, "🤖 Режим: ИИ")
        
    elif data.startswith("summ_normal_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "mode", "normal")
        bot.answer_callback_query(call.id, "📊 Режим: обычный")
        
    elif data.startswith("summ_quote_on_"):
        chat_id = int(data.split("_")[3])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "quote_enabled", True)
        bot.answer_callback_query(call.id, "💬 Цитирование включено!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_summary_settings(user_id, chat_id)
        
    elif data.startswith("summ_quote_off_"):
        chat_id = int(data.split("_")[3])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        update_chat_summary_settings(chat_id, "quote_enabled", False)
        bot.answer_callback_query(call.id, "📝 Цитирование выключено!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_summary_settings(user_id, chat_id)
        
    elif data.startswith("summ_show_"):
        chat_id = int(data.split("_")[2])
        if not is_chat_admin(chat_id, user_id):
            bot.answer_callback_query(call.id, "❌ Нет прав!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "📋 Генерирую...")
        settings = get_chat_summary_settings(chat_id)
        if settings.get("mode") == "ai":
            summary = generate_ai_summary(chat_id, settings.get("ai_style", "troll"))
        else:
            summary = generate_regular_summary(chat_id)
        if summary:
            bot.send_message(user_id, summary, parse_mode="Markdown")
        else:
            bot.send_message(user_id, "📭 Недостаточно сообщений за сегодня")
            
    elif data.startswith("create_"):
        chat_id = int(data.split("_")[1])
        bot.answer_callback_query(call.id, "✏️ Используйте /remind в чате")
        bot.send_message(user_id, f"✏️ Напишите в чате:\n`/remind 15:30 текст`", parse_mode="Markdown")
        
    elif data.startswith("delete_"):
        chat_id = int(data.split("_")[1])
        user_reminders = [r for r in reminders if r.get("chat_id") == chat_id and r.get("user_id") == user_id]
        if not user_reminders:
            bot.answer_callback_query(call.id, "❌ Нет напоминаний")
            return
        markup = InlineKeyboardMarkup()
        for r in user_reminders:
            period = f"{r['hours']:02d}:{r['minutes']:02d}"
            if r.get("daily"):
                period += " (ежедневно)"
            markup.add(InlineKeyboardButton(f"🗑️ {period} — {escape_markdown(r['text'][:30])}", callback_data=f"del_{r['id']}_{chat_id}"))
        markup.add(InlineKeyboardButton("◀ Назад", callback_data=f"remind_{chat_id}"))
        bot.edit_message_text("🗑️ *Выберите:*", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        
    elif data.startswith("del_"):
        parts = data.split("_")
        rid = int(parts[1])
        chat_id = int(parts[2])
        for i, r in enumerate(reminders):
            if r["id"] == rid and r.get("user_id") == user_id:
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders()
                bot.answer_callback_query(call.id, "✅ Удалено!")
                break

# ========== ОТСЛЕЖИВАНИЕ ДОБАВЛЕНИЯ БОТА ==========

@bot.my_chat_member_handler(func=lambda message: True)
def on_bot_added_to_chat(message):
    try:
        chat = message.chat
        chat_id = chat.id
        chat_title = chat.title or f"Чат {chat_id}"
        inviter = message.from_user
        inviter_id = inviter.id
        
        unique_id = f"{chat_id}_0"
        if unique_id not in active_chats:
            active_chats.add(unique_id)
        
        if str(inviter_id) not in chat_users:
            chat_users[str(inviter_id)] = {
                "id": inviter_id,
                "username": inviter.username,
                "first_name": inviter.first_name,
                "last_name": inviter.last_name,
                "full_name": f"{inviter.first_name or ''} {inviter.last_name or ''}".strip(),
                "last_seen": datetime.now(MOSCOW_TZ).isoformat()
            }
            user_cache.save_users(chat_users)
        
        try:
            bot.send_message(inviter_id, f"✅ Бот добавлен в чат *{escape_markdown(chat_title)}*!\n\n⚙️ Меню: `/start`", parse_mode="Markdown")
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")

# ========== СКРЫТЫЕ СООБЩЕНИЯ (ИСПРАВЛЕННАЯ ВЕРСИЯ) ==========
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
        
        # Поиск пользователя
        for uid, user in chat_users.items():
            username = user.get('username')
            if username and username.lower() == target_raw.lower():
                target_id = uid
                target_name = user.get('first_name', target_raw)
                break
        
        if not target_id and target_raw.isdigit():
            target_id = target_raw
        
        if not target_id:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("❓ Как узнать ID", url="https://t.me/userinfobot"))
            result = types.InlineQueryResultArticle(
                id="error",
                title="❌ Пользователь не найден",
                description=f"@{target_raw}",
                input_message_content=types.InputTextMessageContent(f"❌ Пользователь @{target_raw} не найден"),
                reply_markup=markup
            )
            bot.answer_inline_query(query.id, [result], cache_time=0)
            return
        
        msg_id = f"sec_{int(time.time())}_{query.from_user.id}_{random.randint(1000, 9999)}"
        
        secret_messages[msg_id] = {
            "target_id": str(target_id),
            "target_name": target_name,
            "content": content,
            "sender_name": query.from_user.first_name,
            "sender_id": str(query.from_user.id),
            "expires": time.time() + 3600
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать сообщение", callback_data=f"secret_read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Сообщение для {target_name}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔐 *Скрытое сообщение*\nОт: {query.from_user.first_name}\nКому: {target_name}\n\n⬇️ Нажмите на кнопку ниже, чтобы прочитать",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        
        bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)
        print(f"✅ Создано скрытое сообщение для {target_name} (ID: {target_id})")
        
    except Exception as e:
        print(f"❌ Инлайн ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("secret_read_"))
def handle_secret_read(call):
    msg_id = call.data.replace("secret_read_", "")
    
    print(f"🔑 Нажата кнопка для сообщения {msg_id} пользователем {call.from_user.id}")
    
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение не найдено или уже удалено", show_alert=True)
        return
    
    data = secret_messages[msg_id]
    
    # Проверяем получателя
    if str(call.from_user.id) != str(data["target_id"]):
        bot.answer_callback_query(call.id, "❌ Это сообщение не для вас!", show_alert=True)
        return
    
    # Проверяем срок
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Сообщение истекло (хранится 1 час)", show_alert=True)
        del secret_messages[msg_id]
        return
    
    # Формируем текст для всплывающего окна
    alert_text = f"📩 Новое сообщение от {data['sender_name']}!\n\nНажмите OK, чтобы прочитать полный текст в личных сообщениях."
    
    # Полный текст для ЛС
    full_message = f"📩 *Скрытое сообщение*\n\n👤 *Отправитель:* {data['sender_name']}\n\n💬 *Текст:*\n{data['content']}\n\n⏰ *Отправлено:* {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M')}"
    
    # Удаляем из кэша
    del secret_messages[msg_id]
    
    try:
        # Сначала показываем всплывающее уведомление
        bot.answer_callback_query(call.id, alert_text, show_alert=True)
        
        # Затем отправляем полное сообщение в ЛС
        bot.send_message(call.from_user.id, full_message, parse_mode="Markdown")
        print(f"✅ Сообщение {msg_id} отправлено в ЛС {call.from_user.id}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        # Если не получилось, пробуем отправить только в ЛС
        try:
            bot.send_message(call.from_user.id, full_message, parse_mode="Markdown")
            bot.answer_callback_query(call.id, "✅ Сообщение доставлено в личные сообщения!", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "❌ Не удалось доставить сообщение", show_alert=True)

def clean_old_secrets():
    while True:
        time.sleep(3600)
        now = time.time()
        to_delete = [mid for mid, d in secret_messages.items() if d.get("expires", 0) < now]
        for mid in to_delete:
            del secret_messages[mid]
            print(f"🗑️ Удалено истекшее сообщение {mid}")

threading.Thread(target=clean_old_secrets, daemon=True).start()

# ========== ВЕБХУК ==========
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if update:
            if "channel_post" in update:
                post = update["channel_post"]
                chat_id = post["chat"]["id"]
                message_id = post["message_id"]
                if chat_id in [-1002185590715, -1001317416582]:
                    set_reaction(chat_id, message_id)
            bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    print("🔄 Установка вебхука...")
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    print(f"Результат: {r.json()}")
    
    print("🔄 Загрузка данных...")
    load_daily_quotes()
    load_reminders()
    load_summary_settings()
    load_quotes_settings()
    
    print("🔄 Запуск планировщиков...")
    start_all_reminders()
    schedule_all_chat_summaries()
    schedule_all_chat_quotes()
    
    app.run(host="0.0.0.0", port=port)

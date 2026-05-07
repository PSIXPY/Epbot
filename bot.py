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

# === ЗАГРУЗКА КЭША ПОЛЬЗОВАТЕЛЕЙ ===
chat_users = user_cache.load_users()
print(f"👥 Загружено {len(chat_users)} пользователей")

# === ДЛЯ ЦИТАТ ===
QUOTES_CACHE_FILE = "daily_quotes.json"
daily_messages = []
daily_quote_times = [9, 12, 15, 18, 21]
active_chats = set()

def load_daily_quotes():
    global daily_messages
    if os.path.exists(QUOTES_CACHE_FILE):
        try:
            with open(QUOTES_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'):
                    daily_messages = data.get('messages', [])
                    print(f"📚 Загружено {len(daily_messages)} сообщений")
                else:
                    clear_daily_quotes()
        except Exception as e:
            print(f"❌ Ошибка: {e}")

def save_daily_quotes():
    try:
        data = {
            'date': datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'),
            'messages': daily_messages
        }
        with open(QUOTES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(daily_messages)} сообщений")
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
        'unique_id': unique_id
    })
    if len(daily_messages) > 1000:
        daily_messages = daily_messages[-1000:]
    save_daily_quotes()

def add_chat_to_active(message):
    if message.text and message.text.startswith('/'):
        return
    thread_id = message.message_thread_id if message.message_thread_id else 0
    unique_id = f"{message.chat.id}_{thread_id}"
    if unique_id not in active_chats:
        active_chats.add(unique_id)

def get_chat_messages_count(chat_id, thread_id=0):
    unique_id = f"{chat_id}_{thread_id}"
    return len([m for m in daily_messages if m.get('unique_id') == unique_id])

def get_random_quote(chat_id, thread_id=0):
    unique_id = f"{chat_id}_{thread_id}"
    chat_messages = [m for m in daily_messages if m.get('unique_id') == unique_id]
    if len(chat_messages) < 2:
        return None
    quote = random.choice(chat_messages)
    return f"📜 *Цитата дня*\n\n« {quote['text']} »"

def clean_inactive_chats():
    global active_chats
    to_remove = []
    for unique_id in active_chats:
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        chat_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
        if len(chat_messages) < 2:
            to_remove.append(unique_id)
    for item in to_remove:
        active_chats.discard(item)

def schedule_daily_quotes():
    now_moscow = datetime.now(MOSCOW_TZ)
    for hour in daily_quote_times:
        target = now_moscow.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now_moscow:
            target += timedelta(days=1)
        delay = (target - now_moscow).total_seconds()
        timer = threading.Timer(delay, send_random_quote_to_all_chats)
        timer.daemon = True
        timer.start()
        print(f"📜 Цитата на {target.strftime('%H:%M')}")
    midnight = now_moscow.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    midnight_delay = (midnight - now_moscow).total_seconds()
    midnight_timer = threading.Timer(midnight_delay, clear_daily_quotes)
    midnight_timer.daemon = True
    midnight_timer.start()

def send_random_quote_to_all_chats():
    clean_inactive_chats()
    sent_chats = set()
    for unique_id in list(active_chats):
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        if chat_id in sent_chats:
            continue
        chat_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
        if len(chat_messages) < 2:
            continue
        sent_chats.add(chat_id)
        quote = random.choice(chat_messages)
        text = f"📜 *Цитата дня*\n\n« {quote['text']} »"
        try:
            bot.send_message(chat_id, text, parse_mode="Markdown")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = "reminders.json"

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                reminders = json.load(f)
                print(f"⏰ Загружено {len(reminders)} напоминаний")
                return reminders
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
    except Exception as e:
        print(f"Ошибка: {e}")

reminders = load_reminders()
reminder_counter = max([r.get("id", 0) for r in reminders]) if reminders else 0

def send_reminder(reminder):
    try:
        bot.send_message(reminder["chat_id"], f"⏰ НАПОМИНАНИЕ!\n\n{reminder['text']}", message_thread_id=reminder.get("thread_id"))
        print(f"✅ Напоминание {reminder['id']} отправлено")
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

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
    print(f"⏰ Напоминание {reminder['id']} на {target.strftime('%Y-%m-%d %H:%M')}")

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
    messages = [{"role": "system", "content": "Отвечай кратко. НЕ используй теги <think> и рассуждения в ответе."}, *user_histories[user_id]]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3-32b", "messages": messages, "max_tokens": 800, "temperature": 0.2}
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL | re.IGNORECASE)
            answer = re.sub(r'\[think\].*?\[/think\]', '', answer, flags=re.DOTALL | re.IGNORECASE)
            answer = re.sub(r'/\s*think', '', answer, flags=re.IGNORECASE)
            answer = re.sub(r'размышление:.*?(?=\n|$)', '', answer, flags=re.IGNORECASE)
            answer = answer.strip()
            if not answer:
                answer = "⚠️ Не удалось сгенерировать ответ. Попробуйте переформулировать вопрос."
            user_histories[user_id].append({"role": "assistant", "content": answer})
            ai_cache[cache_key] = (time.time(), answer)
            return answer
        elif response.status_code == 429:
            return "⚠️ Лимит. Подождите."
        return f"❌ Ошибка: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"

def set_reaction(chat_id, message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    data = {"chat_id": chat_id, "message_id": message_id, "reaction": [{"type": "emoji", "emoji": "🔥"}]}
    try:
        response = requests.post(url, json=data, timeout=5)
        result = response.json()
        if result.get("ok"):
            print(f"🔥 Реакция на {message_id} в канале {chat_id}")
    except:
        pass

# ========== НАСТРАИВАЕМОЕ САММАРИ ЧАТА ==========

summary_settings = {}
SUMMARY_FILE = "summary_settings.json"

def load_summary_settings():
    global summary_settings
    if os.path.exists(SUMMARY_FILE):
        try:
            with open(SUMMARY_FILE, 'r', encoding='utf-8') as f:
                summary_settings = json.load(f)
                print(f"📋 Загружено {len(summary_settings)} настроек саммари")
        except:
            pass

def save_summary_settings():
    try:
        with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
            json.dump(summary_settings, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранены настройки саммари")
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
            "regular_length": "full"
        }
        save_summary_settings()
    return summary_settings[chat_id_str]

def update_chat_summary_settings(chat_id, key, value):
    chat_id_str = str(chat_id)
    if chat_id_str not in summary_settings:
        summary_settings[chat_id_str] = {"enabled": True, "time": "22:00", "mode": "normal", "ai_style": "troll", "regular_length": "full"}
    summary_settings[chat_id_str][key] = value
    save_summary_settings()

def generate_regular_summary(chat_id):
    today_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
    
    if len(today_messages) < 3:
        return None
    
    total_msgs = len(today_messages)
    users_count = len(set(m.get('author') for m in today_messages))
    
    user_activity = {}
    for msg in today_messages:
        author = msg.get('author_name', 'Участник')
        user_activity[author] = user_activity.get(author, 0) + 1
    top_users = sorted(user_activity.items(), key=lambda x: x[1], reverse=True)[:5]
    
    stop_words = {'и', 'в', 'на', 'не', 'а', 'но', 'за', 'по', 'с', 'у', 'к', 'из', 'от', 'до', 'о', 'об', 'же', 'ли', 'бы', 'это', 'что', 'как', 'так', 'все', 'меня', 'мне', 'тебя', 'тебе', 'его', 'её', 'нас', 'вас', 'их', 'мой', 'твой', 'наш', 'ваш'}
    
    word_count = {}
    for msg in today_messages:
        words = msg.get('text', '').lower().split()
        for word in words:
            word_clean = word.strip('.,!?;:()[]{}"\'')
            if len(word_clean) > 3 and word_clean not in stop_words and not word_clean.startswith('/'):
                word_count[word_clean] = word_count.get(word_clean, 0) + 1
    
    top_words = sorted(word_count.items(), key=lambda x: x[1], reverse=True)[:5]
    random_quote = random.choice(today_messages) if today_messages else None
    
    text = f"📋 *Сводка дня*\n\n"
    text += f"📊 *Статистика:*\n"
    text += f"• Сообщений: {total_msgs}\n"
    text += f"• Участников: {users_count}\n\n"
    
    if top_users:
        text += f"🏆 *Самые активные:*\n"
        for name, count in top_users[:3]:
            text += f"• {name} — {count}\n"
        text += "\n"
    
    if top_words:
        text += f"📝 *Часто обсуждали:* {', '.join([f'\"{word}\" ({count})' for word, count in top_words[:3]])}\n\n"
    
    if random_quote:
        text += f"💬 *Цитата дня:*\n« {random_quote['text'][:100]} »\n— {random_quote['author_name']}"
        if random_quote.get('time'):
            text += f"  •  {random_quote['time']}"
    
    return text

def ask_groq_for_summary(prompt):
    if not GROQ_API_KEY:
        return None
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "qwen/qwen3-32b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1000,
        "temperature": 0.8
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=45)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL | re.IGNORECASE)
            answer = re.sub(r'\[think\].*?\[/think\]', '', answer, flags=re.DOTALL | re.IGNORECASE)
            answer = answer.strip()
            return answer
        else:
            return None
    except:
        return None

def generate_ai_summary(chat_id, style="troll"):
    print(f"🤖 Генерация ИИ-сводки для чата {chat_id}, стиль: {style}")
    
    today_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
    print(f"📊 Найдено сообщений: {len(today_messages)}")
    
    if len(today_messages) < 5:
        print(f"⚠️ Недостаточно сообщений: {len(today_messages)} < 5")
        return None
    
    recent_messages = today_messages[-40:]
    
    chat_log = []
    for msg in recent_messages:
        author = msg.get('author_name', 'Участник')
        text = msg.get('text', '')
        if text and len(text) < 200:
            chat_log.append(f"{author}: {text}")
    
    if not chat_log:
        return None
    
    conversation = "\n".join(chat_log)
    
    if style == "troll":
        prompt = f"""Ты — тролль-журналист. Напиши юмористическую сводку по этому чату.
Используй эмодзи, подкалывай участников, будь остроумным и язвительным.
Формат: разбей на темы с эмодзи и заголовками. БЕЗ СТАТИСТИКИ.
НЕ ИСПОЛЬЗУЙ теги <think> и рассуждения в ответе.

Вот диалог в чате:
{conversation}

Напиши сводку:
- Выдели 4-6 тем
- К каждой теме подбери подходящий эмодзи
- Добавь хештег в конце #summary
- Будь смешным, но не слишком оскорбительным
- Пиши на русском
- НЕ используй цифры и статистику"""
    else:
        prompt = f"""Ты — журналист. Напиши краткую информативную сводку по этому чату.
Разбей на темы с эмодзи и заголовками. Будь нейтральным и объективным. БЕЗ СТАТИСТИКИ.
НЕ ИСПОЛЬЗУЙ теги <think> и рассуждения в ответе.

Вот диалог в чате:
{conversation}

Напиши сводку:
- Выдели 4-6 тем
- К каждой теме подбери эмодзи
- Добавь хештег в конце #summary
- Пиши на русском
- НЕ используй цифры и статистику"""
    
    response = ask_groq_for_summary(prompt)
    
    if response:
        return response
    else:
        return None

def generate_fallback_summary(chat_id):
    today_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
    if len(today_messages) < 3:
        return None
    
    total_msgs = len(today_messages)
    users_count = len(set(m.get('author') for m in today_messages))
    
    return f"🤖 *ИИ временно недоступен*\n\nСегодня в чате {total_msgs} сообщений от {users_count} участников.\n\nПопробуй ещё раз через минуту. #summary"

def send_scheduled_summary(chat_id, thread_id=0):
    settings = get_chat_summary_settings(chat_id)
    
    if not settings.get("enabled", True):
        print(f"⏩ Авто-сводка в чате {chat_id} отключена")
        return
    
    if settings.get("mode") == "ai":
        summary = generate_ai_summary(chat_id, settings.get("ai_style", "troll"))
        if not summary:
            summary = generate_fallback_summary(chat_id)
    else:
        summary = generate_regular_summary(chat_id)
    
    if summary:
        try:
            bot.send_message(chat_id, summary, parse_mode="Markdown", message_thread_id=thread_id if thread_id != 0 else None)
            print(f"📋 Отправлена сводка в чат {chat_id}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    else:
        print(f"⏩ Недостаточно сообщений для сводки в чате {chat_id}")

def schedule_chat_summaries():
    now_moscow = datetime.now(MOSCOW_TZ)
    
    for unique_chat in list(active_chats):
        parts = unique_chat.split("_")
        chat_id = int(parts[0])
        thread_id = int(parts[1]) if len(parts) > 1 else 0
        
        settings = get_chat_summary_settings(chat_id)
        if not settings.get("enabled", True):
            continue
        
        time_str = settings["time"]
        try:
            target_hour, target_minute = map(int, time_str.split(":"))
            target = now_moscow.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            if target <= now_moscow:
                target += timedelta(days=1)
            delay = (target - now_moscow).total_seconds()
            timer = threading.Timer(delay, lambda: send_scheduled_summary(chat_id, thread_id))
            timer.daemon = True
            timer.start()
            print(f"📋 Сводка для чата {chat_id} на {target.strftime('%H:%M')} (режим: {settings['mode']})")
        except:
            pass

# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    print(f"📢 /start от {message.from_user.id}")
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:* `/remind 15:30 текст`\n`/reminds`\n`/delremind ID`\n\n"
            "📜 *Цитаты:* `/quote`\n\n"
            "📋 *Сводка дня:*\n"
            "`/summary` — показать\n"
            "`/summary on` — включить авто\n"
            "`/summary off` — выключить авто\n\n"
            "🎭 *РП команды:* Ответь на сообщение и напиши действие\n"
            "🎭 *На всех:* кончить на всех, сквиртануть на всех\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`\n\n"
            "👑 *Админ-команды:* `/users` `/adduser` `/deluser` `/backup` `/restore`",
            parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:* `/remind 15:30 текст`\n`/reminds`\n`/delremind ID`\n\n"
            "📜 *Цитаты:* `/quote`\n\n"
            "📋 *Сводка дня:* `/summary`\n\n"
            "🎭 *РП команды:* Ответь на сообщение и напиши действие\n"
            "🎭 *На всех:* кончить на всех, сквиртануть на всех\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`",
            parse_mode="Markdown")

@bot.message_handler(commands=['ai'])
def ai_command(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ /ai вопрос")
        return
    msg = bot.reply_to(message, "🤖 Думаю...")
    answer = ask_groq(message.from_user.id, prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id)

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
        "daily": daily
    }
    reminders.append(reminder)
    save_reminders(reminders)
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
    user_reminders = [r for r in reminders if r.get("chat_id") == chat_id]
    if thread_id:
        user_reminders = [r for r in user_reminders if r.get("thread_id") == thread_id]
    if not user_reminders:
        msg = bot.send_message(chat_id, "📭 Нет напоминаний", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    response = "📋 *Напоминания:*\n\n"
    for r in user_reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"{r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 {r['id']} - {period}\n   {r['text'][:40]}\n\n"
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
                if r.get("chat_id") != chat_id:
                    return
                if r.get("thread_id") != thread_id:
                    return
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders(reminders)
                msg = bot.send_message(chat_id, f"✅ Напоминание {rid} удалено", message_thread_id=thread_id)
                delete_after_delay(chat_id, msg.message_id, 10)
                return
        msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
    except:
        msg = bot.send_message(chat_id, "❌ Неверный ID", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)

@bot.message_handler(commands=['quote'])
def quote_command(message):
    thread_id = message.message_thread_id if message.message_thread_id else 0
    count = get_chat_messages_count(message.chat.id, thread_id)
    if count < 2:
        bot.reply_to(message, "📭 Пока нет сообщений для цитаты. Напишите что-нибудь!")
        return
    quote_text = get_random_quote(message.chat.id, thread_id)
    if quote_text:
        bot.reply_to(message, quote_text, parse_mode="Markdown")

# ========== ФУНКЦИЯ ПРОВЕРКИ ПРАВ АДМИНА ЧАТА ==========

def is_chat_admin(chat_id, user_id):
    try:
        chat_member = bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['administrator', 'creator']
    except Exception as e:
        print(f"⚠️ Ошибка проверки прав: {e}")
        return False

# ========== КОМАНДЫ САММАРИ ==========

@bot.message_handler(commands=['summary'])
def summary_command(message):
    parts = message.text.split(maxsplit=1)
    
    if len(parts) > 1:
        subcmd = parts[1].lower()
        
        if subcmd == "on":
            user_id = message.from_user.id
            chat_id = message.chat.id
            
            if not is_chat_admin(chat_id, user_id):
                bot.reply_to(message, "❌ Только администраторы чата могут включать сводку!")
                return
            
            update_chat_summary_settings(chat_id, "enabled", True)
            settings = get_chat_summary_settings(chat_id)
            mode_text = "ИИ" if settings["mode"] == "ai" else "обычная"
            bot.reply_to(message, f"✅ *Авто-сводка включена!*\n\n📋 Режим: {mode_text}\n🕐 Время: {settings['time']}", parse_mode="Markdown")
            return
            
        elif subcmd == "off":
            user_id = message.from_user.id
            chat_id = message.chat.id
            
            if not is_chat_admin(chat_id, user_id):
                bot.reply_to(message, "❌ Только администраторы чата могут выключать сводку!")
                return
            
            update_chat_summary_settings(chat_id, "enabled", False)
            bot.reply_to(message, "✅ *Авто-сводка выключена!*", parse_mode="Markdown")
            return
        
        elif subcmd in ["тролль", "troll"]:
            summary = generate_ai_summary(message.chat.id, "troll")
            if summary:
                bot.reply_to(message, summary, parse_mode="Markdown")
            else:
                bot.reply_to(message, "📭 Недостаточно сообщений для ИИ-сводки (нужно минимум 5)")
            return
            
        elif subcmd in ["обычный", "normal"]:
            summary = generate_regular_summary(message.chat.id)
            if summary:
                bot.reply_to(message, summary, parse_mode="Markdown")
            else:
                bot.reply_to(message, "📭 Недостаточно сообщений для сводки (нужно минимум 3)")
            return
    
    settings = get_chat_summary_settings(message.chat.id)
    if settings.get("mode") == "ai":
        summary = generate_ai_summary(message.chat.id, settings.get("ai_style", "troll"))
    else:
        summary = generate_regular_summary(message.chat.id)
    
    if summary:
        bot.reply_to(message, summary, parse_mode="Markdown")
    else:
        bot.reply_to(message, "📭 Недостаточно сообщений для сводки (нужно минимум 3 для обычной, 5 для ИИ)")

@bot.message_handler(commands=['summary_on'])
def summary_on_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not is_chat_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только администраторы чата могут включать сводку!")
        return
    
    update_chat_summary_settings(chat_id, "enabled", True)
    settings = get_chat_summary_settings(chat_id)
    mode_text = "ИИ" if settings["mode"] == "ai" else "обычная"
    bot.reply_to(message, f"✅ *Авто-сводка включена!*\n\n📋 Режим: {mode_text}\n🕐 Время: {settings['time']}", parse_mode="Markdown")

@bot.message_handler(commands=['summary_off'])
def summary_off_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not is_chat_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только администраторы чата могут выключать сводку!")
        return
    
    update_chat_summary_settings(chat_id, "enabled", False)
    bot.reply_to(message, "✅ *Авто-сводка выключена!*", parse_mode="Markdown")

@bot.message_handler(commands=['summary_mode'])
def summary_mode_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not is_chat_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только администраторы чата могут менять режим!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ `/summary_mode ai` или `/summary_mode normal`", parse_mode="Markdown")
        return
    
    mode = parts[1].strip().lower()
    if mode in ["ai", "ии"]:
        update_chat_summary_settings(chat_id, "mode", "ai")
        bot.reply_to(message, "✅ Режим сводки установлен на *ИИ*", parse_mode="Markdown")
    elif mode in ["normal", "обычный", "обычная"]:
        update_chat_summary_settings(chat_id, "mode", "normal")
        bot.reply_to(message, "✅ Режим сводки установлен на *обычный*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Доступные режимы: ai, normal")

@bot.message_handler(commands=['summary_style'])
def summary_style_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not is_chat_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только администраторы чата могут менять стиль!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ `/summary_style тролль` или `/summary_style обычный`", parse_mode="Markdown")
        return
    
    style = parts[1].strip().lower()
    if style in ["тролль", "troll"]:
        update_chat_summary_settings(chat_id, "ai_style", "troll")
        bot.reply_to(message, "✅ Стиль ИИ-сводки установлен на *тролль*", parse_mode="Markdown")
    elif style in ["обычный", "обычная", "normal"]:
        update_chat_summary_settings(chat_id, "ai_style", "normal")
        bot.reply_to(message, "✅ Стиль ИИ-сводки установлен на *обычный*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Доступные стили: тролль, обычный")

@bot.message_handler(commands=['summary_time'])
def summary_time_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not is_chat_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только администраторы чата могут менять время!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ `/summary_time 20:00`", parse_mode="Markdown")
        return
    
    time_str = parts[1].strip()
    try:
        if ":" in time_str:
            hours, minutes = map(int, time_str.split(":"))
        else:
            hours = int(time_str)
            minutes = 0
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValueError
    except:
        bot.reply_to(message, "❌ Неверный формат времени. Используйте ЧЧ:ММ (МСК)")
        return
    
    update_chat_summary_settings(message.chat.id, "time", time_str)
    bot.reply_to(message, f"✅ Время сводки установлено на {hours:02d}:{minutes:02d} МСК")

@bot.message_handler(commands=['summary_settings'])
def summary_settings_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    settings = get_chat_summary_settings(message.chat.id)
    status_text = "✅ Включена" if settings.get("enabled", True) else "❌ Выключена"
    mode_text = "ИИ (умная)" if settings["mode"] == "ai" else "Обычная (статистика)"
    style_text = "тролль (язвительный)" if settings.get("ai_style") == "troll" else "обычный"
    
    text = f"⚙️ *Настройки сводки в этом чате*\n\n"
    text += f"🟢 Статус: {status_text}\n"
    text += f"🕐 Время отправки: `{settings['time']}` МСК\n"
    text += f"🤖 Режим: *{mode_text}*\n"
    if settings["mode"] == "ai":
        text += f"🎭 Стиль ИИ: *{style_text}*\n"
    
    bot.reply_to(message, text, parse_mode="Markdown")

# === АДМИН-КОМАНДЫ ===

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
    
    text = "📋 *Список пользователей:*\n\n"
    for uid, user in chat_users.items():
        username = user.get('username', 'нет')
        name = user.get('first_name', 'Без имени')
        if username and username != 'нет':
            text += f"• `{uid}` | @{username} | {name}\n"
        else:
            text += f"• `{uid}` | {name}\n"
    
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            bot.send_message(message.chat.id, part, parse_mode="Markdown")
    else:
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
    bot.reply_to(message, f"✅ @{username} добавлен!")

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
        bot.reply_to(message, f"✅ @{target} удалён!")
    else:
        bot.reply_to(message, f"❌ @{target} не найден")

# === БЕКАП ===
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
        clean_reminders = []
        for r in reminders:
            r_copy = {}
            for k, v in r.items():
                if k not in ["timer", "_timer"]:
                    r_copy[k] = v
            clean_reminders.append(r_copy)
        data = {
            "version": "2.0",
            "date": str(datetime.now()),
            "reminders": clean_reminders,
            "chat_users": chat_users
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

@bot.message_handler(content_types=['document'])
def handle_restore_file(message):
    global chat_users, reminder_counter, reminders
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
            save_reminders(reminders)
            start_all_reminders()
        if "chat_users" in data:
            chat_users = data["chat_users"]
            user_cache.save_users(chat_users)
        bot.edit_message_text(f"✅ Восстановлено!\n👥 {len(chat_users)} пользователей\n⏰ {len(reminders)} напоминаний", message.chat.id, status.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", message.chat.id, status.message_id)

# ========== СКРЫТЫЕ СООБЩЕНИЯ ==========
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
                input_message_content=types.InputTextMessageContent(f"❌ @{target_raw} не найден"),
                reply_markup=markup
            )
            bot.answer_inline_query(query.id, [result], cache_time=0)
            return
        msg_id = f"sec_{int(time.time())}_{query.from_user.id}_{random.randint(1000, 9999)}"
        secret_messages[msg_id] = {
            "target_id": target_id,
            "target_name": target_name,
            "content": content,
            "sender_name": query.from_user.first_name,
            "sender_id": query.from_user.id,
            "expires": time.time() + 3600
        }
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"secret_read_{msg_id}"))
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Для {target_name}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔐 *Скрытое сообщение*\nОт: {query.from_user.first_name}\nКому: {target_name}",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)
    except Exception as e:
        print(f"Инлайн ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("secret_read_"))
def handle_secret_read(call):
    msg_id = call.data.replace("secret_read_", "")
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение не найдено", show_alert=True)
        return
    data = secret_messages[msg_id]
    if str(call.from_user.id) != str(data["target_id"]):
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Истекло", show_alert=True)
        del secret_messages[msg_id]
        return
    bot.answer_callback_query(call.id, f"📩 От {data['sender_name']}:\n\n{data['content']}", show_alert=True)

def clean_old_secrets():
    while True:
        time.sleep(3600)
        now = time.time()
        to_delete = [mid for mid, d in secret_messages.items() if d.get("expires", 0) < now]
        for mid in to_delete:
            del secret_messages[mid]

threading.Thread(target=clean_old_secrets, daemon=True).start()

# ========== ФУНКЦИИ ДЛЯ СКЛОНЕНИЯ ИМЁН ==========
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
    
    if preposition in ["в", "на", "за", "про"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'у'
        elif name_lower.endswith('я'):
            return name[:-1] + 'ю'
        else:
            return name
    
    if name_lower.endswith('а'):
        return name[:-1] + 'у'
    elif name_lower.endswith('я'):
        return name[:-1] + 'ю'
    elif name_lower.endswith('й'):
        return name[:-1] + 'ю'
    elif name_lower.endswith('ь'):
        return name[:-1] + 'ю'
    else:
        return name + 'у'

def get_gender(user):
    name = (user.first_name or user.username or "").lower()
    
    male_names = ['владимир', 'вова', 'володя', 'александр', 'саша', 'саня', 'дмитрий', 'дима', 'николай', 'коля', 'сергей', 'андрей', 'алексей', 'иван', 'михаил', 'максим', 'никита', 'кирилл', 'павел', 'артём', 'егор', 'даниил']
    female_names = ['анна', 'аня', 'мария', 'маша', 'елена', 'лена', 'ольга', 'оля', 'татьяна', 'наталья', 'наташа', 'екатерина', 'катя', 'юлия', 'юля', 'ирина', 'ира', 'светлана', 'света', 'виктория', 'вика', 'арина', 'алина', 'александра', 'кристина', 'дарья', 'даша', 'полина', 'валерия', 'лера']
    
    if name in male_names:
        return 'male'
    if name in female_names:
        return 'female'
    
    female_endings = ('а', 'я', 'ия', 'ья')
    male_exceptions = ('никита', 'дима', 'влад', 'лева', 'саша', 'женя', 'валя', 'илья')
    
    if name.endswith(female_endings) and name not in male_exceptions:
        return 'female'
    return 'male'

# ========== РЕАКЦИИ НА ДЕЙСТВИЯ ==========
def handle_actions(message):
    # Команды "на всех" (без ответа)
    if not message.reply_to_message:
        full_text = message.text.strip().lower()
        
        global_actions = {
            "кончить на всех": ("💦", "кончил на всех", "кончила на всех"),
            "сквиртануть на всех": ("💦💦", "сквиртанул на всех", "сквиртанула на всех"),
            "кончить всем в лицо": ("💦", "кончил всем в лицо", "кончила всем в лицо"),
            "сесть на лицо": ("🍑", "хочет сесть на лицо", "хочет сесть на лицо"),
            "вылизать": ("👅", "хочет вылизать всех", "хочет вылизать всех"),
            "унизить": ("😢", "унижает всех", "унижает всех"),
            "опустить": ("😢", "опускает всех", "опускает всех"),
        }
        
        if full_text in global_actions:
            emoji, male_action, female_action = global_actions[full_text]
            sender = message.from_user
            sender_name = sender.first_name or sender.username or "Кто-то"
            sender_gender = get_gender(sender)
            
            if female_action == male_action:
                action = male_action
            else:
                action = male_action if sender_gender == 'male' else female_action
            
            response = f"{emoji} {sender_name} {action}"
            thread_id = message.message_thread_id if message.message_thread_id else None
            try:
                bot.send_message(message.chat.id, response, message_thread_id=thread_id)
                return True
            except Exception as e:
                print(f"❌ Ошибка: {e}")
                return False
        return False
    
    full_text = message.text.strip().lower()
    
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
        "пожатьруку": ("🤝", "пожал руку", "пожала руку", ""),
        "шлепнуть": ("🖐️", "шлепнул", "шлепнула", ""),
        "ущипнуть": ("🤏", "ущипнул", "ущипнула", ""),
        "покормить": ("🍕", "покормил", "покормила", ""),
        "датьпять": ("🙏", "дал пять", "дала пять", ""),
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
        "послатьнахуй": ("🖕", "послал нахуй", "послала нахуй", ""),
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
        "сестьналицо": ("🍑", "сел на лицо", "села на лицо", "на"),
        "сосать": ("👅", "сосал", "сосала", ""),
        "лечь": ("😴", "лёг", "леглá", "на"),
        "спать": ("😴", "лёг спать", "леглá спать", ""),
        "уснуть": ("😴", "уснул", "уснула", ""),
        "пить": ("🍺", "выпил", "выпила", ""),
        "вылизать": ("👅", "вылизал", "вылизала", ""),
        "вылизывать": ("👅", "вылизывал", "вылизывала", ""),
        "засосать": ("🫦", "засосал", "засосала", ""),
        "опустить": ("😢", "опустил", "опустила", ""),
        "привсех": ("👥", "опустил при всех", "опустила при всех", ""),
        "публично": ("👥", "опустил публично", "опустила публично", ""),
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
    
    if preposition:
        declined_target = decline_name(target_name, preposition)
        if preposition in ["в", "на", "за", "про"]:
            target_with_preposition = f"{preposition} {declined_target}"
        else:
            target_with_preposition = f"{preposition} {declined_target}"
    else:
        target_with_preposition = decline_name(target_name, "")
    
    if reply_text:
        response = f"{emoji} {sender_name} {past_action} {target_with_preposition}: {reply_text}"
    else:
        response = f"{emoji} {sender_name} {past_action} {target_with_preposition}"
    
    thread_id = message.message_thread_id if message.message_thread_id else None
    try:
        bot.send_message(message.chat.id, response, message_thread_id=thread_id)
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

# ========== ОСНОВНОЙ ОБРАБОТЧИК ==========
@bot.message_handler(func=lambda message: True)
def main_handler(message):
    if message.text and not message.text.startswith('/') and not message.reply_to_message:
        text_lower = message.text.lower().strip()
        user_name = message.from_user.first_name or message.from_user.username or "Пользователь"
        
        if text_lower in ["привет", "здарова", "ку", "хай"]:
            bot.reply_to(message, f"👋 Привет, {user_name}!")
            return
        if text_lower in ["спасибо", "благодарю", "thanks"]:
            bot.reply_to(message, f"🙏 Пожалуйста, {user_name}!")
            return
        if text_lower in ["пока", "до свидания", "bye"]:
            bot.reply_to(message, f"👋 Пока, {user_name}!")
            return
        if text_lower in ["как дела", "как ты"]:
            bot.reply_to(message, f"😊 У меня всё отлично, {user_name}!")
            return
        if text_lower in ["доброе утро", "доброго утра"]:
            bot.reply_to(message, f"🌅 Доброе утро, {user_name}!")
            return
        if text_lower in ["добрый вечер", "доброго вечера"]:
            bot.reply_to(message, f"🌆 Добрый вечер, {user_name}!")
            return
        if text_lower in ["спокойной ночи", "доброй ночи"]:
            bot.reply_to(message, f"🌙 Спокойной ночи, {user_name}!")
            return
        if text_lower in ["грустно", "печально"]:
            bot.reply_to(message, f"😢 Обнимаю, {user_name}, всё будет хорошо!")
            return
        if text_lower in ["скучаю", "соскучился", "соскучилась"]:
            bot.reply_to(message, f"🥺 Я тоже по тебе скучаю, {user_name}!")
            return
        if text_lower in ["ты лучший", "молодец", "умница"]:
            bot.reply_to(message, f"😊 Спасибо, {user_name}! Ты тоже!")
            return
        if text_lower in ["0+", "0плюс"]:
            bot.reply_to(message, f"📚 Для всех возрастов, {user_name}!")
            return
        if text_lower in ["13+", "13плюс"]:
            bot.reply_to(message, f"🔞 Для подростков 13+, {user_name}!")
            return
        if text_lower in ["18+", "18плюс"]:
            bot.reply_to(message, f"🔞 Только для взрослых 18+, {user_name}!")
            return
    
    if message.text and not message.text.startswith('/'):
        if handle_actions(message):
            return
    
    if message.chat.type in ['group', 'supergroup']:
        global chat_users
        old_count = len(chat_users)
        chat_users = user_cache.save_user_from_message(message, chat_users)
        new_count = len(chat_users)
        if new_count > old_count:
            print(f"✨ Новый пользователь! Всего: {new_count}")
        add_chat_to_active(message)
        get_chat_summary_settings(message.chat.id)
        add_message_to_quotes(message)

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
    
    schedule_daily_quotes()
    load_summary_settings()
    schedule_chat_summaries()
    load_daily_quotes()
    start_all_reminders()
    app.run(host="0.0.0.0", port=port)

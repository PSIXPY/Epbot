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

print("🤖 БОТ ЗАПУЩЕН - ПОЛНАЯ ВЕРСИЯ")
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
    messages = [{"role": "system", "content": "Отвечай кратко."}, *user_histories[user_id]]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3-32b", "messages": messages, "max_tokens": 800, "temperature": 0.2}
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>|/think', '', answer, flags=re.DOTALL).strip()
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

# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    print(f"📢 /start от {message.from_user.id}")
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:* `/remind 15:30 текст`\n`/reminds`\n`/delremind ID`\n\n"
            "📜 *Цитаты:* `/quote`\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`\n\n"
            "👑 *Админ-команды:* `/users` `/adduser` `/deluser` `/backup` `/restore` `/userinfo`\n\n"
            "🎭 *Действия:* Ответь на сообщение и напиши: обнять, поцеловать, ударить, дрочить, кончить, сквиртануть и другие",
            parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:* `/remind 15:30 текст`\n`/reminds`\n`/delremind ID`\n\n"
            "📜 *Цитаты:* `/quote`\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`\n\n"
            "🎭 *Действия:* Ответь на сообщение и напиши: обнять, поцеловать, ударить, дрочить, кончить, сквиртануть и другие",
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

@bot.message_handler(commands=['userinfo'])
def userinfo_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    search_param = None
    username_target = None
    user_id = None
    
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        search_param = parts[1].strip().lstrip("@")
        if search_param.isdigit():
            user_id = search_param
        else:
            username_target = search_param
    
    if not username_target and not user_id and message.reply_to_message:
        user_id = str(message.reply_to_message.from_user.id)
    
    if not username_target and not user_id:
        bot.reply_to(message, "ℹ️ /userinfo @username или /userinfo ID")
        return
    
    if username_target:
        for uid, user in chat_users.items():
            if user.get('username') == username_target:
                user_id = uid
                break
    
    if not user_id and search_param and search_param.isdigit():
        if search_param in chat_users:
            user_id = search_param
        else:
            for uid, user in chat_users.items():
                if str(user.get('id')) == search_param:
                    user_id = uid
                    break
    
    if not user_id:
        bot.reply_to(message, "❌ Пользователь не найден в кэше")
        return
    
    user_data = chat_users.get(str(user_id))
    if not user_data:
        user_data = chat_users.get(user_id)
    
    if user_data:
        username = user_data.get('username', 'нет')
        name = user_data.get('first_name', 'неизвестно')
        last_seen = user_data.get('last_seen', 'неизвестно')[:16]
        has_underscore = username and "_" in username if username != 'нет' else False
        
        text = f"🔍 *Информация о пользователе*\n\n"
        text += f"🆔 *ID:* `{user_id}`\n"
        text += f"👤 *Username:* @{username}\n"
        text += f"📛 *Имя:* {name}\n"
        text += f"🕐 *Последнее сообщение:* {last_seen}\n"
        text += f"📝 *Есть _ в username:* {'✅ ДА' if has_underscore else '❌ НЕТ'}"
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Пользователь не найден")

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
    for uid, user in list(chat_users.items())[:50]:
        username = user.get('username', 'нет')
        name = user.get('first_name', 'Без имени')
        text += f"• `{uid}` | @{username} | {name}\n"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")
    if len(chat_users) > 50:
        bot.send_message(message.chat.id, f"📊 и ещё {len(chat_users)-50} пользователей...\n/users full - полный список")

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

# ========== ФУНКЦИЯ ДЛЯ СКЛОНЕНИЯ ИМЁН ==========
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
    male_exceptions = ['никита', 'дима', 'влад', 'лева', 'саша', 'женя', 'валя', 'илья']
    
    if preposition in ["в", "на", "за", "про", "через", "сквозь", "под", "над"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'у'
        elif name_lower.endswith('я'):
            return name[:-1] + 'ю'
        elif name_lower.endswith('й'):
            return name[:-1] + 'я'
        elif name_lower.endswith('ь'):
            return name[:-1] + 'я'
        else:
            return name + 'а'
    
    if preposition in ["у", "без", "до", "из", "от", "для", "ради", "после", "с", "со"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'ы'
        elif name_lower.endswith('я'):
            return name[:-1] + 'и'
        elif name_lower.endswith('й'):
            return name[:-1] + 'я'
        elif name_lower.endswith('ь'):
            return name[:-1] + 'я'
        else:
            return name + 'а'
    
    if preposition in ["к", "по", "благодаря", "вопреки"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'е'
        elif name_lower.endswith('я'):
            return name[:-1] + 'е'
        elif name_lower.endswith('й'):
            return name[:-1] + 'ю'
        else:
            return name + 'у'
    
    if preposition in ["с", "над", "под", "перед", "за"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'ой'
        elif name_lower.endswith('я'):
            return name[:-1] + 'ей'
        elif name_lower.endswith('й'):
            return name[:-1] + 'ем'
        else:
            return name + 'ом'
    
    if preposition in ["о", "об", "при", "на", "в"]:
        if name_lower.endswith('а'):
            return name[:-1] + 'е'
        elif name_lower.endswith('я'):
            return name[:-1] + 'е'
        else:
            return name + 'е'
    
    if preposition:
        if name_lower.endswith('а'):
            return name[:-1] + 'у'
        elif name_lower.endswith('я'):
            return name[:-1] + 'ю'
        elif name_lower.endswith('й'):
            return name[:-1] + 'я'
        elif name_lower.endswith('ь'):
            return name[:-1] + 'я'
        else:
            return name + 'а'
    
    return name

def get_gender(user):
    name = (user.first_name or "").lower()
    female_endings = ('а', 'я', 'ия', 'ья')
    male_exceptions = ('никита', 'дима', 'влад', 'лева', 'саша', 'женя', 'валя', 'илья')
    if name.endswith(female_endings) and name not in male_exceptions:
        return 'female'
    return 'male'

# ========== РЕАКЦИИ НА ДЕЙСТВИЯ ==========
def handle_actions(message):
    if not message.reply_to_message:
        return False
    
    full_text = message.text.strip().lower()
    
    actions_map = {
        # Романтика и дружба
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
        "пожатьруку": ("🤝", "пожал руку", "пожала руку", ""),
        "шлепнуть": ("🖐️", "шлепнул", "шлепнула", ""),
        "ущипнуть": ("🤏", "ущипнул", "ущипнула", ""),
        "покормить": ("🍕", "покормил", "покормила", ""),
        "дать пять": ("🙏", "дал пять", "дала пять", ""),
        "датьпять": ("🙏", "дал пять", "дала пять", ""),
        "понюхать": ("👃", "понюхал", "понюхала", ""),
        "испугать": ("😱", "испугал", "испугала", ""),
        "рассмешить": ("😂", "рассмешил", "рассмешила", ""),
        "предложить": ("💍", "предложил", "предложила", ""),
        "помочь": ("🫶", "помог", "помогла", ""),
        "уважать": ("🙏", "уважал", "уважала", ""),
        
        # Агрессивные
        "ударить": ("👊", "ударил", "ударила", ""),
        "пнуть": ("🦶", "пнул", "пнула", ""),
        "убить": ("💀", "убил", "убила", ""),
        "сжечь": ("🔥", "сжёг", "сожгла", ""),
        "взорвать": ("💣", "взорвал", "взорвала", ""),
        "расстрелять": ("🔫", "расстрелял", "расстреляла", ""),
        "шмальнуть": ("🔫", "шмальнул", "шмальнула", "в"),
        "задушить": ("🪢", "задушил", "задушила", ""),
        "послать нахуй": ("🖕", "послал нахуй", "послала нахуй", ""),
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
        "пощекотать": ("😂", "пощекотал", "пощекотала", ""),
        
        # 18+
        "дрочить": ("✊", "дрочил", "дрочила", ""),
        "дрочу": ("✊", "дрочил", "дрочила", ""),
        "подрочить": ("✊", "подрочил", "подрочила", ""),
        "отдрочить": ("✊", "отдрочил", "отдрочила", ""),
        "отдрочил": ("✊", "отдрочил", "отдрочила", ""),
        "отдрочила": ("✊", "отдрочила", "отдрочила", ""),
        "лизать": ("👅", "лизал", "лизала", ""),
        "лижет": ("👅", "лизал", "лизала", ""),
        "лизнуть": ("👅", "лизнул", "лизнула", ""),
        "отлизать": ("👅", "отлизал", "отлизала", ""),
        "отлизал": ("👅", "отлизал", "отлизала", ""),
        "отлизала": ("👅", "отлизала", "отлизала", ""),
        "выебать": ("🔞", "выебал", "выебала", ""),
        "выебал": ("🔞", "выебал", "выебала", ""),
        "выебала": ("🔞", "выебала", "выебала", ""),
        "оттрахать": ("🔞", "оттрахал", "оттрахала", ""),
        "оттрахал": ("🔞", "оттрахал", "оттрахала", ""),
        "оттрахала": ("🔞", "оттрахала", "оттрахала", ""),
        "трахнуть": ("🔞", "трахнул", "трахнула", ""),
        "трахнул": ("🔞", "трахнул", "трахнула", ""),
        "трахнула": ("🔞", "трахнула", "трахнула", ""),
        "изнасиловать": ("🔞", "изнасиловал", "изнасиловала", ""),
        "изнасиловал": ("🔞", "изнасиловал", "изнасиловала", ""),
        "изнасиловала": ("🔞", "изнасиловала", "изнасиловала", ""),
        "отсосать": ("👅", "отсосал", "отсосала", ""),
        "отсосал": ("👅", "отсосал", "отсосала", ""),
        "отсосала": ("👅", "отсосала", "отсосала", ""),
        "кончить": ("💦", "кончил", "кончила", "в"),
        "кончил": ("💦", "кончил", "кончила", "в"),
        "кончила": ("💦", "кончила", "кончила", "в"),
        "сквиртануть": ("💦💦", "сквиртанул", "сквиртанула", "на"),
        "сквиртанул": ("💦💦", "сквиртанул", "сквиртанула", "на"),
        "сквиртанула": ("💦💦", "сквиртанула", "сквиртанула", "на"),
        "сквирт": ("💦💦", "сквиртанул", "сквиртанула", "на"),
        "сестьналицо": ("🍑", "сел на лицо", "села на лицо", "на"),
        "сесть на лицо": ("🍑", "сел на лицо", "села на лицо", "на"),
        "на лицо": ("🍑", "сел на лицо", "села на лицо", "на"),
        "вылизать": ("👅", "вылизал", "вылизала", ""),
        "вылизывать": ("👅", "вылизал", "вылизала", ""),
        "засосать": ("🫦", "засосал", "засосала", ""),
        "засосала": ("🫦", "засосал", "засосала", ""),
        "опустить": ("😢", "опустил", "опустила", ""),
        "привсех": ("👥", "унизил при всех", "унизила при всех", ""),
        "при всех": ("👥", "унизил при всех", "унизила при всех", ""),
        "публично": ("👥", "унизил публично", "унизила публично", ""),
        
        # Бытовые
        "лечь": ("😴", "лёг", "леглá", "на"),
        "лёг": ("😴", "лёг", "леглá", "на"),
        "легла": ("😴", "леглá", "леглá", "на"),
        "спать": ("😴", "лёг спать", "леглá спать", ""),
        "уснуть": ("😴", "уснул", "уснула", ""),
        "пить": ("🍺", "выпил", "выпила", ""),
    }
    
    search_key = full_text.replace(" ", "")
    emoji = None
    past_action_male = None
    past_action_female = None
    action = None
    preposition = ""
    reply_text = ""
    
    if search_key in actions_map:
        emoji, past_action_male, past_action_female, preposition = actions_map[search_key]
        action = full_text.split()[0] if full_text.split() else full_text
    else:
        parts = full_text.split(maxsplit=1)
        first_word = parts[0]
        reply_text = parts[1] if len(parts) > 1 else ""
        if first_word in actions_map:
            emoji, past_action_male, past_action_female, preposition = actions_map[first_word]
            action = first_word
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
    # 1. Приветствия и команды без ответа
    if message.text and not message.text.startswith('/') and not message.reply_to_message:
        text_lower = message.text.lower().strip()
        user_name = message.from_user.first_name or message.from_user.username or "Пользователь"
        
        # Приветствия
        if text_lower in ["привет", "здарова", "здравствуй", "хай", "hello", "ку", "приветики", "здравствуйте"]:
            bot.reply_to(message, f"👋 Привет, {user_name}!")
            return
        
        if text_lower in ["доброе утро", "доброго утра", "с добрым утром"]:
            bot.reply_to(message, f"🌅 Доброе утро, {user_name}! Хорошего дня ☀️")
            return
        
        if text_lower in ["добрый вечер", "доброго вечера"]:
            bot.reply_to(message, f"🌆 Добрый вечер, {user_name}! Как прошёл день?")
            return
        
        if text_lower in ["спокойной ночи", "доброй ночи", "сладких снов"]:
            bot.reply_to(message, f"🌙 Спокойной ночи, {user_name}! Сладких снов 💤")
            return
        
        if text_lower in ["спасибо", "благодарю", "thanks", "thank you", "спс"]:
            responses = [f"🙏 Пожалуйста, {user_name}!", f"😊 Всегда рад помочь, {user_name}!", f"🤗 Обращайся, {user_name}!"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["пока", "до свидания", "прощай", "bye", "до встречи", "удачи"]:
            responses = [f"👋 Пока, {user_name}! Возвращайся!", f"😢 До встречи, {user_name}!", f"👋 {user_name}, хорошего дня!"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["как дела", "как дела?", "как ты"]:
            responses = [f"😊 У меня всё отлично, {user_name}! А у тебя?", f"🤖 Работаю, {user_name}! Спасибо что спросил!", f"🎉 Отлично, {user_name}! Что нового?"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["грустно", "печально", "мне грустно", "плохое настроение"]:
            bot.reply_to(message, f"😢 Обнимаю, {user_name}! Всё будет хорошо, ты справишься! 🤗❤️")
            return
        
        if text_lower in ["скучаю", "скучаю по тебе"]:
            bot.reply_to(message, f"🥺 {user_name}, я тоже по тебе скучаю! Приходи почаще! 🤗")
            return
        
        if text_lower in ["ты лучший", "ты лучшая", "лучший бот", "ты крут", "ты крутая", "молодец", "умница"]:
            bot.reply_to(message, f"😊 Спасибо, {user_name}! Ты тоже лучший/лучшая! ❤️")
            return
        
        if text_lower == "0+":
            bot.reply_to(message, f"🍼 Для самых маленьких! Но ты уже большой! 😊")
            return
        if text_lower == "13+":
            bot.reply_to(message, f"🔥 13+ — тут уже интереснее! 😎")
            return
        if text_lower == "18+":
            bot.reply_to(message, f"🔞 18+ — только для взрослых! Ты уверен? 😏")
            return
        
        if text_lower in ["кончить на всех", "кончитьнавсех"]:
            responses = [f"💦 {user_name} кончил(а) на всех присутствующих! 💦", f"🌊 {user_name} устроил(а) настоящий потоп на всех! 💦", f"💥 {user_name} кончил(а) так, что всем досталось! 💦"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["сквиртануть", "сквирт", "сквиртанул", "сквиртанула"]:
            responses = [f"💦💦 {user_name} сквиртанул(а) так, что всех облило! 💦💦", f"🌊 {user_name} устроил(а) настоящий фонтан! 💦💦", f"💦 {user_name} сквиртанул(а) с такой силой! 🔥"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["сесть на лицо", "сестьна лицо", "на лицо"]:
            responses = [f"🍑 {user_name} хочет сесть на лицо? 🤔 Осторожнее!", f"🍑 {user_name}, ты уверен(а)? 👀", f"🍑 Ну {user_name}, это уже слишком! 😳"]
            bot.reply_to(message, random.choice(responses))
            return
        
        if text_lower in ["вылизать", "вылизывать"]:
            bot.reply_to(message, f"👅 {user_name}, ты чего? 🤨")
            return
        
        if text_lower in ["унизить", "опустить"]:
            bot.reply_to(message, f"😢 {user_name}, зачем унижать? Давай лучше мирно! 🤗")
            return
        
        if text_lower in ["дрочить", "подрочить"]:
            bot.reply_to(message, f"✊ {user_name}, ты чего? Не прилюдно же... 🤨")
            return
    
    # 2. Действия при ответе на сообщение
    if message.text and not message.text.startswith('/') and message.reply_to_message:
        if handle_actions(message):
            return
    
    # 3. Автосохранение и цитаты
    if message.chat.type in ['group', 'supergroup']:
        global chat_users
        old_count = len(chat_users)
        chat_users = user_cache.save_user_from_message(message, chat_users)
        new_count = len(chat_users)
        if new_count > old_count:
            print(f"✨ Новый пользователь! Всего: {new_count}")
        add_chat_to_active(message)
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
    load_daily_quotes()
    start_all_reminders()
    app.run(host="0.0.0.0", port=port)

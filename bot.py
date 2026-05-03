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

# === ДЛЯ ЦИТАТ ===
QUOTES_CACHE_FILE = "daily_quotes.json"
daily_messages = []  # Временное хранилище сообщений за сегодня
daily_quote_times = [9, 12, 15, 18, 21]  # Часы отправки цитат (МСК)
active_chats = set()  # Хранилище чатов, куда отправлять цитаты

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
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
    except Exception as e:
        print(f"Ошибка: {e}")

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
    print(f"⏰ Напоминание {reminder['id']} на {target.strftime('%Y-%m-%d %H:%M:%S')}")

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
        requests.post(url, json=data, timeout=5)
    except:
        pass

# ========== ФУНКЦИИ ДЛЯ ЦИТАТ ==========

def load_daily_quotes():
    """Загружает сообщения текущего дня при перезапуске"""
    global daily_messages
    if os.path.exists(QUOTES_CACHE_FILE):
        try:
            with open(QUOTES_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'):
                    daily_messages = data.get('messages', [])
                    print(f"📚 Загружено {len(daily_messages)} сообщений за сегодня")
                else:
                    clear_daily_quotes()
        except:
            pass

def save_daily_quotes():
    """Сохраняет сообщения текущего дня"""
    try:
        data = {
            'date': datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'),
            'messages': daily_messages
        }
        with open(QUOTES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")

def clear_daily_quotes():
    """Очищает кэш сообщений (в 00:00)"""
    global daily_messages
    daily_messages = []
    save_daily_quotes()
    print("🔄 Кэш цитат очищен! Начинаю собирать новые сообщения за сегодня")

def add_message_to_quotes(message):
    """Добавляет сообщение в кэш для цитат"""
    global daily_messages
    
    if not message.text:
        return
    if message.text.startswith('/'):
        return
    # Минимальная длина сообщения - 3 символа (исправлено)
    if len(message.text) < 3:
        print(f"⏩ Сообщение слишком короткое ({len(message.text)} символов): {message.text[:30]}")
        return
    if len(message.text) > 500:
        return
    
    user = message.from_user
    if not user:
        return
    
    # Логируем добавление
    print(f"📝 ДОБАВЛЕНО сообщение в кэш: {message.text[:50]} от {user.first_name}")
    
    daily_messages.append({
        'text': message.text.strip(),
        'author': user.id,
        'author_name': user.first_name or user.username or "Участник",
        'author_username': user.username,
        'time': datetime.now(MOSCOW_TZ).strftime('%H:%M:%S'),
        'chat_id': message.chat.id
    })
    
    if len(daily_messages) > 1000:
        daily_messages = daily_messages[-1000:]
    
    save_daily_quotes()

def add_chat_to_active(message):
    """Добавляет чат в список активных"""
    chat_id = message.chat.id
    if chat_id not in active_chats:
        active_chats.add(chat_id)
        print(f"📍 Добавлен чат {chat_id} в активные")

def send_random_quote_to_chat(chat_id):
    """Отправляет случайную цитату в конкретный чат"""
    global daily_messages
    
    chat_messages = [m for m in daily_messages if m.get('chat_id') == chat_id]
    
    if len(chat_messages) < 3:
        return
    
    quote = random.choice(chat_messages)
    
    text = f"📜 *Цитата дня*\n\n"
    text += f"« {quote['text']} »\n\n"
    text += f"— *{quote['author_name']}*"
    if quote.get('time'):
        text += f"  •  {quote['time']}"
    
    try:
        bot.send_message(chat_id, text, parse_mode="Markdown")
        print(f"📜 Отправлена цитата в чат {chat_id}")
    except Exception as e:
        print(f"❌ Не удалось отправить в чат {chat_id}: {e}")

def send_random_quote_to_all_chats():
    """Отправляет цитаты во все активные чаты"""
    print(f"📜 Отправляю цитаты в {len(active_chats)} чатов...")
    for chat_id in list(active_chats):
        send_random_quote_to_chat(chat_id)

def schedule_daily_quotes():
    """Запускает ежедневную рассылку цитат во все чаты"""
    now_moscow = datetime.now(MOSCOW_TZ)
    
    for hour in daily_quote_times:
        target = now_moscow.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now_moscow:
            target += timedelta(days=1)
        
        delay = (target - now_moscow).total_seconds()
        timer = threading.Timer(delay, send_random_quote_to_all_chats)
        timer.daemon = True
        timer.start()
        print(f"📜 Цитата запланирована на {target.strftime('%H:%M:%S')}")
    
    midnight = now_moscow.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    midnight_delay = (midnight - now_moscow).total_seconds()
    midnight_timer = threading.Timer(midnight_delay, clear_daily_quotes)
    midnight_timer.daemon = True
    midnight_timer.start()
    print(f"🔄 Очистка кэша запланирована на 00:00")

# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    print(f"📢 /start от {message.from_user.id}")
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:*\n`/remind 15:30 текст` - создать\n`/reminds` - список\n`/delremind ID` - удалить\n\n"
            "📜 *Цитаты:*\n`/quote` - случайная цитата из чата\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`\n\n"
            "👑 *Админ-команды (в ЛС):*\n"
            "`/users` - список пользователей\n"
            "`/adduser @username` - добавить\n"
            "`/deluser @username` - удалить\n"
            "`/backup` - создать бекап\n"
            "`/restore` - восстановить",
            parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "✅ *Бот работает!*\n\n"
            "🤖 *ИИ:* `/ai вопрос`\n\n"
            "⏰ *Напоминания:*\n`/remind 15:30 текст` - создать\n`/reminds` - список\n`/delremind ID` - удалить\n\n"
            "📜 *Цитаты:*\n`/quote` - случайная цитата из чата\n\n"
            "📨 *Скрытые сообщения:* `@бот username текст`",
            parse_mode="Markdown")

@bot.message_handler(commands=['ai'])
def ai_command(message):
    print(f"🤖 /ai от {message.from_user.id}")
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
    print(f"📋 /reminds от {message.from_user.id}")
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
    """Отправляет случайную цитату из чата за сегодня"""
    print(f"📜 /quote от {message.from_user.id} в чате {message.chat.id}")
    
    # Отладочная информация
    print(f"📊 Всего сообщений в кэше: {len(daily_messages)}")
    
    # Фильтруем сообщения только из этого чата
    chat_messages = [m for m in daily_messages if m.get('chat_id') == message.chat.id]
    print(f"📊 Сообщений в этом чате: {len(chat_messages)}")
    
    # Показываем первые 3 сообщения для отладки
    for i, msg in enumerate(chat_messages[:3]):
        print(f"   Сообщение {i+1}: {msg['text'][:50]} от {msg['author_name']}")
    
    if len(chat_messages) < 3:
        bot.reply_to(message, f"📭 Пока недостаточно сообщений для цитаты.\nВ этом чате: {len(chat_messages)} сообщений (нужно минимум 3)\n\n✍️ Напишите ещё что-нибудь!")
        return
    
    # Выбираем случайную цитату
    quote = random.choice(chat_messages)
    
    # Формируем красивое сообщение
    text = f"📜 *Цитата дня*\n\n"
    text += f"« {quote['text']} »\n\n"
    text += f"— *{quote['author_name']}*"
    if quote.get('time'):
        text += f"  •  {quote['time']}"
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown")
    print(f"✅ Цитата отправлена в чат {message.chat.id}")

# === АДМИН-КОМАНДЫ ===

@bot.message_handler(commands=['users'])
def show_users(message):
    print(f"👥 /users от {message.from_user.id}")
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
        text += f"• `{uid}` | @{username} | {name}\n"
        
        if len(text) > 3500:
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
            text = ""
    
    if text:
        bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['adduser'])
def add_user_manually(message):
    global chat_users
    print(f"➕ /adduser от {message.from_user.id}")
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
    print(f"❌ /deluser от {message.from_user.id}")
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
    print(f"💾 /backup от {message.from_user.id}")
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
    print(f"📥 /restore от {message.from_user.id}")
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
    print(f"📄 Файл от {message.from_user.id}")
    
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
        
        bot.edit_message_text(
            f"✅ Восстановлено!\n👥 {len(chat_users)} пользователей\n⏰ {len(reminders)} напоминаний",
            message.chat.id, status.message_id
        )
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

# ========== АВТОСБОР ПОЛЬЗОВАТЕЛЕЙ И СООБЩЕНИЙ ДЛЯ ЦИТАТ ==========
@bot.message_handler(func=lambda message: True)
def auto_collect_users(message):
    # Пропускаем команды
    if message.text and message.text.startswith('/'):
        return
    
    # Только группы
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    global chat_users
    old_count = len(chat_users)
    chat_users = user_cache.save_user_from_message(message, chat_users)
    new_count = len(chat_users)
    
    if new_count > old_count:
        print(f"✨ Новый пользователь добавлен! Всего: {new_count}")
    
    # Добавляем чат в активные для цитат
    add_chat_to_active(message)
    
    # Добавляем сообщение в кэш для цитат
    add_message_to_quotes(message)

# ========== ВЕБХУК ==========
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    print("🔄 Установка вебхука...")
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    print(f"Результат: {r.json()}")
    
    # Запускаем планировщик цитат
    schedule_daily_quotes()
    
    # Загружаем сохранённые сообщения за сегодня
    load_daily_quotes()
    
    start_all_reminders()
    app.run(host="0.0.0.0", port=port)

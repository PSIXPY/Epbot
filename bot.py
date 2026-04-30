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
from datetime import datetime, timedelta
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
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

bot = TeleBot(BOT_TOKEN)
secret_messages = {}

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# === КЭШ ПОЛЬЗОВАТЕЛЕЙ ЧАТА ===
USERS_CACHE_FILE = "chat_users.json"

def load_users_cache():
    if os.path.exists(USERS_CACHE_FILE):
        try:
            with open(USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users_cache(users):
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Сохранено {len(users)} пользователей в кэш")
    except Exception as e:
        logger.error(f"Ошибка сохранения кэша: {e}")

chat_users = load_users_cache()

# === ФУНКЦИЯ ДЛЯ СТАВКИ РЕАКЦИИ ===
def set_reaction(chat_id, message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": "🔥"}]
    }
    try:
        response = requests.post(url, json=data, timeout=5)
        result = response.json()
        if result.get("ok"):
            logger.info(f"🔥 Реакция на пост {message_id}")
        else:
            logger.error(f"Ошибка реакции: {result}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === НАПОМИНАНИЯ (ИСПРАВЛЕНО) ===
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

def schedule_reminder(reminder):
    now_moscow = datetime.now(MOSCOW_TZ)
    target = now_moscow.replace(hour=reminder["hours"], minute=reminder["minutes"], second=0, microsecond=0)
    
    if target <= now_moscow:
        target += timedelta(days=1)
    
    delay = (target - now_moscow).total_seconds()
    if delay < 0:
        delay = 0
    
    timer = threading.Timer(delay, execute_reminder, args=[reminder])
    timer.daemon = True
    timer.start()
    reminder["_timer"] = timer
    logger.info(f"⏰ Напоминание {reminder['id']} на {target.strftime('%Y-%m-%d %H:%M:%S')} МСК")

def execute_reminder(reminder):
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
    
    # Если ежедневное - планируем снова
    if reminder.get("daily"):
        schedule_reminder(reminder)
    else:
        # Удаляем одноразовое напоминание
        for i, r in enumerate(reminders):
            if r["id"] == reminder["id"]:
                reminders.pop(i)
                save_reminders(reminders)
                break

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

# === КЭШ И ИСТОРИЯ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

# === ПЕРЕВОДЧИК ===
TRANSLATOR_SETTINGS_FILE = "translator_settings.json"

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
        logger.error(f"Ошибка сохранения: {e}")

translator_settings = load_translator_settings()

def is_translator_enabled(chat_id):
    return translator_settings.get(str(chat_id), False)

def set_translator_enabled(chat_id, enabled):
    translator_settings[str(chat_id)] = enabled
    save_translator_settings(translator_settings)

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
        "model": "mixtral-8x7b-32768",
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

def search_wikipedia(query):
    try:
        wiki_ru = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki_ru.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать на Википедии]({page.fullurl})"
        else:
            return f"❌ *Ничего не найдено* по запросу: `{query}`"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"

# === КОМАНДЫ ===
@bot.message_handler(commands=['start', 'help'])
def help_command(message):
    thread_id = message.message_thread_id
    help_text = """📖 *Команды бота*

⏰ *Напоминания:*
/remind 15:30 текст - создать
/reminds - список
/delremind ID - удалить

🤖 *ИИ:* /ai вопрос

📖 *Википедия:* /wiki запрос

🌐 *Перевод:* /т on / /т off

🎲 *Игры:* /roll | /coin

👑 *Админ:* /backup - бекап (в ЛС)

🔔 *Массовые упоминания:*
/калл - все участники
/админы - администраторы

📊 *Инфо:* /users - кэш, /debug - диагностика"""
    bot.reply_to(message, help_text, parse_mode="Markdown", message_thread_id=thread_id)

@bot.message_handler(commands=['users'])
def show_users(message):
    thread_id = message.message_thread_id
    if not chat_users:
        bot.reply_to(message, "📭 Кэш пользователей пуст", message_thread_id=thread_id)
        return
    
    result = f"👥 *Пользователи в кэше:* {len(chat_users)}\n\n"
    users_list = []
    for uid, data in list(chat_users.items())[:20]:
        username = data.get('username', 'нет')
        name = data.get('first_name', 'Неизвестный')
        users_list.append(f"• {name} (@{username}) - ID: `{uid}`")
    
    result += "\n".join(users_list)
    if len(chat_users) > 20:
        result += f"\n\n... и еще {len(chat_users) - 20}"
    
    bot.reply_to(message, result, parse_mode="Markdown", message_thread_id=thread_id)

@bot.message_handler(commands=['debug'])
def debug_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа")
        return
    
    result = f"🔍 *Диагностика*\n\n"
    result += f"👥 В кэше: {len(chat_users)} пользователей\n"
    result += f"⏰ Напоминаний: {len(reminders)}\n"
    result += f"🔐 Секретных сообщений: {len(secret_messages)}"
    
    bot.reply_to(message, result, parse_mode="Markdown")

# === НАПОМИНАНИЯ КОМАНДЫ (ИСПРАВЛЕНО) ===
@bot.message_handler(commands=['remind'])
def add_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message, "ℹ️ /remind 15:30 текст напоминания", message_thread_id=thread_id)
        return
    
    time_str = parts[1]
    reminder_text = parts[2]
    
    # Парсим время
    try:
        if ":" in time_str:
            hours, minutes = map(int, time_str.split(":"))
        else:
            hours = int(time_str)
            minutes = 0
        
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValueError
    except:
        bot.reply_to(message, "❌ Неправильный формат времени. Пример: /remind 15:30 текст", message_thread_id=thread_id)
        return
    
    global reminder_counter
    reminder_counter += 1
    
    reminder = {
        "id": reminder_counter,
        "chat_id": chat_id,
        "user_id": message.from_user.id,
        "thread_id": thread_id,
        "text": reminder_text,
        "hours": hours,
        "minutes": minutes,
        "daily": False
    }
    
    reminders.append(reminder)
    save_reminders(reminders)
    schedule_reminder(reminder)
    
    bot.reply_to(message, 
        f"✅ *Напоминание создано!*\n\n"
        f"🆔 ID: {reminder_counter}\n"
        f"⏰ Время: {hours:02d}:{minutes:02d} МСК\n"
        f"📝 Текст: {reminder_text}",
        parse_mode="Markdown",
        message_thread_id=thread_id)

@bot.message_handler(commands=['reminds'])
def list_reminders(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    user_reminders = []
    for r in reminders:
        if r.get("chat_id") != chat_id:
            continue
        if thread_id and r.get("thread_id") != thread_id:
            continue
        user_reminders.append(r)
    
    if not user_reminders:
        bot.reply_to(message, "📭 Нет активных напоминаний", message_thread_id=thread_id)
        return
    
    response = "📋 *Активные напоминания:*\n\n"
    for r in user_reminders:
        response += f"🆔 `{r['id']}` - {r['hours']:02d}:{r['minutes']:02d} - {r['text'][:40]}\n"
    
    response += f"\n💡 Удалить: `/delremind ID`"
    bot.reply_to(message, response, parse_mode="Markdown", message_thread_id=thread_id)

@bot.message_handler(commands=['delremind'])
def delete_reminder(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ /delremind ID", message_thread_id=thread_id)
        return
    
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if r.get("chat_id") != chat_id:
                    bot.reply_to(message, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
                    return
                if thread_id and r.get("thread_id") != thread_id:
                    bot.reply_to(message, f"❌ Напоминание {rid} не в этом топике", message_thread_id=thread_id)
                    return
                
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                
                reminders.pop(i)
                save_reminders(reminders)
                bot.reply_to(message, f"✅ Напоминание {rid} удалено", message_thread_id=thread_id)
                return
        
        bot.reply_to(message, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
    except:
        bot.reply_to(message, "❌ Неверный ID", message_thread_id=thread_id)

# === МАССОВЫЕ УПОМИНАНИЯ (ИСПРАВЛЕНО) ===
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['калл', 'call', 'все', 'всех', 'everyone', 'all'])
def call_all_simple(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    users = []
    bot_id = bot.get_me().id
    user_id = message.from_user.id
    
    for uid, user_data in chat_users.items():
        try:
            uid_int = int(uid)
            if uid_int == bot_id or uid_int == user_id:
                continue
            
            username = user_data.get('username')
            if username:
                users.append(f"@{username}")
        except:
            continue
    
    if not users:
        bot.reply_to(message, "❌ Нет пользователей с username для упоминания\n\n💡 Напишите любое сообщение в чат, чтобы бот вас запомнил", message_thread_id=thread_id)
        return
    
    # Отправляем по 50 username за раз
    batch_size = 50
    for i in range(0, len(users), batch_size):
        batch = users[i:i+batch_size]
        mention_text = f"🔔 *{message.from_user.first_name} созывает всех!*\n\n" + " ".join(batch)
        bot.send_message(chat_id, mention_text, parse_mode="Markdown", message_thread_id=thread_id)
        time.sleep(0.3)
    
    bot.reply_to(message, f"✅ Упомянуто {len(users)} пользователей", message_thread_id=thread_id)

@bot.message_handler(commands=['калл', 'все', 'all'])
def call_all_command(message):
    call_all_simple(message)

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['админы', 'admins'])
def call_admins_simple(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    try:
        admins = bot.get_chat_administrators(chat_id)
        admin_mentions = []
        bot_id = bot.get_me().id
        
        for admin in admins:
            admin_user = admin.user
            if admin_user.id != bot_id and admin_user.username:
                admin_mentions.append(f"@{admin_user.username}")
        
        if not admin_mentions:
            bot.reply_to(message, "📭 Нет администраторов с username", message_thread_id=thread_id)
            return
        
        mention_text = f"👑 *{message.from_user.first_name} созывает администраторов!*\n\n" + " ".join(admin_mentions)
        bot.send_message(chat_id, mention_text, parse_mode="Markdown", message_thread_id=thread_id)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}", message_thread_id=thread_id)

@bot.message_handler(commands=['админы', 'admins'])
def call_admins_command(message):
    call_admins_simple(message)

@bot.message_handler(commands=['adduser'])
def add_user_to_cache(message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    
    try:
        member = bot.get_chat_member(chat_id, message.from_user.id)
        if member.status not in ['creator', 'administrator'] and message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "❌ Только для администраторов!", message_thread_id=thread_id)
            return
    except:
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "❌ Ошибка проверки прав", message_thread_id=thread_id)
            return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "ℹ️ /adduser @username", message_thread_id=thread_id)
        return
    
    target = args[1].lstrip("@")
    
    try:
        user_info = bot.get_chat(target)
        user_id = str(user_info.id)
        
        chat_users[user_id] = {
            "id": user_info.id,
            "username": user_info.username,
            "first_name": user_info.first_name,
            "last_name": user_info.last_name or "",
            "added_by": message.from_user.id,
            "added_at": time.time()
        }
        save_users_cache(chat_users)
        
        bot.reply_to(message,
            f"✅ *Пользователь добавлен!*\n\n"
            f"👤 {user_info.first_name}\n"
            f"🆔 `{user_id}`\n"
            f"👤 @{user_info.username if user_info.username else '—'}",
            parse_mode="Markdown",
            message_thread_id=thread_id)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Пользователь `{target}` не найден", parse_mode="Markdown", message_thread_id=thread_id)

# === ДРУГИЕ КОМАНДЫ ===
@bot.message_handler(commands=['ai'])
def ai_command(message):
    thread_id = message.message_thread_id
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ /ai вопрос", message_thread_id=thread_id)
        return
    msg = bot.reply_to(message, "🤖 Думаю...", message_thread_id=thread_id)
    answer = ask_groq(message.from_user.id, prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id)

@bot.message_handler(commands=['wiki'])
def wiki_command(message):
    thread_id = message.message_thread_id
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ /wiki запрос", message_thread_id=thread_id)
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown", disable_web_page_preview=True, message_thread_id=thread_id)

@bot.message_handler(commands=['roll'])
def roll_command(message):
    thread_id = message.message_thread_id
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}", message_thread_id=thread_id)

@bot.message_handler(commands=['coin'])
def coin_command(message):
    thread_id = message.message_thread_id
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}", message_thread_id=thread_id)

@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    thread_id = message.message_thread_id
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
        bot.reply_to(message, "🗑️ История очищена!", message_thread_id=thread_id)
    else:
        bot.reply_to(message, "📭 Нет истории", message_thread_id=thread_id)

# === ПЕРЕВОДЧИК ===
@bot.message_handler(commands=['т'])
def translate_command(message):
    thread_id = message.message_thread_id
    chat_id = message.chat.id
    parts = message.text.split()
    
    if len(parts) < 2:
        status = "✅ Включён" if is_translator_enabled(chat_id) else "❌ Выключен"
        bot.reply_to(
            message, 
            f"🌐 *Переводчик RU↔EN*\n\n📊 Статус: {status}\n\n"
            f"🔹 `/т on` — включить\n"
            f"🔹 `/т off` — выключить",
            parse_mode="Markdown",
            message_thread_id=thread_id
        )
        return
    
    action = parts[1].lower()
    if action == "on":
        set_translator_enabled(chat_id, True)
        bot.reply_to(message, "✅ Включён!", message_thread_id=thread_id)
    elif action == "off":
        set_translator_enabled(chat_id, False)
        bot.reply_to(message, "❌ Выключен", message_thread_id=thread_id)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def auto_translate(message):
    chat_id = message.chat.id
    
    if not is_translator_enabled(chat_id):
        return
    if message.from_user.id == bot.get_me().id:
        return
    if message.text.startswith('/'):
        return
    if message.text.startswith('🔔') or message.text.startswith('👑'):
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
        logger.error(f"Ошибка перевода: {e}")

# === СБОР ПОЛЬЗОВАТЕЛЕЙ ===
@bot.message_handler(content_types=['new_chat_members'])
def handle_new_member(message):
    for new_member in message.new_chat_members:
        if new_member.id == bot.get_me().id:
            continue
        
        user_id = str(new_member.id)
        chat_users[user_id] = {
            "id": new_member.id,
            "first_name": new_member.first_name,
            "last_name": new_member.last_name,
            "username": new_member.username,
            "joined_at": time.time()
        }
        save_users_cache(chat_users)
        logger.info(f"👤 Новый участник: {new_member.first_name}")

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'])
def collect_user_from_message(message):
    if message.from_user and message.from_user.id != bot.get_me().id:
        user = message.from_user
        user_id = str(user.id)
        
        if user_id not in chat_users:
            chat_users[user_id] = {
                "id": user.id,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "username": user.username,
                "last_seen": time.time()
            }
            save_users_cache(chat_users)
            logger.info(f"📝 Добавлен пользователь: {user.first_name}")

# === БЕКАП (ИСПРАВЛЕНО) ===
@bot.message_handler(commands=['backup'])
def backup_full(message):
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
        
        backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        with open(backup_file, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"✅ Бекап {len(backup_data)} напоминаний")
        
        os.remove(backup_file)
        bot.reply_to(message, "✅ Бекап создан!")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)[:100]}")

@bot.message_handler(commands=['restore'])
def restore_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав")
        return
    
    bot.reply_to(message, "📥 Отправьте JSON файл")

@bot.message_handler(content_types=['document'])
def handle_restore(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if not message.document.file_name.endswith('.json'):
        bot.reply_to(message, "❌ Нужен JSON файл")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        file_content = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}").content
        backup_data = json.loads(file_content.decode('utf-8'))
        
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
        
        bot.reply_to(message, f"✅ Восстановлено {len(backup_data)} напоминаний")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)[:100]}")

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
        
        for uid, user_data in chat_users.items():
            username = user_data.get('username')
            if username and username.lower() == target_raw.lower():
                target_id = int(uid)
                target_name = user_data.get('first_name') or target_raw
                break
        
        if not target_id and target_raw.isdigit():
            target_id = int(target_raw)
            target_name = f"Пользователь {target_raw}"
        
        if not target_id:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("❓ Как узнать ID", url="https://t.me/userinfobot"))
            result = types.InlineQueryResultArticle(
                id="error",
                title="❌ Пользователь не найден",
                description=f"@{target_raw} - проверьте правильность",
                input_message_content=types.InputTextMessageContent(
                    f"❌ *Пользователь @{target_raw} не найден*\n\n"
                    f"💡 Используйте `/adduser @{target_raw}` (для админов)",
                    parse_mode="Markdown"
                ),
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
            "expires": time.time() + 10800
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"secret_read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Для {target_name}",
            description=content[:50] + ("..." if len(content) > 50 else ""),
            input_message_content=types.InputTextMessageContent(
                f"🔐 Скрытое сообщение\n\nОт: {query.from_user.first_name}\nКому: {target_name}\nДействует: 3 часа"
            ),
            reply_markup=markup
        )
        
        bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)
        
    except Exception as e:
        logger.error(f"Inline error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("secret_read_"))
def handle_secret_read(call):
    msg_id = call.data.replace("secret_read_", "")
    
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение не найдено", show_alert=True)
        return
    
    data = secret_messages[msg_id]
    
    if call.from_user.id != data["target_id"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Истекло 3 часа", show_alert=True)
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

# === ВЕБХУК ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        
        if update and "channel_post" in update:
            post = update["channel_post"]
            channel_id = post["chat"]["id"]
            if channel_id in [-1002185590715, -1001317416582]:
                message_id = post["message_id"]
                set_reaction(channel_id, message_id)
        
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
        
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# === ЗАПУСК ===
start_all_reminders()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    bot.remove_webhook()
    bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "channel_post", "inline_query", "callback_query", "chat_member", "my_chat_member"]
    )
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info("✅ ВСЕ КОМАНДЫ ИСПРАВЛЕНЫ")
    
    app.run(host="0.0.0.0", port=port)

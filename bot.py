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

# === КЭШ ПОЛЬЗОВАТЕЛЕЙ ===
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
        print(f"💾 Сохранено {len(users)} пользователей в кэш")
    except Exception as e:
        print(f"Ошибка: {e}")

chat_users = load_users_cache()
print(f"👥 Загружено {len(chat_users)} пользователей")

# === ФУНКЦИЯ ДЛЯ УДАЛЕНИЯ СООБЩЕНИЙ ===
def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = "reminders.json"

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
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
        print(f"Ошибка отправки: {e}")

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
    print(f"⏰ Напоминание {reminder['id']} на {target.strftime('%Y-%m-%d %H:%M:%S')} МСК")

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
        if response.json().get("ok"):
            print(f"🔥 Реакция на {message_id}")
    except Exception as e:
        print(f"Ошибка: {e}")

# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    bot.send_message(message.chat.id, "✅ Бот работает!\n\n"
        "🤖 ИИ: /ai вопрос\n\n"
        "⏰ Напоминания (МСК):\n/remind 15:30 текст\n/reminds\n/delremind ID\n\n"
        "💾 Бекап (в ЛС):\n/backup\n/restore\n\n"
        "📨 Скрытые сообщения:\n@бот username текст\n\n"
        "👥 Пользователи (только админ, в ЛС):\n/users\n/adduser @username\n/deluser @username")

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
        msg = bot.send_message(chat_id, "ℹ️ /remind 15:30 текст\n\nПример: /remind 16:00 Позвонить маме", message_thread_id=thread_id)
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
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValueError
    except:
        msg = bot.send_message(chat_id, "❌ Неверный формат времени. Используйте ЧЧ:ММ (МСК)", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
        return
    
    daily = reminder_text.lower().startswith("ежедневно")
    if daily:
        reminder_text = reminder_text[len("ежедневно"):].lstrip()
    
    reminder_counter += 1
    
    now_moscow = datetime.now(MOSCOW_TZ)
    target_today = now_moscow.replace(hour=hours, minute=minutes)
    period = "сегодня" if target_today > now_moscow else "завтра"
    
    if daily:
        period = "каждый день"
    
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
    
    msg = bot.send_message(chat_id, 
        f"✅ *Напоминание создано!*\n\n"
        f"🆔 ID: {reminder_counter}\n"
        f"⏰ Время: {hours:02d}:{minutes:02d} МСК\n"
        f"📅 Период: {period}\n"
        f"📝 Текст: {reminder_text}",
        parse_mode="Markdown",
        message_thread_id=thread_id)
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
        msg = bot.send_message(chat_id, "📭 Нет активных напоминаний", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    
    response = "📋 *Активные напоминания:*\n\n"
    for r in user_reminders:
        if r.get("daily"):
            period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            period = f"{r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 `{r['id']}` - {period}\n   📝 {r['text'][:40]}\n\n"
    
    response += f"\n💡 *Удалить:* `/delremind ID`"
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
                    msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
                    delete_after_delay(chat_id, msg.message_id, 10)
                    return
                if thread_id and r.get("thread_id") != thread_id:
                    msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не в этом топике", message_thread_id=thread_id)
                    delete_after_delay(chat_id, msg.message_id, 10)
                    return
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders(reminders)
                msg = bot.send_message(chat_id, f"✅ *Напоминание {rid} удалено*", parse_mode="Markdown", message_thread_id=thread_id)
                delete_after_delay(chat_id, msg.message_id, 10)
                return
        msg = bot.send_message(chat_id, f"❌ Напоминание {rid} не найдено", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
    except:
        msg = bot.send_message(chat_id, "❌ *Неверный ID*\n\nИспользуйте числа: `/delremind 1`", parse_mode="Markdown", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)

# === УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ===

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав! Только администратор.")
        return
    
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Команда работает только в личных сообщениях!")
        return
    
    if not chat_users:
        bot.send_message(message.chat.id, "📭 Кэш пользователей пуст.")
        return
    
    # Отправляем статистику
    bot.send_message(message.chat.id, f"📊 *Всего пользователей:* {len(chat_users)}", parse_mode="Markdown")
    
    # Формируем и отправляем список
    users_list = "📋 *Сохранённые пользователи:*\n\n"
    for uid, user in chat_users.items():
        username = user.get('username', 'нет')
        name = user.get('full_name', user.get('first_name', 'Без имени'))
        last_seen = user.get('last_seen', 'неизвестно')[:16]
        users_list += f"• `{uid}` | @{username} | {name}\n  └ последнее: {last_seen}\n\n"
        
        if len(users_list) > 3800:
            bot.send_message(message.chat.id, users_list, parse_mode="Markdown")
            users_list = ""
    
    if users_list:
        bot.send_message(message.chat.id, users_list, parse_mode="Markdown")

@bot.message_handler(commands=['adduser'])
def add_user_manually(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав! Только администратор.")
        return
    
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Команда работает только в личных сообщениях!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ /adduser @username - добавить пользователя", parse_mode="Markdown")
        return
    
    input_data = parts[1].strip().lstrip("@")
    
    # Проверяем, может уже есть в кэше
    for uid, user in chat_users.items():
        if user.get('username') == input_data:
            bot.reply_to(message, f"⚠️ Пользователь @{input_data} уже есть в кэше!")
            return
    
    # Добавляем пользователя
    user_id_str = input_data if input_data.isdigit() else f"temp_{int(time.time())}"
    username = input_data if not input_data.isdigit() else None
    
    chat_users[user_id_str] = {
        "id": user_id_str,
        "username": username,
        "first_name": input_data,
        "last_name": "",
        "full_name": input_data,
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users_cache(chat_users)
    bot.reply_to(message, f"✅ Пользователь @{input_data} добавлен!")

@bot.message_handler(commands=['deluser'])
def delete_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав! Только администратор.")
        return
    
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Команда работает только в личных сообщениях!")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ /deluser @username - удалить пользователя")
        return
    
    target = parts[1].strip().lstrip("@")
    
    found_id = None
    for uid, user in chat_users.items():
        if user.get('username') == target:
            found_id = uid
            break
        if uid == target:
            found_id = uid
            break
    
    if found_id:
        del chat_users[found_id]
        save_users_cache(chat_users)
        bot.reply_to(message, f"✅ Пользователь @{target} удалён!")
    else:
        bot.reply_to(message, f"❌ Пользователь @{target} не найден")

# === БЕКАП ===
@bot.message_handler(commands=['backup'])
def backup_command(message):
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    status_msg = bot.reply_to(message, "🔄 Создаю бекап...")
    
    try:
        backup_reminders = []
        for r in reminders:
            r_copy = {}
            for k, v in r.items():
                if k not in ["timer", "_timer"]:
                    r_copy[k] = v
            backup_reminders.append(r_copy)
        
        data = {
            "version": "2.0",
            "date": str(datetime.now()),
            "reminders": backup_reminders,
            "chat_users": chat_users
        }
        
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"✅ Бекап создан!\n📊 Напоминаний: {len(backup_reminders)}\n👥 Пользователей: {len(chat_users)}")
        
        os.remove(filename)
        bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {str(e)[:200]}", message.chat.id, status_msg.message_id)

@bot.message_handler(commands=['restore'])
def restore_command(message):
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    bot.send_message(message.chat.id, "📥 Отправьте JSON файл бекапа")

@bot.message_handler(content_types=['document'])
def handle_restore_file(message):
    global chat_users, reminder_counter
    
    if message.chat.type != 'private':
        return
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    status_msg = bot.reply_to(message, "🔄 Восстанавливаю...")
    
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
        
        if "reminders" in backup_data:
            reminders.clear()
            reminder_counter = 0
            for r in backup_data["reminders"]:
                reminders.append(r)
                if r.get("id", 0) > reminder_counter:
                    reminder_counter = r.get("id", 0)
            save_reminders(reminders)
            start_all_reminders()
        
        if "chat_users" in backup_data:
            chat_users = backup_data["chat_users"]
            save_users_cache(chat_users)
        
        bot.edit_message_text(f"✅ Восстановление завершено!\n👥 Пользователей: {len(chat_users)}", message.chat.id, status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {str(e)[:200]}", message.chat.id, status_msg.message_id)

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
        
        for uid, user_data in chat_users.items():
            username = user_data.get('username')
            if username and username.lower() == target_raw.lower():
                target_id = int(uid) if str(uid).isdigit() else None
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
                description=f"@{target_raw} - проверьте",
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
                f"🔐 *Скрытое сообщение*\nОт: {query.from_user.first_name}\nКому: {target_name}\nДействует: 1 час",
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
    
    if call.from_user.id != data["target_id"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Истекло 1 час", show_alert=True)
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

# ========== АВТОСБОР ПОЛЬЗОВАТЕЛЕЙ С ЛОГАМИ ==========
@bot.message_handler(func=lambda message: True)
def auto_collect_users(message):
    # Только группы
    if message.chat.type not in ['group', 'supergroup']:
        return
    
    user = message.from_user
    if not user:
        return
    
    user_id = str(user.id)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    
    # Проверяем, новый ли пользователь
    is_new = user_id not in chat_users
    
    # Сохраняем
    chat_users[user_id] = {
        "id": user.id,
        "username": username,
        "first_name": first_name,
        "last_name": user.last_name or "",
        "full_name": f"{first_name} {user.last_name or ''}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users_cache(chat_users)
    
    # Логируем!
    time_now = datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')
    if is_new:
        print(f"🆕 [{time_now}] НОВЫЙ пользователь: @{username} ({first_name}) [ID: {user_id}]")
    else:
        print(f"🔄 [{time_now}] Обновлён: @{username} ({first_name}) [ID: {user_id}]")

# ========== ВЕБХУК ==========
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if update and "channel_post" in update:
            post = update["channel_post"]
            if post["chat"]["id"] in [-1002185590715, -1001317416582]:
                set_reaction(post["chat"]["id"], post["message_id"])
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        print(f"Ошибка: {e}")
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
    
    start_all_reminders()
    app.run(host="0.0.0.0", port=port)

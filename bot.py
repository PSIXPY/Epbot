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

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)
secret_messages = {}

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
        print(f"Ошибка сохранения кэша: {e}")

chat_users = load_users_cache()

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
        print(f"👤 Новый участник: {new_member.first_name}")

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
            print(f"📝 Добавлен пользователь: {user.first_name}")

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа")
        return
    
    if not chat_users:
        bot.reply_to(message, "📭 Кэш пользователей пуст")
        return
    
    result = f"👥 *Пользователей в кэше:* {len(chat_users)}\n\n"
    users_list = []
    for uid, data in list(chat_users.items())[:20]:
        username = data.get('username', 'нет')
        name = data.get('first_name', 'Неизвестный')
        users_list.append(f"• {name} (@{username}) - ID: `{uid}`")
    
    result += "\n".join(users_list)
    if len(chat_users) > 20:
        result += f"\n\n... и еще {len(chat_users) - 20}"
    
    bot.reply_to(message, result, parse_mode="Markdown")

@bot.message_handler(commands=['adduser'])
def add_user_to_cache(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа")
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "ℹ️ /adduser @username\n\nПример: /adduser @username")
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
            "added_by": ADMIN_ID,
            "added_at": time.time()
        }
        save_users_cache(chat_users)
        
        bot.reply_to(message, f"✅ Пользователь *{user_info.first_name}* (@{user_info.username}) добавлен в кэш!\n🆔 ID: `{user_id}`", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Пользователь @{target} не найден")

# === КЭШ И ИСТОРИЯ ===
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

# === ФУНКЦИЯ ДЛЯ УДАЛЕНИЯ СООБЩЕНИЙ ===
def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

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
            print(f"🔥 Реакция на пост {message_id}")
    except Exception as e:
        print(f"Ошибка реакции: {e}")

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
        print(f"Ошибка сохранения: {e}")

reminders = load_reminders()
reminder_counter = max([r.get("id", 0) for r in reminders]) if reminders else 0

def send_reminder(reminder):
    try:
        bot.send_message(
            reminder["chat_id"], 
            f"⏰ НАПОМИНАНИЕ!\n\n{reminder['text']}", 
            message_thread_id=reminder.get("thread_id")
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def schedule_reminder(reminder):
    now = datetime.now()
    target = now.replace(hour=reminder["hours"], minute=reminder["minutes"], second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    delay = (target - now).total_seconds()
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


# ========== КОМАНДЫ ==========

@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    bot.send_message(message.chat.id, "✅ Бот работает!\n\n"
        "🤖 ИИ: /ai вопрос\n\n"
        "⏰ Напоминания:\n"
        "/remind 15:30 текст - создать\n"
        "/reminds - список\n"
        "/delremind ID - удалить\n\n"
        "💾 Бекап (в ЛС):\n"
        "/backup - создать\n"
        "/restore - восстановить\n\n"
        "📨 Скрытые сообщения:\n"
        "Введите @бот username текст в любом чате\n\n"
        "👥 Пользователи:\n"
        "/users - показать кэш\n"
        "/adduser @username - добавить в кэш")


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
        if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
            raise ValueError
    except:
        msg = bot.send_message(chat_id, "❌ Неверный формат времени. Используйте ЧЧ:ММ", message_thread_id=thread_id)
        delete_after_delay(chat_id, msg.message_id, 10)
        return
    
    daily = False
    if reminder_text.lower().startswith("ежедневно"):
        daily = True
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
    
    period = "каждый день" if daily else "однократное"
    msg = bot.send_message(chat_id, f"✅ Напоминание создано!\n🆔 ID: {reminder_counter}\n⏰ {hours:02d}:{minutes:02d}\n📅 {period}\n📝 {reminder_text}", message_thread_id=thread_id)
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
    
    response = "📋 АКТИВНЫЕ НАПОМИНАНИЯ:\n\n"
    for r in user_reminders:
        period = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}" if r.get("daily") else f"{r['hours']:02d}:{r['minutes']:02d}"
        response += f"🆔 {r['id']} - {period}\n   📝 {r['text'][:40]}\n\n"
    
    response += f"\n💡 Удалить: /delremind ID"
    msg = bot.send_message(chat_id, response, message_thread_id=thread_id)
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


@bot.message_handler(commands=['backup'])
def backup_command(message):
    print(f"🔵 BACKUP от {message.from_user.id}")
    
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
            bot.send_document(
                message.chat.id, 
                f, 
                caption=f"✅ *Бекап создан!*\n\n"
                       f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                       f"📊 Напоминаний: {len(backup_reminders)}\n"
                       f"👥 Пользователей: {len(chat_users)}",
                parse_mode="Markdown"
            )
        
        os.remove(filename)
        bot.delete_message(message.chat.id, status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {str(e)[:200]}", message.chat.id, status_msg.message_id)


@bot.message_handler(commands=['restore'])
def restore_command(message):
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Команда /restore только в ЛС!")
        return
    
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    bot.send_message(
        message.chat.id,
        "📥 *Восстановление из бекапа*\n\n"
        "1️⃣ Отправьте JSON файл бекапа\n"
        "2️⃣ Файл должен начинаться с `backup_` или `full_backup_`\n"
        "3️⃣ Бот восстановит напоминания и пользователей\n\n"
        "📌 *Пример:* `backup_20250430_175218.json`",
        parse_mode="Markdown"
    )


@bot.message_handler(content_types=['document'])
def handle_restore_file(message):
    global chat_users, reminder_counter  # ← В САМОМ НАЧАЛЕ!
    
    print(f"🔵 Получен файл: {message.document.file_name}")
    
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Отправьте файл в ЛС")
        return
    
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    if not (message.document.file_name.startswith("backup_") or message.document.file_name.startswith("full_backup_")):
        bot.reply_to(message, "❌ Это не файл бекапа!\n\nФайл должен начинаться с `backup_` или `full_backup_`", parse_mode="Markdown")
        return
    
    status_msg = bot.reply_to(message, "🔄 Восстанавливаю...")
    
    try:
        file_info = bot.get_file(message.document.file_id)
        file_content = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}").content
        backup_data = json.loads(file_content.decode('utf-8'))
        
        # Останавливаем старые таймеры
        for r in reminders:
            if "_timer" in r:
                try:
                    r["_timer"].cancel()
                except:
                    pass
        
        restored_reminders = 0
        restored_users = 0
        
        # Восстанавливаем напоминания
        if "reminders" in backup_data:
            reminders.clear()
            reminder_counter = 0
            for r in backup_data["reminders"]:
                reminders.append(r)
                if r.get("id", 0) > reminder_counter:
                    reminder_counter = r.get("id", 0)
            save_reminders(reminders)
            start_all_reminders()
            restored_reminders = len(backup_data["reminders"])
        
        elif isinstance(backup_data, list):
            reminders.clear()
            reminder_counter = 0
            for r in backup_data:
                reminders.append(r)
                if r.get("id", 0) > reminder_counter:
                    reminder_counter = r.get("id", 0)
            save_reminders(reminders)
            start_all_reminders()
            restored_reminders = len(backup_data)
        
        # Восстанавливаем пользователей
        if "chat_users" in backup_data:
            chat_users = backup_data["chat_users"]
            save_users_cache(chat_users)
            restored_users = len(backup_data["chat_users"])
            print(f"👥 Восстановлено пользователей: {restored_users}")
        
        bot.edit_message_text(
            f"✅ *Восстановление завершено!*\n\n"
            f"📊 Восстановлено напоминаний: {restored_reminders}\n"
            f"👥 Восстановлено пользователей: {restored_users}",
            message.chat.id, 
            status_msg.message_id,
            parse_mode="Markdown"
        )
        
    except json.JSONDecodeError:
        bot.edit_message_text("❌ Ошибка: Неверный JSON формат", message.chat.id, status_msg.message_id)
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
                    f"❌ Пользователь @{target_raw} не найден\n\n"
                    f"💡 Убедитесь, что username правильный\n"
                    f"📌 Или используйте числовой ID"
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
            "expires": time.time() + 3600
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"secret_read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id,
            title=f"📨 Скрытое сообщение для {target_name}",
            description=content[:50] + ("..." if len(content) > 50 else ""),
            input_message_content=types.InputTextMessageContent(
                f"🔐 *Скрытое сообщение*\n\n"
                f"📤 От: {query.from_user.first_name}\n"
                f"📥 Кому: {target_name}\n"
                f"⏰ Действует: 1 час",
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
        bot.answer_callback_query(call.id, "❌ Это сообщение не для вас!", show_alert=True)
        return
    
    if time.time() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Срок действия истёк (1 час)", show_alert=True)
        del secret_messages[msg_id]
        return
    
    bot.answer_callback_query(
        call.id, 
        f"📩 От {data['sender_name']}:\n\n{data['content']}", 
        show_alert=True
    )


def clean_old_secrets():
    while True:
        time.sleep(3600)
        now = time.time()
        to_delete = [mid for mid, d in secret_messages.items() if d.get("expires", 0) < now]
        for mid in to_delete:
            del secret_messages[mid]
        if to_delete:
            print(f"🧹 Удалено {len(to_delete)} просроченных сообщений")

threading.Thread(target=clean_old_secrets, daemon=True).start()


# ========== ECHO ==========

@bot.message_handler(func=lambda m: True)
def echo(message):
    print(f"📨 ПОЛУЧЕНО: {message.text} от {message.from_user.id}")
    if message.chat.type == 'private' and not message.text.startswith('/'):
        bot.reply_to(message, f"✅ Получено: {message.text[:50]}")


# ========== ВЕБХУК ==========

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        
        if update and "channel_post" in update:
            post = update["channel_post"]
            channel_id = post["chat"]["id"]
            if channel_id in [-1002185590715, -1001317416582]:
                set_reaction(channel_id, post["message_id"])
        
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

import os
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request
from telebot import TeleBot, types

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("🤖 БОТ С НАПОМИНАНИЯМИ ЗАПУЩЕН")

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

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

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

# === КОМАНДЫ ===
@bot.message_handler(commands=['start', 'help', 'test'])
def start_command(message):
    bot.send_message(message.chat.id, "✅ Бот работает!\n\n/remind 15:30 текст - создать напоминание\n/reminds - список\n/delremind ID - удалить\n/backup - бекап")


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
    
    bot.send_message(message.chat.id, "🔄 Создаю бекап...")
    try:
        backup_reminders = []
        for r in reminders:
            r_copy = {}
            for k, v in r.items():
                if k not in ["timer", "_timer"]:
                    r_copy[k] = v
            backup_reminders.append(r_copy)
        
        data = {
            "version": "1.0",
            "date": str(datetime.now()),
            "reminders": backup_reminders
        }
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(data, f)
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=f"✅ БЕКАП\nНапоминаний: {len(backup_reminders)}")
        os.remove(filename)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")


@bot.message_handler(func=lambda m: True)
def echo(message):
    print(f"📨 ПОЛУЧЕНО: {message.text} от {message.from_user.id}")
    if message.chat.type == 'private' and not message.text.startswith('/'):
        bot.reply_to(message, f"✅ Получено: {message.text[:50]}")


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        print(f"Ошибка: {e}")
        return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    import requests
    
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    print("🔄 Установка вебхука...")
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    print(f"Результат: {r.json()}")
    
    start_all_reminders()
    app.run(host="0.0.0.0", port=port)

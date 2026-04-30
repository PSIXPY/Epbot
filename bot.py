import os
import json
from datetime import datetime
from flask import Flask, request
from telebot import TeleBot, types  # ← ДОБАВИЛИ types

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("🤖 МИНИМАЛЬНЫЙ БОТ ЗАПУЩЕН")


@bot.message_handler(commands=['start', 'help', 'test'])
def start_command(message):
    bot.send_message(message.chat.id, "✅ Бот работает!")


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
        data = {
            "time": str(datetime.now()),
            "user_id": message.from_user.id,
            "test": True
        }
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(data, f)
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="✅ БЕКАП")
        os.remove(filename)
        print("🔵 Бекап отправлен")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        print(f"🔴 Ошибка: {e}")


@bot.message_handler(func=lambda m: True)
def echo(message):
    print(f"📨 ПОЛУЧЕНО: {message.text} от {message.from_user.id}")
    if message.chat.type == 'private':
        bot.reply_to(message, f"✅ Получено: {message.text[:50]}")


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    print("🔵 WEBHOOK ВЫЗВАН")
    try:
        update = request.get_json()
        print(f"🔵 UPDATE: {str(update)[:100]}...")
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
            print("🔵 Update обработан")
        return "OK", 200
    except Exception as e:
        print(f"🔴 Ошибка: {e}")
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
    
    app.run(host="0.0.0.0", port=port)

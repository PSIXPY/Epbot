import os
import json
from datetime import datetime
from flask import Flask, request
from telebot import TeleBot

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("🤖 БОТ ЗАПУЩЕН")


@bot.message_handler(commands=['start', 'help', 'test'])
def start(message):
    bot.send_message(message.chat.id, "✅ Бот работает!")


@bot.message_handler(commands=['backup'])
def backup(message):
    if message.chat.type != 'private':
        bot.reply_to(message, "❌ Только в ЛС!")
        return
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав!")
        return
    
    bot.send_message(message.chat.id, "✅ Создаю файл...")
    try:
        data = {"time": str(datetime.now()), "user": message.from_user.id}
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(data, f)
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="✅ БЕКАП")
        os.remove(filename)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        bot.process_new_updates([types.Update.de_json(request.get_json())])
        return "OK", 200
    except Exception as e:
        print(e)
        return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    
    print(f"📡 Webhook: {webhook_url}")
    app.run(host="0.0.0.0", port=port)

import os
import json
from datetime import datetime
from flask import Flask, request
from telebot import TeleBot

# === ПЕРЕМЕННЫЕ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("=" * 50)
print("🤖 БОТ ЗАПУЩЕН")
print(f"BOT_TOKEN: {BOT_TOKEN[:15] if BOT_TOKEN else 'None'}...")
print(f"RENDER_URL: {RENDER_URL}")
print(f"ADMIN_ID: {ADMIN_ID}")
print("=" * 50)


@bot.message_handler(commands=['test'])
def test_command(message):
    print(f"🔵 Команда /test от {message.from_user.id}")
    bot.send_message(message.chat.id, "✅ Команда /test работает!")


@bot.message_handler(commands=['backup'])
def backup_test(message):
    print(f"🔵 Команда /backup от {message.from_user.id}")
    bot.send_message(message.chat.id, "✅ Команда /backup получена! Создаю файл...")
    
    try:
        test_data = {
            "test": True,
            "time": str(datetime.now()),
            "user_id": message.from_user.id
        }
        filename = f"test_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(test_data, f)
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="✅ ТЕСТОВЫЙ БЕКАП")
        os.remove(filename)
        print("🔵 Файл отправлен")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        print(f"🔴 Ошибка: {e}")


@bot.message_handler(commands=['start', 'help'])
def start_command(message):
    bot.send_message(message.chat.id, "✅ Бот работает! Напишите /test или /backup")


@bot.message_handler(func=lambda m: True)
def catch_all(message):
    print(f"🔴🔴🔴 СООБЩЕНИЕ: '{message.text}' | Чат: {message.chat.id} | Тип: {message.chat.type}")
    if message.chat.type == 'private':
        bot.reply_to(message, f"✅ Бот видит сообщение! Получено: {message.text[:50]}")


@app.route('/', methods=['GET'])
def health():
    return "OK", 200


@app.route('/check', methods=['GET'])
def check():
    return "Bot is running!", 200


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    print("🔵🔵🔵 WEBHOOK ВЫЗВАН")
    try:
        update = request.get_json()
        print(f"🔵 UPDATE: {str(update)[:200]}...")
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        print(f"🔴 ОШИБКА: {e}")
        return "OK", 200


if __name__ == "__main__":
    import requests
    
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    print("=" * 50)
    print("🔄 УСТАНОВКА ВЕБХУКА...")
    
    # Удаляем старый вебхук
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    print(f"🗑 deleteWebhook: {r.json()}")
    
    # Устанавливаем новый
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    print(f"📡 setWebhook: {r.json()}")
    
    # Проверяем
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
    print(f"🔍 getWebhookInfo: {r.json()}")
    print("=" * 50)
    
    app.run(host="0.0.0.0", port=port)

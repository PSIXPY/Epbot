import os
from flask import Flask, request
from telebot import TeleBot

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("=" * 50)
print("ТЕСТОВЫЙ БОТ ЗАПУЩЕН")
print(f"Токен: {BOT_TOKEN[:15] if BOT_TOKEN else 'None'}...")
print(f"URL: {RENDER_URL}")
print("=" * 50)


@bot.message_handler(func=lambda m: True)
def echo(message):
    print(f"🔴 ПОЛУЧЕНО: {message.text} от {message.from_user.id}")
    try:
        bot.reply_to(message, f"✅ Эхо: {message.text[:100]}")
    except Exception as e:
        print(f"Ошибка ответа: {e}")


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    print("🔵 WEBHOOK ВЫЗВАН")
    try:
        update = request.get_json()
        print(f"🔵 UPDATE: {str(update)[:200]}")
        if update:
            bot.process_new_updates([types.Update.de_json(update)])
            print("🔵 Обработано")
        return "OK", 200
    except Exception as e:
        print(f"🔴 ОШИБКА: {e}")
        return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    import requests
    
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    print("🔄 Установка вебхука...")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    print(f"Удаление: {r.json()}")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")
    print(f"Установка: {r.json()}")
    print("=" * 50)
    
    app.run(host="0.0.0.0", port=port)

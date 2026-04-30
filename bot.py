import os
from flask import Flask, request
from telebot import TeleBot

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

print("БОТ ЗАПУЩЕН")

@app.route('/', methods=['GET'])
def health():
    return "OK", 200

@app.route('/check', methods=['GET'])
def check():
    return "OK", 200

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        bot.process_new_updates([types.Update.de_json(request.get_json())])
        return "OK", 200
    except Exception as e:
        print(e)
        return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{BOT_TOKEN}")
    app.run(host="0.0.0.0", port=port)

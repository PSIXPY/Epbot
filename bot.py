import os
import logging
import requests
import wikipediaapi
import random
import time
import threading
import re
import urllib.parse
from datetime import datetime
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# === ПЕРЕМЕННЫЕ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
bot = TeleBot(BOT_TOKEN)
message_links = {}
secret_messages = {}


# === ФУНКЦИЯ ДЛЯ GROQ ===
def ask_groq(prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "qwen/qwen3-32b",
        "messages": [{"role": "system", "content": "Отвечай кратко, по существу."}, {"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            answer = re.sub(r'<think>.*?</think>|/think.*?/think|<think/?>|</think>|/think', '', answer, flags=re.DOTALL)
            return answer.strip() or "🤔 Не удалось сформулировать ответ."
        elif response.status_code == 429:
            return "⚠️ Лимит запросов к ИИ исчерпан! Подождите 1 минуту."
        return f"❌ Ошибка Groq: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


# === ФУНКЦИЯ ПОИСКА В ВИКИПЕДИИ ===
def search_wikipedia(query):
    try:
        wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki.page(query)
        if page.exists():
            summary = page.summary[:500]
            if len(page.summary) > 500:
                summary += "..."
            return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        resp = requests.get("https://ru.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": 1, "srwhat": "text"
        }, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("query", {}).get("search"):
                best = data["query"]["search"][0]["title"]
                page = wiki.page(best)
                if page.exists():
                    summary = page.summary[:500]
                    if len(page.summary) > 500:
                        summary += "..."
                    return f"📖 *{page.title}*\n\n{summary}\n\n[🔗 Читать полностью]({page.fullurl})"
        
        encoded_query = urllib.parse.quote(query)
        google_link = f"https://www.google.com/search?q={encoded_query}"
        yandex_link = f"https://yandex.ru/search/?text={encoded_query}"
        
        return f"""❌ *Ничего не найдено в Википедии* по запросу: "{query}"

💡 *Попробуйте поискать в интернете:*

🔍 [Google]({google_link})
🌐 [Яндекс]({yandex_link})

💬 *Совет:* Для Википедии используйте название статьи, а не вопрос."""
        
    except Exception as e:
        logger.error(f"Wikipedia error: {e}")
        return f"❌ Ошибка при поиске: {str(e)[:100]}"


# === КОМАНДЫ ===
@bot.message_handler(commands=['ai'])
def ai_cmd(message):
    prompt = message.text[3:].strip()
    if not prompt:
        bot.reply_to(message, "ℹ️ `/ai Как дела?`", parse_mode="Markdown")
        return
    msg = bot.reply_to(message, "🤖 Думаю...", parse_mode="Markdown")
    answer = ask_groq(prompt)
    bot.edit_message_text(answer, message.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['wiki'])
def wiki_cmd(message):
    query = message.text[5:].strip()
    if not query:
        bot.reply_to(message, "ℹ️ `/wiki Python`", parse_mode="Markdown")
        return
    result = search_wikipedia(query)
    bot.reply_to(message, result, parse_mode="Markdown")

@bot.message_handler(commands=['roll'])
def roll_cmd(message):
    bot.reply_to(message, f"🎲 {random.randint(1, 100)}")

@bot.message_handler(commands=['coin'])
def coin_cmd(message):
    bot.reply_to(message, f"🪙 {random.choice(['Орёл', 'Решка'])}")

@bot.message_handler(commands=['help', 'start'])
def help_cmd(message):
    help_text = """📖 *Команды бота*

/wiki [запрос] — поиск в Википедии
/ai [запрос] — общение с ИИ
/roll — случайное число (1-100)
/coin — орёл/решка
/help — эта справка

📩 *Скрытые сообщения:*
`@имя_бота @получатель текст`

🔄 *Автоматически:* пересылка сообщений между чатами и 🔥 на новые посты в каналах"""
    bot.reply_to(message, help_text, parse_mode="Markdown")


# === ПЕРЕСЫЛКА СООБЩЕНИЙ ===
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def forward_to_b(message):
    bot.forward_message(CHAT_B, message.chat.id, message.message_id, message_thread_id=CHAT_B_THREAD)
    logger.info(f"Переслано из A в B")

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def forward_to_a(message):
    bot.forward_message(CHAT_A, message.chat.id, message.message_id)
    logger.info(f"Переслано из B в A")


# === ПОСТЫ В КАНАЛАХ (реакция 🔥) ===
@bot.channel_post_handler(func=lambda m: m.chat.id in [-1001317416582, -1002185590715])
def channel_reaction(message):
    try:
        bot.set_message_reaction(message.chat.id, message.message_id, reaction=[types.ReactionTypeEmoji(emoji="🔥")])
    except:
        pass


# === СКРЫТЫЕ СООБЩЕНИЯ ===
@bot.inline_handler(func=lambda query: True)
def inline_query(query):
    try:
        text = query.query.strip()
        if not text:
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return
        target = parts[0].lstrip("@")
        content = parts[1]
        msg_id = f"sec_{int(datetime.now().timestamp() * 1000)}"
        
        secret_messages[msg_id] = {
            "target": target, "content": content, "sender": query.from_user.first_name,
            "expires": datetime.now().timestamp() + 300
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"read_{msg_id}"))
        
        result = types.InlineQueryResultArticle(
            id=msg_id, title=f"Отправить @{target}", description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔔 *Скрытое сообщение* от {query.from_user.first_name} для @{target}",
                parse_mode="Markdown"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(query.id, [result], cache_time=0)
    except Exception as e:
        logger.error(f"Inline error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("read_"))
def read_secret(call):
    msg_id = call.data[5:]
    if msg_id not in secret_messages:
        bot.answer_callback_query(call.id, "❌ Сообщение устарело", show_alert=True)
        return
    data = secret_messages[msg_id]
    if call.from_user.username != data["target"]:
        bot.answer_callback_query(call.id, "❌ Не для вас", show_alert=True)
        return
    if datetime.now().timestamp() > data["expires"]:
        bot.answer_callback_query(call.id, "❌ Сообщение устарело", show_alert=True)
        del secret_messages[msg_id]
        return
    del secret_messages[msg_id]
    bot.delete_message(call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, f"📩 {data['content']}", show_alert=True)


# === ОЧИСТКА УСТАРЕВШИХ ===
def clean_expired():
    while True:
        time.sleep(86400)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]

threading.Thread(target=clean_expired, daemon=True).start()


# === ПРОГРЕВ БОТА ===
def warmup():
    """Прогревает бота, чтобы первый запрос не был холодным"""
    logger.info("🔥 Прогреваю бота...")
    
    if GROQ_API_KEY:
        try:
            test_result = ask_groq("ok")
            logger.info(f"✅ Groq API прогрето")
        except Exception as e:
            logger.warning(f"⚠️ Groq API не прогрето: {e}")
    
    try:
        bot.get_me()
        logger.info("✅ Telegram API прогрето")
    except Exception as e:
        logger.warning(f"⚠️ Telegram API не прогрето: {e}")
    
    logger.info("🔥 Бот готов к работе!")


# === ВЕБХУК ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


# === ЗАПУСК ===
if __name__ == "__main__":
    # Прогреваем бота перед запуском
    warmup()
    
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик: {CHAT_B_THREAD}")
    logger.info(f"Вебхук: {webhook_url}")
    logger.info("Команды: /ai, /wiki, /roll, /coin, /help")
    
    app.run(host="0.0.0.0", port=port)

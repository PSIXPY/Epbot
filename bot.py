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

# Для кэширования ответов AI
ai_cache = {}
CACHE_TTL = 3600  # 1 час

# Для истории диалогов
user_histories = {}
MAX_HISTORY = 10  # храним последние 10 сообщений на пользователя


# === ФУНКЦИЯ ДЛЯ GROQ (с историей, кэшем и увеличенными токенами) ===
def ask_groq(user_id, prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    
    # === КЭШИРОВАНИЕ ===
    cache_key = hashlib.md5(prompt.lower().encode()).hexdigest()
    if cache_key in ai_cache:
        cached_time, cached_answer = ai_cache[cache_key]
        if time.time() - cached_time < CACHE_TTL:
            logger.info(f"💾 Кэш: ответ на '{prompt[:30]}...' взят из кэша")
            return cached_answer
    
    now = time.time()
    
    # === ИСТОРИЯ ДИАЛОГОВ ===
    if user_id not in user_histories:
        user_histories[user_id] = {"messages": [], "created": now}
    
    # Добавляем вопрос в историю
    user_histories[user_id]["messages"].append({"role": "user", "content": prompt, "timestamp": now})
    
    # Оставляем только последние MAX_HISTORY сообщений
    if len(user_histories[user_id]["messages"]) > MAX_HISTORY:
        user_histories[user_id]["messages"] = user_histories[user_id]["messages"][-MAX_HISTORY:]
    
    # Формируем сообщения с историей
    messages = [
        {"role": "system", "content": "Ты — полезный, дружелюбный ассистент. Отвечай кратко, по существу, учитывая контекст предыдущих сообщений."}
    ] + [{"role": msg["role"], "content": msg["content"]} for msg in user_histories[user_id]["messages"]]
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "qwen/qwen3-32b",
        "messages": messages,
        "max_tokens": 800,  # УВЕЛИЧЕНО с 500 до 800
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            answer = response.json()["choices"][0]["message"]["content"]
            # Очистка от тегов think
            answer = re.sub(r'<think>.*?</think>|/think.*?/think|<think/?>|</think>|/think', '', answer, flags=re.DOTALL)
            answer = answer.strip()
            
            # Сохраняем ответ в историю
            user_histories[user_id]["messages"].append({"role": "assistant", "content": answer, "timestamp": now})
            
            # Сохраняем в кэш
            ai_cache[cache_key] = (time.time(), answer)
            
            return answer if answer else "🤔 Не удалось сформулировать ответ."
        elif response.status_code == 429:
            return "⚠️ Лимит запросов к ИИ исчерпан! Подождите 1 минуту."
        return f"❌ Ошибка Groq: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


# === АВТООЧИСТКА ИСТОРИИ (РАЗ В ЧАС, УДАЛЕНИЕ СТАРШЕ 2 ДНЕЙ) ===
def clean_expired_histories():
    """Очищает историю пользователей старше 2 дней"""
    while True:
        time.sleep(3600)  # Проверяем раз в час
        now = time.time()
        expired_users = []
        for user_id, data in user_histories.items():
            # Если история старше 2 дней (172800 секунд)
            if now - data.get("created", now) > 172800:
                expired_users.append(user_id)
            else:
                # Удаляем старые сообщения внутри истории
                original_count = len(data["messages"])
                data["messages"] = [msg for msg in data["messages"] 
                                    if now - msg.get("timestamp", now) < 172800]
                if len(data["messages"]) != original_count:
                    logger.info(f"🧹 Удалено {original_count - len(data['messages'])} старых сообщений у пользователя {user_id}")
        
        for user_id in expired_users:
            del user_histories[user_id]
        if expired_users:
            logger.info(f"🧹 Полностью удалена история {len(expired_users)} пользователей (старше 2 дней)")

# Запускаем очистку в фоновом потоке
threading.Thread(target=clean_expired_histories, daemon=True).start()


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
    user_id = message.from_user.id
    msg = bot.reply_to(message, "🤖 Думаю...", parse_mode="Markdown")
    answer = ask_groq(user_id, prompt)
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
/ai [запрос] — общение с ИИ (с памятью 2 дня)
/roll — случайное число (1-100)
/coin — орёл/решка
/help — эта справка

📩 *Скрытые сообщения:*
`@имя_бота @получатель текст`

🔄 *Автоматически:* пересылка сообщений между чатами и 🔥 на новые посты в каналах

🧠 *AI запоминает:* последние 10 сообщений диалога (история хранится 2 дня)"""
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    user_id = message.from_user.id
    if user_id in user_histories:
        del user_histories[user_id]
        bot.reply_to(message, "🗑️ История ваших диалогов очищена!")
    else:
        bot.reply_to(message, "📭 У вас нет сохранённой истории.")


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


# === ОЧИСТКА УСТАРЕВШИХ СКРЫТЫХ СООБЩЕНИЙ ===
def clean_expired_secrets():
    while True:
        time.sleep(86400)
        now = datetime.now().timestamp()
        expired = [mid for mid, d in secret_messages.items() if d.get("expires", now) < now]
        for mid in expired:
            del secret_messages[mid]
        if expired:
            logger.info(f"🧹 Удалено {len(expired)} устаревших скрытых сообщений")

threading.Thread(target=clean_expired_secrets, daemon=True).start()


# === ПРОГРЕВ БОТА ===
def warmup():
    """Прогревает бота, чтобы первый запрос не был холодным"""
    logger.info("🔥 Прогреваю бота...")
    
    if GROQ_API_KEY:
        try:
            test_result = ask_groq(0, "ok")
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
    logger.info("Команды: /ai, /wiki, /roll, /coin, /help, /clear_history")
    logger.info("🧠 AI: история 2 дня, кэш 1 час, max_tokens=800")
    
    app.run(host="0.0.0.0", port=port)

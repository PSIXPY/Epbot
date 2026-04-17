import os
import time
import logging
from flask import Flask, request
from telebot import TeleBot, types

# === КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ СРЕДЫ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")

# === ИНИЦИАЛИЗАЦИЯ ===
bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище ID обработанных сообщений (защита от циклов)
processed_ids = set()

def get_sender_name(message: types.Message) -> str:
    """Возвращает красивое имя отправителя"""
    user = message.from_user
    if not user:
        return "Неизвестный"
    
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "Пользователь"
    
    if user.username:
        return f"{name} (@{user.username})"
    return name

def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2"""
    special_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in special_chars else c for c in text)

def forward_message(message: types.Message, target_chat_id: int, thread_id: int = None):
    """Пересылает сообщение в целевой чат, добавляя подпись отправителя"""
    if message.message_id in processed_ids:
        return
    processed_ids.add(message.message_id)
    if len(processed_ids) > 1000:
        processed_ids.clear()
    
    try:
        # 1. Формируем подпись с именем отправителя
        sender_name = get_sender_name(message)
        caption_text = f"📨 **От:** {escape_md(sender_name)}"
        
        # 2. Пересылаем ОРИГИНАЛЬНОЕ сообщение (сохраняет стикеры, фото, всё)
        # Это ключевое изменение! Вместо send_sticker используем forward_message
        bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=thread_id
        )
        
        # 3. Отправляем подпись отдельным сообщением (с небольшой задержкой)
        # Это нужно, чтобы подпись не "прилипла" к пересланному стикеру
        time.sleep(0.5)
        bot.send_message(
            chat_id=target_chat_id,
            text=caption_text,
            parse_mode="MarkdownV2",
            message_thread_id=thread_id
        )
        
        # Небольшая пауза, чтобы не уйти во флуд-лимиты
        time.sleep(1)
        
    except Exception as e:
        logger.error(f"Ошибка при пересылке: {e}")


# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===

# Из чата A → в тему канала B
@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    forward_message(message, CHAT_B, CHAT_B_THREAD)

# Из нужной темы канала B → в чат A
@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id == CHAT_B_THREAD)
def handle_chat_b_thread(message):
    forward_message(message, CHAT_A)

# Игнорируем сообщения из других тем канала B
@bot.message_handler(func=lambda m: m.chat.id == CHAT_B and m.message_thread_id != CHAT_B_THREAD)
def ignore_other_threads(message):
    pass  # Ничего не делаем


# === ВЕБХУК ДЛЯ RENDER ===

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Bad request", 400

@app.route("/", methods=["GET"])
def healthcheck():
    return "OK", 200


# === ТОЧКА ВХОДА ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Настройка вебхука при запуске
    bot.remove_webhook()
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    bot.set_webhook(url=webhook_url)
    
    logger.info(f"🤖 Бот запущен")
    logger.info(f"   Чат A: {CHAT_A}")
    logger.info(f"   Канал B: {CHAT_B}")
    logger.info(f"   Тема B: {CHAT_B_THREAD}")
    logger.info(f"   Вебхук: {webhook_url}")
    
    # Запускаем Flask-сервер
    app.run(host="0.0.0.0", port=port)

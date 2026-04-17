import os
import time
import logging
from telebot import TeleBot, types

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ СРЕДЫ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
DELAY = float(os.environ.get("DELAY", 1))

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)
processed_ids = set()

def get_sender_name(message: types.Message) -> str:
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
    special = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in special else c for c in text)

def forward_message(message: types.Message, target_chat_id: int):
    if message.message_id in processed_ids:
        return
    processed_ids.add(message.message_id)
    if len(processed_ids) > 1000:
        processed_ids.clear()
    
    try:
        sender = get_sender_name(message)
        header = f"📨 **От:** {escape_md(sender)}\n\n"
        
        if message.caption:
            content = message.caption
        elif message.text:
            content = message.text
        else:
            content = "📎 *Медиафайл*"
        
        full_text = header + escape_md(content)
        
        if message.photo:
            bot.send_photo(target_chat_id, message.photo[-1].file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.video:
            bot.send_video(target_chat_id, message.video.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.document:
            bot.send_document(target_chat_id, message.document.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.audio:
            bot.send_audio(target_chat_id, message.audio.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.voice:
            bot.send_voice(target_chat_id, message.voice.file_id, caption=full_text, parse_mode="MarkdownV2")
        elif message.sticker:
            bot.send_sticker(target_chat_id, message.sticker.file_id)
            if full_text:
                bot.send_message(target_chat_id, full_text, parse_mode="MarkdownV2")
        elif message.video_note:
            bot.send_video_note(target_chat_id, message.video_note.file_id)
            bot.send_message(target_chat_id, f"📨 **От:** {escape_md(sender)}\n\n🎥 *Видеосообщение*", parse_mode="MarkdownV2")
        elif message.animation:
            caption = f"📨 **От:** {escape_md(sender)}\n\n"
            if message.caption:
                caption += escape_md(message.caption)
            bot.send_animation(target_chat_id, message.animation.file_id, caption=caption, parse_mode="MarkdownV2")
        elif message.poll:
            bot.send_poll(target_chat_id, message.poll.question, [opt.text for opt in message.poll.options])
            bot.send_message(target_chat_id, f"📨 **От:** {escape_md(sender)}", parse_mode="MarkdownV2")
        elif message.location:
            bot.send_location(target_chat_id, message.location.latitude, message.location.longitude)
            bot.send_message(target_chat_id, f"📨 **От:** {escape_md(sender)}", parse_mode="MarkdownV2")
        elif message.contact:
            bot.send_contact(target_chat_id, message.contact.phone_number, message.contact.first_name, last_name=message.contact.last_name)
            bot.send_message(target_chat_id, f"📨 **От:** {escape_md(sender)}", parse_mode="MarkdownV2")
        else:
            bot.send_message(target_chat_id, full_text, parse_mode="MarkdownV2")
        
        time.sleep(DELAY)
    except Exception as e:
        logger.error(f"Ошибка: {e}")

@bot.message_handler(func=lambda m: m.chat.id == CHAT_A)
def handle_chat_a(message):
    forward_message(message, CHAT_B)

@bot.message_handler(func=lambda m: m.chat.id == CHAT_B)
def handle_chat_b(message):
    forward_message(message, CHAT_A)

if __name__ == "__main__":
    logger.info(f"🤖 Бот запущен | Чат A: {CHAT_A} | Чат B: {CHAT_B}")
    bot.infinity_polling()
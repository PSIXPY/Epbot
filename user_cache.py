# user_cache.py - отдельный файл для кэша и сбора пользователей
import os
import json
import time
from telebot import TeleBot

# === ПЕРЕМЕННЫЕ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

bot = TeleBot(BOT_TOKEN)

# === КЭШ ПОЛЬЗОВАТЕЛЕЙ ===
USERS_CACHE_FILE = "chat_users.json"

def load_users_cache():
    if os.path.exists(USERS_CACHE_FILE):
        try:
            with open(USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users_cache(users):
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(users)} пользователей в кэш")
    except Exception as e:
        print(f"Ошибка сохранения кэша: {e}")

chat_users = load_users_cache()


def save_user(user, source=""):
    """Сохраняет пользователя в кэш"""
    if not user or user.id == bot.get_me().id:
        return False
    user_id = str(user.id)
    was_new = user_id not in chat_users
    chat_users[user_id] = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "last_seen": time.time()
    }
    if was_new:
        print(f"📝 НОВЫЙ: {user.first_name} (@{user.username}) - {source}")
        save_users_cache(chat_users)
    return was_new


# === СБОР ИЗ СООБЩЕНИЙ (С ДИАГНОСТИКОЙ) ===
@bot.message_handler(content_types=['text'])
def collect_from_messages(message):
    print(f"🔴🔴🔴 ХЕНДЛЕР СРАБОТАЛ: {message.text[:50] if message.text else 'Нет текста'}")
    print(f"    Чат: {message.chat.id}, Тип: {message.chat.type}")
    
    # Только группы
    if message.chat.type not in ['group', 'supergroup']:
        print(f"⚠️ Пропускаем: чат не группа (тип: {message.chat.type})")
        return
    
    # Пропускаем команды
    if message.text and message.text.startswith('/'):
        print(f"⚠️ Пропускаем: это команда {message.text}")
        return
    
    print(f"✅ Сохраняем автора сообщения...")
    
    # Сохраняем автора
    if message.from_user:
        save_user(message.from_user, "написал сообщение")
    else:
        print("⚠️ message.from_user отсутствует")
    
    # Сохраняем того, кому ответили
    if message.reply_to_message and message.reply_to_message.from_user:
        print(f"✅ Сохраняем того, кому ответили...")
        save_user(message.reply_to_message.from_user, "ответили на сообщение")


# === НОВЫЕ УЧАСТНИКИ ===
@bot.message_handler(content_types=['new_chat_members'])
def handle_new_member(message):
    print(f"🔵 НОВЫЙ УЧАСТНИК: {message.new_chat_members}")
    for new_member in message.new_chat_members:
        if new_member.id == bot.get_me().id:
            continue
        save_user(new_member, "вступил в чат")


# === КОМАНДА /users ===
@bot.message_handler(commands=['users'])
def show_users(message):
    print(f"🟢 КОМАНДА /users от {message.from_user.id}")
    
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа")
        return
    if not chat_users:
        bot.reply_to(message, "📭 Кэш пуст")
        return
    result = f"👥 *Пользователей в кэше:* {len(chat_users)}\n\n"
    users_list = []
    for uid, data in list(chat_users.items())[:30]:
        username = data.get('username', 'нет')
        name = data.get('first_name', 'Неизвестный')
        users_list.append(f"• {name} (@{username}) - ID: `{uid}`")
    result += "\n".join(users_list)
    if len(chat_users) > 30:
        result += f"\n\n... и еще {len(chat_users) - 30}"
    bot.reply_to(message, result, parse_mode="Markdown")


# === КОМАНДА /adduser ===
@bot.message_handler(commands=['adduser'])
def add_user_to_cache(message):
    print(f"🟢 КОМАНДА /adduser от {message.from_user.id}")
    
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "ℹ️ /adduser @username")
        return
    target = args[1].lstrip("@")
    try:
        user_info = bot.get_chat(target)
        save_user(user_info, "добавлен админом")
        bot.reply_to(message, f"✅ *{user_info.first_name}* (@{user_info.username}) добавлен!\n🆔 `{user_info.id}`", parse_mode="Markdown")
    except:
        bot.reply_to(message, f"❌ Пользователь @{target} не найден")


print("🔄 Модуль кэша пользователей загружен")

import json
import os
from datetime import datetime
import pytz

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
USERS_CACHE_FILE = "chat_users.json"

def load_users():
    """Загружает пользователей из файла"""
    if os.path.exists(USERS_CACHE_FILE):
        try:
            with open(USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users(users):
    """Сохраняет пользователей в файл"""
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(users)} пользователей в кэш")
    except Exception as e:
        print(f"Ошибка сохранения: {e}")

def save_user_from_message(message, chat_users):
    """Сохраняет пользователя из сообщения и выводит в лог"""
    user = message.from_user
    if not user:
        return chat_users
    
    user_id = str(user.id)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Проверяем, новый это пользователь или обновление
    is_new = user_id not in chat_users
    
    chat_users[user_id] = {
        "id": user.id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    
    # Выводим в лог с меткой времени
    time_now = datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')
    if is_new:
        print(f"👤 [{time_now}] НОВЫЙ пользователь: @{username} ({first_name}) [ID: {user_id}]")
    else:
        print(f"👤 [{time_now}] Обновлён: @{username} ({first_name}) [ID: {user_id}]")
    
    return chat_users

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
                users = json.load(f)
                print(f"📂 [user_handler] Загружено {len(users)} пользователей")
                return users
        except Exception as e:
            print(f"❌ [user_handler] Ошибка загрузки: {e}")
            return {}
    print("📂 [user_handler] Файл chat_users.json не найден")
    return {}

def save_users(users):
    """Сохраняет пользователей в файл"""
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 [user_handler] Сохранено {len(users)} пользователей")
        return True
    except Exception as e:
        print(f"❌ [user_handler] Ошибка сохранения: {e}")
        return False

def save_user_from_message(message, chat_users):
    """Сохраняет пользователя из сообщения"""
    user = message.from_user
    if not user:
        return chat_users
    
    user_id = str(user.id)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Проверяем наличие нижнего подчёркивания
    has_underscore = "_" in username if username else False
    print(f"🔍 [user_handler] USERNAME: '{username}' | Есть _ : {has_underscore}")
    
    is_new = user_id not in chat_users
    old_username = chat_users.get(user_id, {}).get("username") if not is_new else None
    
    if not is_new and old_username != username:
        print(f"🔄 [user_handler] Обновление username: '{old_username}' → '{username}'")
    
    chat_users[user_id] = {
        "id": user.id,
        "username": username,  # Сохраняем как есть, без изменений
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat(),
        "has_underscore": has_underscore  # Добавляем флаг
    }
    
    save_users(chat_users)
    
    if is_new:
        print(f"🆕 [user_handler] НОВЫЙ: @{username} ({first_name}) [{has_underscore}]")
    else:
        print(f"✅ [user_handler] ОБНОВЛЁН: @{username} ({first_name}) [{has_underscore}]")
    
    return chat_users

def fix_username(chat_users, user_id, correct_username):
    """Принудительно исправляет username (например, добавить _)"""
    if user_id in chat_users:
        old = chat_users[user_id].get("username")
        chat_users[user_id]["username"] = correct_username
        save_users(chat_users)
        print(f"🔧 [user_handler] ПРИНУДИТЕЛЬНО: '{old}' → '{correct_username}'")
        return True
    return False

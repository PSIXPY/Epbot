import json
import os
from datetime import datetime
import pytz

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
USERS_CACHE_FILE = "chat_users.json"

def load_users():
    if os.path.exists(USERS_CACHE_FILE):
        try:
            with open(USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                users = json.load(f)
                print(f"📂 Загружено {len(users)} пользователей")
                return users
        except Exception as e:
            print(f"❌ Ошибка загрузки: {e}")
            return {}
    print("📂 Файл chat_users.json не найден")
    return {}

def save_users(users):
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(users)} пользователей")
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")
        return False

def save_user_from_message(message, chat_users):
    user = message.from_user
    if not user:
        return chat_users
    
    user_id = str(user.id)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    is_new = user_id not in chat_users
    old_username = chat_users.get(user_id, {}).get("username") if not is_new else None
    
    if not is_new and old_username != username:
        print(f"🔄 Обновление username: '{old_username}' → '{username}'")
    
    chat_users[user_id] = {
        "id": user.id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    print(f"✅ Сохранён: @{username} ({first_name})")
    
    return chat_users

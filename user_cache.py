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
                return json.load(f)
        except:
            return {}
    return {}

def save_users(users):
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def save_user_from_message(message, chat_users):
    user = message.from_user
    if not user:
        return chat_users
    
    user_id = str(user.id)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Проверка на подчёркивание
    if username and "_" in username:
        print(f"✅ Username с _ : @{username}")
    elif username:
        print(f"⚠️ Username БЕЗ _ : @{username}")
    
    chat_users[user_id] = {
        "id": user.id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    return chat_users

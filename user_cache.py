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
            print(f"❌ Ошибка: {e}")
            return {}
    return {}

def save_users(users):
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(users)} пользователей")
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def save_user_from_message(message, chat_users):
    """Сохраняет пользователя - принудительно обновляет username"""
    user = message.from_user
    if not user:
        return chat_users
    
    user_id = str(user.id)
    
    # Получаем свежие данные от Telegram
    new_username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Отладочный вывод - показывает есть ли _ в username
    if new_username:
        has_underscore = "_" in new_username
        print(f"🔍 [CACHE] Username: '{new_username}' | Есть _ : {has_underscore}")
    else:
        print(f"🔍 [CACHE] Username: None (нет username)")
    
    # Проверяем старого пользователя
    is_new = user_id not in chat_users
    old_username = chat_users.get(user_id, {}).get("username") if not is_new else None
    
    # ВСЕГДА обновляем данные (принудительно)
    chat_users[user_id] = {
        "id": user.id,
        "username": new_username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    
    # Логируем изменения
    if is_new:
        if new_username:
            print(f"🆕 НОВЫЙ: @{new_username} ({first_name})")
        else:
            print(f"🆕 НОВЫЙ: {first_name} (без username)")
    elif old_username != new_username:
        print(f"🔄 ОБНОВЛЁН: @{old_username} → @{new_username} ({first_name})")
    else:
        if new_username:
            print(f"✅ Обновлён: @{new_username} ({first_name})")
        else:
            print(f"✅ Обновлён: {first_name} (без username)")
    
    return chat_users

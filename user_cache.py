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
                print(f"📂 Загружено {len(users)} пользователей")
                return users
        except Exception as e:
            print(f"❌ Ошибка загрузки: {e}")
            return {}
    print("📂 Файл chat_users.json не найден")
    return {}

def save_users(users):
    """Сохраняет пользователей в файл"""
    try:
        with open(USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        print(f"💾 Сохранено {len(users)} пользователей")
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")
        return False

def save_user_from_message(message, chat_users):
    """Сохраняет пользователя из сообщения - ВСЕГДА ОБНОВЛЯЕТ username"""
    user = message.from_user
    if not user:
        print("⚠️ Нет информации о пользователе")
        return chat_users
    
    user_id = str(user.id)
    
    # Получаем данные от Telegram (свежие!)
    username = user.username if user.username else None
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Проверяем, был ли изменён username
    old_username = chat_users.get(user_id, {}).get("username") if user_id in chat_users else None
    if old_username != username:
        print(f"🔄 Обновление username: '{old_username}' → '{username}' для {first_name}")
    
    # ВСЕГДА обновляем данные
    chat_users[user_id] = {
        "id": user.id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip(),
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    
    print(f"✅ Сохранён: @{username} ({first_name}) [ID: {user_id}]")
    
    return chat_users

def add_user_manual(chat_users, username, user_id=None):
    """Ручное добавление пользователя"""
    import time
    
    if user_id is None:
        user_id = f"manual_{int(time.time())}"
    
    user_id_str = str(user_id)
    
    if user_id_str in chat_users:
        return False, "Пользователь уже существует"
    
    chat_users[user_id_str] = {
        "id": user_id_str,
        "username": username,
        "first_name": username,
        "last_name": "",
        "full_name": username,
        "last_seen": datetime.now(MOSCOW_TZ).isoformat()
    }
    
    save_users(chat_users)
    return True, f"Пользователь @{username} добавлен"

def delete_user(chat_users, identifier):
    """Удаляет пользователя из кэша"""
    found_id = None
    
    for uid, user in chat_users.items():
        if user.get('username') == identifier:
            found_id = uid
            break
        if str(user.get('id')) == identifier:
            found_id = uid
            break
        if uid == identifier:
            found_id = uid
            break
    
    if found_id:
        deleted_user = chat_users.pop(found_id)
        save_users(chat_users)
        return True, deleted_user
    
    return False, None

def get_all_users(chat_users):
    """Возвращает всех пользователей в виде списка"""
    users_list = []
    for uid, user in chat_users.items():
        users_list.append({
            "id": uid,
            "username": user.get('username', 'нет'),
            "name": user.get('full_name', user.get('first_name', 'Без имени')),
            "last_seen": user.get('last_seen', 'неизвестно')
        })
    return users_list

def get_user_count(chat_users):
    return len(chat_users)

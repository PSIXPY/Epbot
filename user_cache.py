# user_cache.py - модуль для кэша пользователей
import os
import json
import time

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
    if not user:
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


print("🔄 Модуль кэша пользователей загружен")

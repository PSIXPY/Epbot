import json
import os
from datetime import datetime
import pytz

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
USERS_CACHE_FILE = "chat_users.json"

def log_and_check_underscore(message):
    """Логирует сообщение и проверяет наличие _ в username"""
    user = message.from_user
    if not user:
        print("⚠️ Нет пользователя")
        return
    
    user_id = user.id
    username = user.username if user.username else "None"
    first_name = user.first_name or ""
    
    # Проверяем наличие подчёркивания
    has_underscore = "_" in username if username != "None" else False
    
    # ВЫВОДИМ В ЛОГ!
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📨 СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ")
    print(f"   ID: {user_id}")
    print(f"   Username: @{username}")
    print(f"   Имя: {first_name}")
    print(f"   Есть _ : {'✅ ДА' if has_underscore else '❌ НЕТ'}")
    print(f"   Время: {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Если нет подчёркивания, но должно быть - предупреждение
    if not has_underscore and username != "None" and len(username) > 5:
        print(f"⚠️ ВНИМАНИЕ: В username '{username}' нет нижнего подчёркивания!")
    
    return has_underscore

def check_user_in_cache(user_id):
    """Проверяет, есть ли пользователь в кэше"""
    if os.path.exists(USERS_CACHE_FILE):
        try:
            with open(USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                users = json.load(f)
                if str(user_id) in users:
                    print(f"✅ Пользователь {user_id} есть в кэше")
                    return True
                else:
                    print(f"❌ Пользователь {user_id} НЕ найден в кэше")
                    return False
        except:
            return False
    return False

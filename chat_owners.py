import json
import os

CHAT_OWNERS_FILE = "chat_owners.json"
chat_owners = {}

def load_chat_owners():
    global chat_owners
    if os.path.exists(CHAT_OWNERS_FILE):
        try:
            with open(CHAT_OWNERS_FILE, 'r', encoding='utf-8') as f:
                chat_owners = json.load(f)
                print(f"👑 Загружено {len(chat_owners)} чатов с владельцами")
        except:
            chat_owners = {}
    return chat_owners

def save_chat_owners():
    try:
        with open(CHAT_OWNERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(chat_owners, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка сохранения chat_owners: {e}")

def set_chat_owner(chat_id, user_id):
    """Устанавливает владельца чата (того, кто добавил бота)"""
    chat_owners[str(chat_id)] = user_id
    save_chat_owners()
    print(f"👑 Чат {chat_id} теперь принадлежит пользователю {user_id}")

def get_chat_owner(chat_id):
    """Возвращает ID владельца чата или None"""
    return chat_owners.get(str(chat_id))

def is_chat_owner(chat_id, user_id):
    """Проверяет, является ли пользователь владельцем чата"""
    owner = get_chat_owner(chat_id)
    return owner == user_id

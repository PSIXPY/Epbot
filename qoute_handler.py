import os
import json
import time
import threading
import random
from datetime import datetime, timedelta
import pytz

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
QUOTES_CACHE_FILE = "daily_quotes.json"

# Глобальные переменные
daily_messages = []
daily_quote_times = [9, 12, 15, 18, 21]
active_chats = set()

def load_daily_quotes():
    """Загружает сообщения текущего дня"""
    global daily_messages
    if os.path.exists(QUOTES_CACHE_FILE):
        try:
            with open(QUOTES_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'):
                    daily_messages = data.get('messages', [])
                    print(f"📚 [QUOTE] Загружено {len(daily_messages)} сообщений")
                else:
                    clear_daily_quotes()
        except Exception as e:
            print(f"❌ [QUOTE] Ошибка загрузки: {e}")
    else:
        print(f"📚 [QUOTE] Файл {QUOTES_CACHE_FILE} не найден, создам новый")

def save_daily_quotes():
    """Сохраняет сообщения текущего дня"""
    try:
        data = {
            'date': datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d'),
            'messages': daily_messages
        }
        with open(QUOTES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 [QUOTE] Сохранено {len(daily_messages)} сообщений")
        return True
    except Exception as e:
        print(f"❌ [QUOTE] Ошибка сохранения: {e}")
        return False

def clear_daily_quotes():
    """Очищает кэш сообщений (в 00:00)"""
    global daily_messages
    daily_messages = []
    save_daily_quotes()
    print("🔄 [QUOTE] Кэш цитат очищен")

def add_message_to_quotes(message):
    """Добавляет сообщение в кэш для цитат"""
    global daily_messages
    
    if not message.text:
        return False
    if message.text.startswith('/'):
        return False
    if len(message.text) < 2:
        return False
    if len(message.text) > 500:
        return False
    
    user = message.from_user
    if not user:
        return False
    
    thread_id = message.message_thread_id if message.message_thread_id else 0
    unique_id = f"{message.chat.id}_{thread_id}"
    
    daily_messages.append({
        'text': message.text.strip(),
        'author': user.id,
        'author_name': user.first_name or user.username or "Участник",
        'author_username': user.username,
        'time': datetime.now(MOSCOW_TZ).strftime('%H:%M:%S'),
        'chat_id': message.chat.id,
        'thread_id': thread_id,
        'unique_id': unique_id
    })
    
    if len(daily_messages) > 1000:
        daily_messages = daily_messages[-1000:]
    
    save_daily_quotes()
    print(f"📝 [QUOTE] Добавлено в {unique_id}: {message.text[:40]}")
    return True

def add_chat_to_active(message):
    """Добавляет чат/топик в список активных"""
    thread_id = message.message_thread_id if message.message_thread_id else 0
    unique_id = f"{message.chat.id}_{thread_id}"
    
    if unique_id not in active_chats:
        active_chats.add(unique_id)
        print(f"📍 [QUOTE] Новый активный чат: {unique_id}")
        return True
    return False

def get_chat_messages_count(chat_id, thread_id=0):
    """Возвращает количество сообщений в чате/топике"""
    unique_id = f"{chat_id}_{thread_id}"
    return len([m for m in daily_messages if m.get('unique_id') == unique_id])

def get_random_quote(chat_id, thread_id=0):
    """Возвращает случайную цитату из чата/топика"""
    unique_id = f"{chat_id}_{thread_id}"
    chat_messages = [m for m in daily_messages if m.get('unique_id') == unique_id]
    
    if len(chat_messages) < 2:
        return None
    
    quote = random.choice(chat_messages)
    return f"📜 *Цитата дня*\n\n« {quote['text']} »"

def send_random_quote_to_chat(bot, chat_id, thread_id=0):
    """Отправляет случайную цитату в конкретный чат/топик"""
    unique_id = f"{chat_id}_{thread_id}"
    chat_messages = [m for m in daily_messages if m.get('unique_id') == unique_id]
    
    print(f"📊 [QUOTE] В {unique_id} найдено {len(chat_messages)} сообщений")
    
    if len(chat_messages) < 2:
        return False
    
    quote = random.choice(chat_messages)
    text = f"📜 *Цитата дня*\n\n« {quote['text']} »"
    
    try:
        bot.send_message(chat_id, text, parse_mode="Markdown", message_thread_id=thread_id if thread_id != 0 else None)
        print(f"✅ [QUOTE] Отправлена в {unique_id}")
        return True
    except Exception as e:
        print(f"❌ [QUOTE] Ошибка отправки в {unique_id}: {e}")
        return False

def send_random_quote_to_all_chats(bot):
    """Отправляет цитаты во все активные чаты/топики"""
    print(f"📜 [QUOTE] Отправляю в {len(active_chats)} чатов/топиков")
    for unique_id in list(active_chats):
        parts = unique_id.split("_")
        chat_id = int(parts[0])
        thread_id = int(parts[1]) if len(parts) > 1 else 0
        send_random_quote_to_chat(bot, chat_id, thread_id)

def schedule_daily_quotes(bot):
    """Запускает ежедневную рассылку цитат"""
    now_moscow = datetime.now(MOSCOW_TZ)
    
    for hour in daily_quote_times:
        target = now_moscow.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now_moscow:
            target += timedelta(days=1)
        delay = (target - now_moscow).total_seconds()
        timer = threading.Timer(delay, lambda: send_random_quote_to_all_chats(bot))
        timer.daemon = True
        timer.start()
        print(f"📜 [QUOTE] Запланирована на {target.strftime('%H:%M:%S')}")
    
    midnight = now_moscow.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    midnight_delay = (midnight - now_moscow).total_seconds()
    midnight_timer = threading.Timer(midnight_delay, clear_daily_quotes)
    midnight_timer.daemon = True
    midnight_timer.start()
    print(f"🔄 [QUOTE] Очистка кэша в 00:00")

def get_stats():
    """Возвращает статистику"""
    total_messages = len(daily_messages)
    total_chats = len(active_chats)
    return {
        'messages': total_messages,
        'chats': total_chats,
        'active_chats': list(active_chats)
    }

import os
import logging
import requests
import wikipediaapi
import random
import time
import threading
import re
import urllib.parse
import hashlib
import json
import tempfile
from datetime import datetime, timedelta
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import PyPDF2
import docx
from io import BytesIO
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
import pytz

# === ПЕРЕМЕННЫЕ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_A = int(os.environ.get("CHAT_A", 0))
CHAT_B = int(os.environ.get("CHAT_B", 0))
CHAT_B_THREAD = int(os.environ.get("CHAT_B_THREAD", 0))
RENDER_URL = os.environ.get("RENDER_URL", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 483977434))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN)
secret_messages = {}
ai_cache = {}
user_histories = {}
MAX_HISTORY = 10
CACHE_TTL = 3600

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

def get_sender_name(message):
    """Получение имени отправителя"""
    if message.sender_chat:
        return "Анонимный админ"
    if message.from_user:
        user = message.from_user
        name = user.first_name or ""
        if user.last_name:
            name += f" {user.last_name}"
        if not name.strip():
            name = user.username or "Пользователь"
        if user.username:
            return f"{name} (@{user.username})"
        return name
    return "Неизвестный"

def delete_after_delay(chat_id, message_id, delay=10):
    threading.Timer(delay, lambda: bot.delete_message(chat_id, message_id)).start()

# === ОБЪЕДИНЕНИЕ ЧАТОВ ===
@bot.message_handler(func=lambda m: m.chat.id in [CHAT_A, CHAT_B])
def relay_messages(message):
    # Пропускаем команды и свои сообщения
    if message.text and message.text.startswith('/'):
        return
    if message.from_user and message.from_user.id == bot.get_me().id:
        return
    
    chat_id = message.chat.id
    sender_name = get_sender_name(message)
    
    # Определяем получателя
    if chat_id == CHAT_A:
        target = CHAT_B
        thread = CHAT_B_THREAD if CHAT_B_THREAD != 0 else None
    elif chat_id == CHAT_B:
        if CHAT_B_THREAD != 0 and message.message_thread_id != CHAT_B_THREAD:
            return
        target = CHAT_A
        thread = None
    else:
        return
    
    try:
        # ТЕКСТ
        if message.text:
            bot.send_message(target, f"📩 {sender_name}\n\n{message.text}", 
                           parse_mode=None, message_thread_id=thread)
        
        # ФОТО
        elif message.photo:
            caption = f"📩 {sender_name}\n\n{message.caption or ''}"
            bot.send_photo(target, message.photo[-1].file_id, 
                          caption=caption[:1024], parse_mode=None, 
                          message_thread_id=thread)
        
        # ВИДЕО
        elif message.video:
            caption = f"📩 {sender_name}\n\n{message.caption or ''}"
            bot.send_video(target, message.video.file_id, 
                          caption=caption[:1024], parse_mode=None,
                          message_thread_id=thread)
        
        # ДОКУМЕНТЫ
        elif message.document:
            caption = f"📩 {sender_name}\n\n{message.caption or ''}"
            bot.send_document(target, message.document.file_id,
                            caption=caption[:1024], parse_mode=None,
                            message_thread_id=thread)
        
        # GIF
        elif message.animation:
            caption = f"📩 {sender_name}\n\n{message.caption or ''}"
            bot.send_animation(target, message.animation.file_id,
                              caption=caption[:1024], parse_mode=None,
                              message_thread_id=thread)
        
        # СТИКЕРЫ
        elif message.sticker:
            bot.send_sticker(target, message.sticker.file_id,
                           message_thread_id=thread)
            bot.send_message(target, f"📩 {sender_name} (стикер)",
                           parse_mode=None, message_thread_id=thread)
        
        # ГОЛОСОВЫЕ
        elif message.voice:
            bot.send_voice(target, message.voice.file_id,
                          caption=f"📩 {sender_name}", parse_mode=None,
                          message_thread_id=thread)
        
        # АУДИО
        elif message.audio:
            bot.send_audio(target, message.audio.file_id,
                          caption=f"📩 {sender_name}", parse_mode=None,
                          message_thread_id=thread)
        
        # ВИДЕОСООБЩЕНИЕ
        elif message.video_note:
            bot.send_video_note(target, message.video_note.file_id,
                              message_thread_id=thread)
            bot.send_message(target, f"📩 {sender_name} (видео)",
                           parse_mode=None, message_thread_id=thread)
        
        logger.info(f"✅ Переслано: {message.content_type}")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")

# === НАПОМИНАНИЯ ===
REMINDERS_FILE = "reminders.json"

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_reminders(reminders):
    to_save = []
    for r in reminders:
        copy = {k: v for k, v in r.items() if k not in ["timer", "_timer"]}
        to_save.append(copy)
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

reminders = load_reminders()
reminder_counter = max([r.get("id", 0) for r in reminders]) if reminders else 0

def parse_time_with_day(s):
    days = {"пн":0,"понедельник":0,"вт":1,"вторник":1,"ср":2,"среда":2,
            "чт":3,"четверг":3,"пт":4,"пятница":4,"сб":5,"суббота":5,
            "вс":6,"воскресенье":6}
    parts = s.lower().split()
    time_part = parts[0]
    daily = False
    weekly = None
    for p in parts[1:]:
        if p in ["ежедневно","каждый","daily","каждый день"]:
            daily = True
        elif p in days:
            weekly = days[p]
    try:
        if ":" in time_part:
            h,m = map(int, time_part.split(":"))
        else:
            h,m = int(time_part), 0
        return h, m, weekly, daily
    except:
        return None, None, None, None

def get_next_time(h, m, weekly=None, daily=False):
    now = datetime.now(MOSCOW_TZ)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if daily:
        if target <= now:
            target += timedelta(days=1)
        return target
    if weekly is not None:
        days_ahead = (weekly - now.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        target = now + timedelta(days=days_ahead)
        return target.replace(hour=h, minute=m, second=0)
    if target <= now:
        target += timedelta(days=1)
    return target

def send_reminder(r):
    try:
        bot.send_message(r["chat_id"], f"⏰ НАПОМИНАНИЕ!\n\n{r['text']}",
                        parse_mode=None, message_thread_id=r.get("thread_id"))
        logger.info(f"✅ Отправлено напоминание {r['id']}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")

def schedule_reminder(r):
    next_time = get_next_time(r["hours"], r["minutes"], r.get("weekly_day"), r.get("daily", False))
    delay = (next_time.astimezone(pytz.UTC) - datetime.now(pytz.UTC)).total_seconds()
    if delay < 0:
        delay = 0
    timer = threading.Timer(delay, lambda: (send_reminder(r), schedule_reminder(r) if r.get("daily") else None))
    timer.daemon = True
    timer.start()
    r["_timer"] = timer

def start_all_reminders():
    for r in reminders:
        schedule_reminder(r)

# === ИИ ===
def ask_groq(user_id, prompt):
    if not GROQ_API_KEY:
        return "❌ Groq API не настроен."
    cache_key = hashlib.md5(prompt.encode()).hexdigest()
    if cache_key in ai_cache:
        t, a = ai_cache[cache_key]
        if time.time() - t < CACHE_TTL:
            return a
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": "user", "content": prompt})
    if len(user_histories[user_id]) > MAX_HISTORY:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY:]
    messages = [{"role": "system", "content": "Отвечай кратко."}, *user_histories[user_id]]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3-32b", "messages": messages, "max_tokens": 800, "temperature": 0.2}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=30)
        if r.status_code == 200:
            a = r.json()["choices"][0]["message"]["content"]
            a = re.sub(r'<think>.*?</think>|/think', '', a, flags=re.DOTALL).strip()
            user_histories[user_id].append({"role": "assistant", "content": a})
            ai_cache[cache_key] = (time.time(), a)
            return a
        return f"❌ Ошибка: {r.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"

def web_search(q):
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        results = soup.find_all('a', class_='result__a', limit=5)
        if not results:
            return None
        res = [f"• {x.get_text()}\n{x.get('href')}" for x in results if x.get('href')]
        return "🔍 Результаты:\n\n" + "\n".join(res)
    except:
        return None

def search_wiki(q):
    try:
        wiki = wikipediaapi.Wikipedia(language='ru', user_agent='TelegramRelayBot/1.0')
        page = wiki.page(q)
        if page.exists():
            s = page.summary[:500] + ("..." if len(page.summary) > 500 else "")
            return f"📖 {page.title}\n\n{s}\n\n🔗 {page.fullurl}"
        return "❌ Ничего не найдено"
    except Exception as e:
        return f"❌ Ошибка: {e}"

# === КОМАНДЫ ===
@bot.message_handler(commands=['start', 'help'])
def help_cmd(m):
    bot.reply_to(m, """📖 Команды

⏰ Напоминания:
/remind 15:30 ежедневно текст
/reminds - список
/delremind ID

🤖 ИИ: /ai вопрос

🌐 Перевод: /т on/off

🎲 Игры: /roll | /coin

👑 Админ: /backup""")

@bot.message_handler(commands=['ai'])
def ai_cmd(m):
    p = m.text[3:].strip()
    if not p:
        bot.reply_to(m, "ℹ️ /ai вопрос")
        return
    msg = bot.reply_to(m, "🤖 Думаю...")
    a = ask_groq(m.from_user.id, p)
    bot.edit_message_text(a, m.chat.id, msg.message_id)

@bot.message_handler(commands=['wiki'])
def wiki_cmd(m):
    q = m.text[5:].strip()
    if not q:
        bot.reply_to(m, "ℹ️ /wiki запрос")
        return
    bot.reply_to(m, search_wiki(q))

@bot.message_handler(commands=['roll'])
def roll_cmd(m):
    bot.reply_to(m, f"🎲 {random.randint(1, 100)}")

@bot.message_handler(commands=['coin'])
def coin_cmd(m):
    bot.reply_to(m, f"🪙 {random.choice(['Орёл', 'Решка'])}")

@bot.message_handler(commands=['clear_history'])
def clear_cmd(m):
    if m.from_user.id in user_histories:
        del user_histories[m.from_user.id]
        bot.reply_to(m, "🗑️ Очищено!")

# === БЕКАП ===
@bot.message_handler(commands=['backup'])
def backup_cmd(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "❌ Нет прав")
        return
    try:
        data = [{k:v for k,v in r.items() if k not in ["timer","_timer"]} for r in reminders]
        fname = f"reminders_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(fname, 'rb') as f:
            bot.send_document(m.chat.id, f, caption=f"Бекап\nДата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\nВсего: {len(data)}")
        os.remove(fname)
    except Exception as e:
        bot.reply_to(m, f"❌ {e}")

@bot.message_handler(commands=['restore'])
def restore_cmd(m):
    if m.from_user.id != ADMIN_ID:
        bot.reply_to(m, "❌ Нет прав")
        return
    bot.reply_to(m, "📥 Отправьте файл бекапа")

@bot.message_handler(content_types=['document'])
def handle_backup(m):
    if m.from_user.id != ADMIN_ID:
        return
    if not m.document.file_name.startswith("reminders_backup_"):
        return
    status = bot.reply_to(m, "🔄 Восстанавливаю...")
    try:
        info = bot.get_file(m.document.file_id)
        data = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}").content
        backup = json.loads(data.decode('utf-8'))
        for r in reminders:
            if "_timer" in r:
                try:
                    r["_timer"].cancel()
                except:
                    pass
        reminders.clear()
        global reminder_counter
        reminder_counter = 0
        for r in backup:
            reminders.append(r)
            if r.get("id", 0) > reminder_counter:
                reminder_counter = r.get("id", 0)
        save_reminders(reminders)
        start_all_reminders()
        bot.delete_message(m.chat.id, status.message_id)
        bot.reply_to(m, f"✅ Восстановлено {len(backup)} напоминаний!")
    except Exception as e:
        bot.delete_message(m.chat.id, status.message_id)
        bot.reply_to(m, f"❌ {e}")

# === НАПОМИНАНИЯ ===
@bot.message_handler(commands=['remind'])
def remind_cmd(m):
    chat_id = m.chat.id
    thread_id = m.message_thread_id
    try:
        bot.delete_message(chat_id, m.message_id)
    except:
        pass
    parts = m.text.split(maxsplit=2)
    if len(parts) < 3:
        msg = bot.reply_to(m, "ℹ️ /remind 15:30 ежедневно текст")
        delete_after_delay(chat_id, msg.message_id)
        return
    time_str, text = parts[1], parts[2]
    clean_text = text
    for kw in ["ежедневно","каждый","daily"]:
        if clean_text.lower().startswith(kw):
            clean_text = clean_text[len(kw):].lstrip()
            break
    h, m, weekly, daily = parse_time_with_day(time_str)
    if h is None:
        msg = bot.reply_to(m, "❌ Формат: /remind 15:30 текст")
        delete_after_delay(chat_id, msg.message_id)
        return
    global reminder_counter
    reminder_counter += 1
    reminder = {
        "id": reminder_counter,
        "chat_id": chat_id,
        "user_id": m.from_user.id,
        "thread_id": thread_id,
        "text": clean_text,
        "hours": h,
        "minutes": m,
        "weekly_day": weekly,
        "daily": daily
    }
    reminders.append(reminder)
    save_reminders(reminders)
    schedule_reminder(reminder)
    now_m = datetime.now(MOSCOW_TZ)
    target = now_m.replace(hour=h, minute=m)
    if daily:
        period = "каждый день"
    elif weekly is not None:
        days = ["пн","вт","ср","чт","пт","сб","вс"]
        period = f"каждый {days[weekly]}"
    else:
        period = "сегодня" if target > now_m else "завтра"
    loc = "в топик" if thread_id else "в этот чат"
    msg = bot.send_message(chat_id, f"✅ Напоминание добавлено!\n\n⏰ {period} в {h:02d}:{m:02d} МСК\n📍 {loc}\n📝 {clean_text}\n🆔 ID: {reminder_counter}",
                          message_thread_id=thread_id)
    delete_after_delay(chat_id, msg.message_id)

@bot.message_handler(commands=['reminds'])
def reminds_cmd(m):
    chat_id = m.chat.id
    thread_id = m.message_thread_id
    try:
        bot.delete_message(chat_id, m.message_id)
    except:
        pass
    user_r = [r for r in reminders if r.get("chat_id") == chat_id]
    if thread_id:
        user_r = [r for r in user_r if r.get("thread_id") == thread_id]
    else:
        user_r = [r for r in user_r if not r.get("thread_id")]
    if not user_r:
        msg = bot.reply_to(m, "📭 Нет напоминаний")
        delete_after_delay(chat_id, msg.message_id, 15)
        return
    resp = "📋 Напоминания:\n\n"
    for r in user_r:
        if r.get("daily"):
            p = f"ежедневно в {r['hours']:02d}:{r['minutes']:02d}"
        elif r.get("weekly_day") is not None:
            days = ["пн","вт","ср","чт","пт","сб","вс"]
            p = f"каждый {days[r['weekly_day']]} в {r['hours']:02d}:{r['minutes']:02d}"
        else:
            p = f"в {r['hours']:02d}:{r['minutes']:02d}"
        resp += f"🆔 {r['id']} - {p}\n   📝 {r['text'][:50]}\n\n"
    msg = bot.send_message(chat_id, resp, message_thread_id=thread_id)
    delete_after_delay(chat_id, msg.message_id, 30)

@bot.message_handler(commands=['delremind'])
def delremind_cmd(m):
    chat_id = m.chat.id
    thread_id = m.message_thread_id
    try:
        bot.delete_message(chat_id, m.message_id)
    except:
        pass
    parts = m.text.split()
    if len(parts) < 2:
        msg = bot.reply_to(m, "ℹ️ /delremind ID")
        delete_after_delay(chat_id, msg.message_id)
        return
    try:
        rid = int(parts[1])
        for i, r in enumerate(reminders):
            if r["id"] == rid:
                if r.get("chat_id") != chat_id:
                    msg = bot.reply_to(m, f"❌ {rid} не найдено")
                    delete_after_delay(chat_id, msg.message_id, 15)
                    return
                if thread_id and r.get("thread_id") != thread_id:
                    msg = bot.reply_to(m, f"❌ {rid} не в этом топике")
                    delete_after_delay(chat_id, msg.message_id, 15)
                    return
                if "_timer" in r:
                    try:
                        r["_timer"].cancel()
                    except:
                        pass
                reminders.pop(i)
                save_reminders(reminders)
                msg = bot.reply_to(m, f"✅ {rid} удалено")
                delete_after_delay(chat_id, msg.message_id)
                return
        msg = bot.reply_to(m, f"❌ {rid} не найдено")
        delete_after_delay(chat_id, msg.message_id, 15)
    except:
        msg = bot.reply_to(m, "❌ Неверный ID")
        delete_after_delay(chat_id, msg.message_id)

# === ПЕРЕВОДЧИК ===
TRANSLATOR_FILE = os.path.join(tempfile.gettempdir(), "translator.json")

def load_tr():
    if os.path.exists(TRANSLATOR_FILE):
        try:
            with open(TRANSLATOR_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_tr(s):
    try:
        with open(TRANSLATOR_FILE, 'w') as f:
            json.dump(s, f)
    except:
        pass

tr_settings = load_tr()

def is_tr_enabled(chat_id):
    return tr_settings.get(str(chat_id), False)

def set_tr_enabled(chat_id, enabled):
    tr_settings[str(chat_id)] = enabled
    save_tr(tr_settings)

@bot.message_handler(commands=['т'])
def tr_cmd(m):
    chat_id = m.chat.id
    parts = m.text.split()
    if len(parts) < 2:
        status = "✅ Вкл" if is_tr_enabled(chat_id) else "❌ Выкл"
        bot.reply_to(m, f"🌐 Статус: {status}\n/т on - вкл\n/т off - выкл")
        return
    if parts[1].lower() == "on":
        set_tr_enabled(chat_id, True)
        bot.reply_to(m, "✅ Включён!")
    elif parts[1].lower() == "off":
        set_tr_enabled(chat_id, False)
        bot.reply_to(m, "❌ Выключен")

@bot.message_handler(func=lambda m: True, content_types=['text'])
def auto_tr(m):
    chat_id = m.chat.id
    if not is_tr_enabled(chat_id):
        return
    if m.from_user.id == bot.get_me().id or m.text.startswith('/') or m.text.startswith('📩'):
        return
    text = m.text.strip()
    if not text or len(text) < 3:
        return
    try:
        has_cyril = any(ord(c) > 1024 for c in text)
        if has_cyril:
            t = GoogleTranslator(source='ru', target='en').translate(text)
        else:
            t = GoogleTranslator(source='en', target='ru').translate(text)
        if t and t != text:
            bot.reply_to(m, t)
    except:
        pass

# === СКРЫТЫЕ ===
@bot.inline_handler(func=lambda q: True)
def inline_q(q):
    try:
        text = q.query.strip()
        if not text or len(text.split()) < 2:
            return
        target, content = text.split(maxsplit=1)
        target = target.lstrip("@")
        try:
            info = bot.get_chat(f"@{target}")
            tid = info.id
            tname = info.first_name or target
        except:
            if target.isdigit():
                tid = int(target)
                tname = f"User {target}"
            else:
                res = types.InlineQueryResultArticle(
                    id="err",
                    title="❌ Не найден",
                    input_message_content=types.InputTextMessageContent(f"❌ {target} не найден")
                )
                bot.answer_inline_query(q.id, [res], cache_time=0)
                return
        mid = f"sec_{int(time.time())}_{q.from_user.id}_{random.randint(1000,9999)}"
        secret_messages[mid] = {
            "target_id": tid,
            "target_name": tname,
            "content": content,
            "sender": q.from_user.first_name,
            "expires": time.time() + 86400
        }
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📩 Прочитать", callback_data=f"sec_{mid}"))
        res = types.InlineQueryResultArticle(
            id=mid,
            title=f"📨 Для {tname}",
            description=content[:50],
            input_message_content=types.InputTextMessageContent(
                f"🔐 Скрытое сообщение\nОт: {q.from_user.first_name}"
            ),
            reply_markup=markup
        )
        bot.answer_inline_query(q.id, [res], cache_time=0, is_personal=True)
    except Exception as e:
        logger.error(f"Inline: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("sec_"))
def secret_read(c):
    mid = c.data[4:]
    if mid not in secret_messages:
        bot.answer_callback_query(c.id, "❌ Не найдено", show_alert=True)
        return
    data = secret_messages[mid]
    if c.from_user.id != data["target_id"]:
        bot.answer_callback_query(c.id, "❌ Не для вас", show_alert=True)
        return
    if time.time() > data["expires"]:
        bot.answer_callback_query(c.id, "❌ Истекло", show_alert=True)
        del secret_messages[mid]
        return
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass
    bot.answer_callback_query(c.id, f"📩 От {data['sender']}:\n\n{data['content']}", show_alert=True)
    del secret_messages[mid]

# === РЕАКЦИИ НА КАНАЛЫ ===
@bot.channel_post_handler(func=lambda m: True)
def channel_react(m):
    allowed = [-1001317416582, -1002185590715]
    if m.chat.id not in allowed:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
        data = {"chat_id": m.chat.id, "message_id": m.message_id, "reaction": [{"type": "emoji", "emoji": "🔥"}]}
        requests.post(url, json=data, timeout=5)
        logger.info(f"🔥 Реакция на пост {m.message_id} в {m.chat.id}")
    except Exception as e:
        logger.error(f"Реакция: {e}")

# === ЗАПУСК ===
start_all_reminders()

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        bot.process_new_updates([types.Update.de_json(update)])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook: {e}")
        return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"Чат A: {CHAT_A}, Чат B: {CHAT_B}, топик B: {CHAT_B_THREAD}")
    app.run(host="0.0.0.0", port=port)

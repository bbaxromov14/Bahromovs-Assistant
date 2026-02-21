import os
import asyncio
import time
import json
import random
import logging
import re
import threading
from datetime import timedelta
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
import google.generativeai as genai

# ===========================================
# 1. ЗАГРУЗКА ПЕРЕМЕННЫХ И НАСТРОЙКИ
# ===========================================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
if OWNER_ID == 0:
    OWNER_ID = None

# ===========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===========================================
def detect_emotion(text):
    t = text.lower()
    if any(x in t for x in ["!", "круто", "ахах", "лол"]):
        return "энергично и живо"
    if any(x in t for x in ["почему", "не работает", "ошибка"]):
        return "спокойно и поддерживающе"
    if any(x in t for x in ["бесит", "задолбало", "ужас"]):
        return "спокойно и уверенно"
    return "нейтрально"

def humanize(text):
    text = text.strip()
    text = re.sub(r"^(В итоге|Таким образом|Итак)[,:]?\s*", "", text, flags=re.IGNORECASE)
    if random.random() < 0.25:
        text = text.replace("очень", "довольно", 1)
    if len(text) > 900:
        text = text[:900].rsplit(".", 1)[0] + "."
    return text

# ===========================================
# 3. КЛАСС MemoryManager
# ===========================================
class MemoryManager:
    def __init__(self, filename="memory.json"):
        self.filename = filename
        self.data = {}
        self.lock = asyncio.Lock()
        self.dirty = False
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"Memory load error: {e}")
                self.data = {}

    async def update(self, uid, text):
        if len(text) < 20:
            return
        uid = str(uid)
        async with self.lock:
            self.data.setdefault(uid, {"facts": []})
            score = 1
            if "?" in text:
                score += 1
            if len(text) > 80:
                score += 1
            self.data[uid]["facts"].append({
                "text": text[:160],
                "score": score,
                "ts": time.time()
            })
            self.data[uid]["facts"] = self.data[uid]["facts"][-20:]
            self.dirty = True

    async def autosave_loop(self):
        while True:
            await asyncio.sleep(8)
            async with self.lock:
                if self.dirty:
                    try:
                        with open(self.filename, "w", encoding="utf-8") as f:
                            json.dump(self.data, f, ensure_ascii=False, indent=2)
                        self.dirty = False
                    except Exception as e:
                        logger.error(f"Memory save error: {e}")

    def get_text(self, uid):
        facts = self.data.get(str(uid), {}).get("facts", [])
        normalized = []
        for item in facts:
            if isinstance(item, str):
                normalized.append({"text": item, "score": 1, "ts": 0})
            elif isinstance(item, dict):
                normalized.append({
                    "text": item.get("text", ""),
                    "score": item.get("score", 1),
                    "ts": item.get("ts", 0)
                })
        normalized = sorted(normalized, key=lambda x: x["score"], reverse=True)[:5]
        return "\n".join(x["text"] for x in normalized)

# ===========================================
# 4. КЛАСС StyleManager
# ===========================================
class StyleManager:
    def __init__(self, filename="my_style.txt"):
        self.filename = filename
        self.lines = []
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            with open(self.filename, "r", encoding="utf-8") as f:
                self.lines = [x.strip() for x in f if x.strip()]

    def save_line(self, text):
        if len(text) < 8 or len(text) > 320:
            return
        if text.startswith("/") or re.search(r'https?://\S+', text):
            return
        if text in self.lines:
            return
        self.lines.append(text)
        self.lines = self.lines[-500:]
        try:
            with open(self.filename, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            logger.error(f"Style save error: {e}")

    def get_examples(self, n=6):
        if not self.lines:
            return "пиши естественно."
        return "\n".join(random.sample(self.lines, min(n, len(self.lines))))

# ===========================================
# 5. КЛАСС GeminiResponder
# ===========================================
class GeminiResponder:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        self.model_name = self._pick_model()

    def _pick_model(self):
        models = []
        for m in genai.list_models():
            methods = getattr(m, "supported_generation_methods", [])
            if "generateContent" in methods:
                models.append(m.name)
        flash = [m for m in models if "flash" in m]
        return flash[0] if flash else models[0] if models else None

    def generate(self, prompt):
        if not self.model_name:
            logger.error("No Gemini model available")
            return ""
        model = genai.GenerativeModel(self.model_name)
        try:
            r = model.generate_content(prompt)
            return r.text if r and r.text else ""
        except Exception as e:
            logger.error(f"Gemini generation error: {e}")
            return ""

# ===========================================
# 6. КЛАСС UserDataCleaner
# ===========================================
class UserDataCleaner:
    def __init__(self, user_last, dialog_until, user_locks, max_age_hours=24):
        self.user_last = user_last
        self.dialog_until = dialog_until
        self.user_locks = user_locks
        self.max_age = timedelta(hours=max_age_hours)

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            now = time.time()
            threshold = now - self.max_age.total_seconds()
            to_remove = [uid for uid, last in self.user_last.items() if last < threshold]
            for uid in to_remove:
                self.user_last.pop(uid, None)
                self.dialog_until.pop(uid, None)
                lock = self.user_locks.get(uid)
                if lock and not lock.locked():
                    self.user_locks.pop(uid, None)
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} inactive users")

# ===========================================
# 7. КЛАСС TelegramAIBot (ИСПРАВЛЕННЫЙ)
# ===========================================
class TelegramAIBot:
    MY_NAMES = ["Bahrom", "Baxrom", "Бахром", "aytchi", "iltmos yordam bering"]
    USER_COOLDOWN = 5
    DIALOG_GRACE = 240

    def __init__(self):
        self.api_id = int(os.getenv("API_ID", "0"))
        self.api_hash = os.getenv("API_HASH")
        self.bot_token = os.getenv("BOT_TOKEN")

        if not self.api_id or not self.api_hash or not self.bot_token:
            raise ValueError("Missing ENV variables")

        # ИСПРАВЛЕНИЕ: Используем StringSession вместо файла
        from telethon.sessions import StringSession
        session_string = os.getenv("SESSION_STRING", "")
        if session_string:
            # Если есть сохраненная сессия, используем её
            self.client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        else:
            # Если нет, создаем новую сессию в памяти
            self.client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        
        self.memory = MemoryManager()
        self.style = StyleManager()
        self.ai = GeminiResponder(os.getenv("GEMINI_API_KEY"))

        self.my_id = None
        self.user_last = {}
        self.dialog_until = {}
        self.user_locks = {}
        self.cleaner = UserDataCleaner(self.user_last, self.dialog_until, self.user_locks)

    def name_called(self, text):
        t = text.lower()
        return any(re.search(rf"\b{re.escape(x)}\b", t) for x in self.MY_NAMES)

    async def is_direct(self, event, text):
        if self.name_called(text):
            return True
        if event.message.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id == self.my_id:
                return True
        return False

    async def adaptive_typing_delay(self, text):
        words = len(text.split())
        delay = min(6.5, 0.7 + words * 0.12)
        await asyncio.sleep(delay * random.uniform(0.85, 1.2))

    async def send_with_retry(self, chat_id, text, reply_to=None, max_retries=3):
        for attempt in range(max_retries):
            try:
                return await self.client.send_message(chat_id, text, reply_to=reply_to)
            except FloodWaitError as e:
                wait = e.seconds
                logger.warning(f"Flood wait: {wait}s, retry {attempt+1}/{max_retries}")
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error(f"Send error: {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        return None

    async def on_message(self, event):
        if not event.is_private:
            return
        if event.out or event.sender_id == self.my_id:
            return

        sender = await event.get_sender()
        if sender and getattr(sender, "bot", False):
            return
        if event.via_bot_id:
            return

        incoming = (event.raw_text or "").strip()
        if len(incoming) < 3:
            return

        uid = event.sender_id
        now = time.time()

        if OWNER_ID and uid == OWNER_ID:
            self.style.save_line(incoming)

        direct = await self.is_direct(event, incoming)
        if direct:
            self.dialog_until[uid] = now + self.DIALOG_GRACE
        elif now > self.dialog_until.get(uid, 0):
            return
        if now - self.user_last.get(uid, 0) < self.USER_COOLDOWN:
            return

        lock = self.user_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            memory = self.memory.get_text(uid)
            emotion = detect_emotion(incoming)

            prompt = f"""
Ты — Бахром, эксперт по кибербезопасности.
Твое имя Бахром.
Не говори что ты ИИ.
Не используй — в сообщениях.
Никогда не матерись.
Если тебя спрашивают про хакинг или кибербезопасность ты даешь максимально лучший и хороший ответ
Придерживайся стиля

ТВОЙ БАЗОВЫЙ СТИЛЬ ОБЩЕНИЯ (важно придерживаться):
{self.style.get_examples()}

ТЕКУЩИЙ ЭМОЦИОНАЛЬНЫЙ ТОН ОТВЕТА:
{emotion}

ПАМЯТЬ:
{memory}

ВОПРОС:
{incoming}
"""
            text = await asyncio.to_thread(self.ai.generate, prompt)
            if not text:
                logger.info(f"Empty response for user {uid}, skipping")
                return

            text = humanize(text)

            try:
                async with self.client.action(event.chat_id, "typing"):
                    await self.adaptive_typing_delay(text)
                await self.send_with_retry(event.chat_id, text, reply_to=event.id)
            except Exception as e:
                logger.exception(f"Failed to send message: {e}")

            await self.memory.update(uid, incoming)
            self.user_last[uid] = now

    async def run(self):
        while True:
            try:
                # ИСПРАВЛЕНИЕ: Убрали создание нового client, используем существующий
                await self.client.start(bot_token=self.bot_token)
                me = await self.client.get_me()
                self.my_id = me.id
                
                # Сохраняем строку сессии для будущих запусков (полезно при первом запуске)
                if not os.getenv("SESSION_STRING"):
                    session_string = self.client.session.save()
                    logger.info(f"✨ СОХРАНИТЕ ЭТУ СТРОКУ В ПЕРЕМЕННУЮ SESSION_STRING: {session_string}")
                
                asyncio.create_task(self.memory.autosave_loop())
                asyncio.create_task(self.cleaner.cleanup_loop())
                logger.info("✅ BOT STARTED SUCCESSFULLY!")
                self.client.add_event_handler(self.on_message, events.NewMessage(incoming=True))
                await self.client.run_until_disconnected()
            except Exception as e:
                logger.exception(f"❌ Bot crashed: {e}")
                await asyncio.sleep(5)
# ===========================================
# 8. ФУНКЦИЯ run_with_reconnect
# ===========================================
async def run_with_reconnect():
    """Запускает бота с автоматическим переподключением"""
    max_retries = 5
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            bot = TelegramAIBot()
            await bot.run()
            break
        except Exception as e:
            logging.error(f"Попытка {attempt + 1}/{max_retries} не удалась: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (attempt + 1))
            else:
                logging.critical("Бот не может запуститься после всех попыток")
                raise

# ===========================================
# 9. FLASK ДЛЯ RENDER
# ===========================================
def run_flask():
    """Запускает Flask сервер"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Запуск Flask сервера на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_bot():
    """Запускает Telegram бота"""
    logger.info("Запуск Telegram бота...")
    try:
        asyncio.run(run_with_reconnect())
    except Exception as e:
        logger.exception(f"Бот упал с ошибкой: {e}")

# ===========================================
# 10. ТОЧКА ВХОДА
# ===========================================
if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask поток запущен")
    
    # Запускаем бота в основном потоке
    run_bot()
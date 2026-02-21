import os
import sys
import logging
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient, events
import google.generativeai as genai
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Бот Бахром работает! Я жив!"

@app.route('/health')
def health():
    return "OK", 200

# Сюда вставьте ВЕСЬ ваш код бота (классы MemoryManager, StyleManager, GeminiResponder, TelegramAIBot)
# Не удаляйте ничего из вашего original кода!

# === ВСТАВЬТЕ СЮДА ВЕСЬ ВАШ ОРИГИНАЛЬНЫЙ КОД ===
# (от начала файла до if __name__ == "__main__":)
# Классы: MemoryManager, StyleManager, GeminiResponder, TelegramAIBot
# Функции: detect_emotion, humanize, run_with_reconnect
# === КОНЕЦ ВСТАВКИ ===

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

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask поток запущен")
    
    # Запускаем бота в основном потоке
    run_bot()
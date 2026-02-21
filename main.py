import os
import sys
import logging
from flask import Flask
from threading import Thread
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask приложение для поддержания активности
app = Flask('')

@app.route('/')
def home():
    return "🤖 Бот Бахром работает!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Запускает Flask сервер в отдельном потоке"""
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    logger.info("Flask сервер запущен для поддержания активности")

# Импортируем ваш бот
try:
    # Переименуйте ваш файл или импортируйте напрямую
    # Если файл называется bybahromoov.py:
    import bybahromoov as bot_module
    
    # Если нужно запустить бот из вашего модуля
    def run_bot():
        """Запускает вашего бота"""
        logger.info("Запуск бота Бахром...")
        # Создаем и запускаем бот
        if hasattr(bot_module, 'TelegramAIBot'):
            bot = bot_module.TelegramAIBot()
            asyncio.run(bot.run())
        else:
            # Если бот запускается другим способом
            logger.error("Не удалось найти класс TelegramAIBot")
            
except ImportError as e:
    logger.error(f"Ошибка импорта бота: {e}")
    # Альтернативный вариант - запуск через subprocess
    import subprocess
    def run_bot():
        subprocess.run([sys.executable, "bybahromoov.py"])

if __name__ == "__main__":
    # Запускаем Flask для поддержания активности
    keep_alive()
    
    # Запускаем бота
    try:
        run_bot()
    except Exception as e:
        logger.exception(f"Критическая ошибка бота: {e}")
#!/usr/bin/env python3
# run.py - Скрипт запуска бота
import asyncio
import logging
import sys
import signal

from bot import HumanTwitchBot, main

# Настройка логирования с цветами
class ColorFormatter(logging.Formatter):
    """Цветное форматирование логов"""
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    
    FORMATS = {
        logging.DEBUG: grey + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.INFO: grey + "%(asctime)s - " + reset + "%(message)s",
        logging.WARNING: yellow + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.ERROR: red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.CRITICAL: bold_red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)

# Настройка логгера
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Консольный вывод с цветами
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(ColorFormatter())
logger.addHandler(ch)

# Файловый вывод
fh = logging.FileHandler('bot.log', encoding='utf-8')
fh.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(file_formatter)
logger.addHandler(fh)

def signal_handler(signum, frame):
    """Обработчик сигналов завершения"""
    print("\n🚨 Получен сигнал завершения...")
    sys.exit(0)

if __name__ == "__main__":
    # Регистрация обработчиков сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 80)
    print("🤖 ЗАПУСК ЧЕЛОВЕЧНОГО ТВИТЧ БОТА")
    print("=" * 80)
    
    try:
        # Запуск бота
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
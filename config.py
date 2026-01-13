# config.py - Конфигурация Twitch AI бота

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# TWITCH КОНФИГУРАЦИЯ
# ============================================================================

TWITCH_TOKEN = os.getenv('TWITCH_TOKEN')
TWITCH_NICK = os.getenv('TWITCH_NICK')
TWITCH_CHANNEL = os.getenv('TWITCH_CHANNEL', 'channel1,channel2')

if not TWITCH_TOKEN or not TWITCH_NICK:
    raise ValueError("Ошибка: TWITCH_TOKEN и TWITCH_NICK должны быть в .env!")

# ============================================================================
# AI API КОНФИГУРАЦИЯ (ГИБРИДНАЯ СИСТЕМА)
# ============================================================================

MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')
if not MISTRAL_API_KEY:
    raise ValueError("Ошибка: MISTRAL_API_KEY должен быть в .env!")

GOOGLE_AI_KEY = os.getenv('GOOGLE_AI_KEY')
if not GOOGLE_AI_KEY:
    raise ValueError("Ошибка: GOOGLE_AI_KEY должен быть в .env!")

# ============================================================================
# ПАРАМЕТРЫ ОТВЕТОВ
# ============================================================================

MAX_RESPONSE_LENGTH = 180
MAX_RESPONSE_LENGTH_MENTIONED = 600
RESPONSE_PROBABILITY = 0.07

# ============================================================================
# КОНТЕКСТ И ТОКЕНЫ
# ============================================================================

CONTEXT_MESSAGE_LIMIT = 15
TOKEN_LIMIT_PER_MINUTE = 500000
TOKEN_LIMIT_PER_DAY = 1000000
RETRY_MAX_ATTEMPTS = 3

# ============================================================================
# СМАЙЛИКИ И НАСТРОЕНИЯ
# ============================================================================

EMOTE_ADD_PROBABILITY = 0.4

MOOD_STATES = [
    "happy", "neutral", "tired", "excited",
    "curious", "sarcastic", "playful"
]

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

LOG_LEVEL = "INFO"
LOG_FILE = "bot.log"

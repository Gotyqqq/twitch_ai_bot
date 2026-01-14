# config.py - Конфигурация максимально человечного бота (УЛУЧШЕННАЯ ВЕРСИЯ)

import os
from dotenv import load_dotenv
from datetime import time

load_dotenv()

# ====================================================================
# ОСНОВНЫЕ НАСТРОЙКИ
# ====================================================================
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_NICK = os.getenv("TWITCH_NICK", "bot_username").lower()
TWITCH_CHANNELS = [ch.strip() for ch in os.getenv("TWITCH_CHANNEL", "").split(',') if ch.strip()]

# API ключи
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ====================================================================
# СИСТЕМА ДВУХ ИИ
# ====================================================================
ANALYZER_MODEL = "mistral-medium"
ANALYZER_CONTEXT_SIZE = 15
ANALYZER_UPDATE_INTERVAL = 30

RESPONDER_MODEL = "gemma-3-27b-it"
RESPONDER_TEMPERATURE = 0.92

# ====================================================================
# ЧЕЛОВЕЧЕСКИЕ ПАРАМЕТРЫ (УЛУЧШЕНО)
# ====================================================================
HUMAN_TYPING_SPEED_WPM = 180
THINKING_TIME_MIN = 1.5
THINKING_TIME_MAX = 4.5
RESPONSE_VARIABILITY = 0.3

# ОПЕЧАТКИ И ЕСТЕСТВЕННОСТЬ
TYPO_PROBABILITY = 0.18  # Увеличено для реализма
TYPO_FIX_PROBABILITY = 0.20  # Уменьшено - не всегда исправляем
STUTTER_PROBABILITY = 0.08  # Увеличено
CAPS_LOCK_PROBABILITY = 0.05  # Новое: случайный капслок при эмоциях
DOUBLE_MESSAGE_PROBABILITY = 0.12  # Новое: дополнение своего сообщения
EMOJI_ONLY_RESPONSE = 0.08  # Новое: ответ только смайликом
SHORT_REACTION_PROBABILITY = 0.25  # Новое: короткие реакции (ага, лол, да)

# СЛЕНГ И СОКРАЩЕНИЯ (НОВОЕ)
USE_SLANG = True
SLANG_PROBABILITY = 0.35
INTERNET_SLANG = {
    'спасибо': ['спс', 'сенкс', 'спасибки'],
    'пожалуйста': ['пж', 'пжлст', 'не за что'],
    'хорошо': ['ок', 'окей', 'норм', 'найс'],
    'понятно': ['ясн', 'пон', 'понял'],
    'не знаю': ['хз', 'незн', 'не в курсе'],
    'сейчас': ['щас', 'ща', 'счас'],
    'кстати': ['кст', 'кста'],
    'наверное': ['наверн', 'мб', 'может'],
    'конечно': ['кнч', 'разумеется'],
}

# ЗАБЫВЧИВОСТЬ (НОВОЕ)
MEMORY_FADE_PROBABILITY = 0.15  # Иногда "забывает" контекст
AFK_PROBABILITY = 0.03  # Вероятность уйти в АФК
AFK_DURATION_MIN = 120  # 2 минуты
AFK_DURATION_MAX = 600  # 10 минут

# НАСТРОЕНИЕ (НОВОЕ)
RANDOM_MOOD_SHIFT = 0.10  # Случайные смены настроения
MOOD_SHIFT_MAGNITUDE = 15  # Насколько сильно меняется

# РАСПИСАНИЕ АКТИВНОСТИ
ACTIVE_HOURS = {
    'morning': (9, 12),
    'day': (14, 18),
    'evening': (20, 23),
    'night': (0, 3),
}

# ====================================================================
# ЭМОЦИОНАЛЬНАЯ СИСТЕМА
# ====================================================================
INITIAL_MOOD = 70
MOOD_MIN = 20
MOOD_MAX = 100

EMOTION_STATES = {
    'excited': {'energy': 90, 'emotes': ['PogChamp', 'Pog', 'KEKW', 'LUL'], 'typo_chance': 0.25, 'caps_chance': 0.15},
    'happy': {'energy': 75, 'emotes': ['FeelsGoodMan', 'Kappa', 'Okayge'], 'typo_chance': 0.12, 'caps_chance': 0.05},
    'neutral': {'energy': 50, 'emotes': ['Kappa', '4Head'], 'typo_chance': 0.08, 'caps_chance': 0.02},
    'tired': {'energy': 30, 'emotes': ['Sadge', 'FeelsBadMan'], 'typo_chance': 0.05, 'caps_chance': 0.01},
    'grumpy': {'energy': 40, 'emotes': ['WeirdChamp', 'MonkaS'], 'typo_chance': 0.03, 'caps_chance': 0.08},
}

# ====================================================================
# СМАЙЛИКИ И 7TV СИСТЕМА
# ====================================================================
FETCH_7TV_EMOTES = True
FETCH_BTTV_EMOTES = True
FETCH_FFZ_EMOTES = True

EMOTE_COOLDOWN_TIME = 300
EMOTE_REUSE_PENALTY = 0.7
EMOTE_DIVERSITY_BONUS = 1.3
MAX_CONSECUTIVE_SAME_EMOTE = 3

EMOTE_PRIORITIES = {
    '7tv': 1.5,
    'bttv': 1.3,
    'ffz': 1.2,
    'twitch': 1.0,
}

# ====================================================================
# СИСТЕМА ОТВЕТОВ (УЛУЧШЕНО)
# ====================================================================
RESPONSE_PROBABILITY_BASE = 0.16  # Немного уменьшено для реализма
MIN_MESSAGES_BEFORE_RESPONSE = 4  # Уменьшено
RESPONSE_COOLDOWN_MIN = 75  # Уменьшено
RESPONSE_COOLDOWN_MAX = 280  # Увеличено разброс

# Длина ответов
SHORT_RESPONSE_MAX = 120
MEDIUM_RESPONSE_MAX = 250
LONG_RESPONSE_MAX = 400

# КОРОТКИЕ РЕАКЦИИ (НОВОЕ)
SHORT_REACTIONS = [
    'ага', 'да', 'не', 'лол', 'хах', 'хз', 'ок', 'норм', 'найс', 'кек',
    'вау', 'ого', 'блин', 'ну да', 'точно', 'факт', 'мб', 'тру', 'ваще',
    'жиза', 'база', 'кринж', 'имба', 'топ', 'эх', 'ясн', 'пон', 'угу'
]

# ====================================================================
# КОНТЕКСТ И ПАМЯТЬ
# ====================================================================
CONTEXT_WINDOW_SIZE = 20
TOPIC_MEMORY_SIZE = 8
USER_FACT_MEMORY = 10

CONTEXT_WEIGHTS = {
    'mentioned': 3.0,
    'question': 2.0,
    'emotional': 1.5,
    'normal': 1.0,
    'bot_own': 0.5,
}

# ====================================================================
# ОТНОШЕНИЯ С ПОЛЬЗОВАТЕЛЯМИ
# ====================================================================
RELATIONSHIP_LEVELS = {
    'stranger': {'response_bonus': 0.0, 'trust': 0.1},
    'acquaintance': {'response_bonus': 0.1, 'trust': 0.3},
    'friend': {'response_bonus': 0.2, 'trust': 0.6},
    'close_friend': {'response_bonus': 0.3, 'trust': 0.8},
    'favorite': {'response_bonus': 0.4, 'trust': 0.9},
    'toxic': {'response_bonus': -0.3, 'trust': 0.0},
}

# ====================================================================
# АНТИ-ТИШИНА И АКТИВНОСТЬ
# ====================================================================
SILENCE_THRESHOLD = 300
ACTIVITY_CHECK_INTERVAL = 60
ENERGY_DECAY_PER_MESSAGE = 0.5
ENERGY_RESTORE_PER_MINUTE = 1.2

# ====================================================================
# БЕЗОПАСНОСТЬ И ФИЛЬТРЫ
# ====================================================================
FORBIDDEN_WORDS = [
    "пидр", "пидор", "пидарас", "педик", "нигер", "ниггер",
    "хохол", "хач", "жид", "даун", "аутист", "дебил"
]

# ====================================================================
# СИСТЕМНЫЕ НАСТРОЙКИ
# ====================================================================
RETRY_ATTEMPTS = 2
RETRY_DELAY = 2
LOG_LEVEL = "INFO"

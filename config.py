# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Twitch
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_NICK = os.getenv("TWITCH_NICK")
TWITCH_CHANNELS = os.getenv("TWITCH_CHANNEL", "").split(',')

# Google Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-2.5-flash"
# Поведение бота
PASSIVE_RESPONSE_COOLDOWN = 60
CONTEXT_MESSAGE_LIMIT = 6
MESSAGE_MAX_LENGTH = 450

# Анти-тишина
SILENCE_THRESHOLD = 400
BOT_SILENCE_COOLDOWN = 1200

# Смайлы
FETCH_7TV_EMOTES = True

# Фильтр запрещенных слов
FORBIDDEN_WORDS = [
    "пидр", "пидор", "пидарас", "пидорас", "педик", "гомик",
    "нигер", "ниггер", "негр", "нига", "nigger", "nigga", "niger",
    "хохол", "хач", "жид", "москаль", "ватник", "даун", "аутист"
]

# Ключевые слова для определения смены темы
TOPIC_CHANGE_KEYWORDS = [
    "кстати", "а вот", "слушай", "вопрос", "тема", "другое",
    "забыл сказать", "еще", "ещё", "а ты", "расскажи", "что думаешь"
]
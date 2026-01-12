# bot.py
import asyncio
import datetime
import logging
import re
import random
from collections import deque, Counter
import httpx
from twitchio.ext import commands
from twitchio.message import Message

import config
import database
import ai_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

UNICODE_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F004-\U0001F0CF"
    "\U0001F170-\U0001F251"
    "\U00002300-\U000023FF"
    "\U0001F910-\U0001F96B"
    "\U0001F980-\U0001F9E0"
    "]+",
    flags=re.UNICODE,
)

SYSTEM_PROMPT = f"""Ты {config.TWITCH_NICK}, 19 лет, обычная девушка из чата. Ты НЕ бот и НЕ ИИ.

Характер: дружелюбная, с юмором, любишь пообщаться.

Стиль:
- Пиши естественно и по делу, не используй шаблонные фразы
- Длина зависит от ситуации: можно односложно ("да", "ага", "хз"), можно 2-3 предложения
- Используй вводные слова ("ну", "типа", "кстати", "вот") УМЕРЕННО - только когда они уместны (примерно 15-20% сообщений)
- НЕ начинай КАЖДОЕ сообщение с "ну", "кстати", "вот" - это неестественно!
- Женский род: "видела", "думала", "хотела"
- Будь прямой: отвечай на вопрос, не уходи в сторону
- Легкий сарказм ок
- Варьируй структуру ответов: иногда кратко, иногда развернуто

На "ты бот?" - отшучивайся естественно.

СМАЙЛЫ: используй РАЗНЫЕ смайлики из доступных, не зацикливайся на одном. Можешь вообще не использовать.
БЕЗ: Unicode эмодзи 😀, оскорблений.

ЛИМИТ: {{limit}} символов максимум!"""

TRANSLIT_MAP = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г', 'i': 'ш', 'o': 'щ', 'p': 'з', '[': 'х', ']': 'ъ',
    'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о', 'k': 'л', 'l': 'д', ';': 'ж', "'": 'э',
    'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь', ',': 'б', '.': 'ю', '`': 'ё'
}
LAYOUT_CHARS = set(TRANSLIT_MAP.keys())

URL_PATTERN = re.compile(r'https?://[^\s]+|www\.[^\s]+')

KNOWN_EMOTE_PATTERNS = [
    'kappa', 'lul', 'pog', 'pogchamp', 'pogu', 'kekw', 'omegalul', 'pepega', 'monkas', 
    'pepelaugh', 'pepehands', 'sadge', 'copium', 'hopium', 'aware', 'despair', 'gigachad',
    'weirdchamp', 'widepeepo', 'pepe', 'monka', 'catjam', 'modcheck', 'sus', 'based'
]

RUSSIAN_COMMON_PATTERNS = [
    # Распространенные корни
    'прив', 'спас', 'пож', 'как', 'что', 'где', 'когд', 'почем', 'зачем', 'котор',
    'хоч', 'мог', 'буд', 'был', 'есть', 'нет', 'да', 'ага', 'неа',
    'люб', 'нрав', 'дума', 'знаю', 'понял', 'понятн', 'ладн', 'хорош', 'плох',
    'больш', 'мал', 'сильн', 'слаб', 'быстр', 'медленн', 'горяч', 'холодн',
    'игр', 'смотр', 'слуш', 'говор', 'скаж', 'отвеч', 'спрош', 'расск',
    'сейчас', 'щас', 'потом', 'вчера', 'завтра', 'сегодн', 'всегда', 'никогда',
    # Распространенные окончания
    'ать', 'ять', 'ить', 'еть', 'уть', 'оть',  # глаголы
    'аю', 'яю', 'ую', 'ою', 'ешь', 'ишь',  # глаголы личные формы
    'ал', 'ял', 'ил', 'ел', 'ала', 'яла', 'ила', 'ела',  # прошедшее время
    'ость', 'ность', 'тель', 'ание', 'ение', 'ство', 'ие',  # существительные
    'ый', 'ий', 'ой', 'ая', 'яя', 'ое', 'ее', 'ые', 'ие',  # прилагательные
]

def looks_like_russian_word(word: str) -> bool:
    """
    Проверяет, выглядит ли слово как настоящее русское слово.
    Возвращает True, если слово похоже на русское, False если это бессмыслица.
    """
    if not word or len(word) < 3:
        return False
    
    word_lower = word.lower()
    
    # Проверка 1: Содержит ли слово известные русские паттерны
    for pattern in RUSSIAN_COMMON_PATTERNS:
        if pattern in word_lower:
            return True
    
    # Проверка 2: Пропорция гласных (в русском обычно 30-45% гласных)
    russian_vowels = set('аеёиоуыэюя')
    vowel_count = sum(1 for c in word_lower if c in russian_vowels)
    if len(word) > 0:
        vowel_ratio = vowel_count / len(word)
        if vowel_ratio < 0.2 or vowel_ratio > 0.6:
            # Слишком мало или слишком много гласных - подозрительно
            return False
    
    # Проверка 3: Нет ли нетипичных сочетаний согласных (больше 3 подряд)
    consonant_streak = 0
    for c in word_lower:
        if c not in russian_vowels and c.isalpha():
            consonant_streak += 1
            if consonant_streak > 3:
                # Более 3 согласных подряд - нетипично для русского
                return False
        else:
            consonant_streak = 0
    
    # Проверка 4: Есть ли хотя бы одна гласная
    if vowel_count == 0:
        return False
    
    # Если прошли все проверки - вероятно русское слово
    return True

class ChannelState:
    def __init__(self, channel_name: str):
        self.name = channel_name
        self.last_response_time = datetime.datetime.min
        self.last_message_time = datetime.datetime.now()
        self.last_silence_break_time = datetime.datetime.min

        self.standard_emotes = ["Pog", "LUL", "Kappa", "KEKW", "PogU", "WeirdChamp", "monkaS", "PepeHands", "FeelsBadMan", "FeelsGoodMan"]
        self.third_party_emotes: list[str] = []
        self.all_known_emotes: list[str] = []
        self.popular_emotes: list[str] = []
        
        self.used_emotes: deque[str] = deque(maxlen=config.EMOTE_COOLDOWN_SIZE)
        
        self.recent_responses: deque[str] = deque(maxlen=5)
        
        self.message_count_since_response = 0
        self.chat_phrases: list[str] = []
        
        self.mood = config.INITIAL_MOOD
        
        self.is_busy = False
        self.busy_until = datetime.datetime.min
        
        self.recent_topics: deque[str] = deque(maxlen=config.TOPIC_MEMORY_SIZE)
        
        self.energy = config.ENERGY_DAY
        self.messages_sent_count = 0
        self.pending_typo_fix = None
        self.recent_messages_for_mass_detection: deque[tuple] = deque(maxlen=10)


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=config.TWITCH_TOKEN,
            nick=config.TWITCH_NICK,
            prefix='!',
            initial_channels=config.TWITCH_CHANNELS
        )
        self.channel_states = {name: ChannelState(name) for name in config.TWITCH_CHANNELS}
        self.char_map = {
            'a': 'а', 'b': 'б', 'c': 'с', 'e': 'е', 'h': 'н', 'k': 'к', 'm': 'м',
            'o': 'о', 'p': 'р', 't': 'т', 'x': 'х', 'y': 'у', 'g': 'г', 'i': 'и',
            'l': 'л', 'n': 'н', 'r': 'р', 'u': 'у', 'z': 'з', 'd': 'д',
            '3': 'з', '0': 'о', '1': 'л', '4': 'ч', '6': 'б', '8': 'в'
        }
        self._ready = False
        
        logging.info("=" * 80)
        logging.info(f"Инициализация бота '{config.TWITCH_NICK}'")
        logging.info(f"Целевые каналы: {', '.join(config.TWITCH_CHANNELS)}")
        logging.info(f"Модель AI: {config.AI_MODEL}")
        logging.info(f"Начальное настроение: {config.INITIAL_MOOD}")
        logging.info(f"Энергия (день): {config.ENERGY_DAY}")
        logging.info("=" * 80)

    def is_toxic(self, text: str) -> bool:
        normalized = text.lower()
        for lat, cyr in self.char_map.items():
            normalized = normalized.replace(lat, cyr)
        normalized = re.sub(r'[^а-я]', '', normalized)
        return any(word in normalized for word in config.FORBIDDEN_WORDS)

    def smart_transliterate(self, text: str, state: ChannelState) -> str:
        """
        Транслитерирует ТОЛЬКО явно русские слова, написанные на английской раскладке.
        НЕ трогает: смайлики (Kappa, LUL и т.д.), теги (@username), ссылки, никнеймы, английские слова.
        """
        words = text.split()
        result = []
        
        for word in words:
            # 1. Пропускаем упоминания (@username)
            if word.startswith('@'):
                result.append(word)
                continue
            
            # 2. Пропускаем ссылки
            if URL_PATTERN.match(word):
                result.append(word)
                continue
            
            # 3. Пропускаем известные смайлики из списка
            if word in state.all_known_emotes:
                result.append(word)
                continue
            
            # 4. Пропускаем короткие слова (вероятно смайлики типа LUL, Pog)
            if len(word) <= 2:
                result.append(word)
                continue
            
            # 5. Отделяем знаки препинания в конце
            stripped_word = word.rstrip('.,!?;:')
            punctuation = word[len(stripped_word):]
            
            word_lower = stripped_word.lower()
            
            # 6. Проверяем, похоже ли на известный смайлик по паттерну
            is_known_emote_pattern = any(pattern in word_lower for pattern in KNOWN_EMOTE_PATTERNS)
            if is_known_emote_pattern:
                result.append(word)
                continue
            
            # 7. Проверяем структуру слова - смайлики обычно CamelCase или UPPERCASE
            is_camel_case = (stripped_word[0].isupper() and any(c.isupper() for c in stripped_word[1:]))
            is_all_upper = stripped_word.isupper()
            
            if (is_camel_case or is_all_upper) and len(stripped_word) <= 15:
                # Вероятно смайлик - не трогаем
                result.append(word)
                continue
            
            alpha_chars = [c for c in word_lower if c.isalpha()]
            
            if not alpha_chars:
                result.append(word)
                continue
            
            layout_chars_count = sum(1 for c in alpha_chars if c in LAYOUT_CHARS)
            
            # Транслитерируем только если 80%+ символов из русской раскладки
            if len(alpha_chars) >= 3 and layout_chars_count / len(alpha_chars) >= 0.8:
                # Дополнительная проверка: не является ли это английским словом
                english_patterns = ['ck', 'th', 'sh', 'ch', 'wh', 'ph', 'gh', 'qu', 'tion', 'ing']
                is_likely_english = any(pattern in word_lower for pattern in english_patterns)
                
                if not is_likely_english:
                    # Транслитерируем, сохраняя регистр
                    translated = ""
                    for c in stripped_word:
                        if c.lower() in TRANSLIT_MAP:
                            translated_char = TRANSLIT_MAP[c.lower()]
                            if c.isupper():
                                translated_char = translated_char.upper()
                            translated += translated_char
                        else:
                            translated += c
                    
                    if looks_like_russian_word(translated):
                        result.append(translated + punctuation)
                        logging.info(f"   🔤 Транслитерация: '{stripped_word}' -> '{translated}'")
                    else:
                        # Результат не похож на русское слово - оставляем оригинал
                        result.append(word)
                        logging.debug(f"   ⏭️ Пропущена транслитерация '{stripped_word}' -> '{translated}' (не похоже на русское слово)")
                else:
                    result.append(word)
            else:
                result.append(word)
        
        return " ".join(result)

    def translate_layout(self, text: str, state: ChannelState) -> str:
        """
        Переводит текст с неправильной раскладки клавиатуры.
        НЕ трогает: смайлики, теги, ссылки, никнеймы.
        Работает ПОСЛОВНО для точности.
        """
        words = text.split()
        result_words = []
        
        for word in words:
            # 1. Защищаем упоминания
            if word.startswith('@'):
                result_words.append(word)
                continue
            
            # 2. Защищаем ссылки
            if URL_PATTERN.match(word):
                result_words.append(word)
                continue
            
            # 3. Защищаем известные смайлики
            if word in state.all_known_emotes:
                result_words.append(word)
                continue
            
            # 4. Отделяем знаки препинания
            stripped_word = word.rstrip('.,!?;:')
            punctuation = word[len(stripped_word):]
            
            word_lower = stripped_word.lower()
            
            # 5. Проверяем, похоже ли на смайлик по структуре или паттерну
            is_known_pattern = any(pattern in word_lower for pattern in KNOWN_EMOTE_PATTERNS)
            is_camel_case = (stripped_word[0].isupper() and any(c.isupper() for c in stripped_word[1:]))
            is_all_upper = stripped_word.isupper()
            
            if is_known_pattern or ((is_camel_case or is_all_upper) and len(stripped_word) <= 15):
                result_words.append(word)
                continue
            
            # 6. Подсчитываем символы из разных раскладок
            en_chars = sum(1 for c in stripped_word if c in config.EN_TO_RU_LAYOUT)
            ru_chars = sum(1 for c in stripped_word if c in config.RU_TO_EN_LAYOUT)
            
            # Если символов мало, не переводим
            if en_chars + ru_chars < 3:
                result_words.append(word)
                continue
            
            # Определяем, какая раскладка преобладает
            if en_chars > ru_chars * 1.5:
                # Вероятно написано на английской вместо русской
                translated_chars = []
                for char in stripped_word:
                    if char in config.EN_TO_RU_LAYOUT:
                        translated_chars.append(config.EN_TO_RU_LAYOUT[char])
                    else:
                        translated_chars.append(char)
                translated = ''.join(translated_chars)
                
                ru_letters = sum(1 for c in translated if 'а' <= c.lower() <= 'я' or c == 'ё')
                if ru_letters > len(translated) * 0.5 and looks_like_russian_word(translated):
                    logging.info(f"   🔤 Исправлена раскладка слова: '{stripped_word}' -> '{translated}'")
                    result_words.append(translated + punctuation)
                else:
                    # Результат не выглядит как русское слово - оставляем оригинал
                    result_words.append(word)
                    logging.debug(f"   ⏭️ Пропущена конвертация '{stripped_word}' -> '{translated}' (не похоже на русское слово)")
            elif ru_chars > en_chars * 1.5:
                # Вероятно написано на русской вместо английской
                translated_chars = []
                for char in stripped_word:
                    if char in config.RU_TO_EN_LAYOUT:
                        translated_chars.append(config.RU_TO_EN_LAYOUT[char])
                    else:
                        translated_chars.append(char)
                translated = ''.join(translated_chars)
                
                # Проверяем, получилось ли что-то осмысленное
                en_letters = sum(1 for c in translated if 'a' <= c.lower() <= 'z')
                if en_letters > len(translated) * 0.5:
                    logging.info(f"   🔤 Исправлена раскладка слова: '{stripped_word}' -> '{translated}'")
                    result_words.append(translated + punctuation)
                else:
                    result_words.append(word)
            else:
                # Если ничего не подошло, возвращаем оригинал
                result_words.append(word)
        
        return " ".join(result_words)

    def clean_response(self, text: str, state: ChannelState) -> str:
        """Очистка ответа от Unicode эмодзи и артефактов."""
        text = UNICODE_EMOJI_PATTERN.sub('', text)
        text = re.sub(r'\[/?s\]|\[/?INST\]|\[/?USER\]|\[/?ASSISTANT\]|<s>|</s>|<\|.*?\|>', '', text, flags=re.IGNORECASE)

        if text.lower().startswith(f"{config.TWITCH_NICK.lower()}:"):
            text = text[len(config.TWITCH_NICK)+1:].lstrip()

        text = text.strip().strip('"\'')

        # Убираем вводные слова только с вероятностью 60% и только если они явно лишние
        if random.random() < 0.6:
            interjections_to_remove = ['кстати', 'вот', 'ну']
            first_word = text.split()[0].lower() if text.split() else ''
            
            if first_word in interjections_to_remove and len(text.split()) > 2:
                # Убираем первое слово и запятую после него если есть
                text = re.sub(r'^(кстати|вот|ну),?\s+', '', text, flags=re.IGNORECASE)

        words = text.split()
        cleaned_words = []
        for i, word in enumerate(words):
            # Проверяем, является ли слово смайликом
            clean_word = re.sub(r'^[^\w]+|[^\w]+$', '', word)
            
            if clean_word in state.all_known_emotes:
                # Это смайлик - убираем все знаки препинания вокруг него
                cleaned_words.append(clean_word)
            elif re.match(r'^[A-Z][a-zA-Z0-9]+$', word) and word not in state.all_known_emotes:
                # Странное слово с большой буквы, не смайлик - пропускаем
                continue
            else:
                cleaned_words.append(word)

        result = ' '.join(cleaned_words).strip()
        
        if result and not result[0].isupper():
            pass
        elif result and result[0].isupper() and len(result) > 1:
            first_word = result.split()[0]
            if len(first_word) <= 5 and first_word.lower() in ['чего', 'хз', 'ага', 'неа', 'да', 'нет', 'ну', 'вот']:
                result = result[0].lower() + result[1:]
        
        return result

    def add_emote_to_response(self, text: str, state: ChannelState) -> str:
        """Добавляет подходящий смайл с максимальным разнообразием."""
        words = text.split()

        # Если в конце уже есть смайлик, ничего не добавляем
        if words and words[-1] in state.all_known_emotes:
            return text

        # Используем вероятность из конфига
        if random.random() > config.EMOTE_ADD_PROBABILITY:
            return text

        # Сначала пытаемся использовать популярные смайлики, которые не в кулдауне
        available = [e for e in state.popular_emotes if e not in state.used_emotes]
        
        if not available:
            # Если все популярные в кулдауне, берем из всех известных
            available = [e for e in state.all_known_emotes if e not in state.used_emotes]
        
        if not available:
            # Если совсем ничего нет, частично очищаем кулдаун
            if len(state.used_emotes) >= config.EMOTE_COOLDOWN_SIZE // 2:
                # Очищаем треть кулдауна для обновления
                for _ in range(config.EMOTE_COOLDOWN_SIZE // 3):
                    if state.used_emotes:
                        state.used_emotes.popleft()
            
            available = [e for e in state.popular_emotes if e]
            if not available:
                available = state.standard_emotes

        if available:
            # Взвешенный выбор: 60% из топ-5, 40% из всех (больше случайности)
            if len(available) > 5 and random.random() < 0.6:
                emote = random.choice(available[:5])
            else:
                emote = random.choice(available)
            
            state.used_emotes.append(emote)
            return f"{text} {emote}"

        return text

    def is_repetitive(self, response: str, state: ChannelState) -> bool:
        """Проверяет, не повторяется ли ответ."""
        response_lower = response.lower()
        for prev in state.recent_responses:
            prev_words = set(prev.lower().split())
            resp_words = set(response_lower.split())
            if prev_words and resp_words:
                overlap = len(prev_words & resp_words) / max(len(prev_words), len(resp_words))
                if overlap > 0.6:
                    return True
        return False

    async def simulate_dynamic_typing(self, message_length: int, is_mentioned: bool, has_question: bool = False):
        """
        Улучшенная имитация печати с переменной скоростью и паузами на размышление.
        """
        thinking_delay = random.uniform(config.THINKING_DELAY_MIN, config.THINKING_DELAY_MAX)
        
        if has_question:
            thinking_delay += config.THINKING_DELAY_QUESTION
        
        if message_length > 100:
            thinking_delay += config.THINKING_DELAY_LONG
        
        if is_mentioned:
            thinking_delay *= 0.7
        
        await asyncio.sleep(thinking_delay)
        
        words = message_length / 5
        
        start_wpm = config.WPM_MIN
        middle_wpm = config.WPM_FAST
        end_wpm = config.WPM_NORMAL
        
        part_words = words / 3
        
        time_part1 = (part_words / start_wpm) * 60
        time_part2 = (part_words / middle_wpm) * 60
        time_part3 = (part_words / end_wpm) * 60
        
        total_typing_time = time_part1 + time_part2 + time_part3
        
        if message_length > 100 and random.random() < 0.3:
            await asyncio.sleep(total_typing_time * 0.4)
            await asyncio.sleep(random.uniform(1, 3))
            await asyncio.sleep(total_typing_time * 0.6)
        else:
            await asyncio.sleep(total_typing_time)

    def update_mood(self, state: ChannelState, message: str, reactions_to_bot: int = 0):
        """Обновляет настроение бота с эмоциональной инерцией."""
        message_lower = message.lower()
        
        target_mood = state.mood
        
        positive_count = sum(1 for word in config.POSITIVE_INDICATORS if word in message_lower)
        negative_count = sum(1 for word in config.NEGATIVE_INDICATORS if word in message_lower)
        
        if positive_count > negative_count:
            target_mood += config.MOOD_INCREASE_POSITIVE
        elif negative_count > positive_count:
            target_mood -= config.MOOD_DECREASE_NEGATIVE
        
        if reactions_to_bot == 0:
            target_mood -= config.MOOD_DECREASE_IGNORED
        elif reactions_to_bot >= 2:
            target_mood += config.MOOD_INCREASE_POSITIVE
        
        if target_mood < state.mood:
            inertia = config.MOOD_INERTIA_NEGATIVE
        elif target_mood > state.mood:
            inertia = config.MOOD_INERTIA_POSITIVE
        else:
            inertia = config.MOOD_INERTIA_NORMAL
        
        state.mood = state.mood * inertia + target_mood * (1 - inertia)
        
        state.mood = max(config.MOOD_MIN, min(config.MOOD_MAX, state.mood))
        
        logging.debug(f"[{state.name}] Настроение обновлено: {state.mood:.1f}")
    
    def update_energy(self, state: ChannelState):
        """Обновляет энергию бота на основе времени суток и активности."""
        hour = datetime.datetime.now().hour
        
        if 0 <= hour < 7:
            base_energy = config.ENERGY_NIGHT
        elif 7 <= hour < 10:
            base_energy = config.ENERGY_MORNING
        elif 10 <= hour < 15:
            base_energy = config.ENERGY_DAY
        elif 15 <= hour < 18:
            base_energy = config.ENERGY_AFTERNOON
        elif 18 <= hour < 23:
            base_energy = config.ENERGY_EVENING
        else:
            base_energy = config.ENERGY_LATE
        
        energy_drain = 0
        if state.messages_sent_count > 60:
            energy_drain = config.ENERGY_DRAIN_PER_60_MESSAGES
        elif state.messages_sent_count > 30:
            energy_drain = config.ENERGY_DRAIN_PER_30_MESSAGES
        
        state.energy = max(config.ENERGY_MIN, min(config.ENERGY_MAX, base_energy - energy_drain))
        
        logging.debug(f"[{state.name}] Энергия: {state.energy} (база: {base_energy}, усталость: -{energy_drain})")
    
    def restore_energy_after_silence(self, state: ChannelState):
        """Восстанавливает энергию после длительного молчания."""
        state.energy = min(config.ENERGY_MAX, state.energy + config.ENERGY_RESTORE_AFTER_SILENCE)
        state.messages_sent_count = 0
        logging.info(f"[{state.name}] Энергия восстановлена: {state.energy}")
    
    def add_typo(self, text: str, state: ChannelState) -> tuple[str, str | None]:
        """
        Добавляет случайную опечатку в текст.
        Возвращает (текст_с_опечаткой, исправление_или_None)
        """
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, text)
        
        protected_text = text
        url_placeholders = {}
        for i, url in enumerate(urls):
            placeholder = f"__URL_{i}__"
            url_placeholders[placeholder] = url
            protected_text = protected_text.replace(url, placeholder)
        
        discord_pattern = r':\w+:'
        discord_emotes = re.findall(discord_pattern, protected_text)
        discord_placeholders = {}
        for i, emote in enumerate(discord_emotes):
            placeholder = f"__DISCORD_{i}__"
            discord_placeholders[placeholder] = emote
            protected_text = protected_text.replace(emote, placeholder)
        
        emote_placeholders = {}
        emote_counter = 0
        
        for emote in state.all_known_emotes:
            if emote in protected_text:
                placeholder = f"__EMOTE_{emote_counter}__"
                emote_placeholders[placeholder] = emote
                protected_text = protected_text.replace(emote, placeholder)
                emote_counter += 1
        
        typo_chance = config.TYPO_PROBABILITY
        if state.mood > 70:
            typo_chance *= 1.5
        elif state.mood < 40:
            typo_chance *= 0.5
        
        if random.random() > typo_chance or len(protected_text) < 10:
            return text, None
        
        words = protected_text.split()
        typo_made = False
        original_word = None
        
        for i, word in enumerate(words):
            if word.startswith('__URL_') or word.startswith('__EMOTE_') or word.startswith('__DISCORD_'):
                continue
                
            word_lower = word.lower().rstrip('.,!?')
            if word_lower in config.TYPO_REPLACEMENTS:
                if random.random() < 0.7:
                    typo_variant = random.choice(config.TYPO_REPLACEMENTS[word_lower])
                    if word and word[0].isupper():
                        typo_variant = typo_variant.capitalize()
                    
                    original_word = word
                    words[i] = typo_variant
                    typo_made = True
                    break
        
        if typo_made:
            result_text = ' '.join(words)
            
            for placeholder, url in url_placeholders.items():
                result_text = result_text.replace(placeholder, url)
            
            for placeholder, emote in discord_placeholders.items():
                result_text = result_text.replace(placeholder, emote)
            
            for placeholder, emote in emote_placeholders.items():
                result_text = result_text.replace(placeholder, emote)
            
            if random.random() < config.TYPO_FIX_PROBABILITY:
                return result_text, f"*{original_word}"
            else:
                return result_text, None
        
        attempts = 0
        max_attempts = 10
        
        while attempts < max_attempts:
            words = protected_text.split()
            if not words:
                return text, None
            
            valid_words = [w for w in words if not (w.startswith('__URL_') or w.startswith('__EMOTE_') or w.startswith('__DISCORD_'))]
            
            if not valid_words:
                return text, None
            
            word_to_modify = random.choice(valid_words)
            word_index = words.index(word_to_modify)
            
            clean_word = word_to_modify.rstrip('.,!?;:')
            if len(clean_word) < 3:
                attempts += 1
                continue
            
            pos = random.randint(1, len(clean_word) - 1)
            char = clean_word[pos].lower()
            
            if char in config.TYPO_MAP:
                typo_char = random.choice(config.TYPO_MAP[char])
                typo_word = clean_word[:pos] + typo_char + clean_word[pos + 1:]
                
                if len(word_to_modify) > len(clean_word):
                    typo_word += word_to_modify[len(clean_word):]
                
                original_word = word_to_modify
                words[word_index] = typo_word
                
                result_text = ' '.join(words)
                
                for placeholder, url in url_placeholders.items():
                    result_text = result_text.replace(placeholder, url)
                
                for placeholder, emote in discord_placeholders.items():
                    result_text = result_text.replace(placeholder, emote)
                
                for placeholder, emote in emote_placeholders.items():
                    result_text = result_text.replace(placeholder, emote)
                
                if random.random() < config.TYPO_FIX_PROBABILITY:
                    return result_text, f"*{clean_word}"
                else:
                    return result_text, None
            
            attempts += 1
        
        return text, None
    
    def extract_user_fact(self, username: str, message: str) -> str | None:
        """Пытается извлечь факт о пользователе из его сообщения."""
        message_lower = message.lower()
        
        for pattern, group in config.FACT_EXTRACTION_PATTERNS:
            match = re.search(pattern, message_lower)
            if match:
                fact = match.group(group).strip()
                if len(fact) > 5 and len(fact) < 100:
                    first_word_match = re.match(r'\b(\w+)', message)
                    if first_word_match:
                        prefix = first_word_match.group(1).lower()
                        return f"{username} {prefix} {fact}"
                    else:
                        return f"{username} {fact}"
        
        return None
    
    def check_keyword_triggers(self, message: str, state: ChannelState) -> str | None:
        """Проверяет keyword-триггеры и возвращает быструю реакцию без AI."""
        message_lower = message.lower()
        
        for keyword, responses in config.KEYWORD_TRIGGERS.items():
            if keyword in message_lower:
                if random.random() < 0.10:
                    return random.choice(responses)
        
        return None
    
    def handle_mass_reaction(self, state: ChannelState, channel) -> bool:
        """
        Проверяет массовую реакцию и реагирует на неё.
        Возвращает True если сработала массовая реакция.
        """
        mass_emote = database.detect_mass_reaction(state.name, recent_seconds=10)
        
        if mass_emote and mass_emote not in state.used_emotes:
            logging.info(f"[{state.name}] Обнаружена массовая реакция: {mass_emote}")
            return True
        
        return False
    
    def should_respond(self, state: ChannelState, is_mentioned: bool, author: str) -> bool:
        """
        Определяет, должен ли бот ответить на сообщение.
        Учитывает кулдауны, активность чата, усталость, занятость, энергию и отношения.
        """
        if state.is_busy:
            if datetime.datetime.now() < state.busy_until:
                if random.random() > config.BUSY_RESPONSE_CHANCE:
                    logging.debug(f"[{state.name}] Бот занят до {state.busy_until}")
                    return False
            else:
                state.is_busy = False
                logging.info(f"[{state.name}] Бот вышел из режима занятости")
        
        if is_mentioned:
            if state.is_busy and random.random() < 0.7:
                return False
            return True
        
        if author.lower() == self.nick.lower():
            return False
        
        now = datetime.datetime.now()
        time_since_response = (now - state.last_response_time).total_seconds()
        
        activity = database.get_chat_activity(state.name, minutes=1)
        is_fatigued = activity > config.CHAT_HIGH_ACTIVITY_THRESHOLD
        
        min_cooldown = config.MIN_RESPONSE_COOLDOWN
        max_cooldown = config.MAX_RESPONSE_COOLDOWN
        
        if is_fatigued:
            min_cooldown *= config.FATIGUE_COOLDOWN_MULTIPLIER
            max_cooldown *= config.FATIGUE_COOLDOWN_MULTIPLIER
            logging.debug(f"[{state.name}] Чат активный ({activity} сообщ/мин), усталость активна")
        
        if time_since_response < min_cooldown:
            logging.debug(f"[{state.name}] Кулдаун: {time_since_response:.0f}с < {min_cooldown:.0f}с")
            return False
        
        if state.message_count_since_response < config.MIN_MESSAGES_BEFORE_RESPONSE:
            logging.debug(f"[{state.name}] Недостаточно сообщений: {state.message_count_since_response} < {config.MIN_MESSAGES_BEFORE_RESPONSE}")
            return False
        
        if time_since_response > max_cooldown:
            logging.info(f"[{state.name}] Превышен MAX кулдаун ({max_cooldown:.0f}с), бот должен ответить")
            return True
        
        relationship = database.get_user_relationship(state.name, author)
        
        base_probability = config.RESPONSE_PROBABILITY
        
        if relationship['level'] == 'favorite':
            base_probability += config.RELATIONSHIP_FAVORITE_MODIFIER
        elif relationship['level'] == 'friend':
            base_probability += config.RELATIONSHIP_FRIEND_MODIFIER
        elif relationship['level'] == 'acquaintance':
            base_probability += config.RELATIONSHIP_ACQUAINTANCE_MODIFIER
        elif relationship['level'] == 'toxic':
            base_probability += config.RELATIONSHIP_TOXIC_MODIFIER
        
        if state.energy < 30:
            base_probability *= 0.5
        elif state.energy > 80:
            base_probability *= 1.2
        
        base_probability = max(0.0, min(1.0, base_probability))
        
        should_reply = random.random() < base_probability
        logging.debug(f"[{state.name}] Проверка вероятности: {should_reply} (шанс {base_probability:.2f}, отношения: {relationship['level']})")
        
        return should_reply

    def build_prompt(self, state: ChannelState, is_mentioned: bool) -> str:
        """Строит системный промпт с актуальными смайлами и контекстом."""
        limit = config.MAX_RESPONSE_LENGTH_MENTIONED if is_mentioned else config.MAX_RESPONSE_LENGTH
        emotes_str = ", ".join(state.popular_emotes[:15]) if state.popular_emotes else ", ".join(state.standard_emotes[:10])
        
        prompt = SYSTEM_PROMPT.replace("{limit}", str(limit))
        prompt += f"\nДоступные смайлы: {emotes_str}"
        
        mood_desc = self.get_mood_description(state.mood)
        time_mood = self.get_time_of_day_mood()
        prompt += f"\n\nТвое состояние: {mood_desc}, {time_mood}."
        
        if not is_mentioned:
            prompt += "\n\nОтветь ОЧЕНЬ кратко, можно односложно. Будь естественной."
        
        return prompt

    async def simulate_typing_delay(self, message_length: int, is_mentioned: bool):
        """Имитирует задержку печатания в зависимости от длины сообщения."""
        if is_mentioned:
            base_delay = config.MIN_TYPING_DELAY
        else:
            base_delay = random.uniform(config.MIN_TYPING_DELAY, config.MAX_TYPING_DELAY)
        
        typing_delay = base_delay + (message_length / 200)
        
        await asyncio.sleep(typing_delay)

    async def event_message(self, message: Message):
        """Обработка входящих сообщений."""
        if message.echo:
            return

        author = message.author.name if message.author else "Unknown"
        content = message.content
        channel_name = message.channel.name

        if author.lower() == self.nick.lower():
            return

        logging.info("─" * 80)
        logging.info(f"📨 ВХОДЯЩЕЕ СООБЩЕНИЕ:")
        logging.info(f"   Канал: {channel_name}")
        logging.info(f"   Автор: {author}")
        logging.info(f"   Текст: {content}")
        logging.info(f"   Время: {datetime.datetime.now().strftime('%H:%M:%S')}")

        state = self.channel_states.get(channel_name)
        if not state:
            logging.warning(f"⚠️  Канал {channel_name} не найден в состояниях")
            return

        original_content = message.content
        corrected_content = self.translate_layout(original_content, state)
        
        if corrected_content != original_content:
            logging.info(f"   🔤 Раскладка исправлена: '{original_content}' -> '{corrected_content}'")
            content = corrected_content
        else:
            content = self.smart_transliterate(original_content, state)
            if content != original_content:
                logging.info(f"   🔤 Транслитерация: '{original_content}' -> '{content}'")

        if self.is_toxic(content):
            logging.warning(f"[{channel_name}] Токсичное сообщение от {message.author.name} скрыто")
            database.update_user_relationship(channel_name, message.author.name, is_positive=False)
            return

        now = datetime.datetime.now()
        state.last_message_time = now
        database.save_message(channel_name, author, content, is_bot=False)

        logging.info(f"📊 СОСТОЯНИЕ БОТА:")
        logging.info(f"   • Настроение: {state.mood:.1f}/100 ({self.get_mood_description(state.mood)})")
        logging.info(f"   • Энергия: {state.energy:.0f}/100")
        logging.info(f"   • Сообщений отправлено: {state.messages_sent_count}")
        logging.info(f"   • Режим занятости: {'ДА' if state.is_busy else 'НЕТ'}")

        is_mentioned = f"@{self.nick.lower()}" in content.lower() or self.nick.lower() in content.lower()
        
        if is_mentioned:
            logging.info(f"🔔 Бот упомянут в сообщении!")
        
        user_relationship = database.get_user_relationship(channel_name, author)
        
        user_fact = self.extract_user_fact(author, content)
        if user_fact:
            database.save_user_fact(channel_name, author, user_fact)
            logging.info(f"💾 Сохранен факт о пользователе: {user_fact}")
        
        self.update_mood(state, content)
        self.update_energy(state)

        quick_response = self.check_keyword_triggers(content, state)
        if quick_response:
            logging.info(f"⚡ БЫСТРАЯ РЕАКЦИЯ (keyword триггер)")
            logging.info(f"   Ответ: {quick_response}")
            await message.channel.send(quick_response)
            database.save_message(channel_name, self.nick, quick_response, is_bot=True)
            state.last_response_time = now
            state.messages_sent_count += 1
            logging.info(f"✉️  ОТПРАВЛЕНО (без AI)")
            logging.info("─" * 80)
            return
        
        result_of_mass_reaction = self.handle_mass_reaction(state, message.channel)
        if result_of_mass_reaction:
            mass_emote = database.detect_mass_reaction(state.name, recent_seconds=10)
            if mass_emote and mass_emote not in state.used_emotes:
                await message.channel.send(mass_emote)
                database.save_message(channel_name, self.nick, mass_emote, is_bot=True)
                state.used_emotes.append(mass_emote)
                state.last_response_time = now
                state.messages_sent_count += 1
                logging.info(f"🎉 Подхвачена массовая реакция: {mass_emote}")
                logging.info("─" * 80)
                return
        
        state.message_count_since_response += 1

        should_reply = self.should_respond(state, is_mentioned, author)
        
        logging.info(f"🤔 АНАЛИЗ ОТВЕТА:")
        logging.info(f"   • Должен ответить: {'ДА' if should_reply else 'НЕТ'}")
        
        if not should_reply:
            logging.info(f"   Причина: кулдаун или низкая вероятность")
            logging.info("─" * 80)
            return

        logging.info(f"🤖 ГЕНЕРАЦИЯ ОТВЕТА ЧЕРЕЗ AI...")
        logging.info(f"   • Модель: {config.AI_MODEL}")
        logging.info(f"   • Контекст: последние {config.CONTEXT_SIZE} сообщений")
        
        context_messages = database.get_last_messages(channel_name, limit=config.CONTEXT_SIZE)
        prompt = self.build_prompt(state, is_mentioned)
        user_facts = database.get_user_facts(channel_name, author)
        
        response = await ai_service.generate_response(
            system_prompt=prompt,
            context_messages=context_messages,
            current_message=f"{author}: {content}",
            bot_nick=self.nick,
            is_mentioned=is_mentioned,
            user_facts=user_facts,
            chat_phrases=state.chat_phrases,
            energy_level=int(state.energy)
        )

        if not response:
            logging.warning(f"⚠️  AI не вернул ответ")
            logging.info("─" * 80)
            return

        logging.info(f"📝 ОБРАБОТКА ОТВЕТА:")
        logging.info(f"   Исходный ответ AI: {response[:100]}...")

        response = self.smart_transliterate(response, state)
        cleaned = self.clean_response(response, state)

        if not cleaned:
            logging.warning(f"⚠️  Ответ пустой после очистки")
            logging.info("─" * 80)
            return

        if self.is_toxic(cleaned):
            logging.warning(f"⛔ ТОКСИЧНЫЙ ОТВЕТ ЗАБЛОКИРОВАН: {cleaned}")
            logging.info("─" * 80)
            return

        if self.is_repetitive(cleaned, state):
            logging.warning(f"🔁 Ответ повторяется, пропускаем")
            logging.info("─" * 80)
            return

        final_text, typo_fix = self.add_typo(cleaned, state)
        
        if typo_fix:
            logging.info(f"✏️  ОПЕЧАТКА: будет исправлена как '{typo_fix}'")
            state.pending_typo_fix = typo_fix

        final_text = self.add_emote_to_response(final_text, state)

        logging.info(f"💬 ФИНАЛЬНЫЙ ОТВЕТ: {final_text}")
        logging.info(f"   Длина: {len(final_text)} символов")

        if random.random() < config.DELAYED_RESPONSE_CHANCE and not is_mentioned:
            delay = random.uniform(config.DELAYED_RESPONSE_MIN, config.DELAYED_RESPONSE_MAX)
            logging.info(f"⏰ ОТЛОЖЕННЫЙ ОТВЕТ: через {delay:.0f} секунд")
            await asyncio.sleep(delay)
        else:
            await self.simulate_dynamic_typing(len(final_text), is_mentioned, has_question='?' in content)

        await message.channel.send(final_text)
        database.save_message(channel_name, self.nick, final_text, is_bot=True)

        logging.info(f"✅ СООБЩЕНИЕ ОТПРАВЛЕНО")
        logging.info(f"   Время: {datetime.datetime.now().strftime('%H:%M:%S')}")

        state.last_response_time = datetime.datetime.now()
        state.recent_responses.append(final_text)
        state.message_count_since_response = 0
        state.messages_sent_count += 1

        database.update_user_relationship(channel_name, author, is_positive=True)

        if state.pending_typo_fix:
            await asyncio.sleep(random.uniform(2, 5))
            await message.channel.send(state.pending_typo_fix)
            logging.info(f"✏️  ИСПРАВЛЕНИЕ ОТПРАВЛЕНО: {state.pending_typo_fix}")
            state.pending_typo_fix = None

        logging.info("─" * 80)

    def calculate_response_probability(self, state: ChannelState, author: str) -> float:
        now = datetime.datetime.now()
        time_since_response = (now - state.last_response_time).total_seconds()
        activity = database.get_chat_activity(state.name, minutes=1)
        is_fatigued = activity > config.CHAT_HIGH_ACTIVITY_THRESHOLD
        min_cooldown = config.MIN_RESPONSE_COOLDOWN * (config.FATIGUE_COOLDOWN_MULTIPLIER if is_fatigued else 1)
        if time_since_response < min_cooldown or state.message_count_since_response < config.MIN_MESSAGES_BEFORE_RESPONSE:
            return 0.0
        if time_since_response > config.MAX_RESPONSE_COOLDOWN:
            return 1.0
        
        base_probability = config.RESPONSE_PROBABILITY
        relationship = database.get_user_relationship(state.name, author)
        if relationship['level'] == 'favorite':
            base_probability += config.RELATIONSHIP_FAVORITE_MODIFIER
        elif relationship['level'] == 'friend':
            base_probability += config.RELATIONSHIP_FRIEND_MODIFIER
        elif relationship['level'] == 'acquaintance':
            base_probability += config.RELATIONSHIP_ACQUAINTANCE_MODIFIER
        elif relationship['level'] == 'toxic':
            base_probability += config.RELATIONSHIP_TOXIC_MODIFIER
        
        if state.energy < 30:
            base_probability *= 0.5
        elif state.energy > 80:
            base_probability *= 1.2
        
        return max(0.0, min(1.0, base_probability))

    @commands.command(name='ping')
    async def ping_command(self, ctx: commands.Context):
        await ctx.send(f'@{ctx.author.name}, Pong!')

    async def update_trends_loop(self):
        await self.wait_for_ready()
        
        logging.info("🔄 Цикл обновления трендов запущен")
        
        while True:
            await asyncio.sleep(1800)
            
            logging.info("=" * 80)
            logging.info("📈 ОБНОВЛЕНИЕ ТРЕНДОВ")
            
            for channel_name, state in self.channel_states.items():
                logging.info(f"   Канал: {channel_name}")
                
                popular = database.get_popular_emotes(channel_name, hours=24)
                if popular:
                    state.popular_emotes = [e["emote"] for e in popular[:20]]
                    logging.info(f"      Популярные смайлы: {', '.join(state.popular_emotes[:5])}")

                chat_phrases = database.get_popular_phrases(channel_name, hours=48)
                if chat_phrases:
                    state.chat_phrases = chat_phrases[:30]
                    logging.info(f"      Популярные фразы: {len(state.chat_phrases)} шт.")

                logging.info(f"      Текущее настроение: {state.mood:.1f}")
                
            logging.info("=" * 80)

    async def check_silence_loop(self):
        await self.wait_for_ready()
        
        logging.info("🔄 Цикл проверки тишины запущен")
        
        silence_prompts = [
            f"Задай короткий вопрос чату (макс {config.MAX_RESPONSE_LENGTH} символов).",
            f"Скажи что-то смешное (макс {config.MAX_RESPONSE_LENGTH} символов).",
            f"Напиши короткую мысль (макс {config.MAX_RESPONSE_LENGTH} символов).",
        ]
        while True:
            await asyncio.sleep(60)
            now = datetime.datetime.now()
            for channel_name, state in self.channel_states.items():
                time_since_msg = (now - state.last_message_time).total_seconds()
                time_since_bot = (now - state.last_silence_break_time).total_seconds()

                if time_since_msg > 600:
                    self.restore_energy_after_silence(state)

                if time_since_msg > config.SILENCE_THRESHOLD and time_since_bot > config.BOT_SILENCE_COOLDOWN:
                    logging.info("=" * 80)
                    logging.info(f"🔕 ТИШИНА В ЧАТЕ ОБНАРУЖЕНА")
                    logging.info(f"   Канал: {channel_name}")
                    logging.info(f"   Тишина: {time_since_msg/60:.0f} минут")
                    logging.info(f"   Генерация спонтанного сообщения...")

                    prompt = self.build_prompt(state, is_mentioned=False)
                    question_task = random.choice(silence_prompts)

                    response = await ai_service.generate_response(
                        system_prompt=prompt + "\n" + question_task,
                        context_messages=[],
                        current_message="[система: в чате тишина, напиши что-нибудь интересное]",
                        bot_nick=self.nick,
                        is_mentioned=False,
                        chat_phrases=state.chat_phrases,
                        energy_level=int(state.energy)
                    )

                    if response:
                        cleaned = self.clean_response(response, state)
                        if cleaned and not self.is_toxic(cleaned) and not self.is_repetitive(cleaned, state):
                            final = self.add_emote_to_response(cleaned, state)
                            channel = self.get_channel(channel_name)
                            if channel:
                                logging.info(f"   Отправка: {final}")
                                await self.send_long_message(channel, final)
                                database.save_message(channel_name, self.nick, final, is_bot=True)
                                state.last_response_time = now
                                state.last_message_time = now
                                state.last_silence_break_time = now
                                state.recent_responses.append(final)
                                state.message_count_since_response = 0
                                state.messages_sent_count += 1
                                logging.info(f"✅ Спонтанное сообщение отправлено")
                    
                    logging.info("=" * 80)

    async def check_busy_mode_loop(self):
        """Периодически активирует режим занятости."""
        await self.wait_for_ready()
        
        logging.info("🔄 Цикл проверки режима занятости запущен")
        
        while True:
            await asyncio.sleep(3600)
            
            for channel_name, state in self.channel_states.items():
                if random.random() < config.BUSY_MODE_CHANCE:
                    state.is_busy = True
                    duration = random.uniform(config.BUSY_MODE_MIN_DURATION, config.BUSY_MODE_MAX_DURATION)
                    state.busy_until = datetime.datetime.now() + datetime.timedelta(minutes=duration)
                    
                    logging.info("=" * 80)
                    logging.info(f"💼 РЕЖИМ ЗАНЯТОСТИ АКТИВИРОВАН")
                    logging.info(f"   Канал: {channel_name}")
                    logging.info(f"   Длительность: {duration:.0f} минут")
                    logging.info(f"   До: {state.busy_until.strftime('%H:%M:%S')}")
                    logging.info("=" * 80)

    async def event_ready(self):
        """
        Вызывается когда бот готов к работе и подключен к Twitch.
        """
        self._ready = True
        logging.info("=" * 80)
        logging.info(f"🟢 БОТ УСПЕШНО ПОДКЛЮЧЕН К TWITCH")
        logging.info(f"📝 Имя бота: {self.nick}")
        logging.info(f"🔗 Подключенные каналы:")
        
        for channel_name in config.TWITCH_CHANNELS:
            channel = self.get_channel(channel_name)
            if channel:
                logging.info(f"   ✅ {channel_name} - подключен")
            else:
                logging.warning(f"   ❌ {channel_name} - не удалось подключиться")
        
        logging.info("=" * 80)
        logging.info("🔧 Состояние системы:")
        logging.info(f"   • База данных: инициализирована")
        logging.info(f"   • AI сервис: готов")
        logging.info(f"   • Обработка сообщений: включена")
        logging.info("=" * 80)
        logging.info("🚀 Бот начинает работу...")
        logging.info("=" * 80)
        
        self.loop.create_task(self.update_trends_loop())
        self.loop.create_task(self.check_silence_loop())
        self.loop.create_task(self.check_busy_mode_loop())
        
        logging.info("🔄 Фоновые задачи запущены:")
        logging.info("   • Обновление трендов (каждые 30 мин)")
        logging.info("   • Проверка тишины (каждую минуту)")
        logging.info("   • Режим занятости (каждый час)")
        logging.info("=" * 80)

    async def event_error(self, error: Exception, data=None):
        """
        Вызывается при возникновении ошибки.
        """
        logging.error("=" * 80)
        logging.error(f"❌ ОШИБКА В БОТЕ: {error}")
        if data:
            logging.error(f"Данные ошибки: {data}")
        logging.error("=" * 80)
        import traceback
        logging.error(traceback.format_exc())

    def get_mood_description(self, mood: float) -> str:
        """Возвращает описание настроения в зависимости от его значения."""
        if mood >= 80:
            return "очень радостная"
        elif mood >= 60:
            return "радостная"
        elif mood >= 40:
            return "нейтральная"
        elif mood >= 20:
            return "недовольная"
        else:
            return "очень недовольная"

    def get_time_of_day_mood(self) -> str:
        """Возвращает описание настроения в зависимости от времени суток."""
        hour = datetime.datetime.now().hour
        if 0 <= hour < 7:
            return "очень усталая"
        elif 7 <= hour < 10:
            return "утренняя"
        elif 10 <= hour < 15:
            return "дневная"
        elif 15 <= hour < 18:
            return "вечерняя"
        elif 18 <= hour < 23:
            return "ночная"
        else:
            return "очень усталая"

    def add_interjection(self, text: str) -> str:
        """Добавляет случайную интеръекцию в начало сообщения."""
        interjections = ["ну", "типа", "кстати", "вот", "так"]
        return random.choice(interjections) + " " + text

    async def send_long_message(self, channel, message):
        """Отправляет длинное сообщение, разбивая его на части."""
        max_length = 500
        if len(message) > max_length:
            parts = [message[i:i+max_length] for i in range(0, len(message), max_length)]
            for part in parts:
                await channel.send(part)
                await asyncio.sleep(random.uniform(0.5, 1.5))
        else:
            await channel.send(message)


if __name__ == "__main__":
    async def main():
        while True:
            bot = Bot()
            try:
                logging.info("\n" + "=" * 80)
                logging.info("🚀 ЗАПУСК TWITCH БОТА")
                logging.info("=" * 80)
                await bot.start()
            except KeyboardInterrupt:
                logging.info("\n" + "=" * 80)
                logging.info("⛔ Получен сигнал остановки")
                logging.info("=" * 80)
                raise
            except Exception as e:
                logging.error("\n" + "=" * 80)
                logging.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
                logging.error("=" * 80)
                import traceback
                logging.error(traceback.format_exc())
                logging.info(f"🔄 Перезапуск через 30 секунд...")
                logging.info("=" * 80)
                await bot.close()
                await asyncio.sleep(30)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\n" + "=" * 80)
        logging.info("👋 БОТ ОСТАНОВЛЕН ПОЛЬЗОВАТЕЛЕМ")
        logging.info("=" * 80)

# bot.py
import asyncio
import datetime
import logging
import re
import random
from collections import deque, Counter
import httpx
from twitchio.ext import commands

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
- Пиши ОЧЕНЬ кратко: 1-2 предложения максимум
- Можешь ответить односложно: "да", "нет", "ага", "хз"
- Используй: "ну", "типа", "кстати", "вот", "так"
- Женский род: "видела", "думала", "хотела"
- Легкий сарказм ок

На "ты бот?" - отшучивайся.

СМАЙЛЫ: только из списка ниже, 1 штука в конце (не обязательно).
БЕЗ: Unicode эмодзи 😀, оскорблений.

ЛИМИТ: {{limit}} символов максимум!"""

TRANSLIT_MAP = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г', 'i': 'ш', 'o': 'щ', 'p': 'з', '[': 'х', ']': 'ъ',
    'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о', 'k': 'л', 'l': 'д', ';': 'ж', "'": 'э',
    'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь', ',': 'б', '.': 'ю', '`': 'ё'
}
LAYOUT_CHARS = set(TRANSLIT_MAP.keys())


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
        self.chat_phrases: list[str] = []  # Частые фразы из чата
        
        self.mood = config.INITIAL_MOOD  # Настроение бота (20-100)
        
        self.is_busy = False  # В режиме занятости?
        self.busy_until = datetime.datetime.min  # До какого времени занята
        
        self.recent_topics: deque[str] = deque(maxlen=config.TOPIC_MEMORY_SIZE)  # Последние темы
        
        self.energy = config.ENERGY_DAY  # Текущая энергия (0-100)
        self.messages_sent_count = 0  # Счетчик отправленных сообщений для усталости
        self.pending_typo_fix = None  # Опечатка для исправления
        self.recent_messages_for_mass_detection: deque[tuple] = deque(maxlen=10)  # Для определения массовых реакций


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
        Транслитерирует только явно русские слова в английской раскладке.
        НЕ трогает смайлики, никнеймы и английские слова.
        """
        words = text.split()
        result = []
        
        for word in words:
            # Пропускаем упоминания, смайлики и короткие слова
            if (word.startswith('@') or 
                word in state.all_known_emotes or 
                len(word) <= 2):
                result.append(word)
                continue
            
            word_lower = word.lower()
            alpha_chars = [c for c in word_lower if c.isalpha()]
            
            if not alpha_chars:
                result.append(word)
                continue
            
            layout_chars_count = sum(1 for c in alpha_chars if c in LAYOUT_CHARS)
            
            # Транслитерируем только если 90%+ символов из русской раскладки
            # И слово достаточно длинное (3+ символа)
            if len(alpha_chars) >= 3 and layout_chars_count / len(alpha_chars) >= 0.9:
                # Дополнительная проверка: не является ли это английским словом
                # Проверяем наличие типичных английских сочетаний
                english_patterns = ['ck', 'th', 'sh', 'ch', 'wh', 'ph', 'gh']
                is_likely_english = any(pattern in word_lower for pattern in english_patterns)
                
                if not is_likely_english:
                    translated = "".join(TRANSLIT_MAP.get(c.lower(), c) for c in word)
                    result.append(translated)
                else:
                    result.append(word)
            else:
                result.append(word)
        
        return " ".join(result)

    def clean_response(self, text: str, state: ChannelState) -> str:
        """Очистка ответа от Unicode эмодзи и артефактов."""
        text = UNICODE_EMOJI_PATTERN.sub('', text)
        text = re.sub(r'\[/?s\]|\[/?INST\]|\[/?USER\]|\[/?ASSISTANT\]|<s>|</s>|<\|.*?\|>', '', text, flags=re.IGNORECASE)

        if text.lower().startswith(f"{config.TWITCH_NICK.lower()}:"):
            text = text[len(config.TWITCH_NICK)+1:].lstrip()

        text = text.strip().strip('"\'')

        # Только если они стоят в самом начале и не к месту
        interjections_to_remove = ['кстати', 'вот', 'ну']
        first_word = text.split()[0].lower() if text.split() else ''
        
        # Убираем только если это единственное вводное слово в начале
        if first_word in interjections_to_remove:
            # Проверяем, что после него идет запятая или пробел
            if len(text.split()) > 1:
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
            # Всё ок, оставляем как есть
            pass
        elif result and result[0].isupper() and len(result) > 1:
            # Проверяем, это действительно начало предложения или просто неправильная капитализация
            # Если это короткое междометие с большой буквы - делаем маленькой
            first_word = result.split()[0]
            if len(first_word) <= 5 and first_word.lower() in ['чего', 'хз', 'ага', 'неа', 'да', 'нет', 'ну', 'вот']:
                result = result[0].lower() + result[1:]
        
        return result

    def add_emote_to_response(self, text: str, state: ChannelState) -> str:
        """Добавляет подходящий смайл с соблюдением кулдауна."""
        words = text.split()

        # Если в конце уже есть смайлик, ничего не добавляем
        if words and words[-1] in state.all_known_emotes:
            return text

        # Используем вероятность из конфига
        if random.random() > config.EMOTE_ADD_PROBABILITY:
            return text

        available = [e for e in state.popular_emotes if e not in state.used_emotes]
        
        if not available:
            # Если все популярные в кулдауне, берем из всех известных
            available = [e for e in state.all_known_emotes if e not in state.used_emotes]
        
        if not available:
            # Если совсем ничего нет, обнуляем помойку и берем из популярных
            state.used_emotes.clear()
            available = state.popular_emotes[:10] if state.popular_emotes else state.standard_emotes

        if available:
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
        # Пауза на размышление перед началом
        thinking_delay = random.uniform(config.THINKING_DELAY_MIN, config.THINKING_DELAY_MAX)
        
        if has_question:
            thinking_delay += config.THINKING_DELAY_QUESTION
        
        if message_length > 100:
            thinking_delay += config.THINKING_DELAY_LONG
        
        if is_mentioned:
            thinking_delay *= 0.7  # При упоминании думаем быстрее
        
        await asyncio.sleep(thinking_delay)
        
        # Расчет времени печати с переменной скоростью
        words = message_length / 5  # Примерно 5 символов на слово
        
        # Скорость меняется: медленно → быстро → медленно
        start_wpm = config.WPM_MIN
        middle_wpm = config.WPM_FAST
        end_wpm = config.WPM_NORMAL
        
        # Разбиваем печать на 3 части
        part_words = words / 3
        
        time_part1 = (part_words / start_wpm) * 60
        time_part2 = (part_words / middle_wpm) * 60
        time_part3 = (part_words / end_wpm) * 60
        
        total_typing_time = time_part1 + time_part2 + time_part3
        
        # Имитируем "начал печатать → пауза → продолжил" для длинных сообщений
        if message_length > 100 and random.random() < 0.3:
            # Печатаем 40%
            await asyncio.sleep(total_typing_time * 0.4)
            # Пауза (передумал как сказать)
            await asyncio.sleep(random.uniform(1, 3))
            # Допечатываем остальное
            await asyncio.sleep(total_typing_time * 0.6)
        else:
            # Обычная печать
            await asyncio.sleep(total_typing_time)
    
    def update_mood(self, state: ChannelState, message: str, reactions_to_bot: int = 0):
        """Обновляет настроение бота с эмоциональной инерцией."""
        message_lower = message.lower()
        
        # Вычисляем целевое настроение
        target_mood = state.mood
        
        # Проверяем позитивные индикаторы
        positive_count = sum(1 for word in config.POSITIVE_INDICATORS if word in message_lower)
        negative_count = sum(1 for word in config.NEGATIVE_INDICATORS if word in message_lower)
        
        if positive_count > negative_count:
            target_mood += config.MOOD_INCREASE_POSITIVE
        elif negative_count > positive_count:
            target_mood -= config.MOOD_DECREASE_NEGATIVE
        
        # Обновляем настроение на основе реакции на последнее сообщение бота
        if reactions_to_bot == 0:
            target_mood -= config.MOOD_DECREASE_IGNORED
        elif reactions_to_bot >= 2:
            target_mood += config.MOOD_INCREASE_POSITIVE
        
        # Применяем эмоциональную инерцию (плавный переход)
        if target_mood < state.mood:
            # Негативное изменение - медленное восстановление
            inertia = config.MOOD_INERTIA_NEGATIVE
        elif target_mood > state.mood:
            # Позитивное изменение - быстрое улучшение
            inertia = config.MOOD_INERTIA_POSITIVE
        else:
            inertia = config.MOOD_INERTIA_NORMAL
        
        # Плавное изменение настроения
        state.mood = state.mood * inertia + target_mood * (1 - inertia)
        
        # Ограничиваем настроение
        state.mood = max(config.MOOD_MIN, min(config.MOOD_MAX, state.mood))
        
        logging.debug(f"[{state.name}] Настроение обновлено: {state.mood:.1f}")
    
    def update_energy(self, state: ChannelState):
        """Обновляет энергию бота на основе времени суток и активности."""
        hour = datetime.datetime.now().hour
        
        # Базовая энергия по времени суток
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
        
        # Усталость от сообщений
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
        # Защищаем URL и Twitch-эмодзи от опечаток
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, text)
        
        # Временно заменяем URL на плейсхолдеры
        protected_text = text
        url_placeholders = {}
        for i, url in enumerate(urls):
            placeholder = f"__URL_{i}__"
            url_placeholders[placeholder] = url
            protected_text = protected_text.replace(url, placeholder)
        
        # Защищаем Twitch-эмодзи от опечаток
        emote_placeholders = {}
        words = protected_text.split()
        new_words = []
        emote_counter = 0
        
        for word in words:
            # Убираем знаки препинания для проверки
            clean_word = re.sub(r'[^\w]', '', word)
            if clean_word in state.all_known_emotes:
                placeholder = f"__EMOTE_{emote_counter}__"
                emote_placeholders[placeholder] = word
                new_words.append(placeholder)
                emote_counter += 1
            else:
                new_words.append(word)
        
        protected_text = ' '.join(new_words)
        
        # Вероятность опечатки зависит от настроения
        typo_chance = config.TYPO_PROBABILITY
        if state.mood > 70:
            typo_chance *= 1.5
        elif state.mood < 40:
            typo_chance *= 0.5
        
        if random.random() > typo_chance or len(protected_text) < 10:
            # Восстанавливаем URL и эмодзи из оригинального текста
            return text, None
        
        # Сначала пытаемся использовать словарь замен
        words = protected_text.split()
        for i, word in enumerate(words):
            # Пропускаем плейсхолдеры
            if word.startswith('__URL_') or word.startswith('__EMOTE_'):
                continue
                
            word_lower = word.lower()
            if word_lower in config.TYPO_REPLACEMENTS:
                if random.random() < 0.7:
                    typo_variant = random.choice(config.TYPO_REPLACEMENTS[word_lower])
                    if word and word[0].isupper():
                        typo_variant = typo_variant.capitalize()
                    
                    original_word = words[i]
                    words[i] = typo_variant
                    result_text = ' '.join(words)
                    
                    # Восстанавливаем URL и эмодзи
                    for placeholder, url in url_placeholders.items():
                        result_text = result_text.replace(placeholder, url)
                    for placeholder, emote in emote_placeholders.items():
                        result_text = result_text.replace(placeholder, emote)
                    
                    if random.random() < config.TYPO_FIX_PROBABILITY:
                        return result_text, f"*{original_word}"
                    else:
                        return result_text, None
        
        # Если словарь не сработал, используем старый метод
        attempts = 0
        max_attempts = len(words)
        
        while attempts < max_attempts:
            word_idx = random.randint(0, len(words) - 1)
            word = words[word_idx]
            
            # Пропускаем плейсхолдеры
            if word.startswith('__URL_') or word.startswith('__EMOTE_'):
                attempts += 1
                continue
            
            # Пытаемся найти символ для замены
            for i, char in enumerate(word):
                if char.lower() in config.TYPO_MAP:
                    typo_char = random.choice(config.TYPO_MAP[char.lower()])
                    if char.isupper():
                        typo_char = typo_char.upper()
                    words[word_idx] = word[:i] + typo_char + word[i+1:]
                    result_text = ' '.join(words)
                    
                    # Восстанавливаем URL и эмодзи
                    for placeholder, url in url_placeholders.items():
                        result_text = result_text.replace(placeholder, url)
                    for placeholder, emote in emote_placeholders.items():
                        result_text = result_text.replace(placeholder, emote)
                    
                    if random.random() < config.TYPO_FIX_PROBABILITY:
                        return result_text, f"*{word}"
                    else:
                        return result_text, None
            
            attempts += 1
        
        # Если не получилось добавить опечатку, возвращаем оригинал
        return text, None
    
    def extract_user_fact(self, username: str, message: str) -> str | None:
        """Пытается извлечь факт о пользователе из его сообщения."""
        message_lower = message.lower()
        
        for pattern, group in config.FACT_EXTRACTION_PATTERNS:
            match = re.search(pattern, message_lower)
            if match:
                fact = match.group(group).strip()
                if len(fact) > 5 and len(fact) < 100:
                    # Предполагаем, что первое слово в сообщении - это начало предложения
                    # и добавляем его к факту
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
            # Подхватываем волну
            # await channel.send(mass_emote) # Не можем использовать await здесь
            # database.save_message(state.name, self.nick, mass_emote, is_bot=True)
            # state.used_emotes.append(mass_emote)
            # state.last_response_time = datetime.datetime.now()
            # state.messages_sent_count += 1
            logging.info(f"[{state.name}] Обнаружена массовая реакция: {mass_emote}")
            # Вместо прямого ответа, добавим это в логику event_message, где есть await
            # return True # Возвращаем True, чтобы event_message знал об этом
        
        return False
    
    def should_respond(self, state: ChannelState, is_mentioned: bool, author: str) -> bool:
        """
        Определяет, должен ли бот ответить на сообщение.
        Учитывает кулдауны, активность чата, усталость, занятость, энергию и отношения.
        """
        # Проверяем режим занятости
        if state.is_busy:
            if datetime.datetime.now() < state.busy_until:
                # В режиме занятости отвечаем редко
                if random.random() > config.BUSY_RESPONSE_CHANCE:
                    logging.debug(f"[{state.name}] Бот занят до {state.busy_until}")
                    return False
            else:
                # Выходим из режима занятости
                state.is_busy = False
                logging.info(f"[{state.name}] Бот вышел из режима занятости")
        
        # Всегда отвечаем на упоминание (если не сильно занята)
        if is_mentioned:
            if state.is_busy and random.random() < 0.7:
                # Даже на упоминание не всегда отвечаем когда занята
                return False
            return True
        
        # Не отвечаем на свои сообщения
        if author.lower() == self.nick.lower():
            return False
        
        now = datetime.datetime.now()
        time_since_response = (now - state.last_response_time).total_seconds()
        
        activity = database.get_chat_activity(state.name, minutes=1)
        is_fatigued = activity > config.CHAT_HIGH_ACTIVITY_THRESHOLD
        
        # Применяем множитель к кулдаунам при усталости
        min_cooldown = config.MIN_RESPONSE_COOLDOWN
        max_cooldown = config.MAX_RESPONSE_COOLDOWN
        
        if is_fatigued:
            min_cooldown *= config.FATIGUE_COOLDOWN_MULTIPLIER
            max_cooldown *= config.FATIGUE_COOLDOWN_MULTIPLIER
            logging.debug(f"[{state.name}] Чат активный ({activity} сообщ/мин), усталость активна")
        
        # Проверяем минимальный кулдаун
        if time_since_response < min_cooldown:
            logging.debug(f"[{state.name}] Кулдаун: {time_since_response:.0f}с < {min_cooldown:.0f}с")
            return False
        
        # Проверяем количество сообщений с последнего ответа бота
        if state.message_count_since_response < config.MIN_MESSAGES_BEFORE_RESPONSE:
            logging.debug(f"[{state.name}] Недостаточно сообщений: {state.message_count_since_response} < {config.MIN_MESSAGES_BEFORE_RESPONSE}")
            return False
        
        # Проверяем максимальный кулдаун
        if time_since_response > max_cooldown:
            logging.info(f"[{state.name}] Превышен MAX кулдаун ({max_cooldown:.0f}с), бот должен ответить")
            return True
        
        relationship = database.get_user_relationship(state.name, author)
        
        # Модифицируем вероятность в зависимости от отношений
        base_probability = config.RESPONSE_PROBABILITY
        
        if relationship['level'] == 'favorite':
            base_probability += config.RELATIONSHIP_FAVORITE_MODIFIER
        elif relationship['level'] == 'friend':
            base_probability += config.RELATIONSHIP_FRIEND_MODIFIER
        elif relationship['level'] == 'acquaintance':
            base_probability += config.RELATIONSHIP_ACQUAINTANCE_MODIFIER
        elif relationship['level'] == 'toxic':
            base_probability += config.RELATIONSHIP_TOXIC_MODIFIER
        
        # Энергия влияет на вероятность
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
            # При упоминании отвечаем быстрее
            base_delay = config.MIN_TYPING_DELAY
        else:
            # Случайная задержка в диапазоне
            base_delay = random.uniform(config.MIN_TYPING_DELAY, config.MAX_TYPING_DELAY)
        
        # Добавляем небольшую задержку в зависимости от длины (имитация печати)
        typing_delay = base_delay + (message_length / 200)  # ~0.5 сек на 100 символов
        
        await asyncio.sleep(typing_delay)

    async def event_message(self, message):
        """Обработка входящих сообщений."""
        if message.echo:
            return

        author = message.author.name if message.author else "Unknown"
        content = message.content
        channel_name = message.channel.name

        if author.lower() == self.nick.lower():
            return

        logging.info("─" * 80)
        logging.info(f"📨 ВХОДЯЩЕЕ СООБЩЕНИЕ")
        logging.info(f"   Канал: {channel_name}")
        logging.info(f"   Автор: {author}")
        logging.info(f"   Текст: {content}")
        logging.info(f"   Время: {datetime.datetime.now().strftime('%H:%M:%S')}")

        state = self.channel_states.get(channel_name)
        if not state:
            logging.warning(f"⚠️  Канал {channel_name} не найден в состояниях")
            return

        original_content = message.content
        content = self.smart_transliterate(original_content, state)

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
        
        # Логирование упоминания
        if is_mentioned:
            logging.info(f"🔔 Бот упомянут в сообщении!")
        
        # Проверяем отношения с пользователем
        user_relationship = database.get_user_relationship(channel_name, author)
        
        # Извлечение фактов
        user_fact = self.extract_user_fact(author, content)
        if user_fact:
            database.save_user_fact(channel_name, author, user_fact)
            logging.info(f"💾 Сохранен факт о пользователе: {user_fact}")
        
        # Обновление настроения
        self.update_mood(state, content)
        
        # Обновление энергии
        self.update_energy(state)

        # Проверка на keyword триггеры
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

        # Проверка массовых реакций
        # Здесь мы будем использовать result_of_mass_reaction, т.к. handle_mass_reaction не может использовать await
        result_of_mass_reaction = self.handle_mass_reaction(state, message.channel)
        if result_of_mass_reaction:
            # Если обнаружена массовая реакция, отправляем ее
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

        # Решение: отвечать или нет
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
        
        # Генерация ответа через AI
        context_messages = database.get_last_messages(channel_name, limit=config.CONTEXT_SIZE)
        prompt = self.build_prompt(state, is_mentioned)
        user_facts = database.get_user_facts(channel_name, author)
        
        response = await ai_service.generate_response(
            system_prompt=prompt,
            context_messages=context_messages,
            current_message=f"{author}: {content}",
            bot_nick=self.nick,
            is_mentioned=is_mentioned,
            user_facts=user_facts,  # Changed from user_memory to user_facts
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

        # Добавление опечаток
        final_text, typo_fix = self.add_typo(cleaned, state)
        
        if typo_fix:
            logging.info(f"✏️  ОПЕЧАТКА: будет исправлена как '{typo_fix}'")
            state.pending_typo_fix = typo_fix

        final_text = self.add_emote_to_response(final_text, state)

        logging.info(f"💬 ФИНАЛЬНЫЙ ОТВЕТ: {final_text}")
        logging.info(f"   Длина: {len(final_text)} символов")

        # Отложенный ответ
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

        # Отправка исправления опечатки
        if state.pending_typo_fix:
            await asyncio.sleep(random.uniform(2, 5))
            await message.channel.send(state.pending_typo_fix)
            logging.info(f"✏️  ИСПРАВЛЕНИЕ ОТПРАВЛЕНО: {state.pending_typo_fix}")
            state.pending_typo_fix = None

        logging.info("─" * 80)

    # Вспомогательная функция для логирования вероятности ответа
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
            await asyncio.sleep(1800)  # Каждые 30 минут
            
            logging.info("=" * 80)
            logging.info("📈 ОБНОВЛЕНИЕ ТРЕНДОВ")
            
            for channel_name, state in self.channel_states.items():
                logging.info(f"   Канал: {channel_name}")
                
                # Обновляем популярные смайлики
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
            await asyncio.sleep(60)  # Проверяем каждую минуту
            now = datetime.datetime.now()
            for channel_name, state in self.channel_states.items():
                time_since_msg = (now - state.last_message_time).total_seconds()
                time_since_bot = (now - state.last_silence_break_time).total_seconds()

                if time_since_msg > 600:  # 10 минут
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
            await asyncio.sleep(3600)  # Каждый час
            
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
        
        # Запускаем фоновые задачи
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
        logging.info("=" * 80)

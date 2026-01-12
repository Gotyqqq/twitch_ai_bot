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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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

        return ' '.join(cleaned_words).strip()

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
        # Вероятность опечатки зависит от настроения
        typo_chance = config.TYPO_PROBABILITY
        if state.mood > 70:
            typo_chance *= 1.5  # Больше опечаток в хорошем настроении
        elif state.mood < 40:
            typo_chance *= 0.5  # Меньше опечаток в плохом настроении
        
        if random.random() > typo_chance or len(text) < 10:
            return text, None
        
        # Сначала пытаемся использовать словарь замен
        words = text.split()
        for i, word in enumerate(words):
            word_lower = word.lower()
            if word_lower in config.TYPO_REPLACEMENTS:
                if random.random() < 0.7:  # 70% шанс использовать словарь
                    typo_variant = random.choice(config.TYPO_REPLACEMENTS[word_lower])
                    # Сохраняем регистр первой буквы
                    if word[0].isupper():
                        typo_variant = typo_variant.capitalize()
                    
                    original_word = words[i]
                    words[i] = typo_variant
                    result_text = ' '.join(words)
                    
                    # Решаем, исправлять ли опечатку
                    if random.random() < config.TYPO_FIX_PROBABILITY:
                        return result_text, f"*{original_word}"
                    else:
                        return result_text, None
        
        # Если словарь не сработал, используем старый метод
        word_idx = random.randint(0, len(words) - 1)
        word = words[word_idx]
        
        for i, char in enumerate(word):
            if char.lower() in config.TYPO_MAP:
                typo_char = random.choice(config.TYPO_MAP[char.lower()])
                if char.isupper():
                    typo_char = typo_char.upper()
                words[word_idx] = word[:i] + typo_char + word[i+1:]
                result_text = ' '.join(words)
                
                if random.random() < config.TYPO_FIX_PROBABILITY:
                    return result_text, f"*{word}"
                else:
                    return result_text, None
        
        return text, None
    
    def check_keyword_triggers(self, message: str, state: ChannelState) -> str | None:
        """Проверяет keyword-триггеры и возвращает быструю реакцию без AI."""
        message_lower = message.lower()
        
        for keyword, responses in config.KEYWORD_TRIGGERS.items():
            if keyword in message_lower:
                # 50% шанс сработать
                if random.random() < 0.5:
                    return random.choice(responses)
        
        return None
    
    async def handle_mass_reaction(self, state: ChannelState, channel) -> bool:
        """
        Проверяет массовую реакцию и реагирует на неё.
        Возвращает True если сработала массовая реакция.
        """
        mass_emote = database.detect_mass_reaction(state.name, recent_seconds=10)
        
        if mass_emote and mass_emote not in state.used_emotes:
            # Подхватываем волну
            await channel.send(mass_emote)
            database.save_message(state.name, self.nick, mass_emote, is_bot=True)
            state.used_emotes.append(mass_emote)
            state.last_response_time = datetime.datetime.now()
            state.messages_sent_count += 1
            logging.info(f"[{state.name}] Подхвачена массовая реакция: {mass_emote}")
            return True
        
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

    def should_respond(self, state: ChannelState, is_mentioned: bool, author: str) -> bool:
        """
        Определяет, должен ли бот ответить на сообщение.
        Учитывает кулдауны, активность чата, усталость, АФК и рандом.
        """
        if state.is_afk:
            if datetime.datetime.now() < state.afk_until:
                logging.debug(f"[{state.name}] Бот в АФК до {state.afk_until}")
                return False
            else:
                # Выходим из АФК
                state.is_afk = False
                logging.info(f"[{state.name}] Бот вышел из АФК")
        
        # Всегда отвечаем на упоминание
        if is_mentioned:
            return True
        
        # Не отвечаем на свои сообщения (на всякий случай)
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
        
        # Используем вероятность
        should_reply = random.random() < config.RESPONSE_PROBABILITY
        logging.debug(f"[{state.name}] Проверка вероятности: {should_reply} (шанс {config.RESPONSE_PROBABILITY})")
        
        return should_reply

    async def event_message(self, message):
        """Обработка входящих сообщений."""
        if message.echo:
            return

        channel_name = message.channel.name
        state = self.channel_states.get(channel_name)
        if not state:
            return

        original_content = message.content
        content = self.smart_transliterate(original_content, state)

        if self.is_toxic(content):
            logging.warning(f"[{channel_name}] Токсичное сообщение от {message.author.name} скрыто")
            database.update_user_relationship(channel_name, message.author.name, is_positive=False)
            return

        now = datetime.datetime.now()
        state.last_message_time = now
        author = message.author.name

        state.message_count_since_response += 1

        self.update_energy(state)

        reactions = database.get_last_bot_response_reactions(channel_name)
        self.update_mood(state, content, reactions)

        user_fact = self.extract_user_fact(author, content)
        if user_fact:
            database.save_user_fact(channel_name, author, user_fact)
            logging.debug(f"[{channel_name}] Сохранен факт: {user_fact}")

        logging.info(f"[{channel_name}] {author}: {content} (сообщений: {state.message_count_since_response}, настроение: {state.mood:.1f}, энергия: {state.energy})")
        database.save_message(channel_name, author, content, is_bot=False)

        await self.handle_commands(message)
        if message.content.startswith('!'):
            return

        is_mentioned = f"@{self.nick.lower()}" in content.lower()
        
        if await self.handle_mass_reaction(state, message.channel):
            return
        
        quick_response = self.check_keyword_triggers(content, state)
        if quick_response and not is_mentioned:
            # Быстрая реакция без AI
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await message.channel.send(quick_response)
            database.save_message(channel_name, self.nick, quick_response, is_bot=True)
            state.last_response_time = now
            state.messages_sent_count += 1
            logging.info(f"[{channel_name}] Быстрая реакция (keyword): {quick_response}")
            return
        
        if self.should_respond(state, is_mentioned, author):
            should_delay = (not is_mentioned and 
                          random.random() < config.DELAYED_RESPONSE_PROBABILITY)
            
            if should_delay:
                delay_time = random.uniform(config.DELAYED_RESPONSE_MIN, config.DELAYED_RESPONSE_MAX)
                logging.info(f"[{channel_name}] Ответ отложен на {delay_time:.0f} секунд")
                await asyncio.sleep(delay_time)
            
            if state.is_busy and not is_mentioned:
                busy_response = random.choice(config.BUSY_SHORT_RESPONSES)
                await asyncio.sleep(random.uniform(1, 2))
                await message.channel.send(busy_response)
                database.save_message(channel_name, self.nick, busy_response, is_bot=True)
                state.last_response_time = now
                state.messages_sent_count += 1
                logging.info(f"[{channel_name}] Короткий ответ (занята): {busy_response}")
                return
            
            logging.info(f"[{channel_name}] Решение: генерировать ответ (упоминание: {is_mentioned})")
            
            activity = database.get_chat_activity(channel_name, minutes=1)
            is_fatigued = activity > config.CHAT_HIGH_ACTIVITY_THRESHOLD
            should_short_reply = (is_fatigued or state.energy < 30) and random.random() < config.FATIGUE_SHORT_RESPONSE_CHANCE
            
            has_question = '?' in content
            await self.simulate_dynamic_typing(len(content), is_mentioned, has_question)
            
            context = database.get_last_messages(channel_name, limit=config.CONTEXT_MESSAGE_LIMIT)
            prompt = self.build_prompt(state, is_mentioned and not should_short_reply)
            
            if should_short_reply:
                prompt += "\n\nОтветь МАКСИМАЛЬНО кратко, буквально 1-3 слова."
                logging.info(f"[{channel_name}] Усталость/низкая энергия: будет короткий ответ")

            hot_topics = database.get_hot_topics(channel_name, time_minutes=10)
            user_facts = database.get_user_facts(channel_name, author)
            mood_state = self.get_mood_description(state.mood)
            
            relationship = database.get_user_relationship(channel_name, author)

            response = await ai_service.generate_response(
                system_prompt=prompt,
                context_messages=context,
                current_message=f"{author}: {content}",
                bot_nick=self.nick,
                is_mentioned=is_mentioned and not should_short_reply,
                chat_phrases=state.chat_phrases,
                hot_topics=hot_topics,
                user_facts=user_facts if random.random() < config.RECALL_USER_FACT_PROBABILITY else None,
                mood_state=mood_state,
                energy_level=int(state.energy),
                relationship_level=relationship['level']
            )

            if response:
                cleaned = self.clean_response(response, state)

                if self.is_repetitive(cleaned, state):
                    logging.info(f"[{channel_name}] Ответ повторяется, пропускаем")
                    return

                if cleaned and not self.is_toxic(cleaned):
                    final_response = self.add_interjection(cleaned)
                    
                    final_response, typo_fix = self.add_typo(final_response, state)
                    
                    final_response = self.add_emote_to_response(final_response, state)
                    
                    should_split = (random.random() < config.SPLIT_MESSAGE_PROBABILITY and 
                                  len(final_response) > 50 and 
                                  not is_mentioned)
                    
                    if should_split:
                        # Разбиваем на две части
                        words = final_response.split()
                        mid = len(words) // 2
                        part1 = ' '.join(words[:mid])
                        part2 = ' '.join(words[mid:])
                        
                        await message.channel.send(part1)
                        await asyncio.sleep(random.uniform(1, 2))
                        await message.channel.send(part2)
                        
                        database.save_message(channel_name, self.nick, part1, is_bot=True)
                        database.save_message(channel_name, self.nick, part2, is_bot=True)
                        final_response = f"{part1} {part2}"
                    else:
                        await self.send_long_message(message.channel, final_response)
                        database.save_message(channel_name, self.nick, final_response, is_bot=True)
                    
                    if typo_fix:
                        await asyncio.sleep(random.uniform(2, 5))
                        await message.channel.send(typo_fix)
                        database.save_message(channel_name, self.nick, typo_fix, is_bot=True)

                    state.last_response_time = now
                    state.recent_responses.append(final_response)
                    state.message_count_since_response = 0
                    state.messages_sent_count += 1
                    
                    database.update_user_relationship(channel_name, author, is_positive=True)
                    
                    if random.random() < config.BUSY_PROBABILITY:
                        busy_duration = random.uniform(config.BUSY_MIN_DURATION, config.BUSY_MAX_DURATION)
                        state.is_busy = True
                        state.busy_until = now + datetime.timedelta(seconds=busy_duration)
                        logging.info(f"[{channel_name}] Бот вошел в режим занятости на {busy_duration/60:.1f} минут")
                else:
                    logging.warning(f"[{channel_name}] Ответ пустой или токсичный: '{response}'")
        else:
            logging.debug(f"[{channel_name}] Решение: не отвечать")

    @commands.command(name='ping')
    async def ping_command(self, ctx: commands.Context):
        await ctx.send(f'@{ctx.author.name}, Pong!')

    async def update_trends_loop(self):
        await self.wait_for_ready()
        
        while True:
            for channel_name, state in self.channel_states.items():
                # Обновляем популярные слова и смайлики
                _, top_emotes = await asyncio.to_thread(
                    database.get_chat_trends, channel_name, state.all_known_emotes
                )
                if top_emotes:
                    state.popular_emotes = top_emotes
                
                chat_phrases = await asyncio.to_thread(
                    database.get_chat_phrases, channel_name
                )
                if chat_phrases:
                    state.chat_phrases = chat_phrases
                    logging.info(f"[{channel_name}] Обновлены фразы чата: {chat_phrases[:3]}...")
                
                logging.info(f"[{channel_name}] Тренды обновлены. Смайлы: {state.popular_emotes[:5]}, Настроение: {state.mood}")
            
            await asyncio.sleep(1800)  # Каждые 30 минут

    async def check_silence_loop(self):
        await self.wait_for_ready()
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
                    logging.info(f"[{channel_name}] Тишина в чате, генерация вопроса...")

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
                                await self.send_long_message(channel, final)
                                database.save_message(channel_name, self.nick, final, is_bot=True)
                                state.last_response_time = now
                                state.last_message_time = now
                                state.last_silence_break_time = now
                                state.recent_responses.append(final)
                                state.message_count_since_response = 0
                                state.messages_sent_count += 1


if __name__ == "__main__":
    async def main():
        while True:
            bot = Bot()
            try:
                logging.info("Запуск бота...")
                await bot.start()
            except Exception as e:
                logging.error(f"Ошибка: {e}. Перезапуск через 30 секунд.")
                await bot.close()
                await asyncio.sleep(30)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")

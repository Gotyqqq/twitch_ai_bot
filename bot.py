# bot.py
import asyncio
import datetime
import logging
import re
import random
from collections import deque
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
        self.is_afk = False  # В режиме АФК?
        self.afk_until = datetime.datetime.min  # До какого времени АФК
        self.recent_topics: deque[str] = deque(maxlen=config.TOPIC_MEMORY_SIZE)  # Последние темы


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

    def add_typo(self, text: str) -> str:
        """Добавляет случайную опечатку в текст."""
        if random.random() > config.TYPO_PROBABILITY or len(text) < 10:
            return text
        
        words = text.split()
        if not words:
            return text
        
        # Выбираем случайное слово
        word_idx = random.randint(0, len(words) - 1)
        word = words[word_idx]
        
        # Ищем букву для замены
        for i, char in enumerate(word):
            if char.lower() in config.TYPO_MAP:
                typo_char = random.choice(config.TYPO_MAP[char.lower()])
                # Сохраняем регистр
                if char.isupper():
                    typo_char = typo_char.upper()
                words[word_idx] = word[:i] + typo_char + word[i+1:]
                break
        
        return ' '.join(words)
    
    def add_interjection(self, text: str) -> str:
        """Добавляет междометие в начало сообщения."""
        if random.random() > config.INTERJECTION_PROBABILITY:
            return text
        
        interjection = random.choice(config.INTERJECTIONS)
        
        # Если сообщение короткое или уже начинается с междометия, не добавляем
        if len(text) < 15 or text.lower().startswith(tuple(config.INTERJECTIONS)):
            return text
        
        # Решаем, добавлять запятую или нет
        if random.random() < 0.5:
            return f"{interjection}, {text}"
        else:
            return f"{interjection} {text}"
    
    def update_mood(self, state: ChannelState, message: str, reactions_to_bot: int = 0):
        """Обновляет настроение бота на основе сообщений."""
        message_lower = message.lower()
        
        # Проверяем позитивные индикаторы
        positive_count = sum(1 for word in config.POSITIVE_INDICATORS if word in message_lower)
        negative_count = sum(1 for word in config.NEGATIVE_INDICATORS if word in message_lower)
        
        if positive_count > negative_count:
            state.mood += config.MOOD_INCREASE_POSITIVE
        elif negative_count > positive_count:
            state.mood -= config.MOOD_DECREASE_NEGATIVE
        
        # Обновляем настроение на основе реакции на последнее сообщение бота
        if reactions_to_bot == 0:
            state.mood -= config.MOOD_DECREASE_IGNORED
        elif reactions_to_bot >= 2:
            state.mood += config.MOOD_INCREASE_POSITIVE
        
        # Ограничиваем настроение
        state.mood = max(config.MOOD_MIN, min(config.MOOD_MAX, state.mood))
        
        logging.debug(f"[{state.name}] Настроение обновлено: {state.mood}")
    
    def get_mood_description(self, mood: int) -> str:
        """Возвращает описание настроения для промпта."""
        if mood >= 80:
            return "отличное, веселая и энергичная"
        elif mood >= 60:
            return "хорошее, дружелюбная"
        elif mood >= 40:
            return "нейтральное, спокойная"
        else:
            return "не очень, немного грустная или уставшая"
    
    def get_time_of_day_mood(self) -> str:
        """Возвращает описание состояния в зависимости от времени суток."""
        hour = datetime.datetime.now().hour
        
        if config.MORNING_START <= hour < config.EVENING_START:
            return "бодрая, день в разгаре"
        elif config.EVENING_START <= hour < config.NIGHT_START:
            return "активная, вечер - лучшее время"
        else:
            return "сонная, поздно уже"
    
    def extract_user_fact(self, username: str, message: str) -> str | None:
        """Пытается извлечь факт о пользователе из его сообщения."""
        message_lower = message.lower()
        
        # Паттерны для извлечения фактов
        patterns = [
            (r'я (играю|люблю|смотрю|слушаю|занимаюсь) (.+)', 2),
            (r'у меня (.+)', 1),
            (r'я (.+ лет|работаю|учусь)', 1),
        ]
        
        for pattern, group in patterns:
            match = re.search(pattern, message_lower)
            if match:
                fact = match.group(group).strip()
                if len(fact) > 5 and len(fact) < 100:
                    return f"{username} {match.group(1)} {fact}"
        
        return None

    async def event_ready(self):
        logging.info(f'Бот {self.nick} запущен. Каналы: {", ".join(config.TWITCH_CHANNELS)}')
        for channel_name in config.TWITCH_CHANNELS:
            database.init_db(channel_name)
        await self.fetch_and_prepare_emotes()

        asyncio.create_task(self.update_trends_loop())
        asyncio.create_task(self.check_silence_loop())
        logging.info("Фоновые задачи запущены.")

    async def fetch_and_prepare_emotes(self):
        if not config.FETCH_7TV_EMOTES:
            return
        logging.info("Загрузка 7TV смайлов...")
        try:
            users = await self.fetch_users(names=config.TWITCH_CHANNELS)
            if not users:
                return
            user_map = {user.name: user.id for user in users}

            async with httpx.AsyncClient() as http_client:
                for channel_name, state in self.channel_states.items():
                    user_id = user_map.get(channel_name)
                    if not user_id:
                        continue
                    try:
                        response = await http_client.get(f"https://7tv.io/v3/users/twitch/{user_id}")
                        if response.status_code == 200:
                            data = response.json()
                            emote_set = data.get('emote_set', {})
                            if emote_set and 'emotes' in emote_set:
                                state.third_party_emotes = [e['name'] for e in emote_set['emotes']]
                                logging.info(f"[{channel_name}] Загружено {len(state.third_party_emotes)} 7TV смайлов")
                    except Exception as e:
                        logging.warning(f"[{channel_name}] Ошибка загрузки 7TV: {e}")

                    state.all_known_emotes = state.standard_emotes + state.third_party_emotes
                    state.popular_emotes = state.all_known_emotes[:20]
        except Exception as e:
            logging.error(f"Ошибка при получении смайлов: {e}")

    async def send_long_message(self, channel, text: str):
        """Отправляет длинное сообщение, разбивая на части по 450 символов."""
        if len(text) <= config.MESSAGE_MAX_LENGTH:
            await channel.send(text)
            return

        words = text.split()
        current = ""
        
        for word in words:
            test_line = f"{current} {word}".strip() if current else word
            
            if len(test_line) > config.MESSAGE_MAX_LENGTH:
                # Отправляем текущую часть
                if current:
                    await channel.send(current)
                    await asyncio.sleep(1.8)  # Небольшая задержка между частями
                current = word
            else:
                current = test_line
        
        # Отправляем остаток
        if current:
            await channel.send(current)

    def build_prompt(self, state: ChannelState, is_mentioned: bool = False) -> str:
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
            return

        now = datetime.datetime.now()
        state.last_message_time = now
        author = message.author.name

        state.message_count_since_response += 1

        reactions = database.get_last_bot_response_reactions(channel_name)
        self.update_mood(state, content, reactions)

        user_fact = self.extract_user_fact(author, content)
        if user_fact:
            database.save_user_fact(channel_name, author, user_fact)
            logging.debug(f"[{channel_name}] Сохранен факт: {user_fact}")

        logging.info(f"[{channel_name}] {author}: {content} (сообщений с последнего ответа: {state.message_count_since_response}, настроение: {state.mood})")
        database.save_message(channel_name, author, content, is_bot=False)

        await self.handle_commands(message)
        if message.content.startswith('!'):
            return

        is_mentioned = f"@{self.nick.lower()}" in content.lower()
        
        if self.should_respond(state, is_mentioned, author):
            logging.info(f"[{channel_name}] Решение: генерировать ответ (упоминание: {is_mentioned})")
            
            activity = database.get_chat_activity(channel_name, minutes=1)
            is_fatigued = activity > config.CHAT_HIGH_ACTIVITY_THRESHOLD
            should_short_reply = is_fatigued and random.random() < config.FATIGUE_SHORT_RESPONSE_CHANCE
            
            await self.simulate_typing_delay(len(content), is_mentioned)
            
            context = database.get_last_messages(channel_name, limit=config.CONTEXT_MESSAGE_LIMIT)
            prompt = self.build_prompt(state, is_mentioned and not should_short_reply)
            
            if should_short_reply:
                prompt += "\n\nОтветь МАКСИМАЛЬНО кратко, буквально 1-3 слова."
                logging.info(f"[{channel_name}] Усталость: будет короткий ответ")

            hot_topics = database.get_hot_topics(channel_name, time_minutes=10)
            user_facts = database.get_user_facts(channel_name, author)
            mood_state = self.get_mood_description(state.mood)

            response = await ai_service.generate_response(
                system_prompt=prompt,
                context_messages=context,
                current_message=f"{author}: {content}",
                bot_nick=self.nick,
                is_mentioned=is_mentioned and not should_short_reply,
                chat_phrases=state.chat_phrases,
                hot_topics=hot_topics,
                user_facts=user_facts if random.random() < config.RECALL_USER_FACT_PROBABILITY else None,
                mood_state=mood_state
            )

            if response:
                cleaned = self.clean_response(response, state)

                if self.is_repetitive(cleaned, state):
                    logging.info(f"[{channel_name}] Ответ повторяется, пропускаем")
                    return

                if cleaned and not self.is_toxic(cleaned):
                    final_response = self.add_interjection(cleaned)
                    final_response = self.add_typo(final_response)
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

                    state.last_response_time = now
                    state.recent_responses.append(final_response)
                    state.message_count_since_response = 0
                    
                    if random.random() < config.AFK_PROBABILITY:
                        afk_duration = random.uniform(config.AFK_MIN_DURATION, config.AFK_MAX_DURATION)
                        state.is_afk = True
                        state.afk_until = now + datetime.timedelta(seconds=afk_duration)
                        logging.info(f"[{channel_name}] Бот ушел в АФК на {afk_duration/60:.1f} минут")
                else:
                    logging.warning(f"[{channel_name}] Ответ пустой или токсичный: '{response}'")
        else:
            logging.debug(f"[{channel_name}] Решение: не отвечать")

    @commands.command(name='ping')
    async def ping_command(self, ctx: commands.Context):
        await ctx.send(f'@{ctx.author.name}, Pong!')

    async def update_trends_loop(self):
        await self.wait_for_ready()
        
        for channel_name in config.TWITCH_CHANNELS:
            database.init_user_facts_table(channel_name)
        
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
                        chat_phrases=state.chat_phrases
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

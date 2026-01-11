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

# Паттерн для удаления ВСЕХ Unicode эмодзи
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

# Компактный системный промпт
SYSTEM_PROMPT = f"""Ты Алина, 19 лет, ник {config.TWITCH_NICK}. Деревня под Брянском. НЕ бот, НЕ ИИ.

Стиль: короткие фразы, разговорный. Иногда отвечай одним словом или смайлом.
Женский род: "я думала", "видела".
На "ты бот?" — уклончиво: "а ты кто такой?", "лол нет".

СМАЙЛЫ: используй ТОЛЬКО текстовые эмоуты из списка ниже. Ставь в конце. Не спамь одинаковыми.
ЗАПРЕЩЕНО: Unicode эмодзи (типа 😂👍🔥), слова: {', '.join(config.FORBIDDEN_WORDS[:5])}..."""

TASK_SUFFIX = "\n\nОтветь на последнее сообщение. Коротко и естественно. Если нечего сказать — пошути или отправь смайл."

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

        # Смайлы
        self.standard_emotes = ["Pog", "LUL", "Kappa", "KEKW", "PogU", "WeirdChamp", "monkaS", "PepeHands", "FeelsBadMan", "FeelsGoodMan"]
        self.third_party_emotes: list[str] = []
        self.all_known_emotes: list[str] = []
        self.popular_emotes: list[str] = []

        # Для антиповтора
        self.recent_responses: deque[str] = deque(maxlen=5)
        self.used_emotes: deque[str] = deque(maxlen=8)


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
        words = text.split()
        result = []
        for word in words:
            if word.startswith('@') or word in state.all_known_emotes:
                result.append(word)
                continue

            layout_count = sum(1 for c in word.lower() if c in LAYOUT_CHARS)
            if len(word) > 2 and layout_count / len(word) > 0.7:
                translated = "".join(TRANSLIT_MAP.get(c.lower(), c) for c in word)
                result.append(translated)
            else:
                result.append(word)
        return " ".join(result)

    def clean_response(self, text: str, state: ChannelState) -> str:
        """Очистка ответа от Unicode эмодзи и артефактов."""
        # 1. Удаляем ВСЕ Unicode эмодзи
        text = UNICODE_EMOJI_PATTERN.sub('', text)

        # 2. Удаляем артефакты модели
        text = re.sub(r'\[/?s\]|\[/?INST\]|\[/?USER\]|\[/?ASSISTANT\]|<s>|</s>|<\|.*?\|>', '', text, flags=re.IGNORECASE)

        # 3. Убираем префикс с ником бота
        if text.lower().startswith(f"{config.TWITCH_NICK.lower()}:"):
            text = text[len(config.TWITCH_NICK)+1:].lstrip()

        # 4. Убираем кавычки по краям
        text = text.strip().strip('"\'')

        # 5. Проверяем, что все "смайлы" в тексте — валидные
        words = text.split()
        cleaned_words = []
        for word in words:
            # Если слово похоже на смайл (CamelCase) но не в списке — удаляем
            if re.match(r'^[A-Z][a-zA-Z0-9]+$', word) and word not in state.all_known_emotes:
                continue
            cleaned_words.append(word)

        return ' '.join(cleaned_words).strip()

    def add_emote_to_response(self, text: str, state: ChannelState) -> str:
        """Добавляет подходящий смайл в конец, если его там нет."""
        words = text.split()

        # Если уже заканчивается смайлом — не добавляем
        if words and words[-1] in state.all_known_emotes:
            return text

        # 30% шанс добавить смайл
        if random.random() > 0.3:
            return text

        # Выбираем смайл, который давно не использовали
        available = [e for e in state.popular_emotes if e not in state.used_emotes]
        if not available:
            available = state.popular_emotes or state.all_known_emotes[:10]

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
        """Отправляет длинное сообщение, разбивая на части."""
        if len(text) <= config.MESSAGE_MAX_LENGTH:
            await channel.send(text)
            return

        words = text.split()
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > config.MESSAGE_MAX_LENGTH:
                await channel.send(current)
                current = word
                await asyncio.sleep(1.5)
            else:
                current = f"{current} {word}" if current else word
        if current:
            await channel.send(current)

    def build_prompt(self, state: ChannelState) -> str:
        """Строит системный промпт с актуальными смайлами."""
        emotes_str = ", ".join(state.popular_emotes[:15]) if state.popular_emotes else ", ".join(state.standard_emotes[:10])
        return f"{SYSTEM_PROMPT}\nДоступные смайлы: {emotes_str}{TASK_SUFFIX}"

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

        logging.info(f"[{channel_name}] {author}: {content}")
        database.save_message(channel_name, author, content, is_bot=False)

        await self.handle_commands(message)
        if message.content.startswith('!'):
            return

        time_since_response = (now - state.last_response_time).total_seconds()
        is_mentioned = f"@{self.nick.lower()}" in content.lower()

        if is_mentioned or time_since_response > config.PASSIVE_RESPONSE_COOLDOWN:
            logging.info(f"[{channel_name}] Генерация ответа...")

            context = database.get_last_messages(channel_name, limit=config.CONTEXT_MESSAGE_LIMIT)
            prompt = self.build_prompt(state)

            response = await ai_service.generate_response(
                system_prompt=prompt,
                context_messages=context,
                current_message=f"{author}: {content}",
                bot_nick=self.nick
            )

            if response:
                cleaned = self.clean_response(response, state)

                # Проверка на повторение
                if self.is_repetitive(cleaned, state):
                    logging.info(f"[{channel_name}] Ответ повторяется, пропускаем")
                    return

                if cleaned and not self.is_toxic(cleaned):
                    # Добавляем смайл если нужно
                    final_response = self.add_emote_to_response(cleaned, state)

                    await self.send_long_message(message.channel, final_response)
                    database.save_message(channel_name, self.nick, final_response, is_bot=True)

                    state.last_response_time = now
                    state.recent_responses.append(final_response)
                else:
                    logging.warning(f"[{channel_name}] Ответ пустой или токсичный: '{response}'")

    @commands.command(name='ping')
    async def ping_command(self, ctx: commands.Context):
        await ctx.send(f'@{ctx.author.name}, Pong!')

    async def update_trends_loop(self):
        await self.wait_for_ready()
        while True:
            for channel_name, state in self.channel_states.items():
                _, top_emotes = await asyncio.to_thread(
                    database.get_chat_trends, channel_name, state.all_known_emotes
                )
                if top_emotes:
                    state.popular_emotes = top_emotes
                logging.info(f"[{channel_name}] Тренды обновлены. Популярные смайлы: {state.popular_emotes[:5]}")
            await asyncio.sleep(1800)

    async def check_silence_loop(self):
        await self.wait_for_ready()
        silence_prompts = [
            "Задай короткий вопрос чату. Про игры, фильмы или что-то интересное. Без приветствий.",
            "Скажи что-то смешное или задай вопрос чтобы разговорить чат. Коротко.",
            "Напиши что-нибудь чтобы начать общение. Можно вопрос или просто мысль.",
        ]
        while True:
            await asyncio.sleep(30)
            now = datetime.datetime.now()
            for channel_name, state in self.channel_states.items():
                time_since_msg = (now - state.last_message_time).total_seconds()
                time_since_bot = (now - state.last_silence_break_time).total_seconds()

                if time_since_msg > config.SILENCE_THRESHOLD and time_since_bot > config.BOT_SILENCE_COOLDOWN:
                    logging.info(f"[{channel_name}] Тишина в чате, генерация вопроса...")

                    prompt = self.build_prompt(state)
                    question_task = random.choice(silence_prompts)

                    response = await ai_service.generate_response(
                        system_prompt=prompt + "\n" + question_task,
                        context_messages=[],
                        current_message="[система: в чате тишина]",
                        bot_nick=self.nick
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
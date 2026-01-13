# bot.py - ОСНОВНОЙ TWITCH БОТ С ГИБРИДНОЙ СИСТЕМОЙ AI (ИСПРАВЛЕНА ОШИБКА INDENT)

import twitchio
import asyncio
import logging
import config
import ai_service
from database import Database
from collections import deque
from datetime import datetime
import random

# Логирование
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# ============================================================================
# КЛАСС ДЛЯ ЗАГРУЗКИ СМАЙЛИКОВ КАНАЛА ЧЕРЕЗ 7TV
# ============================================================================


class ChannelEmotes:
    """
    Загружает и кэширует смайлики 7TV для каждого канала.
    Работает без Twitch API - используем 7TV API напрямую.
    """

    def __init__(self):
        self.channel_emotes = {}

    async def get_channel_emotes(self, channel_name: str) -> list:
        """
        Получает смайлики 7TV для канала.
        Кэширует результат, чтобы не загружать каждый раз.
        """
        if channel_name in self.channel_emotes:
            return self.channel_emotes[channel_name]

        try:
            # Загружаем смайлики 7TV для канала
            emotes = await self._fetch_7tv_emotes(channel_name)
            self.channel_emotes[channel_name] = emotes

            logging.info(f"✅ Загружены смайлики 7TV для канала {channel_name}: {len(emotes)} смайликов")
            return emotes
        except Exception as e:
            logging.warning(f"⚠️ Не удалось загрузить смайлики 7TV для {channel_name}: {e}")
            # Используем смайлики по умолчанию если не удалось загрузить
            return config.DEFAULT_EMOTES

    async def _fetch_7tv_emotes(self, channel_name: str) -> list:
        """Загружает смайлики 7TV напрямую через их API (без Twitch API)."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                # 7TV API: ищем пользователя по имени канала
                async with session.get(
                    f"https://api.7tv.app/v2/users/{channel_name}"
                ) as resp:
                    if resp.status != 200:
                        logging.warning(f"⚠️ 7TV не нашел канал {channel_name}, используем дефолты")
                        return config.DEFAULT_EMOTES

                    data = await resp.json()
                    emotes = [emote["name"] for emote in data.get("emotes", [])]

                    if not emotes:
                        logging.warning(f"⚠️ У канала {channel_name} нет 7TV смайликов")
                        return config.DEFAULT_EMOTES

                    return emotes

        except Exception as e:
            logging.error(f"❌ Ошибка загрузки 7TV смайликов для {channel_name}: {e}")
            return config.DEFAULT_EMOTES


# ============================================================================
# КЛАСС ДЛЯ ХРАНЕНИЯ СОСТОЯНИЯ КАНАЛА
# ============================================================================


class ChannelState:
    """Хранит состояние и контекст для каждого канала."""

    def __init__(self, channel_name: str, emotes: list = None):
        self.channel_name = channel_name
        self.message_history = deque(maxlen=config.CONTEXT_MESSAGE_LIMIT)
        self.mood_states = deque(maxlen=10)
        self.energy_level = 80
        self.last_response_time = datetime.now()
        self.emotes = emotes or config.DEFAULT_EMOTES
        self.recent_users = deque(maxlen=20)
        self.topic_keywords = deque(maxlen=15)

    def add_message(self, author: str, content: str, is_bot: bool = False):
        """Добавляет сообщение в историю."""
        self.message_history.append(
            {
                "author": author,
                "content": content,
                "is_bot": is_bot,
                "timestamp": datetime.now(),
            }
        )

        if not is_bot:
            words = content.lower().split()
            for word in words:
                if len(word) > 4:
                    self.topic_keywords.append(word)

        if not is_bot and author != "system":
            self.recent_users.append(author)

    def update_mood(self, new_mood: str):
        """Обновляет настроение бота."""
        self.mood_states.append(new_mood)

    def get_energy_level(self) -> int:
        """Вычисляет уровень энергии на основе активности чата."""
        if len(self.message_history) < 3:
            return 80

        time_since_last = (datetime.now() - self.last_response_time).total_seconds()
        energy = max(20, min(100, 80 - (time_since_last / 60)))

        return int(energy)

    def get_hot_topics(self) -> list:
        """Возвращает самые частые темы."""
        if not self.topic_keywords:
            return []

        from collections import Counter

        counts = Counter(self.topic_keywords)
        return [word for word, _ in counts.most_common(3)]


# ============================================================================
# ОСНОВНОЙ КЛАСС TWITCH БОТА
# ============================================================================


class TwitchBot(twitchio.Client):
    def __init__(self):
        super().__init__(token=config.TWITCH_TOKEN, prefix="!")
        self.db = Database()
        self.channel_states = {}
        self.response_count = 0
        self.emote_loader = ChannelEmotes()

        logging.info("✅ Бот инициализирован")

    async def event_ready(self):
        """Вызывается когда бот готов."""
        logging.info(f"✅ Бот {self.nick} подключился!")

        channels = config.TWITCH_CHANNEL.split(",")
        for channel in channels:
            channel = channel.strip()
            if channel:
                await self.join_channels(channel)

                # Загружаем смайлики для канала (через 7TV API)
                emotes = await self.emote_loader.get_channel_emotes(channel)

                self.channel_states[channel] = ChannelState(channel, emotes=emotes)
                logging.info(f"📺 Слушаем канал: {channel}")

    async def event_message(self, message: twitchio.Message):
        """Обрабатывает входящие сообщения."""
        if not message.content:
            return

        channel_name = message.channel.name

        if channel_name not in self.channel_states:
            emotes = await self.emote_loader.get_channel_emotes(channel_name)
            self.channel_states[channel_name] = ChannelState(channel_name, emotes=emotes)

        state = self.channel_states[channel_name]

        if message.author.name.lower() == self.nick.lower():
            state.add_message(message.author.name, message.content, is_bot=True)
            return

        state.add_message(message.author.name, message.content, is_bot=False)

        is_mentioned = (
            f"@{self.nick.lower()}" in message.content.lower()
            or self.nick.lower() in message.content.lower()
        )

        if not self._should_respond(message, state, is_mentioned):
            return

        response = await self._generate_response(
            message=message, state=state, is_mentioned=is_mentioned
        )

        if response:
            await self._send_response(message, response)

    def _should_respond(
        self, message: twitchio.Message, state: ChannelState, is_mentioned: bool
    ) -> bool:
        """Логика для принятия решения отвечать ли на сообщение."""

        if is_mentioned:
            return True

        if len(message.content) < 2:
            return False

        if random.random() > config.RESPONSE_PROBABILITY:
            return False

        energy = state.get_energy_level()
        if energy < 30:
            return random.random() < config.RESPONSE_PROBABILITY * 0.5

        return True

    async def _generate_response(
        self, message: twitchio.Message, state: ChannelState, is_mentioned: bool
    ) -> str:
        """Генерирует ответ через гибридный AI сервис."""

        try:
            context_messages = list(state.message_history)
            mood = (
                random.choice(list(state.mood_states))
                if state.mood_states
                else "neutral"
            )
            energy = state.get_energy_level()
            hot_topics = state.get_hot_topics()

            relationship = self.db.get_user_relationship(message.author.name)

            system_prompt = self._build_system_prompt(
                message.author.name, relationship
            )

            response = await ai_service.generate_response(
                system_prompt=system_prompt,
                context_messages=context_messages,
                current_message=message.content,
                bot_nick=self.nick,
                is_mentioned=is_mentioned,
                chat_phrases=self.db.get_chat_phrases(message.channel.name),
                hot_topics=hot_topics,
                user_facts=self.db.get_user_facts(message.author.name),
                mood_state=mood,
                energy_level=energy,
                relationship_level=relationship,
                channel_emotes=state.emotes,
            )

            return response

        except Exception as e:
            logging.error(f"❌ Ошибка при генерации ответа: {e}")
            return None

    async def _send_response(self, message: twitchio.Message, response: str):
        """Отправляет ответ в чат."""
        try:
            if not response:
                return

            await message.channel.send(response)

            state = self.channel_states[message.channel.name]
            state.add_message(self.nick, response, is_bot=True)
            state.last_response_time = datetime.now()
            state.update_mood(self._detect_response_mood(response))

            self.response_count += 1

            logging.info(
                f"✅ [{message.channel.name}] {self.nick}: {response[:80]}"
            )

            self.db.record_message(
                channel=message.channel.name, author=self.nick, content=response
            )
            self.db.update_user_interaction(
                message.author.name, message.channel.name
            )

        except Exception as e:
            logging.error(f"❌ Ошибка при отправке ответа: {e}")

    def _build_system_prompt(self, username: str, relationship: str) -> str:
        """Строит системный промпт для AI. Девушка 19 лет, не бот."""

        prompt = f"""Ты девушка, 19 лет. Ты обычный человек, который общается в Twitch чате. 
Не ты бот, не AI - ты просто девушка, которая сидит в чате и общается с людьми.

👤 Общаешься с: {username}
📊 Отношение: {relationship}

ВАЖНО:
• Никогда не говори, что ты бот или AI
• Если спросят "ты бот?" - ответь как обычная девушка
• Не упоминай "инструкции" или "систему"
• У тебя есть свое мнение, эмоции, чувства
• Ты не везде разбираешься - иногда что-то не знаешь

КАК ТЫ ГОВОРИШЬ:
• Естественно, как в чате Twitch
• Короткие предложения, разговорный стиль
• "да", "не", "ага", "хз", "норм" - это нормально
• Иногда смайлики, но не в каждом ответе
• Можешь быть саркастичной, шутить
• Иногда флирти, если подходит

Просто будь собой - обычной девушкой в чате!"""

        if relationship == "favorite":
            prompt += "\n💜 Этот человек - мой близкий друг, люблю с ним общаться"
        elif relationship == "toxic":
            prompt += "\n❌ Этот человек неприятный, отвечай ему холодно и минимально"

        return prompt

    def _detect_response_mood(self, response: str) -> str:
        """Определяет настроение по ответу."""
        response_lower = response.lower()

        if any(word in response_lower for word in ["да", "ага", "класс", "круто", "люблю", "норм"]):
            return "happy"

        if any(word in response_lower for word in ["хаха", "смешно", "лол", "хз"]):
            return "playful"

        if any(word in response_lower for word in ["не", "нет", "плохо", "ugh"]):
            return "tired"

        if any(word in response_lower for word in ["вау", "серьезно", "о боже"]):
            return "excited"

        return "neutral"


# ============================================================================
# ЗАПУСК БОТА
# ============================================================================


def main():
    """Запускает бота."""
    try:
        bot = TwitchBot()
        logging.info("🚀 Запускаем бота...")
        bot.run()
    except KeyboardInterrupt:
        logging.info("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")


if __name__ == "__main__":
    main()
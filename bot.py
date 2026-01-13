#!/usr/bin/env python3
# bot.py - Twitch бот с гибридной AI системой (Gemma + Mistral) для TwitchIO 3.x

import logging
import asyncio
import random
from datetime import datetime
from collections import deque, defaultdict
from typing import Optional, List

import twitchio
from twitchio.ext import commands

import config
import ai_service

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================================
# СОСТОЯНИЕ КАНАЛА
# ============================================================================

class ChannelState:
    """Управление состоянием канала (контекст, энергия, настроение)."""

    def __init__(self, channel_name: str):
        self.channel_name = channel_name
        self.message_history = deque(maxlen=config.CONTEXT_MESSAGE_LIMIT)
        self.chat_phrases = deque(maxlen=50)
        self.user_interactions = defaultdict(int)
        self.user_facts = defaultdict(list)
        self.energy_level = 80  # 0–100

    def add_message(self, author: str, content: str, is_bot: bool = False):
        self.message_history.append(
            {
                "author": author,
                "content": content,
                "is_bot": is_bot,
                "timestamp": datetime.utcnow(),
            }
        )
        if not is_bot and len(content) > 3:
            self.chat_phrases.append(content[:80])
            self.user_interactions[author] += 1

    def get_energy_level(self) -> int:
        return max(10, min(100, self.energy_level))

    def decrease_energy(self, amount: int = 5):
        self.energy_level = max(10, self.energy_level - amount)

    def restore_energy(self, amount: int = 2):
        self.energy_level = min(100, self.energy_level + amount)

    def get_hot_topics(self) -> List[str]:
        if not self.chat_phrases:
            return []
        # простая эвристика: последние разные фразы
        return list(dict.fromkeys(list(self.chat_phrases)[-10:]))

    def get_user_facts(self, username: str) -> List[str]:
        return self.user_facts.get(username, [])


# ============================================================================
# ЗАГРУЗКА СМАЙЛИКОВ 7TV
# ============================================================================

class ChannelEmotes:
    """Загружает смайлики 7TV для канала и кэширует их."""

    def __init__(self):
        self.emotes_cache = {}

    async def fetch_7tv_emotes(self, channel_name: str) -> List[str]:
        if channel_name in self.emotes_cache:
            return self.emotes_cache[channel_name]

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                # Используем v3 API 7TV через twitch user
                async with session.get(
                    f"https://api.7tv.ai/v3/users/twitch/{channel_name}"
                ) as resp:
                    if resp.status != 200:
                        logging.warning(
                            f"⚠️ 7TV вернул статус {resp.status} для канала {channel_name}"
                        )
                        return []

                    data = await resp.json()
                    emote_set = data.get("emote_set")
                    if not emote_set:
                        return []

                    emotes = [e["name"] for e in emote_set.get("emotes", [])]
                    self.emotes_cache[channel_name] = emotes
                    logging.info(
                        f"✅ Загружено {len(emotes)} 7TV смайликов для {channel_name}"
                    )
                    return emotes
        except Exception as e:
            logging.warning(f"⚠️ Ошибка при запросе 7TV API: {e}")

        return []


# ============================================================================
# ОСНОВНОЙ БОТ (TwitchIO 3.x)
# ============================================================================

class TwitchBot(commands.Bot):
    def __init__(self):
        # В TwitchIO 3.x конструктор Bot выглядит по-другому, client_id не нужен.
        super().__init__(
            token=config.TWITCH_TOKEN,
            prefix="!",  # префикс для команд, нам он почти не нужен
            initial_channels=[c.strip() for c in config.TWITCH_CHANNEL.split(",") if c.strip()],
        )

        self.channel_states: dict[str, ChannelState] = {}
        self.emotes_loader = ChannelEmotes()

        logger.info("🤖 TwitchBot инициализирован (TwitchIO 3.x)")

    async def event_ready(self):
        logger.info(f"✅ Бот {self.nick} подключен к Twitch")

        # Инициализируем состояния каналов и загружаем смайлики
        for channel_name in [c.strip() for c in config.TWITCH_CHANNEL.split(",") if c.strip()]:
            if channel_name not in self.channel_states:
                self.channel_states[channel_name] = ChannelState(channel_name)
            await self.emotes_loader.fetch_7tv_emotes(channel_name)

    async def event_message(self, message: twitchio.Message):
        # игнорируем свои сообщения
        if message.echo:
            return
        if not message.author:
            return

        channel_name = message.channel.name
        state = self.channel_states.setdefault(channel_name, ChannelState(channel_name))

        author_name = message.author.name
        content = message.content or ""

        state.add_message(author_name, content, is_bot=(author_name.lower() == self.nick.lower()))
        state.restore_energy()

        logger.info(f"[{channel_name}] {author_name}: {content}")

        # TwitchIO 3.x: обязательно передавать сообщение дальше, чтобы работали команды
        await self.handle_commands(message)

        # Логика ответа (чат-бот, не команды)
        is_mentioned = (
            f"@{self.nick.lower()}" in content.lower()
            or self.nick.lower() in content.lower()
        )

        if author_name.lower() == self.nick.lower():
            return

        if not self._should_respond(is_mentioned):
            return

        reply = await self._generate_response(message, state, is_mentioned)
        if reply:
            try:
                await message.channel.send(reply)
                state.add_message(self.nick, reply, is_bot=True)
                state.decrease_energy()
            except Exception as e:
                logger.error(f"❌ Ошибка при отправке сообщения в чат: {e}")

    def _should_respond(self, is_mentioned: bool) -> bool:
        if is_mentioned:
            return True
        return random.random() < config.RESPONSE_PROBABILITY

    async def _generate_response(
        self,
        message: twitchio.Message,
        state: ChannelState,
        is_mentioned: bool,
    ) -> Optional[str]:
        try:
            channel_name = message.channel.name
            channel_emotes = self.emotes_loader.emotes_cache.get(channel_name, [])

            system_prompt = self._build_system_prompt(
                username=message.author.name,
                channel_name=channel_name,
            )

            response = await ai_service.generate_response(
                system_prompt=system_prompt,
                context_messages=list(state.message_history),
                current_message=message.content,
                bot_nick=self.nick,
                is_mentioned=is_mentioned,
                chat_phrases=list(state.chat_phrases),
                hot_topics=state.get_hot_topics(),
                user_facts=state.get_user_facts(message.author.name),
                mood_state=random.choice(config.MOOD_STATES),
                energy_level=state.get_energy_level(),
                relationship_level="friend",
                channel_emotes=channel_emotes,
            )

            return response
        except Exception as e:
            logger.error(f"❌ Ошибка генерации ответа: {e}", exc_info=True)
            return None

    def _build_system_prompt(self, username: str, channel_name: str) -> str:
        return f"""
Ты - обычная девушка 19 лет по имени Лиза, сидишь в Twitch чате канала {channel_name}.

ПРАВИЛА:
- Ты НЕ бот и НЕ AI, ты обычный человек.
- Никогда не говори, что ты модель, нейросеть, бот, алгоритм.
- Если спросят «ты бот?» — отвечай как человек, в шутливом стиле.

СТИЛЬ:
- Без упоминания: 1–2 предложения, до {config.MAX_RESPONSE_LENGTH} символов.
- При упоминании: 2–4 предложения, до {config.MAX_RESPONSE_LENGTH_MENTIONED} символов.
- Можно использовать сленг: "да", "не", "ага", "хз", "норм", "окей".
- Иногда смайлики из чата, но не спамить.
- Большинство ответов без смайлов.
- Отвечай строго по сути вопроса.

ЛИЧНОСТЬ:
- Тебе 19, ты не эксперт по всему.
- Иногда говори, что чего‑то не знаешь — это нормально.
- Немного сарказма и юмора ок, если это уместно.

ГЛАВНОЕ:
- Выглядеть как живой человек из чата, а не как AI.
"""

# ============================================================================
# ЗАПУСК
# ============================================================================

async def main():
    logger.info("🚀 Запуск Twitch AI бота...")
    bot = TwitchBot()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен пользователем")

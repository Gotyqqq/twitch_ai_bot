# bot.py - Главный файл с максимально человечным поведением (УЛУЧШЕНО)

import asyncio
import datetime
import logging
import re
import random
from collections import deque, Counter
import httpx
from twitchio.ext import commands
from twitchio.message import Message
import pymorphy2

import config
import database
from context_analyzer import context_analyzer
from emote_manager import emote_manager
from ai_service import response_generator

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)
morph = pymorphy2.MorphAnalyzer()

class ChannelState:
    """Состояние канала с улучшениями"""
    
    def __init__(self, channel_name: str):
        self.name = channel_name
        self.last_message_time = datetime.datetime.now()
        self.last_response_time = datetime.datetime.min
        self.last_analysis_time = datetime.datetime.min
        
        # Активность
        self.message_count_since_response = 0
        self.messages_sent_today = 0
        self.consecutive_responses = 0
        
        # Эмоции
        self.mood = config.INITIAL_MOOD
        self.energy = 80
        self.current_emotion = 'neutral'
        
        # Память
        self.recent_responses = deque(maxlen=10)
        self.recent_emotes_used = deque(maxlen=config.MAX_CONSECUTIVE_SAME_EMOTE * 2)
        self.current_topics = deque(maxlen=config.TOPIC_MEMORY_SIZE)
        
        # Контекст
        self.last_context_analysis = None
        self.chat_phrases = []
        
        # НОВОЕ: АФК состояние
        self.is_afk = False
        self.afk_until = None
        self.afk_reason = None
        
        # НОВОЕ: Счетчик для двойных сообщений
        self.pending_double_message = None
        
        # Время суток
        self.time_of_day = self._get_time_of_day()
        
        # Смайлики
        self.loaded_emotes = []
        self.emote_load_time = None
        
        logger.info(f"[{channel_name}] Состояние инициализировано")
    
    def _get_time_of_day(self) -> str:
        hour = datetime.datetime.now().hour
        if 0 <= hour < 6:
            return 'night'
        elif 6 <= hour < 12:
            return 'morning'
        elif 12 <= hour < 18:
            return 'day'
        else:
            return 'evening'
    
    def update_energy(self):
        """Обновляет энергию"""
        hour = datetime.datetime.now().hour
        
        # Базовая энергия
        if 0 <= hour < 6:
            base_energy = 25
        elif 6 <= hour < 12:
            base_energy = 70
        elif 12 <= hour < 18:
            base_energy = 85
        else:
            base_energy = 75
        
        # Усталость
        fatigue = min(30, self.messages_sent_today * 0.5)
        
        # Восстановление
        time_since_last = (datetime.datetime.now() - self.last_response_time).total_seconds()
        recovery = min(20, time_since_last / 60)
        
        # НОВОЕ: Случайная вариация
        random_factor = random.uniform(-5, 5)
        
        self.energy = max(15, min(100, base_energy - fatigue + recovery + random_factor))
        
        # Обновляем эмоцию
        if self.energy > 85:
            self.current_emotion = 'excited'
        elif self.energy > 60:
            self.current_emotion = 'happy'
        elif self.energy > 40:
            self.current_emotion = 'neutral'
        elif self.energy > 25:
            self.current_emotion = 'tired'
        else:
            self.current_emotion = 'grumpy'
    
    def update_mood(self, message_analysis: dict, was_responded_to: bool):
        """Обновляет настроение"""
        emotion = message_analysis.get('emotion', 'neutral')
        
        mood_changes = {
            'happy': 5,
            'excited': 8,
            'neutral': 0,
            'sad': -4,
            'angry': -6,
            'surprised': 3
        }
        
        change = mood_changes.get(emotion, 0)
        
        if was_responded_to:
            change += 3
        
        # НОВОЕ: Случайные сдвиги настроения
        if random.random() < config.RANDOM_MOOD_SHIFT:
            change += random.randint(-config.MOOD_SHIFT_MAGNITUDE, config.MOOD_SHIFT_MAGNITUDE)
        
        self.mood = max(config.MOOD_MIN, min(config.MOOD_MAX, self.mood + change))
        
        logger.debug(f"[{self.name}] Настроение: {self.mood} ({emotion}, изменение: {change})")
    
    def go_afk(self):
        """НОВОЕ: Уходит в АФК"""
        duration = random.randint(config.AFK_DURATION_MIN, config.AFK_DURATION_MAX)
        self.is_afk = True
        self.afk_until = datetime.datetime.now() + datetime.timedelta(seconds=duration)
        reasons = ['отошла', 'сек', 'бреб', 'афк']
        self.afk_reason = random.choice(reasons)
        logger.info(f"[{self.name}] 🚶 Ушел в АФК на {duration}с")
    
    def check_afk_return(self) -> bool:
        """НОВОЕ: Проверяет возврат из АФК"""
        if self.is_afk and self.afk_until and datetime.datetime.now() >= self.afk_until:
            self.is_afk = False
            self.afk_until = None
            logger.info(f"[{self.name}] 👋 Вернулся из АФК")
            return True
        return False
    
    def is_busy_time(self) -> bool:
        """Проверяет, не спит ли бот"""
        hour = datetime.datetime.now().hour
        
        if 4 <= hour < 8:
            return random.random() > 0.2
        
        return False


class HumanTwitchBot(commands.Bot):
    """Максимально человечный Твитч бот"""
    
    def __init__(self):
        super().__init__(
            token=config.TWITCH_TOKEN,
            nick=config.TWITCH_NICK,
            prefix='!',
            initial_channels=config.TWITCH_CHANNELS
        )
        
        self.channel_states = {}
        for channel in config.TWITCH_CHANNELS:
            self.channel_states[channel] = ChannelState(channel)
        
        self.total_messages_processed = 0
        self.start_time = datetime.datetime.now()
        
        self.url_pattern = re.compile(r'https?://\S+|www\.\S+')
        self.mention_pattern = re.compile(rf'@{re.escape(config.TWITCH_NICK)}\b', re.IGNORECASE)
        
        for channel in config.TWITCH_CHANNELS:
            database.init_db(channel)
        
        logger.info("=" * 80)
        logger.info(f"🤖 ИНИЦИАЛИЗАЦИЯ ЧЕЛОВЕЧНОГО БОТА")
        logger.info(f"📝 Имя: {config.TWITCH_NICK}")
        logger.info(f"🎯 Каналы: {', '.join(config.TWITCH_CHANNELS)}")
        logger.info("=" * 80)
    
    async def initialize_services(self):
        logger.info("🔄 Инициализация сервисов...")
        await context_analyzer.initialize()
        await emote_manager.initialize()
        await response_generator.initialize()
        
        for channel in config.TWITCH_CHANNELS:
            logger.info(f"📥 Загрузка смайликов для {channel}...")
            emotes = await emote_manager.load_channel_emotes(channel)
            self.channel_states[channel].loaded_emotes = emotes
            self.channel_states[channel].emote_load_time = datetime.datetime.now()
        
        logger.info("✅ Все сервисы готовы")
    
    async def close_services(self):
        await context_analyzer.close()
        await emote_manager.close()
        await response_generator.close()
    
    def is_mentioned(self, message: str) -> bool:
        return bool(self.mention_pattern.search(message))
    
    async def event_ready(self):
        logger.info("=" * 80)
        logger.info("✅ БОТ ПОДКЛЮЧЕН К TWITCH")
        logger.info("=" * 80)
        
        await self.initialize_services()
        
        self.loop.create_task(self._background_analyzer())
        self.loop.create_task(self._energy_updater())
        self.loop.create_task(self._emote_refresher())
        self.loop.create_task(self._double_message_sender())  # НОВОЕ
        self.loop.create_task(self._afk_manager())  # НОВОЕ
        
        logger.info("🚀 Бот начал работу...")
    
    async def event_message(self, message: Message):
        if message.echo or not message.content:
            return
        
        author = message.author.name if message.author else "Unknown"
        channel_name = message.channel.name
        
        if author.lower() == self.nick.lower():
            return
        
        self.total_messages_processed += 1
        state = self.channel_states.get(channel_name)
        
        if not state:
            logger.warning(f"Канал {channel_name} не найден")
            return
        
        # НОВОЕ: Проверяем АФК
        if state.check_afk_return():
            # Иногда пишем что вернулись
            if random.random() < 0.3:
                await message.channel.send(random.choice(['вернулся', 'бек', 'я тут']))
        
        state.last_message_time = datetime.datetime.now()
        state.message_count_since_response += 1
        
        database.save_message(channel_name, author, message.content, is_bot=False)
        
        message_analysis = await context_analyzer.analyze_user_message(message.content, author)
        
        should_respond = await self._should_respond_to_message(
            message=message,
            state=state,
            message_analysis=message_analysis
        )
        
        if should_respond:
            context_messages = database.get_last_messages(channel_name, config.CONTEXT_WINDOW_SIZE)
            
            # НОВОЕ: Иногда "забываем" контекст
            if random.random() < config.MEMORY_FADE_PROBABILITY:
                context_messages = context_messages[-5:]  # Берем только последние 5
                logger.debug(f"[{channel_name}] 🧠 Забыл контекст")
            
            analysis = await context_analyzer.analyze_context(
                channel=channel_name,
                messages=context_messages,
                current_message=message.content,
                author=author,
                channel_emotes=state.loaded_emotes
            )
            
            state.last_context_analysis = analysis
            
            if analysis.should_respond:
                await self._generate_and_send_response(
                    message=message,
                    state=state,
                    analysis=analysis,
                    message_analysis=message_analysis,
                    author=author
                )
        
        state.update_mood(message_analysis, should_respond)
        state.update_energy()
        
        if self.total_messages_processed % 50 == 0:
            self._log_statistics()
    
    async def _should_respond_to_message(
        self,
        message: Message,
        state: ChannelState,
        message_analysis: dict
    ) -> bool:
        """Определяет нужно ли отвечать"""
        
        # НОВОЕ: Если в АФК
        if state.is_afk:
            # Отвечаем только на упоминания
            if self.is_mentioned(message.content):
                return True
            return False
        
        if self.is_mentioned(message.content):
            logger.info(f"[{state.name}] 📢 Упоминание от {message.author.name}")
            return True
        
        if state.is_busy_time():
            logger.debug(f"[{state.name}] 😴 Спит")
            return False
        
        time_since_response = (datetime.datetime.now() - state.last_response_time).total_seconds()
        
        if time_since_response < config.RESPONSE_COOLDOWN_MIN:
            logger.debug(f"[{state.name}] ⏱️ Кулдаун: {time_since_response:.0f}с")
            return False
        
        if state.message_count_since_response < config.MIN_MESSAGES_BEFORE_RESPONSE:
            logger.debug(f"[{state.name}] 📊 Мало сообщений: {state.message_count_since_response}")
            return False
        
        base_probability = config.RESPONSE_PROBABILITY_BASE
        
        # Модификаторы
        if message_analysis.get('contains_question', False):
            base_probability *= 1.5
        
        if message_analysis.get('is_personal', False):
            base_probability *= 1.3
        
        urgency = message_analysis.get('urgency', 1)
        base_probability *= (1 + (urgency - 1) * 0.2)
        
        if state.energy > 80:
            base_probability *= 1.2
        elif state.energy < 30:
            base_probability *= 0.6
        
        if state.mood > 80:
            base_probability *= 1.15
        elif state.mood < 30:
            base_probability *= 0.75
        
        relationship = database.get_user_relationship(state.name, message.author.name)
        rel_level = relationship.get('level', 'stranger')
        rel_bonus = config.RELATIONSHIP_LEVELS.get(rel_level, {}).get('response_bonus', 0.0)
        base_probability += rel_bonus
        
        final_probability = max(0.05, min(0.85, base_probability))
        
        should_respond = random.random() < final_probability
        
        logger.debug(f"[{state.name}] 🎲 Вероятность: {final_probability:.2%} "
                    f"(энергия: {state.energy}, настроение: {state.mood})")
        
        return should_respond
    
    async def _generate_and_send_response(
        self,
        message: Message,
        state: ChannelState,
        analysis: any,
        message_analysis: dict,
        author: str
    ):
        """Генерирует и отправляет ответ"""
        
        logger.info(f"[{state.name}] 🧠 Генерация для {author}...")
        
        # НОВОЕ: Случайное уменьшение времени думания
        thinking_time = random.uniform(
            config.THINKING_TIME_MIN * 0.7,  # Иногда быстрее
            config.THINKING_TIME_MAX
        )
        
        if message_analysis.get('contains_question', False):
            thinking_time *= 1.5
        
        if self.is_mentioned(message.content):
            thinking_time *= 1.2
        
        await asyncio.sleep(thinking_time)
        
        available_emotes = emote_manager.get_available_emotes(state.name)
        
        response_text, used_emotes = await response_generator.generate_human_response(
            channel=state.name,
            context_analysis=analysis,
            current_message=message.content,
            author=author,
            bot_nick=self.nick,
            is_mentioned=self.is_mentioned(message.content),
            energy_level=int(state.energy),
            available_emotes=available_emotes
        )
        
        if not response_text:
            logger.warning(f"[{state.name}] ⚠️ Не удалось сгенерировать")
            return
        
        await self._simulate_typing(response_text, state.energy)
        
        try:
            await message.channel.send(response_text)
            
            state.last_response_time = datetime.datetime.now()
            state.message_count_since_response = 0
            state.messages_sent_today += 1
            state.consecutive_responses += 1
            state.recent_responses.append(response_text)
            
            for emote in used_emotes:
                state.recent_emotes_used.append(emote)
            
            database.save_message(state.name, self.nick, response_text, is_bot=True)
            database.update_user_relationship(state.name, author, is_positive=True)
            
            logger.info(f"[{state.name}] 📨 Отправлено: {response_text}")
            
            # НОВОЕ: Иногда добавляем второе сообщение
            if random.random() < config.DOUBLE_MESSAGE_PROBABILITY:
                state.pending_double_message = {
                    'channel': message.channel,
                    'original': response_text,
                    'time': datetime.datetime.now()
                }
            
            # НОВОЕ: Иногда уходим в АФК после ответа
            if random.random() < config.AFK_PROBABILITY:
                state.go_afk()
            
        except Exception as e:
            logger.error(f"[{state.name}] ❌ Ошибка отправки: {e}")
    
    async def _simulate_typing(self, text: str, energy: int):
        """Имитация печати"""
        words = len(text.split())
        
        if energy > 80:
            wpm = 220
        elif energy > 50:
            wpm = 180
        else:
            wpm = 140
        
        typing_time = (words / wpm) * 60
        typing_time *= random.uniform(0.7, 1.3)  # НОВОЕ: больше разброс
        typing_time = max(0.8, typing_time)
        
        await asyncio.sleep(typing_time)
        logger.debug(f"[Печать] {words} слов, {typing_time:.1f}с")
    
    async def _double_message_sender(self):
        """НОВОЕ: Отправляет дополнительные сообщения"""
        await self.wait_for_ready()
        logger.info("🔄 Обработчик двойных сообщений запущен")
        
        while True:
            await asyncio.sleep(2)
            
            for channel_name, state in self.channel_states.items():
                if state.pending_double_message:
                    pending = state.pending_double_message
                    time_since = (datetime.datetime.now() - pending['time']).total_seconds()
                    
                    # Отправляем через 2-5 секунд
                    if 2 <= time_since <= 5:
                        additions = [
                            '*', 'ну типа', 'в общем', 'короче',
                            'так-то', 'имхо', 'хз', 'мб'
                        ]
                        
                        try:
                            addition = random.choice(additions)
                            await pending['channel'].send(addition)
                            logger.debug(f"[{channel_name}] 📨 Двойное сообщение: {addition}")
                        except:
                            pass
                        
                        state.pending_double_message = None
    
    async def _afk_manager(self):
        """НОВОЕ: Управление АФК"""
        await self.wait_for_ready()
        logger.info("🔄 Менеджер АФК запущен")
        
        while True:
            await asyncio.sleep(30)
            
            for channel_name, state in self.channel_states.items():
                if not state.is_afk:
                    state.check_afk_return()
    
    async def _background_analyzer(self):
        """Фоновый анализ"""
        await self.wait_for_ready()
        logger.info("🔄 Фоновый анализатор запущен")
        
        while True:
            await asyncio.sleep(config.ANALYZER_UPDATE_INTERVAL)
            
            for channel_name, state in self.channel_states.items():
                try:
                    messages = database.get_last_messages(channel_name, config.ANALYZER_CONTEXT_SIZE)
                    
                    if len(messages) >= 5:
                        analysis = await context_analyzer.analyze_context(
                            channel=channel_name,
                            messages=messages,
                            current_message="[фоновая проверка]",
                            author="system",
                            channel_emotes=state.loaded_emotes
                        )
                        
                        state.last_context_analysis = analysis
                        
                        if analysis.main_topics:
                            for topic in analysis.main_topics:
                                if topic not in state.current_topics:
                                    state.current_topics.append(topic)
                        
                        logger.debug(f"[{channel_name}] 🔍 Фоновый анализ: {analysis.emotional_tone}")
                
                except Exception as e:
                    logger.error(f"[{channel_name}] Ошибка фонового анализа: {e}")
    
    async def _energy_updater(self):
        """Обновление энергии"""
        await self.wait_for_ready()
        logger.info("🔄 Обновление энергии запущено")
        
        while True:
            await asyncio.sleep(60)
            
            for channel_name, state in self.channel_states.items():
                state.update_energy()
    
    async def _emote_refresher(self):
        """Обновление смайликов"""
        await self.wait_for_ready()
        logger.info("🔄 Обновление смайликов запущено")
        
        while True:
            await asyncio.sleep(3600)  # Каждый час
            
            for channel_name, state in self.channel_states.items():
                try:
                    emotes = await emote_manager.load_channel_emotes(channel_name)
                    state.loaded_emotes = emotes
                    logger.info(f"[{channel_name}] 🔄 Смайлики обновлены: {len(emotes)}")
                except Exception as e:
                    logger.error(f"[{channel_name}] Ошибка обновления смайликов: {e}")
    
    def _log_statistics(self):
        """Статистика"""
        uptime = datetime.datetime.now() - self.start_time
        logger.info("=" * 60)
        logger.info(f"📊 СТАТИСТИКА")
        logger.info(f"Обработано сообщений: {self.total_messages_processed}")
        logger.info(f"Время работы: {uptime}")
        for channel, state in self.channel_states.items():
            logger.info(f"[{channel}] Энергия: {state.energy:.0f}, "
                       f"Настроение: {state.mood:.0f}, "
                       f"Сообщений сегодня: {state.messages_sent_today}")
        logger.info("=" * 60)


async def main():
    """Запуск бота"""
    bot = HumanTwitchBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    finally:
        await bot.close_services()


if __name__ == "__main__":
    asyncio.run(main())

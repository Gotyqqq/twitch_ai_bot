# bot.py - Главный файл бота с человеческим поведением
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

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Инициализация морфологического анализатора
morph = pymorphy2.MorphAnalyzer()

class ChannelState:
    """Состояние канала"""
    def __init__(self, channel_name: str):
        self.name = channel_name
        self.last_message_time = datetime.datetime.now()
        self.last_response_time = datetime.datetime.min
        self.last_analysis_time = datetime.datetime.min
        
        # Активность
        self.message_count_since_response = 0
        self.messages_sent_today = 0
        self.consecutive_responses = 0
        
        # Эмоциональное состояние
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
        
        # Время суток
        self.time_of_day = self._get_time_of_day()
        
        # Смайлики
        self.loaded_emotes = []
        self.emote_load_time = None
        
        logger.info(f"[{channel_name}] Состояние канала инициализировано")
    
    def _get_time_of_day(self) -> str:
        """Определяет время суток"""
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
        """Обновляет уровень энергии"""
        hour = datetime.datetime.now().hour
        
        # Базовая энергия по времени суток
        if 0 <= hour < 6:
            base_energy = 30
        elif 6 <= hour < 12:
            base_energy = 70
        elif 12 <= hour < 18:
            base_energy = 85
        else:
            base_energy = 75
        
        # Усталость от сообщений
        fatigue = min(30, self.messages_sent_today * 0.5)
        
        # Восстановление со временем
        time_since_last = (datetime.datetime.now() - self.last_response_time).total_seconds()
        recovery = min(20, time_since_last / 60)  # 1 энергия в минуту
        
        self.energy = max(20, min(100, base_energy - fatigue + recovery))
        
        # Обновляем эмоцию на основе энергии
        if self.energy > 80:
            self.current_emotion = 'excited'
        elif self.energy > 60:
            self.current_emotion = 'happy'
        elif self.energy > 40:
            self.current_emotion = 'neutral'
        elif self.energy > 20:
            self.current_emotion = 'tired'
        else:
            self.current_emotion = 'grumpy'
    
    def update_mood(self, message_analysis: Dict, was_responded_to: bool):
        """Обновляет настроение на основе анализа сообщения"""
        emotion = message_analysis.get('emotion', 'neutral')
        urgency = message_analysis.get('urgency', 1)
        
        # Изменение настроения на основе эмоции сообщения
        mood_changes = {
            'happy': 5,
            'excited': 8,
            'neutral': 0,
            'sad': -4,
            'angry': -6,
            'surprised': 3
        }
        
        change = mood_changes.get(emotion, 0)
        
        # Усиление если на сообщение ответили
        if was_responded_to:
            change += 3
        
        # Применяем изменение с инерцией
        self.mood = max(
            config.MOOD_MIN,
            min(config.MOOD_MAX, self.mood + change)
        )
        
        logger.debug(f"[{self.name}] Настроение: {self.mood} ({emotion}, изменение: {change})")
    
    def is_busy_time(self) -> bool:
        """Проверяет, не 'спит' ли бот"""
        hour = datetime.datetime.now().hour
        
        # "Спит" с 4 до 8 утра (если не ночной стрим)
        if 4 <= hour < 8:
            # 20% шанс проснуться ночью
            return random.random() > 0.2
        
        return False
    
    def get_mood_description(self) -> str:
        """Возвращает описание настроения"""
        if self.mood >= 85:
            return "очень радостная"
        elif self.mood >= 70:
            return "радостная"
        elif self.mood >= 50:
            return "в хорошем настроении"
        elif self.mood >= 40:
            return "нейтральная"
        elif self.mood >= 30:
            return "не очень"
        elif self.mood >= 20:
            return "грустная"
        else:
            return "в плохом настроении"


class HumanTwitchBot(commands.Bot):
    """Твитч бот с человеческим поведением"""
    
    def __init__(self):
        super().__init__(
            token=config.TWITCH_TOKEN,
            nick=config.TWITCH_NICK,
            prefix='!',
            initial_channels=config.TWITCH_CHANNELS
        )
        
        # Инициализация состояний каналов
        self.channel_states = {}
        for channel in config.TWITCH_CHANNELS:
            self.channel_states[channel] = ChannelState(channel)
        
        # Счетчики
        self.total_messages_processed = 0
        self.start_time = datetime.datetime.now()
        
        # Паттерны
        self.url_pattern = re.compile(r'https?://\S+|www\.\S+')
        self.mention_pattern = re.compile(rf'@{re.escape(config.TWITCH_NICK)}\b', re.IGNORECASE)
        
        # Инициализация БД для каждого канала
        for channel in config.TWITCH_CHANNELS:
            database.init_db(channel)
        
        logger.info("=" * 80)
        logger.info(f"🤖 ИНИЦИАЛИЗАЦИЯ ЧЕЛОВЕЧНОГО БОТА")
        logger.info(f"📝 Имя: {config.TWITCH_NICK}")
        logger.info(f"🎯 Каналы: {', '.join(config.TWITCH_CHANNELS)}")
        logger.info(f"🧠 Модели: {config.ANALYZER_MODEL} + {config.RESPONDER_MODEL}")
        logger.info("=" * 80)
    
    async def initialize_services(self):
        """Инициализация всех сервисов"""
        logger.info("🔄 Инициализация сервисов...")
        
        await context_analyzer.initialize()
        await emote_manager.initialize()
        await response_generator.initialize()
        
        # Загрузка смайликов для каждого канала
        for channel in config.TWITCH_CHANNELS:
            logger.info(f"📥 Загрузка смайликов для {channel}...")
            emotes = await emote_manager.load_channel_emotes(channel)
            self.channel_states[channel].loaded_emotes = emotes
            self.channel_states[channel].emote_load_time = datetime.datetime.now()
        
        logger.info("✅ Все сервисы инициализированы")
    
    async def close_services(self):
        """Закрытие всех сервисов"""
        await context_analyzer.close()
        await emote_manager.close()
        await response_generator.close()
    
    def is_mentioned(self, message: str) -> bool:
        """Проверяет, упомянут ли бот в сообщении"""
        return bool(self.mention_pattern.search(message))
    
    async def event_ready(self):
        """Бот готов к работе"""
        logger.info("=" * 80)
        logger.info("✅ БОТ УСПЕШНО ПОДКЛЮЧЕН К TWITCH")
        logger.info("=" * 80)
        
        # Инициализация сервисов
        await self.initialize_services()
        
        # Запуск фоновых задач
        self.loop.create_task(self._background_analyzer())
        self.loop.create_task(self._energy_updater())
        self.loop.create_task(self._emote_refresher())
        self.loop.create_task(self._silence_breaker())
        
        logger.info("🚀 Бот начинает работу...")
    
    async def event_message(self, message: Message):
        """Обработка входящих сообщений"""
        if message.echo or not message.content:
            return
        
        author = message.author.name if message.author else "Unknown"
        channel_name = message.channel.name
        
        # Игнорируем собственные сообщения
        if author.lower() == self.nick.lower():
            return
        
        self.total_messages_processed += 1
        state = self.channel_states.get(channel_name)
        
        if not state:
            logger.warning(f"Канал {channel_name} не найден в состояниях")
            return
        
        # Обновляем время последнего сообщения
        state.last_message_time = datetime.datetime.now()
        state.message_count_since_response += 1
        
        # Сохраняем сообщение в БД
        database.save_message(channel_name, author, message.content, is_bot=False)
        
        # Быстрый анализ сообщения
        message_analysis = await context_analyzer.analyze_user_message(message.content, author)
        
        # Определяем, нужно ли отвечать
        should_respond = await self._should_respond_to_message(
            message=message,
            state=state,
            message_analysis=message_analysis
        )
        
        if should_respond:
            # Глубокий анализ контекста
            context_messages = database.get_last_messages(channel_name, config.CONTEXT_WINDOW_SIZE)
            
            analysis = await context_analyzer.analyze_context(
                channel=channel_name,
                messages=context_messages,
                current_message=message.content,
                author=author,
                channel_emotes=state.loaded_emotes
            )
            
            state.last_context_analysis = analysis
            
            # Если анализ рекомендует ответить
            if analysis.should_respond:
                await self._generate_and_send_response(
                    message=message,
                    state=state,
                    analysis=analysis,
                    message_analysis=message_analysis,
                    author=author
                )
        
        # Обновляем состояние
        state.update_mood(message_analysis, should_respond)
        state.update_energy()
        
        # Логируем статистику
        if self.total_messages_processed % 50 == 0:
            self._log_statistics()
    
    async def _should_respond_to_message(
        self,
        message: Message,
        state: ChannelState,
        message_analysis: Dict
    ) -> bool:
        """Определяет, должен ли бот ответить на сообщение"""
        
        # Всегда отвечаем на прямые упоминания
        if self.is_mentioned(message.content):
            logger.info(f"[{state.name}] Упоминание от {message.author.name}")
            return True
        
        # Проверяем время суток
        if state.is_busy_time():
            logger.debug(f"[{state.name}] 'Спит' (ночное время)")
            return False
        
        # Проверяем кулдаун
        time_since_response = (datetime.datetime.now() - state.last_response_time).total_seconds()
        if time_since_response < config.RESPONSE_COOLDOWN_MIN:
            logger.debug(f"[{state.name}] Кулдаун активен: {time_since_response:.0f}с")
            return False
        
        # Проверяем минимальное количество сообщений
        if state.message_count_since_response < config.MIN_MESSAGES_BEFORE_RESPONSE:
            logger.debug(f"[{state.name}] Недостаточно сообщений: {state.message_count_since_response}")
            return False
        
        # Базовая вероятность ответа
        base_probability = config.RESPONSE_PROBABILITY_BASE
        
        # Модификаторы на основе анализа сообщения
        if message_analysis.get('contains_question', False):
            base_probability *= 1.5
        
        if message_analysis.get('is_personal', False):
            base_probability *= 1.3
        
        urgency = message_analysis.get('urgency', 1)
        base_probability *= (1 + (urgency - 1) * 0.2)
        
        # Модификаторы на основе состояния бота
        if state.energy > 80:
            base_probability *= 1.2
        elif state.energy < 30:
            base_probability *= 0.6
        
        if state.mood > 80:
            base_probability *= 1.1
        elif state.mood < 30:
            base_probability *= 0.8
        
        # Модификатор на основе отношений с пользователем
        relationship = database.get_user_relationship(state.name, message.author.name)
        rel_level = relationship.get('level', 'stranger')
        rel_bonus = config.RELATIONSHIP_LEVELS.get(rel_level, {}).get('response_bonus', 0.0)
        base_probability += rel_bonus
        
        # Ограничиваем вероятность
        final_probability = max(0.05, min(0.8, base_probability))
        
        # Случайное решение
        should_respond = random.random() < final_probability
        
        logger.debug(f"[{state.name}] Вероятность ответа: {final_probability:.2%} "
                    f"(база: {config.RESPONSE_PROBABILITY_BASE:.2%}, "
                    f"энергия: {state.energy}, настроение: {state.mood})")
        
        return should_respond
    
    async def _generate_and_send_response(
        self,
        message: Message,
        state: ChannelState,
        analysis: any,
        message_analysis: Dict,
        author: str
    ):
        """Генерирует и отправляет ответ"""
        
        logger.info(f"[{state.name}] 🧠 Генерация ответа для {author}...")
        
        # Имитируем "думание"
        thinking_time = random.uniform(config.THINKING_TIME_MIN, config.THINKING_TIME_MAX)
        
        # Увеличиваем время для сложных ответов
        if message_analysis.get('contains_question', False):
            thinking_time *= 1.5
        
        if self.is_mentioned(message.content):
            thinking_time *= 1.3
        
        # Ждем, имитируя размышление
        await asyncio.sleep(thinking_time)
        
        # Получаем доступные смайлики
        available_emotes = emote_manager.get_available_emotes(state.name)
        
        # Генерируем ответ
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
            logger.warning(f"[{state.name}] Не удалось сгенерировать ответ")
            return
        
        # Имитируем печать
        await self._simulate_typing(response_text, state.energy)
        
        # Отправляем ответ
        try:
            await message.channel.send(response_text)
            
            # Обновляем состояние
            state.last_response_time = datetime.datetime.now()
            state.message_count_since_response = 0
            state.messages_sent_today += 1
            state.consecutive_responses += 1
            state.recent_responses.append(response_text)
            
            # Отмечаем использованные смайлики
            for emote in used_emotes:
                state.recent_emotes_used.append(emote)
            
            # Сохраняем в БД
            database.save_message(state.name, self.nick, response_text, is_bot=True)
            
            # Обновляем отношения с пользователем
            database.update_user_relationship(state.name, author, is_positive=True)
            
            logger.info(f"[{state.name}] 📨 Отправлено: {response_text}")
            
            # С вероятностью добавляем исправление опечатки
            if random.random() < config.TYPO_FIX_PROBABILITY:
                await asyncio.sleep(random.uniform(2, 5))
                await message.channel.send(f"*{self._get_fixed_version(response_text)}")
                logger.debug(f"[{state.name}] Исправление опечатки отправлено")
            
        except Exception as e:
            logger.error(f"[{state.name}] Ошибка отправки сообщения: {e}")
    
    async def _simulate_typing(self, text: str, energy: int):
        """Имитирует печать сообщения"""
        # Рассчитываем время печати
        words = len(text.split())
        
        # Базовая скорость в словах в минуту
        if energy > 80:
            wpm = 220  # Быстро
        elif energy > 50:
            wpm = 180  # Нормально
        else:
            wpm = 140  # Медленно
        
        # Время печати в секундах
        typing_time = (words / wpm) * 60
        
        # Добавляем случайность
        typing_time *= random.uniform(0.8, 1.2)
        
        # Минимальное время
        typing_time = max(1.0, typing_time)
        
        # Ждем
        await asyncio.sleep(typing_time)
        
        logger.debug(f"[Симуляция печати] {words} слов, {typing_time:.1f} сек, энергия: {energy}")
    
    def _get_fixed_version(self, text: str) -> str:
        """Возвращает исправленную версию текста"""
        # Простые исправления
        fixes = {
            'превет': 'привет',
            'спс': 'спасибо',
            'щас': 'сейчас',
            'ваще': 'вообще',
            'чо': 'что',
            'норм': 'нормально',
            'кста': 'кстати',
            'сиводня': 'сегодня',
            'завтра': 'завтра',
        }
        
        words = text.split()
        for i, word in enumerate(words):
            word_lower = word.lower().rstrip('.,!?')
            if word_lower in fixes:
                fixed = fixes[word_lower]
                # Сохраняем регистр и пунктуацию
                if word[0].isupper():
                    fixed = fixed.capitalize()
                if word[-1] in '.,!?':
                    fixed += word[-1]
                words[i] = fixed
                break
        
        return ' '.join(words)
    
    async def _background_analyzer(self):
        """Фоновая задача анализа контекста"""
        await self.wait_for_ready()
        
        logger.info("🔄 Фоновый анализатор запущен")
        
        while True:
            await asyncio.sleep(config.ANALYZER_UPDATE_INTERVAL)
            
            for channel_name, state in self.channel_states.items():
                try:
                    # Получаем последние сообщения
                    messages = database.get_last_messages(channel_name, config.ANALYZER_CONTEXT_SIZE)
                    
                    if len(messages) >= 5:  # Анализируем только если есть контекст
                        analysis = await context_analyzer.analyze_context(
                            channel=channel_name,
                            messages=messages,
                            current_message="[фоновая проверка]",
                            author="system",
                            channel_emotes=state.loaded_emotes
                        )
                        
                        state.last_context_analysis = analysis
                        
                        # Обновляем темы
                        if analysis.main_topics:
                            for topic in analysis.main_topics:
                                if topic not in state.current_topics:
                                    state.current_topics.append(topic)
                        
                        logger.debug(f"[{channel_name}] Фоновый анализ: {analysis.emotional_tone}")
                        
                except Exception as e:
                    logger.error(f"[{channel_name}] Ошибка фонового анализа: {e}")
    
    async def _energy_updater(self):
        """Фоновая задача обновления энергии"""
        await self.wait_for_ready()
        
        logger.info("🔄 Обновление энергии запущено")
        
        while True:
            await asyncio.sleep(60)  # Каждую минуту
            
            for channel_name, state in self.channel_states.items():
                state.update_energy()
                
                # Сбрасываем счетчик сообщений каждые 24 часа
                hour = datetime.datetime.now().hour
                if hour == 0:  # В полночь
                    state.messages_sent_today = 0
    
    async def _emote_refresher(self):
        """Фоновая задача обновления смайликов"""
        await self.wait_for_ready()
        
        logger.info("🔄 Обновление смайликов запущено")
        
        while True:
            await asyncio.sleep(3600)  # Каждый час
            
            for channel_name, state in self.channel_states.items():
                try:
                    # Перезагружаем смайлики раз в 6 часов
                    if (state.emote_load_time is None or 
                        (datetime.datetime.now() - state.emote_load_time).total_seconds() > 21600):
                        
                        logger.info(f"[{channel_name}] Перезагрузка смайликов...")
                        emotes = await emote_manager.load_channel_emotes(channel_name)
                        state.loaded_emotes = emotes
                        state.emote_load_time = datetime.datetime.now()
                        
                except Exception as e:
                    logger.error(f"[{channel_name}] Ошибка обновления смайликов: {e}")
    
    async def _silence_breaker(self):
        """Фоновая задача для разговора в тишине"""
        await self.wait_for_ready()
        
        logger.info("🔄 Система 'анти-тишина' запущена")
        
        silence_questions = [
            "о чем думаете?",
            "что нового?",
            "как настроение?",
            "во что играем?",
            "что смотрим?",
            "какие планы?",
            "что по музыке?",
            "какой контент сегодня?",
        ]
        
        while True:
            await asyncio.sleep(config.ACTIVITY_CHECK_INTERVAL)
            
            for channel_name, state in self.channel_states.items():
                try:
                    time_since_message = (datetime.datetime.now() - state.last_message_time).total_seconds()
                    time_since_response = (datetime.datetime.now() - state.last_response_time).total_seconds()
                    
                    # Если тишина больше порога и бот не говорил недавно
                    if (time_since_message > config.SILENCE_THRESHOLD and 
                        time_since_response > config.BOT_SILENCE_COOLDOWN):
                        
                        # Проверяем, не "спит" ли бот
                        if state.is_busy_time():
                            continue
                        
                        # Выбираем вопрос
                        question = random.choice(silence_questions)
                        
                        # Имитируем размышление
                        await asyncio.sleep(random.uniform(3, 8))
                        
                        # Отправляем сообщение
                        channel = self.get_channel(channel_name)
                        if channel:
                            await channel.send(question)
                            
                            # Обновляем состояние
                            state.last_response_time = datetime.datetime.now()
                            state.last_message_time = datetime.datetime.now()
                            state.messages_sent_today += 1
                            
                            # Сохраняем в БД
                            database.save_message(channel_name, self.nick, question, is_bot=True)
                            
                            logger.info(f"[{channel_name}] 🗣️  Прервал тишину: {question}")
                            
                except Exception as e:
                    logger.error(f"[{channel_name}] Ошибка системы 'анти-тишина': {e}")
    
    def _log_statistics(self):
        """Логирует статистику работы бота"""
        uptime = datetime.datetime.now() - self.start_time
        hours = uptime.total_seconds() / 3600
        
        logger.info("=" * 80)
        logger.info("📊 СТАТИСТИКА БОТА")
        logger.info(f"   Время работы: {hours:.1f} часов")
        logger.info(f"   Обработано сообщений: {self.total_messages_processed}")
        
        for channel_name, state in self.channel_states.items():
            logger.info(f"   [{channel_name}]: "
                       f"настроение={state.mood:.0f}, "
                       f"энергия={state.energy:.0f}, "
                       f"сообщений сегодня={state.messages_sent_today}")
        
        logger.info("=" * 80)
    
    async def event_error(self, error: Exception, data=None):
        """Обработка ошибок"""
        logger.error("=" * 80)
        logger.error(f"❌ ОШИБКА: {error}")
        if data:
            logger.error(f"Данные: {data}")
        logger.error("=" * 80)
        
        import traceback
        logger.error(traceback.format_exc())
    
    @commands.command(name='статус')
    async def status_command(self, ctx: commands.Context):
        """Команда !статус - показывает состояние бота"""
        state = self.channel_states.get(ctx.channel.name)
        if not state:
            return
        
        mood_desc = state.get_mood_description()
        energy_level = "🔋" * (state.energy // 20)
        
        status_msg = (
            f"@{ctx.author.name} Настроение: {mood_desc} "
            f"({state.mood:.0f}/100) {energy_level} "
            f"Энергия: {state.energy:.0f}%"
        )
        
        await ctx.send(status_msg)
    
    @commands.command(name='смайлы')
    async def emotes_command(self, ctx: commands.Context):
        """Команда !смайлы - показывает статистику смайликов"""
        state = self.channel_states.get(ctx.channel.name)
        if not state:
            return
        
        emote_count = len(state.loaded_emotes) if state.loaded_emotes else 0
        
        response = (
            f"@{ctx.author.name} В этом канале знаю {emote_count} смайликов "
            f"(7TV, BTTV, FFZ, Twitch). "
            f"Последние использованные: {', '.join(list(state.recent_emotes_used)[-3:]) if state.recent_emotes_used else 'пока нет'}"
        )
        
        await ctx.send(response)


async def main():
    """Главная функция запуска бота"""
    bot = HumanTwitchBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("\n⛔ Остановка по команде пользователя")
    except Exception as e:
        logger.error(f"\n❌ Критическая ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await bot.close_services()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
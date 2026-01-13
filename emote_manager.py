# emote_manager.py - Управление смайликами с 7TV и системой "помойки"
import logging
import aiohttp
import asyncio
from typing import Dict, List, Set, Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque
import json

logger = logging.getLogger(__name__)

class EmoteManager:
    """Управляет смайликами: загрузка, кеширование, система 'помойки'"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.channel_emotes: Dict[str, List[str]] = {}  # Смайлики по каналам
        self.emote_sources: Dict[str, Dict[str, List[str]]] = {}  # Источники по каналам
        self.emote_usage: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))  # Использование
        self.emote_cooldown: Dict[str, Dict[str, datetime]] = {}  # Смайлики в "помойке"
        self.recent_emotes: Dict[str, deque] = {}  # Последние использованные смайлы
        
    async def initialize(self):
        """Инициализация сессии"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
    
    async def load_channel_emotes(self, channel_name: str) -> List[str]:
        """Загружает все смайлики для канала (7TV, BTTV, FFZ, Twitch)"""
        logger.info(f"[{channel_name}] Загрузка смайликов...")
        
        all_emotes = []
        sources = {}
        
        try:
            # Загружаем смайлики из разных источников
            emotes_7tv = await self._load_7tv_emotes(channel_name)
            emotes_bttv = await self._load_bttv_emotes(channel_name)
            emotes_ffz = await self._load_ffz_emotes(channel_name)
            emotes_twitch = self._get_twitch_emotes()
            
            # Сохраняем по источникам
            sources = {
                '7tv': emotes_7tv,
                'bttv': emotes_bttv,
                'ffz': emotes_ffz,
                'twitch': emotes_twitch
            }
            
            # Объединяем все смайлики (убираем дубликаты)
            emotes_set = set()
            for source, emotes in sources.items():
                emotes_set.update(emotes)
            
            all_emotes = list(emotes_set)
            self.channel_emotes[channel_name] = all_emotes
            self.emote_sources[channel_name] = sources
            self.recent_emotes[channel_name] = deque(maxlen=20)
            self.emote_cooldown[channel_name] = {}
            
            logger.info(f"[{channel_name}] Загружено смайликов: "
                       f"7TV: {len(emotes_7tv)}, "
                       f"BTTV: {len(emotes_bttv)}, "
                       f"FFZ: {len(emotes_ffz)}, "
                       f"Twitch: {len(emotes_twitch)}, "
                       f"Всего: {len(all_emotes)}")
            
            return all_emotes
            
        except Exception as e:
            logger.error(f"[{channel_name}] Ошибка загрузки смайликов: {e}")
            # Возвращаем базовые твич смайлы
            base_emotes = self._get_twitch_emotes()
            self.channel_emotes[channel_name] = base_emotes
            return base_emotes
    
    async def _load_7tv_emotes(self, channel_name: str) -> List[str]:
        """Загружает смайлики 7TV"""
        try:
            # Получаем ID канала
            user_id = await self._get_7tv_user_id(channel_name)
            if not user_id:
                return []
            
            # Получаем смайлики канала
            url = f"https://7tv.io/v3/users/twitch/{user_id}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    emotes = []
                    
                    # Получаем смайлики из разных мест
                    if 'emote_set' in data and 'emotes' in data['emote_set']:
                        for emote in data['emote_set']['emotes']:
                            emotes.append(emote['name'])
                    
                    return emotes
        except Exception as e:
            logger.debug(f"[{channel_name}] Ошибка загрузки 7TV: {e}")
        
        return []
    
    async def _load_bttv_emotes(self, channel_name: str) -> List[str]:
        """Загружает смайлики BTTV"""
        try:
            # Глобальные BTTV смайлики
            url_global = "https://api.betterttv.net/3/cached/emotes/global"
            # Смайлики канала
            url_channel = f"https://api.betterttv.net/3/cached/users/twitch/{channel_name}"
            
            emotes = []
            
            # Глобальные
            async with self.session.get(url_global) as response:
                if response.status == 200:
                    data = await response.json()
                    for emote in data:
                        emotes.append(emote['code'])
            
            # Канальные
            async with self.session.get(url_channel) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'channelEmotes' in data:
                        for emote in data['channelEmotes']:
                            emotes.append(emote['code'])
                    if 'sharedEmotes' in data:
                        for emote in data['sharedEmotes']:
                            emotes.append(emote['code'])
            
            return emotes
        except Exception as e:
            logger.debug(f"[{channel_name}] Ошибка загрузки BTTV: {e}")
            return []
    
    async def _load_ffz_emotes(self, channel_name: str) -> List[str]:
        """Загружает смайлики FFZ"""
        try:
            url = f"https://api.frankerfacez.com/v1/room/{channel_name}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    emotes = []
                    
                    if 'sets' in data:
                        for set_id, set_data in data['sets'].items():
                            if 'emoticons' in set_data:
                                for emote in set_data['emoticons']:
                                    emotes.append(emote['name'])
                    
                    return emotes
        except Exception as e:
            logger.debug(f"[{channel_name}] Ошибка загрузки FFZ: {e}")
            return []
    
    async def _get_7tv_user_id(self, channel_name: str) -> Optional[str]:
        """Получает ID пользователя 7TV"""
        try:
            url = f"https://7tv.io/v3/users/twitch/{channel_name}"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['id']
        except:
            return None
    
    def _get_twitch_emotes(self) -> List[str]:
        """Возвращает список базовых Twitch смайликов"""
        return [
            # Базовые
            "Kappa", "KappaPride", "LUL", "LULW", "OMEGALUL",
            "Pog", "PogU", "PogChamp", "Poggers", "KEKW",
            # Эмоциональные
            "monkaS", "monkaW", "PepeHands", "Sadge", "FeelsGoodMan",
            "FeelsBadMan", "FeelsWeirdMan", "WeirdChamp", "AYAYA",
            # Действия
            "Clap", "PauseChamp", "ResidentSleeper", "BibleThump",
            "SourPls", "NotLikeThis", "TriHard", "Jebaited",
            # Реакции
            "WutFace", "4Head", "DansGame", "SwiftRage", "FailFish",
            "VoHiYo", "PJSalt", "CoolCat", "MrDestructoid",
            # Современные
            "gachiHYPER", "peepoClown", "Aware", "Clueless",
            "GIGACHAD", "Chatting", "Copege", "Madge", "BatChest"
        ]
    
    def get_available_emotes(self, channel_name: str, exclude_recent: int = 5) -> List[str]:
        """Получает доступные смайлики, исключая недавно использованные"""
        if channel_name not in self.channel_emotes:
            return self._get_twitch_emotes()
        
        all_emotes = self.channel_emotes[channel_name]
        recent = self.recent_emotes.get(channel_name, deque(maxlen=20))
        
        # Исключаем смайлики в "помойке"
        cooldown_emotes = self.emote_cooldown.get(channel_name, {})
        now = datetime.now()
        
        # Очищаем просроченные смайлы из "помойки"
        expired = [e for e, t in cooldown_emotes.items() 
                  if (now - t).total_seconds() > 300]  # 5 минут
        for emote in expired:
            del cooldown_emotes[emote]
        
        # Фильтруем доступные смайлы
        available = []
        for emote in all_emotes:
            if emote in cooldown_emotes:
                continue  # Пропускаем смайлы в "помойке"
            
            # Штрафуем недавно использованные
            usage_count = self.emote_usage[channel_name].get(emote, 0)
            recent_penalty = 1.0
            
            # Если использовался недавно - штраф
            if emote in recent:
                recent_penalty = 0.3
            
            # Добавляем с весом
            weight = self._calculate_emote_weight(emote, channel_name, usage_count, recent_penalty)
            available.append((emote, weight))
        
        # Сортируем по весу (убывание)
        available.sort(key=lambda x: x[1], reverse=True)
        
        # Возвращаем топ N смайликов
        return [e for e, _ in available[:50]]
    
    def _calculate_emote_weight(self, emote: str, channel: str, usage: int, recent_penalty: float) -> float:
        """Рассчитывает вес смайлика для выбора"""
        # Базовый вес
        weight = 1.0
        
        # Штраф за частое использование
        usage_penalty = max(0.1, 1.0 / (usage + 1))
        weight *= usage_penalty
        
        # Штраф за недавнее использование
        weight *= recent_penalty
        
        # Бонус за источник (7TV приоритетнее)
        for source, emotes in self.emote_sources.get(channel, {}).items():
            if emote in emotes:
                if source == '7tv':
                    weight *= 1.5
                elif source == 'bttv':
                    weight *= 1.3
                elif source == 'ffz':
                    weight *= 1.2
                break
        
        # Бонус за длину (короткие смайлы чаще используются)
        if len(emote) <= 6:
            weight *= 1.2
        
        return weight
    
    def mark_emote_used(self, channel_name: str, emote: str):
        """Отмечает смайлик как использованный"""
        # Увеличиваем счетчик использования
        self.emote_usage[channel_name][emote] = self.emote_usage[channel_name].get(emote, 0) + 1
        
        # Добавляем в список недавно использованных
        if channel_name in self.recent_emotes:
            self.recent_emotes[channel_name].append(emote)
        
        # Отправляем в "помойку" на некоторое время с вероятностью
        import random
        if random.random() < 0.3:  # 30% шанс отправить в "помойку"
            self.emote_cooldown.setdefault(channel_name, {})[emote] = datetime.now()
            logger.debug(f"[{channel_name}] Смайлик {emote} отправлен в 'помойку'")
    
    def get_random_emote(self, channel_name: str, exclude: List[str] = None) -> Optional[str]:
        """Выбирает случайный смайлик с учетом весов"""
        available = self.get_available_emotes(channel_name)
        
        if not available:
            return None
        
        if exclude:
            available = [e for e in available if e not in exclude]
        
        if not available:
            return None
        
        import random
        # Выбираем с учетом весов (первые в списке имеют больший вес)
        if random.random() < 0.7:  # 70% шанс выбрать из топ-10
            top_n = min(10, len(available))
            return random.choice(available[:top_n])
        else:  # 30% шанс выбрать случайный
            return random.choice(available)
    
    def should_add_emote(self, channel_name: str) -> bool:
        """Определяет, нужно ли добавить смайлик к сообщению"""
        if channel_name not in self.channel_emotes:
            return False
        
        # Более часто добавляем смайлики в активных чатах
        import random
        base_chance = 0.4  # 40% базовый шанс
        
        # Увеличиваем шанс если мало смайлов использовалось недавно
        recent = self.recent_emotes.get(channel_name, deque())
        if len(recent) < 5:
            base_chance += 0.2
        
        return random.random() < base_chance

# Глобальный экземпляр менеджера смайликов
emote_manager = EmoteManager()
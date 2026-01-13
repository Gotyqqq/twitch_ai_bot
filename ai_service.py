# ai_service.py - Система генерации ответов с использованием двух ИИ
import logging
import asyncio
import aiohttp
import json
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import config

from context_analyzer import context_analyzer, ContextAnalysis
from emote_manager import emote_manager

logger = logging.getLogger(__name__)

class HumanResponseGenerator:
    """Генератор человеческих ответов с использованием двух ИИ"""
    
    def __init__(self):
        self.gemini_api_key = config.GEMINI_API_KEY
        self.session: Optional[aiohttp.ClientSession] = None
        self.response_styles = {}
        self.last_responses = {}
        
    async def initialize(self):
        """Инициализация"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        # Инициализируем стили ответов
        self._init_response_styles()
    
    async def close(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
    
    def _init_response_styles(self):
        """Инициализация стилей ответов для разных настроений"""
        self.response_styles = {
            'excited': {
                'temperament': "энергичная, восторженная",
                'phrases': ['ого!', 'вау!', 'крутяк!', 'обожаю!', 'потрясающе!'],
                'sentence_length': 'short',
                'emote_frequency': 0.6
            },
            'happy': {
                'temperament': "радостная, дружелюбная",
                'phrases': ['здорово', 'отлично', 'рада слышать', 'мне нравится'],
                'sentence_length': 'medium',
                'emote_frequency': 0.5
            },
            'neutral': {
                'temperament': "спокойная, уравновешенная",
                'phrases': ['понятно', 'интересно', 'давай посмотрим', 'возможно'],
                'sentence_length': 'medium',
                'emote_frequency': 0.3
            },
            'tired': {
                'temperament': "уставшая, задумчивая",
                'phrases': ['устала', 'сонная', 'поздно уже', 'завтра поговорим'],
                'sentence_length': 'short',
                'emote_frequency': 0.2
            },
            'grumpy': {
                'temperament': "раздраженная, саркастичная",
                'phrases': ['ну и что', 'опять', 'надоело', 'сколько можно'],
                'sentence_length': 'short',
                'emote_frequency': 0.1
            }
        }
    
    async def generate_human_response(
        self,
        channel: str,
        context_analysis: ContextAnalysis,
        current_message: str,
        author: str,
        bot_nick: str,
        is_mentioned: bool,
        energy_level: int,
        available_emotes: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Генерирует человеческий ответ на основе анализа контекста.
        Возвращает (текст_ответа, использованные_смайлики)
        """
        await self.initialize()
        
        # Определяем стиль ответа на основе анализа
        response_style = self._determine_response_style(context_analysis, energy_level, is_mentioned)
        
        # Строим промпт для генератора
        prompt = self._build_response_prompt(
            context_analysis=context_analysis,
            current_message=current_message,
            author=author,
            response_style=response_style,
            is_mentioned=is_mentioned,
            available_emotes=available_emotes
        )
        
        # Генерируем ответ через Gemini
        raw_response = await self._generate_with_gemini(prompt, response_style)
        
        if not raw_response:
            # Fallback на простой ответ
            raw_response = self._generate_fallback_response(context_analysis, current_message)
        
        # Обрабатываем ответ: добавляем смайлы, опечатки, делаем человечным
        processed_response, used_emotes = self._humanize_response(
            raw_response=raw_response,
            channel=channel,
            response_style=response_style,
            is_mentioned=is_mentioned,
            energy_level=energy_level,
            available_emotes=available_emotes
        )
        
        # Запоминаем последний ответ
        self.last_responses[channel] = processed_response
        
        logger.info(f"[{channel}] Сгенерирован ответ ({response_style['mood']}): {processed_response[:100]}...")
        
        return processed_response, used_emotes
    
    def _determine_response_style(
        self, 
        analysis: ContextAnalysis, 
        energy: int, 
        is_mentioned: bool
    ) -> Dict:
        """Определяет стиль ответа на основе анализа и состояния"""
        mood = analysis.suggested_mood
        if mood not in self.response_styles:
            mood = 'neutral'
        
        style = self.response_styles[mood].copy()
        
        # Корректируем на основе энергии
        if energy < 30:
            style['temperament'] += ", очень уставшая"
            style['sentence_length'] = 'very_short'
            style['emote_frequency'] *= 0.5
        elif energy > 80:
            style['temperament'] += ", полная энергии"
            style['sentence_length'] = 'long'
            style['emote_frequency'] *= 1.3
        
        # Корректируем для упоминаний
        if is_mentioned:
            style['sentence_length'] = 'long'
            style['emote_frequency'] *= 1.2
        
        style['mood'] = mood
        return style
    
    def _build_response_prompt(
        self,
        context_analysis: ContextAnalysis,
        current_message: str,
        author: str,
        response_style: Dict,
        is_mentioned: bool,
        available_emotes: List[str]
    ) -> str:
        """Строит промпт для генерации ответа"""
        
        # Определяем длину ответа
        length_map = {
            'very_short': "1-5 слов",
            'short': "5-10 слов",
            'medium': "1-2 предложения",
            'long': "2-4 предложения"
        }
        target_length = length_map.get(response_style['sentence_length'], "1-2 предложения")
        
        # Доступные смайлики (первые 20 для экономии токенов)
        emotes_sample = ", ".join(available_emotes[:20]) if available_emotes else "нет доступных"
        
        prompt = f"""Ты {config.TWITCH_NICK}, 19 лет. Ты в твитч-чате, общаешься естественно как человек.

ТВОЁ СОСТОЯНИЕ:
- Настроение: {response_style['temperament']}
- Эмоциональный тон чата: {context_analysis.emotional_tone}
- Основные темы: {', '.join(context_analysis.main_topics[:3]) if context_analysis.main_topics else 'общий разговор'}

КОНТЕКСТ ДИАЛОГА (кратко):
{context_analysis.summary}

НОВОЕ СООБЩЕНИЕ от {author}:
"{current_message}"

{"⚠️ Тебя упомянули - отвечай развернуто и внимательно!" if is_mentioned else ""}

ДОСТУПНЫЕ СМАЙЛИКИ (используй РАЗНЫЕ, не повторяйся):
{emotes_sample}

ТВОИ ПРАВИЛА:
1. Отвечай ЕСТЕСТВЕННО, как обычный человек в чате
2. Длина: {target_length}
3. Используй РАЗГОВОРНЫЙ русский (можно сленг)
4. БЕЗ шаблонных фраз ("привет, как дела?")
5. МОЖНО с опечатками (редко, для естественности)
6. Используй смайлики уместно (не более 1-2 в ответе)
7. ОТВЕЧАЙ ПО СУТИ, не уходи от темы
8. Будь {response_style['temperament']} в ответе

Примеры хороших ответов:
- "ага, видел это" (коротко)
- "ну такое, мне не очень зашло" (с эмоцией)
- "ой, а я не в курсе, что там было" (естественно)
- "лол, точно" (со смайликом)

Твой ответ (только текст, без кавычек):"""
        
        return prompt
    
    async def _generate_with_gemini(self, prompt: str, response_style: Dict) -> Optional[str]:
        """Генерирует ответ через Gemini API"""
        if not self.gemini_api_key:
            logger.error("Gemini API key не настроен")
            return None
        
        # Определяем температуру на основе стиля
        temperature = 0.9  # Высокая для разнообразия
        if response_style['mood'] == 'grumpy':
            temperature = 0.7
        elif response_style['mood'] == 'excited':
            temperature = 1.0
        
        # Определяем длину токенов
        length_map = {
            'very_short': 50,
            'short': 100,
            'medium': 200,
            'long': 350
        }
        max_tokens = length_map.get(response_style['sentence_length'], 150)
        
        # Увеличиваем для упоминаний
        if 'mentioned' in prompt.lower():
            max_tokens = min(500, max_tokens * 2)
        
        try:
            # Используем прямой HTTP вызов к Gemini
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemma-3-27b-it:generateContent"
            
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "topP": 0.95,
                    "topK": 60
                },
                "safetySettings": [
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    }
                ]
            }
            
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.gemini_api_key
            }
            
            async with self.session.post(
                f"{url}?key={self.gemini_api_key}",
                json=payload,
                headers=headers,
                timeout=30
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        
                        # Очищаем ответ
                        text = self._clean_generated_text(text)
                        return text
                
                logger.error(f"Gemini API error: {response.status}")
                return None
                
        except asyncio.TimeoutError:
            logger.error("Gemini API timeout")
            return None
        except Exception as e:
            logger.error(f"Gemini API exception: {e}")
            return None
    
    def _clean_generated_text(self, text: str) -> str:
        """Очищает сгенерированный текст"""
        # Убираем кавычки в начале и конце
        text = text.strip('"\'').strip()
        
        # Убираем маркеры типа "Ответ:", "Бот:", и т.д.
        import re
        prefixes = [f"{config.TWITCH_NICK}:", "бот:", "ответ:", "assistant:"]
        for prefix in prefixes:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        
        # Убираем лишние пробелы
        text = re.sub(r'\s+', ' ', text)
        
        return text
    
    def _generate_fallback_response(self, analysis: ContextAnalysis, message: str) -> str:
        """Генерирует простой ответ если AI не сработал"""
        message_lower = message.lower()
        
        # Простые реакции на основе анализа
        if analysis.emotional_tone in ['happy', 'excited']:
            responses = ['ого!', 'круто', 'рада за тебя', 'здорово']
        elif analysis.emotional_tone in ['sad', 'angry']:
            responses = ['понятно', 'жаль', 'бывает', 'сочувствую']
        else:
            responses = ['ага', 'ясно', 'интересно', 'поняла']
        
        import random
        return random.choice(responses)
    
    def _humanize_response(
        self,
        raw_response: str,
        channel: str,
        response_style: Dict,
        is_mentioned: bool,
        energy_level: int,
        available_emotes: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Делает ответ более человечным:
        - Добавляет смайлики
        - Добавляет опечатки
        - Корректирует пунктуацию
        """
        if not raw_response:
            return "", []
        
        used_emotes = []
        text = raw_response
        
        # 1. Добавляем смайлики (с вероятностью)
        emote_chance = response_style['emote_frequency']
        if random.random() < emote_chance and available_emotes:
            # Выбираем смайлик
            emote = emote_manager.get_random_emote(channel)
            if emote:
                # Решаем куда добавить смайлик
                if random.random() < 0.7:  # 70% в конец
                    text = f"{text} {emote}"
                else:  # 30% в начало (реже)
                    text = f"{emote} {text}"
                
                used_emotes.append(emote)
                emote_manager.mark_emote_used(channel, emote)
        
        # 2. Добавляем опечатки (с меньшей вероятностью при упоминании)
        typo_chance = config.TYPO_PROBABILITY
        if is_mentioned:
            typo_chance *= 0.5  # Меньше опечаток при серьезных ответах
        
        if random.random() < typo_chance and len(text) > 10:
            text = self._add_typo(text)
        
        # 3. Добавляем "запинки" при низкой энергии
        if energy_level < 40 and random.random() < config.STUTTER_PROBABILITY:
            stutters = ['типа', 'ну', 'это', 'как бы', 'в общем']
            if len(text.split()) > 3:
                words = text.split()
                insert_pos = random.randint(0, len(words) // 2)
                words.insert(insert_pos, random.choice(stutters))
                text = ' '.join(words)
        
        # 4. Корректируем регистр для естественности
        text = self._adjust_capitalization(text)
        
        # 5. Убираем лишние точки в конце если есть смайлик
        if used_emotes and text.endswith('.'):
            text = text[:-1]
        
        return text.strip(), used_emotes
    
    def _add_typo(self, text: str) -> str:
        """Добавляет реалистичную опечатку"""
        if len(text) < 5:
            return text
        
        # Список типичных опечаток
        common_typos = {
            'привет': ['превет', 'привт', 'приветь'],
            'спасибо': ['спс', 'спасиб', 'спасибки'],
            'нормально': ['норм', 'нормас', 'нормуль'],
            'вообще': ['ваще', 'вобще', 'вапще'],
            'что': ['чо', 'че', 'шо'],
            'чтобы': ['чтоб', 'чтобы'],
            'когда': ['када', 'когды'],
            'потому что': ['потомучто', 'потамушта'],
            'сегодня': ['сиводня', 'седня'],
            'завтра': ['завтра', 'зафтра'],
        }
        
        words = text.split()
        for i, word in enumerate(words):
            word_lower = word.lower().rstrip('.,!?')
            
            # Проверяем на типичные опечатки
            if word_lower in common_typos and random.random() < 0.3:
                typo = random.choice(common_typos[word_lower])
                
                # Сохраняем регистр
                if word[0].isupper():
                    typo = typo.capitalize()
                
                # Сохраняем знаки препинания
                if word[-1] in '.,!?':
                    typo += word[-1]
                
                words[i] = typo
                break
        
        # Если не нашли типичную опечатку, делаем случайную
        if words == text.split():
            # Выбираем случайное слово длиной > 3
            long_words = [i for i, w in enumerate(words) if len(w) > 3]
            if long_words:
                idx = random.choice(long_words)
                word = words[idx]
                
                # Меняем одну букву
                if len(word) > 1:
                    pos = random.randint(0, len(word) - 1)
                    char = word[pos]
                    
                    # Карта частых опечаток
                    typo_map = {
                        'а': 'о', 'о': 'а', 'е': 'и', 'и': 'е',
                        'т': 'р', 'р': 'т', 'н': 'м', 'м': 'н',
                        'к': 'л', 'л': 'к', 'п': 'р', 'с': 'ш'
                    }
                    
                    if char.lower() in typo_map:
                        new_char = typo_map[char.lower()]
                        if char.isupper():
                            new_char = new_char.upper()
                        
                        words[idx] = word[:pos] + new_char + word[pos+1:]
        
        return ' '.join(words)
    
    def _adjust_capitalization(self, text: str) -> str:
        """Корректирует регистр для естественности"""
        if not text:
            return text
        
        # Не начинаем с заглавной если это междометие или короткое слово
        first_word = text.split()[0].lower() if text.split() else ""
        informal_starts = ['ну', 'типа', 'короче', 'вот', 'так', 'ага', 'хз']
        
        if first_word in informal_starts and len(text) > len(first_word) + 2:
            # Делаем первую букву строчной
            text = text[0].lower() + text[1:]
        
        return text

# Глобальный экземпляр генератора
response_generator = HumanResponseGenerator()
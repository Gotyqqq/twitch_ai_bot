# ai_service.py - Улучшенная система генерации ответов

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
    """Генератор максимально человечных ответов"""
    
    def __init__(self):
        self.gemini_api_key = config.GEMINI_API_KEY
        self.session: Optional[aiohttp.ClientSession] = None
        self.response_styles = {}
        self.last_responses = {}
        self.conversation_memory = {}  # Новое: память диалогов
        
    async def initialize(self):
        if not self.session:
            self.session = aiohttp.ClientSession()
        self._init_response_styles()
        
    async def close(self):
        if self.session:
            await self.session.close()
            
    def _init_response_styles(self):
        self.response_styles = {
            'excited': {
                'temperament': "энергичная, восторженная",
                'phrases': ['ого!', 'вау!', 'крутяк!', 'обожаю!', 'потрясающе!', 'ИМБА!', 'ВАУ!'],
                'sentence_length': 'short',
                'emote_frequency': 0.7,
                'caps_chance': 0.2
            },
            'happy': {
                'temperament': "радостная, дружелюбная",
                'phrases': ['здорово', 'отлично', 'рада', 'мне нравится', 'найс', 'топ'],
                'sentence_length': 'medium',
                'emote_frequency': 0.5,
                'caps_chance': 0.05
            },
            'neutral': {
                'temperament': "спокойная, уравновешенная",
                'phrases': ['понятно', 'интересно', 'давай посмотрим', 'возможно', 'мб', 'хз'],
                'sentence_length': 'medium',
                'emote_frequency': 0.3,
                'caps_chance': 0.02
            },
            'tired': {
                'temperament': "уставшая, задумчивая",
                'phrases': ['устала', 'сонная', 'поздно уже', 'завтра', 'хз', 'лень'],
                'sentence_length': 'very_short',
                'emote_frequency': 0.15,
                'caps_chance': 0.01
            },
            'grumpy': {
                'temperament': "раздраженная, саркастичная",
                'phrases': ['ну и что', 'опять', 'надоело', 'сколько можно', 'блин'],
                'sentence_length': 'short',
                'emote_frequency': 0.1,
                'caps_chance': 0.1
            },
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
        """Генерирует максимально человечный ответ"""
        
        await self.initialize()
        
        # НОВОЕ: Иногда отвечаем только коротко
        if not is_mentioned and random.random() < config.SHORT_REACTION_PROBABILITY:
            return self._generate_short_reaction(context_analysis, energy_level, available_emotes)
        
        # НОВОЕ: Иногда отвечаем только смайликом
        if not is_mentioned and random.random() < config.EMOJI_ONLY_RESPONSE and available_emotes:
            emote = emote_manager.get_random_emote(channel)
            if emote:
                return (emote, [emote])
        
        response_style = self._determine_response_style(context_analysis, energy_level, is_mentioned)
        
        prompt = self._build_response_prompt(
            context_analysis=context_analysis,
            current_message=current_message,
            author=author,
            response_style=response_style,
            is_mentioned=is_mentioned,
            available_emotes=available_emotes,
            channel=channel
        )
        
        raw_response = await self._generate_with_gemini(prompt, response_style)
        
        if not raw_response:
            raw_response = self._generate_fallback_response(context_analysis, current_message)
        
        # НОВОЕ: Применяем сленг
        if config.USE_SLANG and random.random() < config.SLANG_PROBABILITY:
            raw_response = self._apply_slang(raw_response)
        
        processed_response, used_emotes = self._humanize_response(
            raw_response=raw_response,
            channel=channel,
            response_style=response_style,
            is_mentioned=is_mentioned,
            energy_level=energy_level,
            available_emotes=available_emotes
        )
        
        # Сохраняем в память
        self._save_to_memory(channel, processed_response, author, current_message)
        
        self.last_responses[channel] = processed_response
        logger.info(f"[{channel}] Ответ ({response_style['mood']}): {processed_response[:80]}...")
        
        return processed_response, used_emotes
    
    def _generate_short_reaction(
        self, 
        analysis: ContextAnalysis, 
        energy: int,
        emotes: List[str]
    ) -> Tuple[str, List[str]]:
        """НОВОЕ: Генерирует короткую реакцию"""
        
        reactions = config.SHORT_REACTIONS.copy()
        
        # Выбираем реакцию в зависимости от тона
        if analysis.emotional_tone in ['happy', 'excited']:
            reactions.extend(['ахаха', 'кек', 'лол', 'найс', 'топ'])
        elif analysis.emotional_tone in ['sad', 'angry']:
            reactions.extend(['эх', 'жаль', 'грустно', 'печаль'])
        
        reaction = random.choice(reactions)
        
        # Иногда добавляем смайлик
        used_emotes = []
        if random.random() < 0.4 and emotes:
            emote = random.choice(emotes[:20])
            reaction = f"{reaction} {emote}"
            used_emotes = [emote]
        
        return (reaction, used_emotes)
    
    def _apply_slang(self, text: str) -> str:
        """НОВОЕ: Применяет интернет-сленг"""
        
        words = text.split()
        for i, word in enumerate(words):
            word_clean = word.lower().rstrip('.,!?')
            
            if word_clean in config.INTERNET_SLANG:
                if random.random() < 0.6:  # 60% заменяем
                    slang = random.choice(config.INTERNET_SLANG[word_clean])
                    # Сохраняем регистр и пунктуацию
                    if word[0].isupper():
                        slang = slang.capitalize()
                    if word[-1] in '.,!?':
                        slang += word[-1]
                    words[i] = slang
        
        return ' '.join(words)
    
    def _save_to_memory(self, channel: str, response: str, author: str, message: str):
        """НОВОЕ: Сохраняет контекст диалога"""
        
        if channel not in self.conversation_memory:
            self.conversation_memory[channel] = []
        
        self.conversation_memory[channel].append({
            'author': author,
            'message': message,
            'bot_response': response,
            'timestamp': datetime.now()
        })
        
        # Ограничиваем размер памяти
        if len(self.conversation_memory[channel]) > 20:
            self.conversation_memory[channel] = self.conversation_memory[channel][-20:]
    
    def _determine_response_style(
        self,
        analysis: ContextAnalysis,
        energy: int,
        is_mentioned: bool
    ) -> Dict:
        """Определяет стиль ответа"""
        
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
            style['emote_frequency'] *= 1.3
        
        # Корректируем для упоминаний
        if is_mentioned:
            style['sentence_length'] = 'medium' if energy < 50 else 'long'
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
        available_emotes: List[str],
        channel: str
    ) -> str:
        """Строит промпт для генерации"""
        
        length_map = {
            'very_short': "1-5 слов",
            'short': "5-10 слов",
            'medium': "1-2 предложения",
            'long': "2-4 предложения"
        }
        
        target_length = length_map.get(response_style['sentence_length'], "1-2 предложения")
        emotes_sample = ", ".join(available_emotes[:20]) if available_emotes else "нет"
        
        # НОВОЕ: Добавляем память прошлых диалогов
        memory_context = ""
        if channel in self.conversation_memory and self.conversation_memory[channel]:
            recent_mem = self.conversation_memory[channel][-3:]
            memory_context = "\nВСПОМИНАЕШЬ (может быть полезно):\n"
            for mem in recent_mem:
                memory_context += f"- {mem['author']} говорил: {mem['message'][:50]}...\n"
        
        prompt = f"""Ты {config.TWITCH_NICK}, обычная 19-летняя девушка в твитч-чате. Общаешься МАКСИМАЛЬНО естественно.

ТВОЁ СОСТОЯНИЕ:
- Настроение: {response_style['temperament']}
- Тон чата: {context_analysis.emotional_tone}
- Темы: {', '.join(context_analysis.main_topics[:3]) if context_analysis.main_topics else 'просто болтают'}

{memory_context}

КОНТЕКСТ:
{context_analysis.summary}

НОВОЕ от {author}:
"{current_message}"

{f"⚠️ ТЕБЯ УПОМЯНУЛИ - ответь по делу!" if is_mentioned else ""}

СМАЙЛИКИ: {emotes_sample}

ВАЖНО:
1. Пиши {target_length}, как в обычном чате
2. МОЖНО: сленг, сокращения, опечатки, эмоции
3. НЕЛЬЗЯ: шаблонные фразы, излишняя вежливость, "правильный" язык
4. Будь {response_style['temperament']}
5. Иногда можешь ответить очень кратко (ага/лол/хз)
6. Пиши ЖИВО, как обычный человек в 2025

Примеры ХОРОШИХ ответов:
- "ваще согл"
- "хз, не помню"
- "ахаха точно"
- "мб попробую"
- "найс"

Твой ответ (только текст, БЕЗ кавычек):"""
        
        return prompt
    
    async def _generate_with_gemini(self, prompt: str, response_style: Dict) -> Optional[str]:
        """Генерирует через Gemini"""
        
        if not self.gemini_api_key:
            logger.error("Gemini API key не настроен")
            return None
        
        temperature = 0.95  # Высокая для разнообразия
        if response_style['mood'] == 'tired':
            temperature = 0.7
        elif response_style['mood'] == 'excited':
            temperature = 1.0
        
        length_map = {
            'very_short': 50,
            'short': 100,
            'medium': 200,
            'long': 350
        }
        
        max_tokens = length_map.get(response_style['sentence_length'], 150)
        
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            
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
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
            }
            
            headers = {
                "Content-Type": "application/json"
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
                        return self._clean_generated_text(text)
                
                logger.error(f"Gemini API error: {response.status}")
                return None
                
        except Exception as e:
            logger.error(f"Gemini exception: {e}")
            return None
    
    def _clean_generated_text(self, text: str) -> str:
        """Очищает сгенерированный текст"""
        
        text = text.strip('"\'').strip()
        
        # Убираем префиксы
        import re
        prefixes = [f"{config.TWITCH_NICK}:", "бот:", "ответ:", "assistant:", "я:"]
        for prefix in prefixes:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        
        text = re.sub(r'\s+', ' ', text)
        return text
    
    def _generate_fallback_response(self, analysis: ContextAnalysis, message: str) -> str:
        """Запасной ответ"""
        
        message_lower = message.lower()
        
        if analysis.emotional_tone in ['happy', 'excited']:
            responses = ['ого!', 'круто', 'найс', 'топ', 'кек']
        elif analysis.emotional_tone in ['sad', 'angry']:
            responses = ['эх', 'жаль', 'понял', 'бывает']
        else:
            responses = ['ага', 'ясн', 'пон', 'хз', 'мб']
        
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
        """Делает ответ человечным"""
        
        if not raw_response:
            return "", []
        
        used_emotes = []
        text = raw_response
        
        # 1. Смайлики
        emote_chance = response_style['emote_frequency']
        if random.random() < emote_chance and available_emotes:
            emote = emote_manager.get_random_emote(channel)
            if emote:
                if random.random() < 0.7:
                    text = f"{text} {emote}"
                else:
                    text = f"{emote} {text}"
                used_emotes.append(emote)
                emote_manager.mark_emote_used(channel, emote)
        
        # 2. НОВОЕ: Капслок при эмоциях
        caps_chance = response_style.get('caps_chance', 0.02)
        if random.random() < caps_chance and len(text) > 5:
            # Делаем всё в капслоке
            text = text.upper()
        
        # 3. Опечатки
        typo_chance = config.TYPO_PROBABILITY
        if is_mentioned:
            typo_chance *= 0.6
        
        if random.random() < typo_chance and len(text) > 10:
            text = self._add_realistic_typo(text)
        
        # 4. Запинки
        if energy_level < 40 and random.random() < config.STUTTER_PROBABILITY:
            stutters = ['типа', 'ну', 'это', 'как бы', 'в общем', 'короче', 'вот']
            words = text.split()
            if len(words) > 2:
                insert_pos = random.randint(0, min(2, len(words)-1))
                words.insert(insert_pos, random.choice(stutters))
                text = ' '.join(words)
        
        # 5. Корректируем регистр
        text = self._adjust_capitalization(text)
        
        # 6. Убираем точку если есть смайлик
        if used_emotes and text.endswith('.'):
            text = text[:-1]
        
        return text.strip(), used_emotes
    
    def _add_realistic_typo(self, text: str) -> str:
        """УЛУЧШЕННЫЕ опечатки"""
        
        if len(text) < 5:
            return text
        
        # Типичные опечатки
        common_typos = {
            'привет': ['превет', 'привт', 'приветь', 'прив'],
            'спасибо': ['спасиб', 'спс', 'сяс'],
            'нормально': ['норм', 'нармально'],
            'вообще': ['ваще', 'вобще'],
            'что': ['чо', 'че', 'шо'],
            'когда': ['када', 'когдп'],
            'потому': ['потаму', 'патаму'],
            'сегодня': ['седня', 'севодня'],
            'сейчас': ['щас', 'счас', 'сичас'],
            'наверное': ['наверн', 'навернео'],
            'понятно': ['пон', 'понятн'],
            'интересно': ['интересн', 'интиресно'],
        }
        
        words = text.split()
        for i, word in enumerate(words):
            word_clean = word.lower().rstrip('.,!?')
            
            # Проверяем типичные опечатки
            if word_clean in common_typos and random.random() < 0.4:
                typo = random.choice(common_typos[word_clean])
                if word[0].isupper():
                    typo = typo.capitalize()
                if word[-1] in '.,!?':
                    typo += word[-1]
                words[i] = typo
                break
        
        # Если не нашли, делаем случайную
        if words == text.split() and len(words) > 0:
            # Пропускаем букву
            long_words_idx = [i for i, w in enumerate(words) if len(w) > 4]
            if long_words_idx and random.random() < 0.5:
                idx = random.choice(long_words_idx)
                word = words[idx]
                pos = random.randint(1, len(word)-2)
                words[idx] = word[:pos] + word[pos+1:]
        
        return ' '.join(words)
    
    def _adjust_capitalization(self, text: str) -> str:
        """Корректирует регистр"""
        
        if not text or text.isupper():  # Если капслок - оставляем
            return text
        
        first_word = text.split()[0].lower() if text.split() else ""
        informal_starts = ['ну', 'типа', 'короче', 'вот', 'так', 'ага', 'хз', 'лол', 'ок']
        
        if first_word in informal_starts:
            text = text[0].lower() + text[1:]
        
        return text

# Глобальный экземпляр
response_generator = HumanResponseGenerator()

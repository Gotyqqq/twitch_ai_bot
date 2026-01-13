# context_analyzer.py - Мозг бота, анализирует контекст и эмоции
import logging
import asyncio
import aiohttp
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import config

logger = logging.getLogger(__name__)

@dataclass
class ContextAnalysis:
    """Результат анализа контекста"""
    summary: str                     # Краткая сводка диалога
    emotional_tone: str              # Эмоциональный тон (радостный, грустный и т.д.)
    main_topics: List[str]           # Основные темы обсуждения
    relationship_status: Dict[str, str]  # Отношения с участниками
    suggested_mood: str              # Рекомендуемое настроение для ответа
    should_respond: bool             # Стоит ли отвечать
    response_style: str              # Стиль ответа (краткий, развернутый, шутливый)
    relevant_emotes: List[str]       # Релевантные смайлики

class ContextAnalyzer:
    """Анализирует контекст чата с помощью Mistral"""
    
    def __init__(self):
        self.api_key = config.MISTRAL_API_KEY
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache = {}  # Кеш анализов по каналам
        self.last_update = {}
        
    async def initialize(self):
        """Инициализация сессии"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
    
    def _should_update_analysis(self, channel: str) -> bool:
        """Проверяет, нужно ли обновлять анализ"""
        if channel not in self.last_update:
            return True
        
        time_since = datetime.now() - self.last_update[channel]
        return time_since.total_seconds() > config.ANALYZER_UPDATE_INTERVAL
    
    async def analyze_context(
        self,
        channel: str,
        messages: List[Dict],
        current_message: str,
        author: str,
        channel_emotes: List[str]
    ) -> ContextAnalysis:
        """
        Глубокий анализ контекста чата.
        Использует Mistral для понимания эмоций, тем и отношений.
        """
        # Проверяем кеш
        cache_key = f"{channel}:{hash(str(messages[-5:]))}"
        if cache_key in self.cache and not self._should_update_analysis(channel):
            return self.cache[cache_key]
        
        await self.initialize()
        
        # Подготавливаем контекст для анализа
        formatted_context = self._format_context_for_analysis(messages, current_message, author)
        
        # Системный промпт для анализа
        system_prompt = """Ты - психологический анализатор твитч-чата. 
Анализируй контекст диалога и предоставляй рекомендации для ответа.

Твои задачи:
1. Определи эмоциональный тон диалога (радостный, грустный, нейтральный, возбужденный)
2. Выдели 2-3 основные темы обсуждения
3. Оцени отношения между участниками
4. Рекомендуй настроение для ответа
5. Предложи стиль ответа (краткий, развернутый, шутливый, серьезный)
6. Выбери релевантные смайлики из предоставленного списка

Будь кратким и конкретным в анализе."""
        
        # Собираем доступные смайлики (первые 30 для экономии токенов)
        available_emotes = ", ".join(channel_emotes[:30])
        
        user_prompt = f"""Контекст диалога:
{formatted_context}

Новое сообщение от {author}: "{current_message}"

Доступные смайлики в этом канале: {available_emotes}

Проанализируй и предоставь JSON в следующем формате:
{{
    "summary": "краткая сводка диалога (1-2 предложения)",
    "emotional_tone": "эмоциональный_тон",
    "main_topics": ["тема1", "тема2", "тема3"],
    "relationship_status": {{"участник1": "статус", "участник2": "статус"}},
    "suggested_mood": "настроение_для_ответа",
    "should_respond": true/false,
    "response_style": "стиль_ответа",
    "relevant_emotes": ["смайлик1", "смайлик2", "смайлик3"]
}}"""
        
        try:
            # Вызываем Mistral API
            response_text = await self._call_mistral_analysis(system_prompt, user_prompt)
            
            # Парсим JSON ответ
            analysis_data = self._parse_analysis_response(response_text)
            
            # Создаем объект анализа
            analysis = ContextAnalysis(
                summary=analysis_data.get("summary", "Диалог продолжается"),
                emotional_tone=analysis_data.get("emotional_tone", "neutral"),
                main_topics=analysis_data.get("main_topics", []),
                relationship_status=analysis_data.get("relationship_status", {}),
                suggested_mood=analysis_data.get("suggested_mood", "neutral"),
                should_respond=analysis_data.get("should_respond", True),
                response_style=analysis_data.get("response_style", "normal"),
                relevant_emotes=analysis_data.get("relevant_emotes", [])
            )
            
            # Кешируем результат
            self.cache[cache_key] = analysis
            self.last_update[channel] = datetime.now()
            
            # Очищаем старые записи из кеша
            self._clean_cache()
            
            logger.info(f"[{channel}] Анализ контекста: {analysis.emotional_tone}, темы: {analysis.main_topics}")
            return analysis
            
        except Exception as e:
            logger.error(f"[{channel}] Ошибка анализа контекста: {e}")
            # Возвращаем анализ по умолчанию
            return ContextAnalysis(
                summary="Ошибка анализа",
                emotional_tone="neutral",
                main_topics=[],
                relationship_status={},
                suggested_mood="neutral",
                should_respond=True,
                response_style="normal",
                relevant_emotes=[]
            )
    
    def _format_context_for_analysis(self, messages: List[Dict], current: str, author: str) -> str:
        """Форматирует контекст для анализа"""
        context_lines = []
        
        for msg in messages[-config.ANALYZER_CONTEXT_SIZE:]:
            speaker = "бот" if msg["is_bot"] else msg["author"]
            context_lines.append(f"{speaker}: {msg['content']}")
        
        context_lines.append(f"НОВОЕ СООБЩЕНИЕ от {author}: {current}")
        
        return "\n".join(context_lines)
    
    async def _call_mistral_analysis(self, system_prompt: str, user_prompt: str) -> str:
        """Вызывает Mistral API для анализа"""
        if not self.api_key:
            raise ValueError("Mistral API key not configured")
        
        url = "https://api.mistral.ai/v1/chat/completions"
        
        payload = {
            "model": config.ANALYZER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,  # Низкая температура для консистентного анализа
            "max_tokens": 500,
            "response_format": {"type": "json_object"}
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        async with self.session.post(url, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data["choices"][0]["message"]["content"]
            else:
                error_text = await response.text()
                raise Exception(f"Mistral API error: {response.status} - {error_text}")
    
    def _parse_analysis_response(self, response_text: str) -> Dict:
        """Парсит JSON ответ от анализатора"""
        try:
            # Убираем возможные лишние символы
            text = response_text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON анализа: {e}")
            # Пытаемся извлечь JSON из текста
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except:
                    pass
            
            # Возвращаем значения по умолчанию
            return {
                "summary": "Анализ не удался",
                "emotional_tone": "neutral",
                "main_topics": [],
                "relationship_status": {},
                "suggested_mood": "neutral",
                "should_respond": True,
                "response_style": "normal",
                "relevant_emotes": []
            }
    
    def _clean_cache(self):
        """Очищает старые записи из кеша"""
        max_cache_size = 50
        if len(self.cache) > max_cache_size:
            # Удаляем самые старые записи
            keys_to_remove = list(self.cache.keys())[:len(self.cache) - max_cache_size]
            for key in keys_to_remove:
                del self.cache[key]
    
    async def analyze_user_message(self, message: str, author: str) -> Dict:
        """Быстрый анализ отдельного сообщения пользователя"""
        system_prompt = """Ты анализируешь сообщения в твитч-чате. 
Определи: эмоциональный тон, содержит ли вопрос, личное обращение."""
        
        user_prompt = f"""Сообщение от {author}: "{message}"

Ответь в формате JSON:
{{
    "emotion": "нейтральный/радостный/грустный/злой/удивленный",
    "contains_question": true/false,
    "is_personal": true/false,
    "urgency": 1-5
}}"""
        
        try:
            response = await self._call_mistral_analysis(system_prompt, user_prompt)
            return json.loads(response)
        except:
            return {
                "emotion": "neutral",
                "contains_question": False,
                "is_personal": False,
                "urgency": 1
            }

# Глобальный экземпляр анализатора
context_analyzer = ContextAnalyzer()
ai_service.py - ГИБРИДНАЯ СИСТЕМА: Google Gemma для анализа + Mistral для генерации
import logging
import asyncio
import config
from typing import Optional
import time
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(name)

============================================================================
ПРОВЕРКА И ЗАГРУЗКА ЗАВИСИМОСТЕЙ
============================================================================
mistral_available = False
google_available = False
mistral_client = None
gemma_model = None

try:
from mistralai import Mistral
mistral_available = True
mistral_client = Mistral(api_key=config.MISTRAL_API_KEY)
logger.info("✅ Mistral загружена успешно")
except ImportError:
logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Mistral не установлена!")
logger.error(" Выполни на сервере: pip install mistralai")
except Exception as e:
logger.error(f"❌ Ошибка при загрузке Mistral: {e}")

try:
import google.generativeai as genai
google_available = True
genai.configure(api_key=config.GOOGLE_AI_KEY)
gemma_model = genai.GenerativeModel("gemini-2.0-flash")
logger.info("✅ Google Generative AI загружена успешно")
except ImportError:
logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Google Generative AI не установлена!")
logger.error(" Выполни на сервере: pip install google-generativeai")
except Exception as e:
logger.error(f"❌ Ошибка при загрузке Google AI: {e}")

============================================================================
ОТСЛЕЖИВАНИЕ ТОКЕНОВ
============================================================================
token_usage = {
"mistral_tokens": 0,
"gemma_tokens": 0,
"minute_tokens": 0,
"day_tokens": 0,
"minute_reset_time": time.time(),
"day_reset_time": time.time(),
}

request_lock = asyncio.Lock()

def add_token_usage(mistral_tokens: int = 0, gemma_tokens: int = 0):
"""Добавляет использованные токены в счетчик."""
total = mistral_tokens + gemma_tokens
token_usage["mistral_tokens"] += mistral_tokens
token_usage["gemma_tokens"] += gemma_tokens
token_usage["minute_tokens"] += total
token_usage["day_tokens"] += total

============================================================================
АНАЛИЗ КОНТЕКСТА (Google Gemma)
============================================================================
async def analyze_context(
context_messages: list, current_message: str, bot_nick: str
) -> Optional[dict]:
"""Анализирует контекст с помощью Google Gemma."""

text
if not google_available or gemma_model is None:
    logger.warning("⚠️  Gemma недоступна, используем дефолтный анализ")
    return {
        "theme": "общий разговор",
        "sentiment": "neutral",
        "tone": "friendly",
        "key_topic": current_message[:30],
        "context_summary": "Текущее сообщение",
        "reply_direction": "просто ответить естественно",
    }

context_text = "\n".join(
    [
        f"{'Я' if msg['is_bot'] else msg['author']}: {msg['content']}"
        for msg in context_messages[-12:]
    ]
)

analysis_prompt = f"""Проанализируй контекст разговора и текущее сообщение. Ответь ТОЛЬКО в формате JSON.
КОНТЕКСТ:
{context_text}

НОВОЕ СООБЩЕНИЕ: {current_message}

Верни JSON (ТОЛЬКО JSON):
{{
"theme": "основная тема разговора (2-3 слова)",
"sentiment": "positive/neutral/negative",
"tone": "friendly/serious/joking/flirty/sarcastic",
"key_topic": "главная тема для ответа",
"context_summary": "краткое резюме контекста (1 предложение)",
"reply_direction": "как нужно ответить - направление ответа"
}}
"""

text
try:
    response = gemma_model.generate_content(
        analysis_prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.3,
            max_output_tokens=300,
        ),
    )

    if not response or not response.text:
        return None

    analysis_text = response.text.strip()

    try:
        analysis = json.loads(analysis_text)
        add_token_usage(gemma_tokens=250)
        return analysis
    except json.JSONDecodeError:
        logger.warning("⚠️  Gemma вернула невалидный JSON")
        return {
            "theme": "общий разговор",
            "sentiment": "neutral",
            "tone": "friendly",
            "key_topic": current_message[:30],
            "context_summary": "Текущее сообщение",
            "reply_direction": "просто ответить естественно",
        }

except Exception as e:
    logger.error(f"❌ Ошибка анализа контекста Gemma: {e}")
    return None
============================================================================
ГЕНЕРАЦИЯ ОТВЕТА (Mistral Large)
============================================================================
async def generate_response(
system_prompt: str,
context_messages: list,
current_message: str,
bot_nick: str,
is_mentioned: bool = False,
chat_phrases: Optional[list] = None,
hot_topics: Optional[list] = None,
user_facts: Optional[list] = None,
mood_state: Optional[str] = None,
energy_level: int = 80,
relationship_level: str = "stranger",
channel_emotes: Optional[list] = None,
) -> Optional[str]:
"""Главная функция генерации ответа."""

text
if not mistral_available or mistral_client is None:
    logger.error("❌ Mistral недоступна, не могу генерировать ответ!")
    return None

async with request_lock:
    # Анализируем контекст
    logger.info("🧠 Анализируем контекст (Google Gemma)...")
    context_analysis = await asyncio.to_thread(
        analyze_context,
        list(context_messages[-config.CONTEXT_MESSAGE_LIMIT :]),
        current_message,
        bot_nick,
    )

    if not context_analysis:
        context_analysis = {
            "theme": "разговор",
            "sentiment": "neutral",
            "tone": "friendly",
            "key_topic": current_message[:30],
            "context_summary": "Текущее сообщение",
            "reply_direction": "просто ответить",
        }

    # Определяем параметры ответа
    if is_mentioned:
        max_length = config.MAX_RESPONSE_LENGTH_MENTIONED
        max_tokens = 500
    else:
        max_length = config.MAX_RESPONSE_LENGTH
        max_tokens = 250

    # Улучшиваем промпт
    enhanced_prompt = system_prompt
    enhanced_prompt += f"""
[АНАЛИЗ КОНТЕКСТА]:
• Тема: {context_analysis.get('theme', 'неизвестна')}
• Тон: {context_analysis.get('tone', 'дружелюбный')}
• О чем: {context_analysis.get('context_summary', 'неясно')}
"""

text
    # Генерируем ответ
    logger.info("✍️ Генерируем ответ (Mistral Large)...")

    try:
        response = await asyncio.to_thread(
            mistral_client.chat.complete,
            model="mistral-large-latest",
            messages=[
                {"role": "user", "content": enhanced_prompt},
                {"role": "user", "content": current_message},
            ],
            temperature=0.85,
            max_output_tokens=max_tokens,
            top_p=0.9,
        )

        if not response or not response.choices:
            return None

        answer = response.choices.message.content.strip()
        add_token_usage(mistral_tokens=max_tokens)

        # Обрезаем до макс длины
        if len(answer) > max_length:
            truncated = answer[:max_length]
            last_punct = max(
                truncated.rfind("."),
                truncated.rfind("!"),
                truncated.rfind("?"),
            )
            if last_punct > max_length // 2:
                answer = truncated[: last_punct + 1]
            else:
                answer = truncated.rsplit(" ", 1) + "."

        logger.info("✅ Ответ сгенерирован")
        return answer

    except Exception as e:
        logger.error(f"❌ Ошибка генерации Mistral: {e}")
        return None
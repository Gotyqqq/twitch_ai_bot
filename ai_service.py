# ai_service.py - ГИБРИДНАЯ СИСТЕМА: Google Gemma для анализа + Mistral для генерации

import logging
import asyncio
import config
from typing import Optional
import time
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# ПРОВЕРКА И ЗАГРУЗКА ЗАВИСИМОСТЕЙ
# ============================================================================

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
    logger.error("Ошибка: Mistral не установлена! pip install mistralai")
except Exception as e:
    logger.error("Ошибка при загрузке Mistral: %s", e)

try:
    import google.generativeai as genai
    google_available = True
    genai.configure(api_key=config.GOOGLE_AI_KEY)
    gemma_model = genai.GenerativeModel("gemini-2.0-flash")
    logger.info("✅ Google Generative AI загружена успешно")
except ImportError:
    logger.error("Ошибка: Google Generative AI не установлена! pip install google-generativeai")
except Exception as e:
    logger.error("Ошибка при загрузке Google AI: %s", e)

# ============================================================================
# ОТСЛЕЖИВАНИЕ ТОКЕНОВ
# ============================================================================

token_usage = {
    "mistral_tokens": 0,
    "gemma_tokens": 0,
    "minute_tokens": 0,
    "day_tokens": 0,
    "minute_reset_time": time.time(),
    "day_reset_time": time.time(),
}

request_lock = asyncio.Lock()


def add_token_usage(mistral_tokens=0, gemma_tokens=0):
    """Добавляет использованные токены в счетчик."""
    total = mistral_tokens + gemma_tokens
    token_usage["mistral_tokens"] += mistral_tokens
    token_usage["gemma_tokens"] += gemma_tokens
    token_usage["minute_tokens"] += total
    token_usage["day_tokens"] += total


# ============================================================================
# АНАЛИЗ КОНТЕКСТА (Google Gemma)
# ============================================================================

async def analyze_context(context_messages, current_message, bot_nick):
    """Анализирует контекст с помощью Google Gemma."""

    if not google_available or gemma_model is None:
        logger.warning("Gemma недоступна, используем дефолтный анализ")
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
            "Я: %s" % msg["content"] if msg["is_bot"] else "%s: %s" % (msg["author"], msg["content"])
            for msg in context_messages[-12:]
        ]
    )

    analysis_prompt = """Проанализируй контекст разговора и текущее сообщение. Ответь ТОЛЬКО в формате JSON.

КОНТЕКСТ:
%s

НОВОЕ СООБЩЕНИЕ: %s

Верни JSON (ТОЛЬКО JSON):
{
  "theme": "основная тема разговора (2-3 слова)",
  "sentiment": "positive/neutral/negative",
  "tone": "friendly/serious/joking/flirty/sarcastic",
  "key_topic": "главная тема для ответа",
  "context_summary": "краткое резюме контекста (1 предложение)",
  "reply_direction": "как нужно ответить - направление ответа"
}
""" % (context_text, current_message)

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
            logger.warning("Gemma вернула невалидный JSON")
            return {
                "theme": "общий разговор",
                "sentiment": "neutral",
                "tone": "friendly",
                "key_topic": current_message[:30],
                "context_summary": "Текущее сообщение",
                "reply_direction": "просто ответить естественно",
            }

    except Exception as e:
        logger.error("Ошибка анализа контекста Gemma: %s", e)
        return None


# ============================================================================
# ГЕНЕРАЦИЯ ОТВЕТА (Mistral Large)
# ============================================================================

async def generate_response(
    system_prompt,
    context_messages,
    current_message,
    bot_nick,
    is_mentioned=False,
    chat_phrases=None,
    hot_topics=None,
    user_facts=None,
    mood_state=None,
    energy_level=80,
    relationship_level="stranger",
    channel_emotes=None,
):
    """Главная функция генерации ответа."""

    if not mistral_available or mistral_client is None:
        logger.error("Mistral недоступна, не могу генерировать ответ!")
        return None

    async with request_lock:
        logger.info("Анализируем контекст (Google Gemma)...")
        context_analysis = await asyncio.to_thread(
            analyze_context,
            list(context_messages[-config.CONTEXT_MESSAGE_LIMIT:]),
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

        if is_mentioned:
            max_length = config.MAX_RESPONSE_LENGTH_MENTIONED
            max_tokens = 500
        else:
            max_length = config.MAX_RESPONSE_LENGTH
            max_tokens = 250

        enhanced_prompt = system_prompt
        enhanced_prompt += """

[АНАЛИЗ КОНТЕКСТА]:
Тема: %s
Тон: %s
О чем: %s
""" % (
            context_analysis.get("theme", "неизвестна"),
            context_analysis.get("tone", "дружелюбный"),
            context_analysis.get("context_summary", "неясно"),
        )

        logger.info("Генерируем ответ (Mistral Large)...")

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

            answer = response.choices[0].message.content.strip()
            add_token_usage(mistral_tokens=max_tokens)

            if len(answer) > max_length:
                truncated = answer[:max_length]
                last_punct = max(
                    truncated.rfind("."),
                    truncated.rfind("!"),
                    truncated.rfind("?"),
                )
                if last_punct > max_length // 2:
                    answer = truncated[:last_punct + 1]
                else:
                    answer = truncated.rsplit(" ", 1)[0] + "."

            logger.info("Ответ сгенерирован")
            return answer

        except Exception as e:
            logger.error("Ошибка генерации Mistral: %s", e)
            return None

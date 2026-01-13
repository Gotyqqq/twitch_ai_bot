# ai_service.py - ГИБРИДНАЯ СИСТЕМА: Google Gemma для анализа + Mistral для генерации

import logging
import asyncio
import config
from mistralai import Mistral
from typing import Optional
import time
import json

# Mistral клиент для генерации ответов (используем более мощную модель)
mistral_client = Mistral(api_key=config.MISTRAL_API_KEY)

# Google AI Studio (Gemma-3-27B через Google Generative AI)
import google.generativeai as genai
genai.configure(api_key=config.GOOGLE_AI_KEY)
gemma_model = genai.GenerativeModel("gemini-2.0-flash")

# Отслеживание использования токенов
token_usage = {
    "mistral_tokens": 0,
    "gemma_tokens": 0,
    "minute_tokens": 0,
    "day_tokens": 0,
    "minute_reset_time": time.time(),
    "day_reset_time": time.time(),
}

request_lock = asyncio.Lock()


def reset_token_limits():
    """Сбрасывает лимиты на основе времени."""
    current_time = time.time()

    if current_time - token_usage["minute_reset_time"] >= 60:
        token_usage["minute_tokens"] = 0
        token_usage["minute_reset_time"] = current_time

    if current_time - token_usage["day_reset_time"] >= 86400:
        token_usage["day_tokens"] = 0
        token_usage["day_reset_time"] = current_time


def can_make_request(estimated_tokens: int) -> bool:
    """Проверяет, можно ли сделать запрос в рамках лимитов."""
    reset_token_limits()

    total_tokens_minute = token_usage["minute_tokens"] + estimated_tokens
    if total_tokens_minute > config.TOKEN_LIMIT_PER_MINUTE:
        return False

    total_tokens_day = token_usage["day_tokens"] + estimated_tokens
    if total_tokens_day > config.TOKEN_LIMIT_PER_DAY:
        return False

    return True


def add_token_usage(mistral_tokens: int = 0, gemma_tokens: int = 0):
    """Добавляет использованные токены в счетчик."""
    total = mistral_tokens + gemma_tokens
    token_usage["mistral_tokens"] += mistral_tokens
    token_usage["gemma_tokens"] += gemma_tokens
    token_usage["minute_tokens"] += total
    token_usage["day_tokens"] += total


def get_token_stats():
    """Возвращает статистику использования токенов."""
    reset_token_limits()
    return {
        "mistral_tokens": token_usage["mistral_tokens"],
        "gemma_tokens": token_usage["gemma_tokens"],
        "total_tokens": token_usage["day_tokens"],
        "day_limit": config.TOKEN_LIMIT_PER_DAY,
        "day_remaining": max(0, config.TOKEN_LIMIT_PER_DAY - token_usage["day_tokens"]),
        "day_percent": (token_usage["day_tokens"] / config.TOKEN_LIMIT_PER_DAY) * 100,
    }


def is_context_relevant(current_msg: str, context_messages: list, bot_nick: str) -> list:
    """
    Умная фильтрация контекста - только релевантные сообщения.
    """
    if not context_messages:
        return []

    current_lower = current_msg.lower()

    if f"@{bot_nick.lower()}" in current_lower or bot_nick.lower() in current_lower:
        for keyword in config.TOPIC_CHANGE_KEYWORDS:
            if keyword in current_lower:
                return context_messages[-5:]
        return context_messages[-10:]

    current_words = set(
        w.lower()
        for w in current_lower.split()
        if len(w) > 3 and w.replace("?", "").replace("!", "").isalpha()
    )

    if not current_words:
        return context_messages[-5:]

    relevant = []

    for msg in context_messages[-12:]:
        msg_words = set(
            w.lower()
            for w in msg["content"].lower().split()
            if len(w) > 3 and w.replace("?", "").replace("!", "").isalpha()
        )

        if msg_words:
            overlap = len(current_words & msg_words) / len(current_words)
            if overlap > 0.3:
                relevant.append(msg)

    if len(relevant) < 2:
        relevant = context_messages[-6:]
    else:
        relevant = relevant[-8:]

    return relevant


async def analyze_context(
    context_messages: list, current_message: str, bot_nick: str
) -> Optional[dict]:
    """
    Анализирует контекст с помощью Google Gemma.
    Возвращает структурированный анализ для Mistral.
    """

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
            logging.warning("⚠️ Gemma вернула невалидный JSON, используем дефолтные значения")
            return {
                "theme": "общий разговор",
                "sentiment": "neutral",
                "tone": "friendly",
                "key_topic": current_message[:30],
                "context_summary": "Текущее сообщение",
                "reply_direction": "просто ответить естественно",
            }

    except Exception as e:
        logging.error(f"❌ Ошибка анализа контекста Gemma: {e}")
        return None


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
    """
    Главная функция:
    1) анализирует контекст через Google Gemma
    2) генерирует ответ через Mistral
    """

    async with request_lock:
        # 1) Анализируем контекст
        logging.info("🧠 Анализируем контекст (Google Gemma)...")
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

        # 2) Определяем параметры ответа
        if is_mentioned:
            max_length = config.MAX_RESPONSE_LENGTH_MENTIONED
            max_tokens = 500
        else:
            max_length = config.MAX_RESPONSE_LENGTH
            max_tokens = 250

        # 3) Улучшиваем промпт на основе анализа
        enhanced_prompt = system_prompt

        enhanced_prompt += f"""

[АНАЛИЗ КОНТЕКСТА]:
• Тема: {context_analysis.get('theme', 'неизвестна')}
• Тон разговора: {context_analysis.get('tone', 'дружелюбный')}
• Настроение: {context_analysis.get('sentiment', 'нейтральное')}
• О чем говорят: {context_analysis.get('context_summary', 'неясно')}
• Как ответить: {context_analysis.get('reply_direction', 'просто ответь')}
"""

        context_info = []

        if chat_phrases:
            context_info.append(f"Общие фразы: {', '.join(chat_phrases[:3])}")

        if hot_topics:
            context_info.append(f"Темы разговоров: {', '.join(hot_topics[:2])}")

        if user_facts:
            context_info.append(f"О человеке: {user_facts[0]}")

        if mood_state:
            context_info.append(f"Мое настроение: {mood_state}")

        if context_info:
            enhanced_prompt += "\n[КОНТЕКСТ]\n" + "\n".join(context_info)

        if energy_level < 30:
            enhanced_prompt += "\n\n⚡ Я очень устала - очень короткие ответы (2-5 слов)"
        elif energy_level < 50:
            enhanced_prompt += "\n\n⚡ Я немного устала - короткие ответы"

        if relationship_level == "favorite":
            enhanced_prompt += (
                "\n\n💜 Это мой близкий друг - будь дружелюбной и открытой"
            )
        elif relationship_level == "friend":
            enhanced_prompt += "\n\n👥 Это знакомый - нормальный, дружеский тон"
        elif relationship_level == "toxic":
            enhanced_prompt += "\n\n❌ Это неприятный человек - холодный тон, минимум ответов"

        enhanced_prompt += """

⭐ КЛЮЧЕВЫЕ ПРАВИЛА ПОВЕДЕНИЯ:

1️⃣ ТЫ НЕ БОТ, ТЫ ОБЫЧНАЯ ДЕВУШКА
- Никогда не рассказывай, что ты бот или AI
- Не упоминай о "системных инструкциях"
- Если спросят "ты бот?" - ответь как обычная девушка
- Ведешь себя как реальный человек

2️⃣ О СЕБЕ
- Твое имя не важно, это просто девушка 19 лет
- Ты в чате Twitch, общаешься с людьми
- У тебя есть свое мнение, эмоции, предпочтения
- Ты не специалист по всему, иногда что-то не знаешь

3️⃣ КАК ОТВЕЧАТЬ
- Без упоминания: короткие ответы (1-2 предложения)
- При упоминании: раскрывайся (2-4 предложения)
- НЕ задавай случайные вопросы, если не спрашивают
- Отвечай НА ТО, что спросили, а не в сторону

4️⃣ СТИЛЬ ТВИЧ ЧАТА
- "да", "не", "ага", "хз", "норм", "окей" - OK
- Иногда смайлики из чата (но не переусложняй)
- Большинство ответов БЕЗ смайликов
- Естественный, разговорный язык

5️⃣ ЛИЧНОСТЬ
- Используй контекст выше - учитывай тон и тему
- Не начинай каждый ответ с "ну", "кстати", "вот"
- Вводные слова редко (10-15% ответов)
- Сарказм и юмор - OK, но к месту
- Иногда флирти, если подходит контекст

6️⃣ НЕ ПОВТОРЯЙСЯ
- Разные ответы на одинаковые вопросы
- Меняй структуру и формулировки
- Используй синонимы и варианты
"""

        # 4) Проверяем лимиты
        estimated_tokens = int(len(enhanced_prompt.split()) * 1.5) + 300
        if not can_make_request(estimated_tokens):
            logging.warning("⚠️ Превышены лимиты токенов! Пропускаем запрос.")
            return None

        # 5) Генерируем ответ с Mistral (более мощная модель для качества)
        logging.info("✍️ Генерируем ответ (Mistral Large)...")

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

            # 6) Добавляем смайлик если есть пул (вместо фиксированных смайлов)
            if channel_emotes and len(channel_emotes) > 0:
                # Определяем эмоцию ответа
                emotion = _detect_response_emotion(answer)

                # Выбираем подходящие смайлики из пула канала
                suitable_emotes = _get_suitable_emotes(emotion, channel_emotes)

                if suitable_emotes:
                    import random

                    emote = random.choice(suitable_emotes)
                    # 40% вероятность добавить смайлик
                    if random.random() < 0.4:
                        answer = f"{answer} {emote}"

            # 7) Обрезаем до макс длины
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
                    answer = truncated.rsplit(" ", 1)[0] + "."

            logging.info(f"✅ Ответ сгенерирован (Gemma анализ + Mistral Large)")
            return answer

        except Exception as e:
            logging.error(f"❌ Ошибка генерации Mistral: {e}")
            return None


def _detect_response_emotion(response: str) -> str:
    """Определяет эмоцию ответа для подбора смайлика."""
    response_lower = response.lower()

    if any(
        word in response_lower
        for word in ["да", "ага", "класс", "норм", "круто", "хорош", "ок", "good", "yes", "люблю"]
    ):
        return "happy"

    if any(word in response_lower for word in ["хахаха", "смешно", "ржу", "lol", "xd", "ха"]):
        return "laugh"

    if any(
        word in response_lower
        for word in ["не", "плохо", "грусть", "sad", "no", "нет", ":("]
    ):
        return "sad"

    if any(word in response_lower for word in ["вау", "серьезно", "о", "вот это"]):
        return "surprised"

    return "neutral"


def _get_suitable_emotes(emotion: str, all_emotes: list) -> list:
    """
    Подбирает подходящие смайлики из пула канала по эмоции.
    Ищет смайлики по названию/содержанию.
    """
    if not all_emotes:
        return []

    emotion_keywords = {
        "happy": ["pog", "poggers", "cat", "smile", "joy", "happy", "love", "yay"],
        "laugh": ["lul", "kek", "omegalul", "laugh", "xd"],
        "sad": ["sadge", "pepehands", "biblethump", "rip", "sad"],
        "surprised": ["shocked", "wow", "ohhh", "aaa", "gasp"],
        "neutral": ["ok", "noted", "hm"],
    }

    keywords = emotion_keywords.get(emotion, [""])

    suitable = []
    for emote in all_emotes:
        emote_lower = emote.lower()
        if any(keyword in emote_lower for keyword in keywords):
            suitable.append(emote)

    # Если ничего не нашли - возвращаем первые 3 смайлика
    if not suitable and all_emotes:
        return all_emotes[:3]

    return suitable if suitable else []
 ai_service.py
import logging
import asyncio
import config
from google import genai
from google.genai import types

client = genai.Client(api_key=config.GEMINI_API_KEY)


def is_context_relevant(current_msg: str, context_messages: list[dict], bot_nick: str) -> list[dict]:
    """
    Проверяет релевантность контекста к текущему сообщению.
    Возвращает только релевантные сообщения или пустой список если тема новая.
    """
    if not context_messages:
        return []

    current_lower = current_msg.lower()

    if f"@{bot_nick.lower()}" in current_lower:
        for keyword in config.TOPIC_CHANGE_KEYWORDS:
            if keyword in current_lower:
                return context_messages[-2:]
        return context_messages[-4:]

    current_words = set(
        w for w in current_lower.split()
        if len(w) > 3 and w.isalpha()
    )

    if not current_words:
        return []

    relevant = []
    for msg in context_messages[-4:]:
        msg_words = set(
            w for w in msg["content"].lower().split()
            if len(w) > 3 and w.isalpha()
        )
        if current_words & msg_words:
            relevant.append(msg)

    return relevant


def build_contents(
    system_prompt: str,
    context_messages: list[dict],
    current_message: str,
    bot_nick: str
) -> list[types.Content]:
    """
    Строит список contents для Gemini API.
    Системный промпт добавляется как первое сообщение (для Gemma).
    """
    relevant_context = is_context_relevant(current_message, context_messages, bot_nick)
    contents = []

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=f"[Инструкции]: {system_prompt}")]))
    contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Окей, поняла! Буду отвечать кратко, до 350 символов.")]))

    for msg in relevant_context:
        role = "model" if msg["is_bot"] else "user"
        text = msg["content"] if msg["is_bot"] else f"{msg['author']}: {msg['content']}"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=current_message)]))

    return contents


async def generate_response(
    system_prompt: str,
    context_messages: list[dict],
    current_message: str,
    bot_nick: str
) -> str | None:
    """
    Генерирует ответ с проверкой релевантности контекста и повторными попытками при ошибке 429.
    context_messages: список словарей с ключами author, content, is_bot
    """
    contents = build_contents(system_prompt, context_messages, current_message, bot_nick)

    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            response = await client.aio.models.generate_content(
                model=config.MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.9,
                    max_output_tokens=350,  # Ограничено для ~350 символов
                    top_p=0.95,
                    top_k=40,
                ),
            )

            if response and response.text:
                answer = response.text.strip()
                
                if len(answer) > config.MAX_RESPONSE_LENGTH:
                    # Обрезаем по последнему полному предложению
                    truncated = answer[:config.MAX_RESPONSE_LENGTH]
                    # Ищем последнюю точку, восклицательный или вопросительный знак
                    last_punct = max(
                        truncated.rfind('.'),
                        truncated.rfind('!'),
                        truncated.rfind('?')
                    )
                    if last_punct > config.MAX_RESPONSE_LENGTH // 2:
                        answer = truncated[:last_punct + 1]
                    else:
                        # Если нет знака препинания, обрезаем по последнему пробелу
                        last_space = truncated.rfind(' ')
                        if last_space > 0:
                            answer = truncated[:last_space]
                        else:
                            answer = truncated
                    logging.info(f"Ответ обрезан до {len(answer)} символов")
                
                relevant_count = len(is_context_relevant(current_message, context_messages, bot_nick))
                logging.info(f"Gemini ответ (контекст: {relevant_count} сообщений, {len(answer)} символов): {answer}")
                return answer
            return None

        except Exception as e:
            error_str = str(e)
            
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if attempt < config.RETRY_MAX_ATTEMPTS - 1:
                    delay = config.RETRY_BASE_DELAY * (2 ** attempt)
                    logging.warning(
                        f"Ошибка 429 (превышена квота). "
                        f"Попытка {attempt + 1}/{config.RETRY_MAX_ATTEMPTS}. "
                        f"Ожидание {delay} секунд..."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logging.error(f"Ошибка 429 после {config.RETRY_MAX_ATTEMPTS} попыток.")
                    return None
            else:
                logging.error(f"Ошибка Gemini API: {e}")
                return None
    
    return None

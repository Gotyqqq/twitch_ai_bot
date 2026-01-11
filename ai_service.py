# ai_service.py
import logging
import time
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
    Системный промпт добавляется как первое сообщение пользователя.
    """
    relevant_context = is_context_relevant(current_message, context_messages, bot_nick)
    contents = []

    # <CHANGE> Добавляем системный промпт как первое сообщение (для моделей без system_instruction)
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=f"[Инструкции]: {system_prompt}")]))
    contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Понял, буду следовать этим инструкциям.")]))

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
    Генерирует ответ с проверкой релевантности контекста.
    context_messages: список словарей с ключами author, content, is_bot
    """
    # <CHANGE> Передаем system_prompt в build_contents
    contents = build_contents(system_prompt, context_messages, current_message, bot_nick)

    # <CHANGE> Добавляем retry логику для ошибки 429
    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            response = await client.aio.models.generate_content(
                model=config.MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    # <CHANGE> Убрали system_instruction - не поддерживается Gemma
                    temperature=0.95,
                    max_output_tokens=600,
                    top_p=0.95,
                    top_k=40,
                ),
            )

            if response and response.text:
                answer = response.text.strip()
                
                # <CHANGE> Проверка минимальной длины ответа
                if len(answer) < config.MIN_RESPONSE_LENGTH:
                    logging.info(f"Ответ слишком короткий ({len(answer)} символов), запрашиваем расширение...")
                    contents.append(types.Content(role="model", parts=[types.Part.from_text(text=answer)]))
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text="Расскажи подробнее, ответ должен быть развёрнутым (минимум 400 символов).")]))
                    
                    response = await client.aio.models.generate_content(
                        model=config.MODEL_NAME,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            temperature=0.95,
                            max_output_tokens=600,
                            top_p=0.95,
                            top_k=40,
                        ),
                    )
                    if response and response.text:
                        answer = response.text.strip()
                
                relevant_count = len(is_context_relevant(current_message, context_messages, bot_nick))
                logging.info(f"Ответ ({len(answer)} символов, контекст: {relevant_count}): {answer}")
                return answer
            return None

        except Exception as e:
            error_str = str(e)
            # <CHANGE> Обработка ошибки 429 (превышена квота)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                delay = config.RETRY_BASE_DELAY * (2 ** attempt)
                logging.warning(f"Ошибка 429, попытка {attempt + 1}/{config.RETRY_MAX_ATTEMPTS}. Ждём {delay} сек...")
                time.sleep(delay)
                continue
            logging.error(f"Ошибка Gemini API: {e}")
            return None
    
    logging.error("Все попытки исчерпаны")
    return None
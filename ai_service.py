# ai_service.py
import logging
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
    context_messages: list[dict],
    current_message: str,
    bot_nick: str
) -> list[types.Content]:
    """
    Строит список contents для Gemini API.
    """
    relevant_context = is_context_relevant(current_message, context_messages, bot_nick)
    contents = []

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
    contents = build_contents(context_messages, current_message, bot_nick)

    try:
        response = await client.aio.models.generate_content(
            model=config.MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.9,
                max_output_tokens=300,
                top_p=0.95,
                top_k=40,
            ),
        )

        if response and response.text:
            answer = response.text.strip()
            relevant_count = len(is_context_relevant(current_message, context_messages, bot_nick))
            logging.info(f"Gemini ответ (контекст: {relevant_count} сообщений): {answer}")
            return answer
        return None

    except Exception as e:
        logging.error(f"Ошибка Gemini API: {e}")
        return None
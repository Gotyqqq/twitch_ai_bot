# ai_service.py
import logging
import asyncio
import config
from google import genai
from google.genai import types

client = genai.Client(api_key=config.GEMINI_API_KEY)

request_queue = asyncio.Queue()
request_lock = asyncio.Lock()


def is_context_relevant(current_msg: str, context_messages: list[dict], bot_nick: str) -> list[dict]:
    """
    Проверяет релевантность контекста к текущему сообщению.
    Возвращает только релевантные сообщения или пустой список если тема новая.
    """
    if not context_messages:
        return []

    current_lower = current_msg.lower()

    # При упоминании бота даем больше контекста
    if f"@{bot_nick.lower()}" in current_lower:
        for keyword in config.TOPIC_CHANGE_KEYWORDS:
            if keyword in current_lower:
                return context_messages[-3:]
        return context_messages[-6:]

    current_words = set(
        w for w in current_lower.split()
        if len(w) > 3 and w.isalpha()
    )

    if not current_words:
        return []

    relevant = []
    for msg in context_messages[-6:]:
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
    bot_nick: str,
    is_mentioned: bool = False
) -> list[types.Content]:
    """
    Строит список contents для Gemini API.
    """
    relevant_context = is_context_relevant(current_message, context_messages, bot_nick)
    contents = []

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=f"[Инструкции]: {system_prompt}")]))
    
    if is_mentioned:
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Понятно! Отвечу подробнее, раз меня спросили.")]))
    else:
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Ок, буду краткой!")]))

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
    bot_nick: str,
    is_mentioned: bool = False,
    chat_phrases: list[str] = None,
    hot_topics: list[str] = None,
    user_facts: list[str] = None,
    mood_state: str = None,
    energy_level: int = 80,
    relationship_level: str = 'stranger'
) -> str | None:
    """
    Генерирует ответ через очередь запросов.
    """
    async with request_lock:
        # Выбираем лимит символов в зависимости от упоминания
        max_length = config.MAX_RESPONSE_LENGTH_MENTIONED if is_mentioned else config.MAX_RESPONSE_LENGTH
        
        enhanced_prompt = system_prompt
        
        if chat_phrases and len(chat_phrases) > 0:
            phrases_sample = chat_phrases[:5]
            enhanced_prompt += f"\n\nПопулярные фразы в этом чате (можешь использовать похожий стиль): {', '.join(phrases_sample)}"
        
        if hot_topics and len(hot_topics) > 0:
            enhanced_prompt += f"\n\nГорячие темы сейчас: {', '.join(hot_topics)}"
        
        if user_facts and len(user_facts) > 0:
            enhanced_prompt += f"\n\nЧто ты знаешь об этом человеке: {'; '.join(user_facts[:2])}"
        
        if mood_state:
            enhanced_prompt += f"\n\nТвое настроение: {mood_state}"
        
        if energy_level < 30:
            enhanced_prompt += "\n\nТы очень устала, отвечай кратко (5-10 слов)."
        elif energy_level < 50:
            enhanced_prompt += "\n\nТы немного устала, отвечай не слишком длинно."
        
        if relationship_level == 'favorite':
            enhanced_prompt += "\n\nЭто твой хороший друг, будь особенно дружелюбной и открытой."
        elif relationship_level == 'friend':
            enhanced_prompt += "\n\nВы уже знакомы, можно быть более открытой."
        elif relationship_level == 'toxic':
            enhanced_prompt += "\n\nЭтот человек был токсичен, отвечай холодно и кратко."
        
        enhanced_prompt += """

ВАЖНЫЕ ПРАВИЛА:
1. ОТВЕЧАЙ НА ВОПРОС - не уходи от темы, не задавай случайные вопросы
2. БУДЬ ПРЯМОЙ - если тебя спросили, просто ответь, не рассказывай о себе без причины
3. РАЗНООБРАЗИЕ - не используй одни и те же слова/фразы постоянно
4. ВВОДНЫЕ СЛОВА - используй "ну", "кстати", "вот" РЕДКО (только 10-20% ответов)
5. СМАЙЛИКИ - не используй один и тот же смайлик постоянно, меняй их
6. БЕЗ ЛИШНЕГО - не добавляй "кстати я голодная" или другие случайные комментарии

При упоминании (@bot):
- Отвечай ПОДРОБНЕЕ и РАЗВЕРНУТЕЕ (2-4 предложения)
- Можешь добавить контекст или объяснение
- Будь более вовлеченной в диалог
- Можешь задать уточняющий вопрос если что-то неясно

Примеры хороших ответов:

Сообщение: "нейронки быстро работают"
✅ "ага, очень быстро стали"
✅ "прогресс реально заметен"  
✅ "быстрее чем раньше точно"
❌ "ну да, кстати как работа?" (случайный вопрос)
❌ "ну вот это прогресс" (лишние вводные)

Сообщение: "@bot, что думаешь про это?"
✅ "думаю это интересная тема, можно много говорить. а ты сам как относишься?" (развернуто при упоминании)
✅ "честно не уверена, надо разобраться получше. что конкретно интересует?" (подробнее + уточнение)
❌ "хз" (слишком кратко при упоминании)
❌ "ну вот не знаю, кстати ты в игры играешь?" (лишние слова + не в тему)

Сообщение: "привет"
✅ "привет"
✅ "прив"
✅ "здарова"
❌ "ну привет, кстати как дела?" (слишком много для простого привета)

Ты НЕ должна:
- Начинать большинство сообщений с "ну", "кстати", "вот"  
- Задавать вопросы не по теме разговора
- Рассказывать о себе без причины
- Использовать один смайлик во всех сообщениях
- Повторять одни и те же фразы/структуры

Ты ДОЛЖНА:
- Отвечать на заданный вопрос или реагировать на сказанное
- Быть естественной и разнообразной
- При упоминании отвечать более развернуто (2-4 предложения)
- Менять стиль и слова в зависимости от ситуации
- Использовать разные смайлики или вообще без них"""

        contents = build_contents(enhanced_prompt, context_messages, current_message, bot_nick, is_mentioned)
        
        max_tokens = 700 if is_mentioned else 250

        for attempt in range(config.RETRY_MAX_ATTEMPTS):
            try:
                response = await client.aio.models.generate_content(
                    model=config.MODEL_NAME,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.9,  # Увеличили для разнообразия
                        max_output_tokens=max_tokens,
                        top_p=0.95,
                        top_k=50,  # Увеличили для большего разнообразия
                    ),
                )

                if response and response.text:
                    answer = response.text.strip()
                    
                    if len(answer) > max_length:
                        truncated = answer[:max_length]
                        last_punct = max(
                            truncated.rfind('.'),
                            truncated.rfind('!'),
                            truncated.rfind('?')
                        )
                        if last_punct > max_length // 2:
                            answer = truncated[:last_punct + 1]
                        else:
                            last_space = truncated.rfind(' ')
                            if last_space > 0:
                                answer = truncated[:last_space]
                            else:
                                answer = truncated
                        logging.info(f"Ответ обрезан до {len(answer)} символов")
                    
                    relevant_count = len(is_context_relevant(current_message, context_messages, bot_nick))
                    logging.info(f"Gemini ответ (упоминание: {is_mentioned}, контекст: {relevant_count}, длина: {len(answer)}): {answer}")
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

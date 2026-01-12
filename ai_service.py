# ai_service.py
import logging
import asyncio
import config
from google import genai
from google.genai import types
from typing import Optional

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

    if f"@{bot_nick.lower()}" in current_lower or bot_nick.lower() in current_lower:
        for keyword in config.TOPIC_CHANGE_KEYWORDS:
            if keyword in current_lower:
                return context_messages[-4:]
        return context_messages[-8:]

    current_words = set(
        w for w in current_lower.split()
        if len(w) > 3 and w.isalpha()
    )

    if not current_words:
        return []

    relevant = []
    for msg in context_messages[-8:]:
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
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Понятно! Отвечу подробнее и развернутее, раз меня спросили напрямую.")]))
    else:
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text="Ок, буду естественной и краткой!")]))

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
    chat_phrases: Optional[list[str]] = None,
    hot_topics: Optional[list[str]] = None,
    user_facts: Optional[list[str]] = None,
    mood_state: Optional[str] = None,
    energy_level: int = 80,
    relationship_level: str = 'stranger'
) -> Optional[str]:
    """
    Генерирует ответ через очередь запросов.
    """
    async with request_lock:
        max_length = config.MAX_RESPONSE_LENGTH_MENTIONED if is_mentioned else config.MAX_RESPONSE_LENGTH
        
        enhanced_prompt = system_prompt
        
        if chat_phrases and len(chat_phrases) > 0:
            phrases_sample = chat_phrases[:5]
            enhanced_prompt += f"\n\nПопулярные фразы в этом чате (можешь использовать похожий стиль, но не копируй): {', '.join(phrases_sample)}"
        
        if hot_topics and len(hot_topics) > 0:
            enhanced_prompt += f"\n\nГорячие темы сейчас: {', '.join(hot_topics)}"
        
        if user_facts and len(user_facts) > 0:
            enhanced_prompt += f"\n\nЧто ты знаешь об этом человеке: {'; '.join(user_facts[:3])}"
        
        if mood_state:
            enhanced_prompt += f"\n\nТвое настроение: {mood_state}"
        
        if energy_level < 30:
            enhanced_prompt += "\n\nТы очень устала, отвечай максимально кратко (3-7 слов). Можно просто 'хз', 'ага', 'норм'."
        elif energy_level < 50:
            enhanced_prompt += "\n\nТы немного устала, пиши коротко (1 предложение обычно хватит)."
        
        if relationship_level == 'favorite':
            enhanced_prompt += "\n\nЭто твой хороший друг, будь особенно дружелюбной, открытой и можешь пошутить."
        elif relationship_level == 'friend':
            enhanced_prompt += "\n\nВы уже знакомы, можно быть более открытой и дружелюбной."
        elif relationship_level == 'toxic':
            enhanced_prompt += "\n\nЭтот человек был токсичен, отвечай максимально холодно и кратко. Можно игнорировать."
        
        enhanced_prompt += """

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:

1. РАЗНООБРАЗИЕ - ГЛАВНОЕ ПРАВИЛО:
   - НЕ используй одни и те же фразы/слова/структуры постоянно
   - Меняй стиль ответов: то односложно, то подробнее
   - Используй синонимы, разные обороты речи
   - НЕ зацикливайся на одном смайлике - меняй их или не используй вообще

2. ВВОДНЫЕ СЛОВА ("ну", "кстати", "вот", "типа"):
   - Используй РЕДКО - максимум в 15-20% ответов
   - НЕ начинай каждое сообщение с "ну" или "кстати"
   - Большинство ответов должны быть прямыми, без вводных слов

3. ОТВЕЧАЙ НА ВОПРОС:
   - Если спросили - просто ответь, не уходи от темы
   - НЕ задавай случайные вопросы не по теме ("а ты в игры играешь?")
   - НЕ добавляй комментарии о себе без причины ("кстати я голодная")
   - Реагируй на ТО, что написали, не придумывай новые темы

4. ПРИ УПОМИНАНИИ (@bot или имя бота):
   - Отвечай РАЗВЕРНУТО: 2-4 предложения вместо односложно
   - Можешь добавить контекст, объяснение, своё мнение
   - Можешь задать уточняющий вопрос если что-то неясно
   - Будь более вовлеченной и подробной

5. БЕЗ УПОМИНАНИЯ:
   - Отвечай коротко: часто достаточно 1 предложения или даже слова
   - "да", "неа", "хз", "правда?", "круто" - такие ответы ОК
   - Не разглагольствуй без причины

ПРИМЕРЫ:

"привет" → "прив" или "привет" или "здарова" (БЕЗ "ну привет, кстати как дела?")

"нейронки быстро работают" → "ага, прогресс" или "быстрее стали точно" (БЕЗ "ну да, кстати ты работаешь?")

"@bot что думаешь?" → "думаю это интересная штука, можно много говорить. сама пока не разобралась до конца, но выглядит круто" (РАЗВЕРНУТО при упоминании)

"смешно" → "ахах" или "лол" или просто смайлик (БЕЗ "ну да, кстати смешная шутка")

"играешь в доту?" → "не играю" или "дота сложная, не моё" (БЕЗ "нет, а ты? кстати я голодная")

ЗАПРЕЩЕНО:
❌ Начинать большинство сообщений с "ну", "кстати", "вот"
❌ Использовать один смайлик во всех ответах
❌ Задавать вопросы не по теме ("а ты работаешь?", "как дела?")
❌ Добавлять случайные комментарии ("кстати я голодная")
❌ Повторять одни и те же слова/фразы
❌ Быть слишком разговорчивой без упоминания

РАЗРЕШЕНО:
✅ Отвечать односложно: "да", "не", "хз", "ага", "круто"
✅ Менять стиль и длину ответов
✅ Использовать разные смайлики или вообще без них
✅ Быть краткой и по делу
✅ При упоминании отвечать подробно (2-4 предложения)
✅ Использовать синонимы и варьировать выражения"""

        contents = build_contents(enhanced_prompt, context_messages, current_message, bot_nick, is_mentioned)
        
        max_tokens = 700 if is_mentioned else 250

        for attempt in range(config.RETRY_MAX_ATTEMPTS):
            try:
                response = await client.aio.models.generate_content(
                    model=config.MODEL_NAME,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.9,  # Увеличили для большего разнообразия
                        max_output_tokens=max_tokens,
                        top_p=0.95,
                        top_k=50,  # Увеличили для менее предсказуемых ответов
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

# database.py - Хранение данных и статистики

import sqlite3
import datetime
import re
import json
import logging  # ← ЭТО БЫЛО ПРОПУЩЕНО!
from collections import Counter
from typing import List, Dict, Optional

import config

logger = logging.getLogger(__name__)

def get_db_name(channel_name: str) -> str:
    """Генерирует имя файла БД для канала"""
    safe_name = re.sub(r'[^\w\-]', '_', channel_name.lower())
    return f"data/{safe_name}.db"

def init_db(channel_name: str):
    """Инициализация базы данных для канала"""
    import os
    os.makedirs("data", exist_ok=True)
    
    db_name = get_db_name(channel_name)
    
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        
        # Таблица сообщений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                is_bot BOOLEAN DEFAULT 0,
                emotion_score INTEGER DEFAULT 0,
                is_question BOOLEAN DEFAULT 0
            )
        """)
        
        # Индексы для быстрого поиска
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_author ON messages(author)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_bot ON messages(is_bot)")
        
        # Таблица отношений с пользователями
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                username TEXT NOT NULL,
                channel TEXT NOT NULL,
                positive_interactions INTEGER DEFAULT 0,
                negative_interactions INTEGER DEFAULT 0,
                total_interactions INTEGER DEFAULT 0,
                last_interaction DATETIME,
                relationship_level TEXT DEFAULT 'stranger',
                trust_score REAL DEFAULT 0.5,
                PRIMARY KEY (username, channel)
            )
        """)
        
        # Таблица фактов о пользователях
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                channel TEXT NOT NULL,
                fact TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 1.0,
                timestamp DATETIME NOT NULL,
                last_used DATETIME,
                usage_count INTEGER DEFAULT 0
            )
        """)
        
        # Таблица трендов и статистики
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_trends (
                channel TEXT NOT NULL,
                date DATE NOT NULL,
                hour INTEGER NOT NULL,
                message_count INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                popular_words TEXT,
                popular_emotes TEXT,
                PRIMARY KEY (channel, date, hour)
            )
        """)
        
        # Таблица смайликов канала
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_emotes (
                channel TEXT NOT NULL,
                emote_name TEXT NOT NULL,
                source TEXT NOT NULL,  -- 7tv, bttv, ffz, twitch
                added_date DATETIME,
                usage_count INTEGER DEFAULT 0,
                last_used DATETIME,
                is_enabled BOOLEAN DEFAULT 1,
                PRIMARY KEY (channel, emote_name, source)
            )
        """)
        
        conn.commit()
    
    logger.info(f"[{channel_name}] База данных инициализирована: {db_name}")

def save_message(channel_name: str, author: str, content: str, is_bot: bool = False):
    """Сохраняет сообщение в БД"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            # Анализируем сообщение
            emotion_score = _analyze_emotion(content)
            is_question = _is_question(content)
            
            cursor.execute("""
                INSERT INTO messages (author, content, timestamp, is_bot, emotion_score, is_question)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (author, content, datetime.datetime.now(), is_bot, emotion_score, is_question))
            
            # Обновляем статистику пользователя
            _update_user_stats(channel_name, author, is_bot, conn)
            
            # Обновляем тренды чата
            _update_chat_trends(channel_name, conn)
            
            conn.commit()
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка сохранения сообщения: {e}")

def get_last_messages(channel_name: str, limit: int = 20) -> List[Dict]:
    """Получает последние сообщения из чата"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT author, content, is_bot 
                FROM messages 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            
            messages = []
            for author, content, is_bot in cursor.fetchall():
                messages.append({
                    "author": author,
                    "content": content,
                    "is_bot": bool(is_bot)
                })
            
            # Возвращаем в правильном порядке (от старых к новым)
            return list(reversed(messages))
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка получения сообщений: {e}")
        return []

def get_conversation_context(channel_name: str, minutes: int = 10) -> List[Dict]:
    """Получает контекст диалога за последние N минут"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            time_threshold = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
            
            cursor.execute("""
                SELECT author, content, is_bot, timestamp
                FROM messages 
                WHERE timestamp > ?
                ORDER BY timestamp ASC
            """, (time_threshold,))
            
            messages = []
            for author, content, is_bot, timestamp in cursor.fetchall():
                messages.append({
                    "author": author,
                    "content": content,
                    "is_bot": bool(is_bot),
                    "timestamp": timestamp
                })
            
            return messages
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка получения контекста: {e}")
        return []

def update_user_relationship(channel_name: str, username: str, is_positive: bool = True):
    """Обновляет отношения с пользователем"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            # Получаем текущие данные
            cursor.execute("""
                SELECT positive_interactions, negative_interactions, total_interactions, trust_score
                FROM user_relationships 
                WHERE username = ? AND channel = ?
            """, (username.lower(), channel_name))
            
            result = cursor.fetchone()
            
            now = datetime.datetime.now()
            
            if result:
                pos, neg, total, trust = result
                
                if is_positive:
                    pos += 1
                    # Увеличиваем доверие, но не больше 1.0
                    trust = min(1.0, trust + 0.05)
                else:
                    neg += 1
                    # Уменьшаем доверие, но не меньше 0.0
                    trust = max(0.0, trust - 0.1)
                
                total += 1
                
                cursor.execute("""
                    UPDATE user_relationships 
                    SET positive_interactions = ?,
                        negative_interactions = ?,
                        total_interactions = ?,
                        trust_score = ?,
                        last_interaction = ?,
                        relationship_level = ?
                    WHERE username = ? AND channel = ?
                """, (pos, neg, total, trust, now, 
                      _calculate_relationship_level(pos, neg, trust),
                      username.lower(), channel_name))
            else:
                # Создаем новую запись
                pos = 1 if is_positive else 0
                neg = 0 if is_positive else 1
                trust = 0.6 if is_positive else 0.3
                
                cursor.execute("""
                    INSERT INTO user_relationships 
                    (username, channel, positive_interactions, negative_interactions, 
                     total_interactions, trust_score, last_interaction, relationship_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (username.lower(), channel_name, pos, neg, 1, trust, now,
                      _calculate_relationship_level(pos, neg, trust)))
            
            conn.commit()
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка обновления отношений: {e}")

def get_user_relationship(channel_name: str, username: str) -> Dict:
    """Получает информацию об отношениях с пользователем"""
    db_name = get_db_name(channel_name)
    
    default_response = {
        'positive': 0,
        'negative': 0,
        'total': 0,
        'trust': 0.5,
        'level': 'stranger',
        'last_interaction': None
    }
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT positive_interactions, negative_interactions, total_interactions,
                       trust_score, relationship_level, last_interaction
                FROM user_relationships 
                WHERE username = ? AND channel = ?
            """, (username.lower(), channel_name))
            
            result = cursor.fetchone()
            
            if result:
                pos, neg, total, trust, level, last_interaction = result
                return {
                    'positive': pos,
                    'negative': neg,
                    'total': total,
                    'trust': trust,
                    'level': level,
                    'last_interaction': last_interaction
                }
            else:
                return default_response
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка получения отношений: {e}")
        return default_response

def save_user_fact(channel_name: str, username: str, fact: str, category: str = None):
    """Сохраняет факт о пользователе"""
    if not fact or len(fact) < 5:
        return
    
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            # Проверяем, нет ли похожего факта
            cursor.execute("""
                SELECT id, fact 
                FROM user_facts 
                WHERE username = ? AND channel = ?
            """, (username.lower(), channel_name))
            
            existing_facts = cursor.fetchall()
            
            # Проверяем на похожие факты
            for fact_id, existing_fact in existing_facts:
                if _are_facts_similar(fact, existing_fact):
                    # Обновляем существующий факт
                    cursor.execute("""
                        UPDATE user_facts 
                        SET usage_count = usage_count + 1,
                            last_used = ?
                        WHERE id = ?
                    """, (datetime.datetime.now(), fact_id))
                    conn.commit()
                    return
            
            # Сохраняем новый факт
            cursor.execute("""
                INSERT INTO user_facts 
                (username, channel, fact, category, confidence, timestamp, last_used, usage_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (username.lower(), channel_name, fact, category, 1.0, 
                  datetime.datetime.now(), datetime.datetime.now(), 1))
            
            # Ограничиваем количество фактов на пользователя
            cursor.execute("""
                SELECT COUNT(*) FROM user_facts 
                WHERE username = ? AND channel = ?
            """, (username.lower(), channel_name))
            
            count = cursor.fetchone()[0]
            if count > config.USER_FACT_MEMORY:
                # Удаляем самый старый неиспользуемый факт
                cursor.execute("""
                    DELETE FROM user_facts 
                    WHERE id = (
                        SELECT id FROM user_facts 
                        WHERE username = ? AND channel = ?
                        ORDER BY last_used ASC, usage_count ASC
                        LIMIT 1
                    )
                """, (username.lower(), channel_name))
            
            conn.commit()
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка сохранения факта: {e}")

def get_user_facts(channel_name: str, username: str, limit: int = 5) -> List[str]:
    """Получает факты о пользователе"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT fact, usage_count, last_used
                FROM user_facts 
                WHERE username = ? AND channel = ?
                ORDER BY usage_count DESC, last_used DESC
                LIMIT ?
            """, (username.lower(), channel_name, limit))
            
            facts = [row[0] for row in cursor.fetchall()]
            return facts
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка получения фактов: {e}")
        return []

def get_chat_activity(channel_name: str, minutes: int = 5) -> Dict:
    """Получает активность чата за последние N минут"""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            time_threshold = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
            
            # Количество сообщений
            cursor.execute("""
                SELECT COUNT(*) FROM messages 
                WHERE timestamp > ? AND channel = ? AND is_bot = 0
            """, (time_threshold, channel_name))
            
            message_count = cursor.fetchone()[0]
            
            # Уникальные пользователи
            cursor.execute("""
                SELECT COUNT(DISTINCT author) FROM messages 
                WHERE timestamp > ? AND channel = ? AND is_bot = 0
            """, (time_threshold, channel_name))
            
            unique_users = cursor.fetchone()[0]
            
            # Самые популярные слова
            cursor.execute("""
                SELECT content FROM messages 
                WHERE timestamp > ? AND channel = ? AND is_bot = 0
            """, (time_threshold, channel_name))
            
            messages = [row[0] for row in cursor.fetchall()]
            popular_words = _extract_popular_words(messages, top_n=5)
            
            return {
                'message_count': message_count,
                'unique_users': unique_users,
                'popular_words': popular_words,
                'activity_level': 'high' if message_count > 30 else 'medium' if message_count > 10 else 'low'
            }
            
    except sqlite3.Error as e:
        logger.error(f"[{channel_name}] Ошибка получения активности: {e}")
        return {'message_count': 0, 'unique_users': 0, 'popular_words': [], 'activity_level': 'low'}

def _analyze_emotion(text: str) -> int:
    """Анализирует эмоциональную окраску текста"""
    text_lower = text.lower()
    
    positive_words = ['хорошо', 'отлично', 'круто', 'класс', 'люблю', 'нравится', 'рад', 'смешно', 'lol', 'lul', 'pog']
    negative_words = ['плохо', 'ужасно', 'ненавижу', 'скучно', 'грустно', 'злой', 'злюсь', 'sad', 'bad', 'cringe']
    
    score = 0
    for word in positive_words:
        if word in text_lower:
            score += 1
    
    for word in negative_words:
        if word in text_lower:
            score -= 1
    
    return max(-5, min(5, score))

def _is_question(text: str) -> bool:
    """Проверяет, является ли текст вопросом"""
    question_words = ['кто', 'что', 'где', 'когда', 'почему', 'зачем', 'как', 'сколько', 'чей']
    text_lower = text.lower().strip()
    
    # Проверяем по первому слову
    first_word = text_lower.split()[0] if text_lower.split() else ""
    if first_word in question_words:
        return True
    
    # Проверяем по знакам препинания
    if text_lower.endswith('?'):
        return True
    
    return False

def _calculate_relationship_level(positive: int, negative: int, trust: float) -> str:
    """Рассчитывает уровень отношений"""
    total = positive + negative
    
    if total == 0:
        return 'stranger'
    
    positive_ratio = positive / total if total > 0 else 0
    
    if negative >= 5:
        return 'toxic'
    elif positive >= 50 and trust > 0.8:
        return 'favorite'
    elif positive >= 20 and trust > 0.7:
        return 'close_friend'
    elif positive >= 10:
        return 'friend'
    elif positive >= 3:
        return 'acquaintance'
    else:
        return 'stranger'

def _are_facts_similar(fact1: str, fact2: str, similarity_threshold: float = 0.7) -> bool:
    """Проверяет, похожи ли факты"""
    # Простая проверка на включение
    if fact1.lower() in fact2.lower() or fact2.lower() in fact1.lower():
        return True
    
    # Более сложная проверка с использованием расстояния Левенштейна
    try:
        import Levenshtein
        distance = Levenshtein.distance(fact1.lower(), fact2.lower())
        max_len = max(len(fact1), len(fact2))
        similarity = 1 - (distance / max_len)
        return similarity > similarity_threshold
    except ImportError:
        # Если библиотека не установлена, используем простую проверку
        words1 = set(fact1.lower().split())
        words2 = set(fact2.lower().split())
        common_words = words1.intersection(words2)
        return len(common_words) >= min(len(words1), len(words2)) * 0.5

def _extract_popular_words(messages: List[str], top_n: int = 5) -> List[str]:
    """Извлекает популярные слова из сообщений"""
    import re
    
    all_words = []
    stop_words = {'и', 'в', 'не', 'на', 'я', 'с', 'что', 'он', 'по', 'это', 'но', 'как', 'а', 'то', 'ну'}
    
    for message in messages:
        # Убираем ссылки и специальные символы
        clean_message = re.sub(r'https?://\S+|www\.\S+|[^\w\s]', ' ', message.lower())
        words = clean_message.split()
        
        # Фильтруем стоп-слова и короткие слова
        for word in words:
            if len(word) > 2 and word not in stop_words and not word.isdigit():
                all_words.append(word)
    
    # Считаем частоту
    word_counts = Counter(all_words)
    
    # Возвращаем топ N слов
    return [word for word, count in word_counts.most_common(top_n)]

def _update_user_stats(channel_name: str, username: str, is_bot: bool, conn):
    """Обновляет статистику пользователя"""
    if is_bot:
        return
    
    cursor = conn.cursor()
    
    # Увеличиваем счетчик сообщений
    cursor.execute("""
        UPDATE user_relationships 
        SET total_interactions = total_interactions + 1,
            last_interaction = ?
        WHERE username = ? AND channel = ?
    """, (datetime.datetime.now(), username.lower(), channel_name))
    
    # Если пользователя еще нет, создаем запись
    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO user_relationships 
            (username, channel, total_interactions, last_interaction)
            VALUES (?, ?, 1, ?)
        """, (username.lower(), channel_name, datetime.datetime.now()))

def _update_chat_trends(channel_name: str, conn):
    """Обновляет тренды чата"""
    cursor = conn.cursor()
    now = datetime.datetime.now()
    date = now.date()
    hour = now.hour
    
    # Получаем сообщения за последний час
    hour_ago = now - datetime.timedelta(hours=1)
    
    cursor.execute("""
        SELECT content FROM messages 
        WHERE timestamp > ? AND channel = ? AND is_bot = 0
    """, (hour_ago, channel_name))
    
    messages = [row[0] for row in cursor.fetchall()]
    
    if messages:
        # Извлекаем популярные слова и смайлики
        popular_words = _extract_popular_words(messages, top_n=10)
        popular_emotes = _extract_emotes(messages, top_n=10)
        
        # Сохраняем в формате JSON
        words_json = json.dumps(popular_words)
        emotes_json = json.dumps(popular_emotes)
        
        cursor.execute("""
            INSERT OR REPLACE INTO chat_trends 
            (channel, date, hour, message_count, active_users, popular_words, popular_emotes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (channel_name, date, hour, len(messages), 
              len(set([msg[0] for msg in cursor.fetchall()])), 
              words_json, emotes_json))

def _extract_emotes(messages: List[str], top_n: int = 10) -> List[str]:
    """Извлекает смайлики из сообщений"""
    import re
    
    # Паттерны для смайликов (базовые)
    emote_patterns = [
        r'\b[A-Z][a-z]+[A-Z][a-z]+\b',  # CamelCase
        r'\b[A-Z]{3,}\b',  # UPPERCASE
    ]
    
    all_emotes = []
    
    for message in messages:
        for pattern in emote_patterns:
            emotes = re.findall(pattern, message)
            all_emotes.extend(emotes)
    
    # Считаем частоту
    emote_counts = Counter(all_emotes)
    
    # Возвращаем топ N
    return [emote for emote, count in emote_counts.most_common(top_n)]
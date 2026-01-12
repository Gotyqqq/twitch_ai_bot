# database.py
import sqlite3
import datetime
import re
from collections import Counter
import config

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]+",
    flags=re.UNICODE,
)
URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')


def get_db_name(channel_name: str) -> str:
    return f"{channel_name}.db"


def init_db(channel_name: str):
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                is_bot INTEGER DEFAULT 0
            )
        """)
        
        cursor.execute("PRAGMA table_info(messages)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'is_bot' not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_bot INTEGER DEFAULT 0")
            print(f"[{channel_name}] Добавлена колонка is_bot в таблицу messages")
        
        # Инициализация таблицы для хранения фактов о пользователях
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                fact TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                UNIQUE(username, fact)
            )
        """)
        conn.commit()


def save_message(channel_name: str, author: str, content: str, is_bot: bool = False):
    cleaned_content = URL_PATTERN.sub('[ссылка]', content)
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (author, content, timestamp, is_bot) VALUES (?, ?, ?, ?)",
            (author, cleaned_content, datetime.datetime.now(), 1 if is_bot else 0)
        )
        conn.commit()


def get_last_messages(channel_name: str, limit: int) -> list[dict]:
    """Возвращает список словарей с author, content и is_bot."""
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT author, content, is_bot FROM messages ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        messages = cursor.fetchall()
    return [
        {"author": author, "content": content, "is_bot": bool(is_bot)}
        for author, content, is_bot in reversed(messages)
    ]


def get_chat_phrases(channel_name: str, min_frequency: int = 3) -> list[str]:
    """
    Анализирует частые фразы из 2-3 слов в чате для копирования стиля.
    """
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            # Берем только сообщения пользователей (не бота)
            cursor.execute(
                "SELECT content FROM messages WHERE is_bot = 0 ORDER BY timestamp DESC LIMIT 300"
            )
            messages = cursor.fetchall()
        
        if not messages:
            return []
        
        phrases = []
        for (message,) in messages:
            words = message.lower().split()
            # Извлекаем фразы из 2-3 слов
            for i in range(len(words) - 1):
                two_word = f"{words[i]} {words[i+1]}"
                if len(two_word) > 5:  # Минимальная длина фразы
                    phrases.append(two_word)
                
                if i < len(words) - 2:
                    three_word = f"{words[i]} {words[i+1]} {words[i+2]}"
                    if len(three_word) > 8:
                        phrases.append(three_word)
        
        # Считаем частоту
        phrase_counts = Counter(phrases)
        # Возвращаем только частые фразы
        return [phrase for phrase, count in phrase_counts.most_common(20) if count >= min_frequency]
    except sqlite3.Error as e:
        print(f"Ошибка БД {db_name}: {e}")
        return []


def get_chat_trends(channel_name: str, known_emotes: list[str], top_n: int = 12) -> tuple[list[str], list[str]]:
    """Увеличено top_n для большего разнообразия смайликов."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM messages ORDER BY timestamp DESC LIMIT 500")
            messages = cursor.fetchall()

        if not messages:
            return [], []

        all_words = []
        all_emotes = []
        stop_words = {'и', 'в', 'не', 'на', 'я', 'с', 'что', 'он', 'по', 'это', 'но', 'как', 'а', 'то', 'ну', 'ты', 'мы', 'вы', 'за', 'из', 'от', 'до', '[ссылка]'}
        forbidden_set = set(config.FORBIDDEN_WORDS)
        emotes_set = set(known_emotes)

        for (message,) in messages:
            words = message.split()
            for word in words:
                if word in emotes_set:
                    all_emotes.append(word)
                else:
                    cleaned = re.sub(r'[^\w]', '', word).lower()
                    if len(cleaned) > 2 and cleaned not in stop_words and cleaned not in forbidden_set:
                        all_words.append(cleaned)

        word_counts = Counter(all_words)
        emote_counts = Counter(all_emotes)

        return (
            [w for w, _ in word_counts.most_common(top_n)],
            [e for e, _ in emote_counts.most_common(top_n)]
        )
    except sqlite3.Error as e:
        print(f"Ошибка БД {db_name}: {e}")
        return [], []


def count_messages_since_bot(channel_name: str) -> int:
    """Считает сообщения пользователей после последнего сообщения бота."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            # Находим последнее сообщение бота
            cursor.execute(
                "SELECT id FROM messages WHERE is_bot = 1 ORDER BY timestamp DESC LIMIT 1"
            )
            last_bot = cursor.fetchone()
            
            if not last_bot:
                # Если бот еще не писал, считаем все сообщения
                cursor.execute("SELECT COUNT(*) FROM messages WHERE is_bot = 0")
                return cursor.fetchone()[0]
            
            # Считаем сообщения пользователей после последнего сообщения бота
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE is_bot = 0 AND id > ?",
                (last_bot[0],)
            )
            return cursor.fetchone()[0]
    except sqlite3.Error as e:
        print(f"Ошибка БД {db_name}: {e}")
        return 0


def save_user_fact(channel_name: str, username: str, fact: str):
    """Сохраняет факт о пользователе."""
    if not fact or len(fact) < 10:  # Игнорируем слишком короткие факты
        return
    
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO user_facts (username, fact, timestamp) VALUES (?, ?, ?)",
                (username.lower(), fact, datetime.datetime.now())
            )
            conn.commit()
            
            # Ограничиваем количество фактов
            cursor.execute(
                "SELECT COUNT(*) FROM user_facts WHERE username = ?",
                (username.lower(),)
            )
            count = cursor.fetchone()[0]
            
            if count > config.MAX_USER_FACTS // 10:  # Максимум 5 фактов на пользователя
                cursor.execute(
                    "DELETE FROM user_facts WHERE id IN "
                    "(SELECT id FROM user_facts WHERE username = ? ORDER BY timestamp ASC LIMIT 1)",
                    (username.lower(),)
                )
                conn.commit()
        except sqlite3.Error:
            pass


def get_user_facts(channel_name: str, username: str) -> list[str]:
    """Получает факты о пользователе."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fact FROM user_facts WHERE username = ? ORDER BY timestamp DESC LIMIT 5",
                (username.lower(),)
            )
            return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error:
        return []


def get_chat_activity(channel_name: str, minutes: int = 1) -> int:
    """Возвращает количество сообщений за последние N минут."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp > ? AND is_bot = 0",
                (threshold,)
            )
            return cursor.fetchone()[0]
    except sqlite3.Error:
        return 0


def get_last_bot_response_reactions(channel_name: str) -> int:
    """Считает количество ответов на последнее сообщение бота (для feedback loop)."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            # Находим последнее сообщение бота
            cursor.execute(
                "SELECT id, timestamp FROM messages WHERE is_bot = 1 ORDER BY timestamp DESC LIMIT 1"
            )
            last_bot = cursor.fetchone()
            
            if not last_bot:
                return 0
            
            bot_id, bot_time = last_bot
            # Считаем сообщения пользователей в течение 30 секунд после сообщения бота
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE is_bot = 0 AND id > ? AND timestamp < ?",
                (bot_id, bot_time + datetime.timedelta(seconds=30))
            )
            return cursor.fetchone()[0]
    except sqlite3.Error:
        return 0


def get_hot_topics(channel_name: str, time_minutes: int = 10) -> list[str]:
    """Анализирует самые обсуждаемые слова за последние N минут."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(minutes=time_minutes)
            cursor.execute(
                "SELECT content FROM messages WHERE timestamp > ? AND is_bot = 0",
                (threshold,)
            )
            messages = cursor.fetchall()
        
        if not messages:
            return []
        
        words = []
        stop_words = {'и', 'в', 'не', 'на', 'я', 'с', 'что', 'он', 'по', 'это', 'но', 'как', 'а', 'то', 'ну', 'ты'}
        
        for (message,) in messages:
            for word in message.lower().split():
                cleaned = re.sub(r'[^\w]', '', word)
                if len(cleaned) > 3 and cleaned not in stop_words:
                    words.append(cleaned)
        
        word_counts = Counter(words)
        return [w for w, _ in word_counts.most_common(5)]
    except sqlite3.Error:
        return []

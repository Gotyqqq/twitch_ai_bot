# database.py
import sqlite3
import datetime
import re
from collections import Counter
import config
import logging

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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                fact TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                UNIQUE(username, fact)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                positive_count INTEGER DEFAULT 0,
                negative_count INTEGER DEFAULT 0,
                total_messages INTEGER DEFAULT 0,
                relationship_level TEXT DEFAULT 'stranger',
                last_interaction DATETIME
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emote_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emote TEXT NOT NULL,
                usage_count INTEGER DEFAULT 1,
                last_used DATETIME NOT NULL,
                UNIQUE(emote)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS popular_phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase TEXT NOT NULL,
                usage_count INTEGER DEFAULT 1,
                last_used DATETIME NOT NULL,
                UNIQUE(phrase)
            )
        """)
        
        conn.commit()
        print(f"[{channel_name}] База данных инициализирована")


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
            cursor.execute(
                "SELECT content FROM messages WHERE is_bot = 0 ORDER BY timestamp DESC LIMIT 300"
            )
            messages = cursor.fetchall()
        
        if not messages:
            return []
        
        phrases = []
        for (message,) in messages:
            words = message.lower().split()
            for i in range(len(words) - 1):
                two_word = f"{words[i]} {words[i+1]}"
                if len(two_word) > 5:
                    phrases.append(two_word)
                
                if i < len(words) - 2:
                    three_word = f"{words[i]} {words[i+1]} {words[i+2]}"
                    if len(three_word) > 8:
                        phrases.append(three_word)
        
        phrase_counts = Counter(phrases)
        return [phrase for phrase, count in phrase_counts.most_common(20) if count >= min_frequency]
    except sqlite3.Error as e:
        print(f"Ошибка БД {db_name}: {e}")
        return []


def get_chat_trends(channel_name: str, known_emotes: list[str], top_n: int = 15) -> tuple[list[str], list[str]]:
    """Анализирует популярные слова и эмодзи. Увеличен top_n для большего разнообразия."""
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
            cursor.execute(
                "SELECT id FROM messages WHERE is_bot = 1 ORDER BY timestamp DESC LIMIT 1"
            )
            last_bot = cursor.fetchone()
            
            if not last_bot:
                cursor.execute("SELECT COUNT(*) FROM messages WHERE is_bot = 0")
                return cursor.fetchone()[0]
            
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
    if not fact or len(fact) < 10:
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
            
            cursor.execute(
                "SELECT COUNT(*) FROM user_facts WHERE username = ?",
                (username.lower(),)
            )
            count = cursor.fetchone()[0]
            
            if count > config.MAX_USER_FACTS // 10:
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
    """Считает, сколько пользователей ответили на последнее сообщение бота."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, timestamp FROM messages WHERE is_bot = 1 ORDER BY timestamp DESC LIMIT 1"
            )
            last_bot = cursor.fetchone()
            
            if not last_bot:
                return 0
            
            bot_id, bot_time = last_bot
            if isinstance(bot_time, str):
                bot_time = datetime.datetime.fromisoformat(bot_time)
            
            end_time = bot_time + datetime.timedelta(seconds=30)
            end_time_str = end_time.isoformat()
            
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE is_bot = 0 AND id > ? AND timestamp < ?",
                (bot_id, end_time_str)
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
    except sqlite3.Error as e:
        print(f"Ошибка БД {db_name}: {e}")
        return []


def update_user_relationship(channel_name: str, username: str, is_positive: bool = True):
    """Обновляет статистику отношений с пользователем."""
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='user_relationships'
        """)
        
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    positive_count INTEGER DEFAULT 0,
                    negative_count INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    relationship_level TEXT DEFAULT 'stranger',
                    last_interaction DATETIME
                )
            """)
            conn.commit()
        
        cursor.execute(
            "SELECT positive_count, negative_count, total_messages FROM user_relationships WHERE username = ?",
            (username.lower(),)
        )
        result = cursor.fetchone()
        
        if result:
            positive, negative, total = result
            if is_positive:
                positive += 1
            else:
                negative += 1
            total += 1
            
            cursor.execute(
                """UPDATE user_relationships 
                   SET positive_count = ?, negative_count = ?, total_messages = ?, last_interaction = ?
                   WHERE username = ?""",
                (positive, negative, total, datetime.datetime.now(), username.lower())
            )
        else:
            cursor.execute(
                """INSERT INTO user_relationships 
                   (username, positive_count, negative_count, total_messages, last_interaction)
                   VALUES (?, ?, ?, ?, ?)""",
                (username.lower(), 1 if is_positive else 0, 0 if is_positive else 1, 1, datetime.datetime.now())
            )
        
        conn.commit()
        
        update_relationship_level(channel_name, username)


def update_relationship_level(channel_name: str, username: str):
    """Определяет уровень отношений на основе статистики."""
    db_name = get_db_name(channel_name)
    with sqlite3.connect(db_name) as conn:
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT positive_count, negative_count FROM user_relationships WHERE username = ?",
            (username.lower(),)
        )
        result = cursor.fetchone()
        
        if not result:
            return
        
        positive, negative = result
        
        if negative >= config.RELATIONSHIP_TOXIC_THRESHOLD:
            level = 'toxic'
        elif positive >= config.RELATIONSHIP_FAVORITE_THRESHOLD:
            level = 'favorite'
        elif positive >= config.RELATIONSHIP_FRIEND_THRESHOLD:
            level = 'friend'
        elif positive >= config.RELATIONSHIP_ACQUAINTANCE_THRESHOLD:
            level = 'acquaintance'
        else:
            level = 'stranger'
        
        cursor.execute(
            "UPDATE user_relationships SET relationship_level = ? WHERE username = ?",
            (level, username.lower())
        )
        conn.commit()


def get_user_relationship(channel_name: str, username: str) -> dict:
    """Получает информацию об отношениях с пользователем."""
    db_name = get_db_name(channel_name)
    
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='user_relationships'
            """)
            
            if not cursor.fetchone():
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_relationships (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL UNIQUE,
                        positive_count INTEGER DEFAULT 0,
                        negative_count INTEGER DEFAULT 0,
                        total_messages INTEGER DEFAULT 0,
                        relationship_level TEXT DEFAULT 'stranger',
                        last_interaction DATETIME
                    )
                """)
                conn.commit()
            
            cursor.execute("""
                SELECT positive_count, negative_count, total_messages, 
                       relationship_level, last_interaction
                FROM user_relationships
                WHERE username = ?
            """, (username.lower(),))
            
            row = cursor.fetchone()
            if row:
                return {
                    "positive_count": row[0],
                    "negative_count": row[1],
                    "total_messages": row[2],
                    "relationship_level": row[3] or "stranger",
                    "last_interaction": row[4]
                }
            else:
                return {
                    "positive_count": 0,
                    "negative_count": 0,
                    "total_messages": 0,
                    "relationship_level": "stranger",
                    "last_interaction": None
                }
    except sqlite3.Error as e:
        logging.error(f"Ошибка при получении user_relationship для {username}: {e}")
        return {
            "positive_count": 0,
            "negative_count": 0,
            "total_messages": 0,
            "relationship_level": "stranger",
            "last_interaction": None
        }


def detect_mass_reaction(channel_name: str, recent_seconds: int = 10) -> str | None:
    """Определяет массовую реакцию (если 3+ человека написали одно и то же)."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(seconds=recent_seconds)
            cursor.execute(
                "SELECT content FROM messages WHERE timestamp > ? AND is_bot = 0",
                (threshold,)
            )
            messages = cursor.fetchall()
            
            if len(messages) < config.MASS_REACTION_THRESHOLD:
                return None
            
            emote_counts = Counter()
            for (message,) in messages:
                words = message.split()
                for word in words:
                    if word in config.MASS_REACTION_EMOTES:
                        emote_counts[word] += 1
            
            for emote, count in emote_counts.most_common(1):
                if count >= config.MASS_REACTION_THRESHOLD:
                    return emote
            
            return None
    except sqlite3.Error:
        return None



def track_emote_usage(channel_name: str, emote: str):
    """Трекает использование смайлика для статистики."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO emote_usage (emote, usage_count, last_used)
                VALUES (?, 1, ?)
                ON CONFLICT(emote) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used = ?
            """, (emote, datetime.datetime.now(), datetime.datetime.now()))
            conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Ошибка трекинга смайлика {emote}: {e}")


def get_popular_emotes(channel_name: str, hours: int = 24) -> list[dict]:
    """Получает популярные смайлики за последние N часов."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(hours=hours)
            cursor.execute("""
                SELECT emote, usage_count FROM emote_usage
                WHERE last_used > ?
                ORDER BY usage_count DESC
                LIMIT 25
            """, (threshold,))
            return [{"emote": row[0], "count": row[1]} for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logging.error(f"Ошибка получения популярных смайликов: {e}")
        return []


def track_phrase_usage(channel_name: str, phrase: str):
    """Трекает использование фразы для статистики."""
    if len(phrase) < 5 or len(phrase) > 100:
        return
    
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO popular_phrases (phrase, usage_count, last_used)
                VALUES (?, 1, ?)
                ON CONFLICT(phrase) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used = ?
            """, (phrase.lower(), datetime.datetime.now(), datetime.datetime.now()))
            conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Ошибка трекинга фразы: {e}")


def get_popular_phrases(channel_name: str, hours: int = 48) -> list[str]:
    """Получает популярные фразы за последние N часов."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(hours=hours)
            cursor.execute("""
                SELECT phrase FROM popular_phrases
                WHERE last_used > ? AND usage_count >= 3
                ORDER BY usage_count DESC
                LIMIT 30
            """, (threshold,))
            return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logging.error(f"Ошибка получения популярных фраз: {e}")
        return []


def get_user_message_stats(channel_name: str, username: str, days: int = 7) -> dict:
    """Получает статистику сообщений пользователя за последние N дней."""
    db_name = get_db_name(channel_name)
    try:
        with sqlite3.connect(db_name) as conn:
            cursor = conn.cursor()
            threshold = datetime.datetime.now() - datetime.timedelta(days=days)
            
            cursor.execute("""
                SELECT COUNT(*), AVG(LENGTH(content))
                FROM messages
                WHERE author = ? AND is_bot = 0 AND timestamp > ?
            """, (username, threshold))
            
            result = cursor.fetchone()
            if result:
                return {
                    "message_count": result[0] or 0,
                    "avg_message_length": round(result[1] or 0, 1)
                }
            return {"message_count": 0, "avg_message_length": 0}
    except sqlite3.Error as e:
        logging.error(f"Ошибка получения статистики пользователя {username}: {e}")
        return {"message_count": 0, "avg_message_length": 0}

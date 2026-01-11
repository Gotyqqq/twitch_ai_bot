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
        
        # <CHANGE> Миграция: добавляем колонку is_bot если её нет
        cursor.execute("PRAGMA table_info(messages)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'is_bot' not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_bot INTEGER DEFAULT 0")
            print(f"[{channel_name}] Добавлена колонка is_bot в таблицу messages")
        
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


def get_chat_trends(channel_name: str, known_emotes: list[str], top_n: int = 8) -> tuple[list[str], list[str]]:
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
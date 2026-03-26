"""Бот анализа текстового корпуса — единый файл запуска.

Usage:
    python main.py

Requires TELEGRAM_TOKEN to be set in the .env file or as an environment variable.
"""

# ---------------------------------------------------------------------------
# Standard-library & third-party imports
# ---------------------------------------------------------------------------
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
from collections import Counter
from sqlite3 import Error

import telebot
from dotenv import load_dotenv
from wordcloud import WordCloud

# ---------------------------------------------------------------------------
# Configuration  (previously config.py)
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN = "7655484821:AAH-V6qCSQsKi216uIEMe1hw08mhq4erIx0"
DB_FILE: str = os.getenv('DB_FILE', 'corpus_analysis.sqlite3')
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
MAX_TEXT_LENGTH: int = int(os.getenv('MAX_TEXT_LENGTH', '10000'))
TOP_WORDS: int = int(os.getenv('TOP_WORDS', '20'))
COLLECT_WINDOW: int = int(os.getenv('COLLECT_WINDOW', '3'))  # seconds

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database  (previously database.py)
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_file='corpus_analysis.sqlite3'):
        """Create a database connection to a SQLite database."""
        self.conn = None
        try:
            self.conn = sqlite3.connect(db_file, check_same_thread=False)
            self._init_schema()
        except Error as e:
            print(f'Database connection error: {e}')

    def _init_schema(self):
        """Create required tables if they do not exist."""
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                analysis_result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS corpus_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS named_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                combined_text TEXT NOT NULL,
                analysis_result TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )

    def close_connection(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()

    def _create_table(self, table_creation_sql):
        try:
            c = self.conn.cursor()
            c.execute(table_creation_sql)
            self.conn.commit()
        except Error as e:
            print(f'Table creation error: {e}')

    def insert_analysis(self, analysis_data):
        """Insert a new analysis record. analysis_data = (user_id, result_text)."""
        sql = 'INSERT INTO analyses(user_id, analysis_result) VALUES(?, ?)'
        try:
            cur = self.conn.cursor()
            cur.execute(sql, analysis_data)
            self.conn.commit()
            return cur.lastrowid
        except Error as e:
            print(f'insert_analysis error: {e}')
            return None

    def save_corpus_text(self, user_id: int, text: str) -> int | None:
        """Save a raw corpus text submitted by the user. Returns the new row id."""
        sql = 'INSERT INTO corpus_texts(user_id, text) VALUES(?, ?)'
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, text))
            self.conn.commit()
            return cur.lastrowid
        except Error as e:
            print(f'save_corpus_text error: {e}')
            return None

    def get_corpus_stats(self, user_id: int) -> dict:
        """Return basic corpus stats for a user: total texts and total characters."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT COUNT(*), COALESCE(SUM(LENGTH(text)), 0) FROM corpus_texts WHERE user_id = ?',
                (user_id,),
            )
            count, total_chars = cur.fetchone()
            return {'count': count, 'total_chars': total_chars}
        except Error as e:
            print(f'get_corpus_stats error: {e}')
            return {'count': 0, 'total_chars': 0}

    def save_named_analysis(self, user_id: int, name: str, combined_text: str,
                            analysis_result: str) -> int | None:
        """Persist a named corpus analysis. Returns the new row id."""
        sql = (
            'INSERT INTO named_analyses(user_id, name, combined_text, analysis_result) '
            'VALUES(?, ?, ?, ?)'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, name, combined_text, analysis_result))
            self.conn.commit()
            return cur.lastrowid
        except Error as e:
            print(f'save_named_analysis error: {e}')
            return None

# ---------------------------------------------------------------------------
# Text analyser  (previously text_analyzer.py)
# ---------------------------------------------------------------------------

# Common Ossetian (Iron dialect) stopwords — function words, pronouns,
# particles, conjunctions, and frequent auxiliary verb forms.
_OSSETIAN_STOPWORDS = {
    # Personal pronouns
    'æз', 'ды', 'уый', 'мах', 'сымах', 'уыдон',
    # Possessive particles / short pronouns
    'мæ', 'дæ', 'йæ', 'нæ', 'уæ', 'сæ', 'мæн', 'дæу',
    # Conjunctions and discourse particles
    'æмæ', 'æви', 'фæлæ', 'уæдта', 'уæд', 'æрмæстдæр',
    # Temporal / conditional conjunctions
    'куы', 'куыд', 'кæд', 'амæ',
    # Demonstratives and determiners
    'уыцы', 'ацы', 'иу', 'иуæй',
    # Negation
    'ма', 'нæй',
    # Common copula and auxiliary forms
    'у', 'сты', 'уыд', 'уыдысты', 'уа', 'уой',
    # Adverbs of place and time
    'ам', 'уым', 'ныр', 'æрмæст', 'дæр', 'та',
    # Postpositions / case-like particles
    'æрдæм', 'æхсæн', 'фæстæ', 'размæ', 'хуызæн',
    # Other high-frequency function words
    'цы', 'чи', 'кæй', 'кæм', 'кæцæй',
    'æй', 'ын', 'ыл', 'æнæ', 'дзы',
    'уæм', 'сæм', 'ыф', 'æм',
}

# Sentence boundary: split on . ! ? followed by whitespace or end-of-string.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
# Token: sequences of Unicode letters (including æ / Æ) or digits.
_TOKEN_RE = re.compile(r'[^\W\d_]+|\d+', re.UNICODE)


class TextAnalyzer:
    def __init__(self):
        self.stop_words = _OSSETIAN_STOPWORDS

    def tokenize(self, text):
        return _TOKEN_RE.findall(text.lower())

    def lemmatize(self, tokens):
        # Ossetian morphological resources are not available via pymorphy2;
        # return each token in its lowercase form as a stand-in for the lemma.
        return tokens

    def remove_stopwords(self, tokens):
        return [token for token in tokens if token not in self.stop_words]

    def get_frequency_distribution(self, tokens):
        return dict(Counter(tokens).most_common(50))

    def get_text_stats(self, text):
        sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]
        tokens = self.tokenize(text)
        return {
            'total_words': len(tokens),
            'unique_words': len(set(tokens)),
            'sentences': len(sentences),
            'avg_word_length': sum(len(w) for w in tokens) / len(tokens) if tokens else 0,
            'lexical_diversity': len(set(tokens)) / len(tokens) if tokens else 0,
        }

    def analyze(self, text):
        tokens = self.tokenize(text)
        lemmas = self.lemmatize(tokens)
        cleaned = self.remove_stopwords(lemmas)
        return {
            'stats': self.get_text_stats(text),
            'frequency': self.get_frequency_distribution(cleaned),
            'tokens_count': len(tokens),
            'lemmas_count': len(set(lemmas)),
        }

# ---------------------------------------------------------------------------
# Visualiser  (previously visualizer.py)
# ---------------------------------------------------------------------------

class DataVisualizer:
    def plot_word_cloud(self, freq_dict_or_text, title='Word Cloud'):
        """Generate a word cloud image and save to a temp file. Returns file path.

        The caller is responsible for deleting the returned file after use.
        """
        if isinstance(freq_dict_or_text, dict):
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate_from_frequencies(freq_dict_or_text)
        else:
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate(freq_dict_or_text)
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp.close()
        wc.to_file(tmp.name)
        return tmp.name

# ---------------------------------------------------------------------------
# Bot globals
# ---------------------------------------------------------------------------
analyzer = TextAnalyzer()
db = Database(DB_FILE)
vis = DataVisualizer()
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ---------------------------------------------------------------------------
# Per-user message-collection state (for the 3-second analysis window)
# ---------------------------------------------------------------------------

_user_buffers: dict[int, list[str]] = {}   # user_id -> buffered texts
_user_timers: dict[int, threading.Timer] = {}  # user_id -> active debounce timer
_buffer_lock = threading.Lock()


def _ru_plural(n: int, form1: str, form2: str, form5: str) -> str:
    """Return the correct Russian plural form for *n*.

    form1 — used for 1 (одно сообщение)
    form2 — used for 2-4 (два сообщения)
    form5 — used for 5+ and 11-19 (пять сообщений)
    """
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 19:
        return form5
    n1 = n_abs % 10
    if n1 == 1:
        return form1
    if 2 <= n1 <= 4:
        return form2
    return form5


def _flush_user_buffer(user_id: int, chat_id: int) -> None:
    """Fire after COLLECT_WINDOW seconds of inactivity for a user.

    Combines all buffered texts, runs full analysis, sends the results, and
    then asks the user to name the corpus via a next-step handler.
    """
    with _buffer_lock:
        texts = _user_buffers.pop(user_id, [])
        _user_timers.pop(user_id, None)

    if not texts:
        return

    # Persist each individual text so /corpus stats stay accurate.
    for t in texts:
        db.save_corpus_text(user_id, t)

    combined_text = '\n'.join(texts)
    result = analyzer.analyze(combined_text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])

    n = len(texts)
    msg_form = _ru_plural(n, 'сообщения', 'сообщений', 'сообщений')
    reply = (
        f'📊 *Анализ {n} {msg_form}*\n\n'
        f'*Статистика:*\n'
        f'  • Слов (всего): {stats["total_words"]}\n'
        f'  • Уникальных слов: {stats["unique_words"]}\n'
        f'  • Предложений: {stats["sentences"]}\n'
        f'  • Средняя длина слова: {stats["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {stats["lexical_diversity"]:.2%}\n'
        f'  • Токенов: {result["tokens_count"]}\n'
        f'  • Уникальных лемм: {result["lemmas_count"]}\n\n'
        f'*Топ {TOP_WORDS} слов:*\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'

    sent = bot.send_message(chat_id, reply, parse_mode='Markdown')
    bot.send_message(chat_id, '📝 Введите название для этого корпуса (или /skip, чтобы пропустить):')
    bot.register_next_step_handler(sent, _receive_corpus_name, user_id, combined_text, result)


def _receive_corpus_name(message: telebot.types.Message, user_id: int,
                         combined_text: str, result: dict) -> None:
    """Next-step handler: receives the corpus name chosen by the user."""
    if message.text and message.text.strip().lower() in ('/skip', 'skip'):
        bot.reply_to(message, '⏭ Корпус не сохранён.')
        return

    name = (message.text or '').strip()
    if not name:
        bot.reply_to(message, '⏭ Название не указано. Корпус не сохранён.')
        return

    db.save_named_analysis(user_id, name, combined_text, json.dumps(result))
    bot.reply_to(message, f'✅ Корпус *{name}* сохранён!', parse_mode='Markdown')

# ---------------------------------------------------------------------------
# Bot handlers  (previously bot.py)
# ---------------------------------------------------------------------------


def _get_text(message: telebot.types.Message) -> str | None:
    """Extract and validate the text argument from the command."""
    parts = message.text.split(maxsplit=1)
    text = parts[1].strip() if len(parts) > 1 else ''
    if not text:
        bot.reply_to(message, 'Введите текст после команды. Пример:\n/analyze текст для анализа')
        return None
    if len(text) > MAX_TEXT_LENGTH:
        bot.reply_to(
            message,
            f'Текст слишком длинный. Максимальная длина: {MAX_TEXT_LENGTH} символов.',
        )
        return None
    return text


@bot.message_handler(commands=['start'])
def start(message: telebot.types.Message) -> None:
    """Send a welcome message explaining available commands."""
    bot.reply_to(
        message,
        '👋 Добро пожаловать в *Бот анализа корпуса*!\n\n'
        'Бот анализирует тексты.\n\n'
        'Команды:\n'
        '  /analyze <текст> — статистика + самые частые слова\n'
        '  /frequency <текст> — диаграмма частотности слов\n'
        '  /wordcloud <текст> — облако слов\n'
        '  /stats <текст> — краткая статистика текста\n'
        '  /corpus — статистика вашего корпуса\n\n'
        '📝 Вы также можете просто отправить одно или несколько сообщений подряд.\n'
        f'Через {COLLECT_WINDOW} {_ru_plural(COLLECT_WINDOW, "секунду", "секунды", "секунд")} '
        'после последнего сообщения бот проанализирует '
        'все тексты вместе и попросит дать название корпусу.\n\n'
        'Введите текст после команды.',
        parse_mode='Markdown',
    )


@bot.message_handler(commands=['analyze'])
def analyze(message: telebot.types.Message) -> None:
    """/analyze <text> — run full analysis and return formatted results."""
    text = _get_text(message)
    if text is None:
        return

    result = analyzer.analyze(text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])

    reply = (
        f'📊 *Результаты анализа*\n\n'
        f'*Статистика:*\n'
        f'  • Слов (всего): {stats["total_words"]}\n'
        f'  • Уникальных слов: {stats["unique_words"]}\n'
        f'  • Предложений: {stats["sentences"]}\n'
        f'  • Средняя длина слова: {stats["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {stats["lexical_diversity"]:.2%}\n'
        f'  • Токенов: {result["tokens_count"]}\n'
        f'  • Уникальных лемм: {result["lemmas_count"]}\n\n'
        f'*Топ {TOP_WORDS} слов:*\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'

    user_id = message.from_user.id
    db.insert_analysis((user_id, json.dumps(result['stats'])))
    bot.reply_to(message, reply, parse_mode='Markdown')


@bot.message_handler(commands=['frequency'])
def frequency(message: telebot.types.Message) -> None:
    """/frequency <text> — send top word frequencies as a text list."""
    text = _get_text(message)
    if text is None:
        return

    freq_dict = analyzer.analyze(text)['frequency']
    if not freq_dict:
        bot.reply_to(message, 'Нет слов для частотного анализа.')
        return

    top = sorted(freq_dict.items(), key=lambda x: x[1], reverse=True)[:20]
    lines = ['📊 *Частота слов:*\n\n']
    for word, count in top:
        lines.append(f'  {word}: {count}\n')
    bot.reply_to(message, ''.join(lines), parse_mode='Markdown')


@bot.message_handler(commands=['wordcloud'])
def wordcloud(message: telebot.types.Message) -> None:
    """/wordcloud <text> — generate and send a word cloud image."""
    text = _get_text(message)
    if text is None:
        return

    freq_dict = analyzer.analyze(text)['frequency']
    if not freq_dict:
        bot.reply_to(message, 'Нет слов для создания облака слов.')
        return

    image_path = vis.plot_word_cloud(freq_dict, title='Облако слов')
    try:
        with open(image_path, 'rb') as img:
            bot.send_photo(message.chat.id, img, caption='Облако слов')
    finally:
        os.unlink(image_path)


@bot.message_handler(commands=['stats'])
def stats(message: telebot.types.Message) -> None:
    """/stats <text> — return brief text statistics."""
    text = _get_text(message)
    if text is None:
        return

    s = analyzer.get_text_stats(text)
    reply = (
        f'📈 *Статистика текста*\n\n'
        f'  • Слов (всего): {s["total_words"]}\n'
        f'  • Уникальных слов: {s["unique_words"]}\n'
        f'  • Предложений: {s["sentences"]}\n'
        f'  • Средняя длина слова: {s["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {s["lexical_diversity"]:.2%}\n'
    )
    bot.reply_to(message, reply, parse_mode='Markdown')


@bot.message_handler(commands=['corpus'])
def corpus(message: telebot.types.Message) -> None:
    """/corpus — show corpus statistics for the current user."""
    user_id = message.from_user.id
    corpus_stats = db.get_corpus_stats(user_id)
    reply = (
        f'📚 *Ваш корпус*\n\n'
        f'  • Текстов сохранено: {corpus_stats["count"]}\n'
        f'  • Всего символов: {corpus_stats["total_chars"]}\n\n'
        f'Отправьте любое текстовое сообщение, чтобы добавить его в корпус.'
    )
    bot.reply_to(message, reply, parse_mode='Markdown')


@bot.message_handler(content_types=['text'])
def add_to_corpus(message: telebot.types.Message) -> None:
    """Buffer plain-text messages; after COLLECT_WINDOW seconds of inactivity,
    analyse them all together and ask the user to name the resulting corpus.
    """
    # Ignore messages that start with '/' (commands not matched by other handlers).
    if message.text.startswith('/'):
        return
    text = message.text.strip()
    if not text:
        return
    if len(text) > MAX_TEXT_LENGTH:
        bot.reply_to(
            message,
            f'Текст слишком длинный. Максимальная длина: {MAX_TEXT_LENGTH} символов.',
        )
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    with _buffer_lock:
        # Cancel the existing countdown so it resets on every new message.
        if user_id in _user_timers:
            _user_timers[user_id].cancel()
        _user_buffers.setdefault(user_id, []).append(text)
        timer = threading.Timer(COLLECT_WINDOW, _flush_user_buffer, args=(user_id, chat_id))
        timer.daemon = True
        _user_timers[user_id] = timer
        timer.start()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError('TELEGRAM_TOKEN is not set. Please configure it in your .env file.')

    logger.info('Bot is starting...')
    try:
        bot.infinity_polling()
    finally:
        db.close_connection()
        logger.info('Database connection closed.')


if __name__ == '__main__':
    main()

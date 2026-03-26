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
class ColoredFormatter(logging.Formatter):
    """Logging formatter that adds ANSI color codes based on log level."""

    _COLORS = {
        logging.DEBUG:    '\033[36m',   # Cyan
        logging.INFO:     '\033[32m',   # Green
        logging.WARNING:  '\033[33m',   # Yellow
        logging.ERROR:    '\033[31m',   # Red
        logging.CRITICAL: '\033[35m',   # Magenta
    }
    _RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno, '')
        message = super().format(record)
        return f'{color}{message}{self._RESET}'


_handler = logging.StreamHandler()
_handler.setFormatter(
    ColoredFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)
logging.root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logging.root.addHandler(_handler)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database  (previously database.py)
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_file='corpus_analysis.sqlite3'):
        """Create a database connection to a SQLite database."""
        logger.info('[DB] Подключение к базе данных: %s', db_file)
        self.conn = None
        try:
            self.conn = sqlite3.connect(db_file, check_same_thread=False)
            logger.info('[DB] Соединение установлено')
            self._init_schema()
        except Error as e:
            logger.error('[DB] Ошибка подключения: %s', e)

    def _init_schema(self):
        """Create required tables if they do not exist."""
        logger.info('[DB] Инициализация схемы базы данных')
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
        logger.info('[DB] Схема готова')

    def close_connection(self):
        """Close the database connection."""
        logger.info('[DB] Закрытие соединения с базой данных')
        if self.conn:
            self.conn.close()

    def _create_table(self, table_creation_sql):
        # Extract table name for a readable log line
        table_name = table_creation_sql.split('EXISTS')[-1].split('(')[0].strip()
        logger.info('[DB] Создание таблицы (если не существует): %s', table_name)
        try:
            c = self.conn.cursor()
            c.execute(table_creation_sql)
            self.conn.commit()
        except Error as e:
            logger.error('[DB] Ошибка создания таблицы %s: %s', table_name, e)

    def insert_analysis(self, analysis_data):
        """Insert a new analysis record. analysis_data = (user_id, result_text)."""
        logger.info('[DB] Сохранение анализа для user_id=%s', analysis_data[0])
        sql = 'INSERT INTO analyses(user_id, analysis_result) VALUES(?, ?)'
        try:
            cur = self.conn.cursor()
            cur.execute(sql, analysis_data)
            self.conn.commit()
            logger.info('[DB] Анализ сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] insert_analysis error: %s', e)
            return None

    def save_corpus_text(self, user_id: int, text: str) -> int | None:
        """Save a raw corpus text submitted by the user. Returns the new row id."""
        logger.info('[DB] Сохранение текста корпуса для user_id=%s (%d симв.)', user_id, len(text))
        sql = 'INSERT INTO corpus_texts(user_id, text) VALUES(?, ?)'
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, text))
            self.conn.commit()
            logger.info('[DB] Текст сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] save_corpus_text error: %s', e)
            return None

    def get_corpus_stats(self, user_id: int) -> dict:
        """Return basic corpus stats for a user: total texts and total characters."""
        logger.info('[DB] Запрос статистики корпуса для user_id=%s', user_id)
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT COUNT(*), COALESCE(SUM(LENGTH(text)), 0) FROM corpus_texts WHERE user_id = ?',
                (user_id,),
            )
            count, total_chars = cur.fetchone()
            logger.info('[DB] Статистика: текстов=%s, символов=%s', count, total_chars)
            return {'count': count, 'total_chars': total_chars}
        except Error as e:
            logger.error('[DB] get_corpus_stats error: %s', e)
            return {'count': 0, 'total_chars': 0}

    def get_named_analysis(self, user_id: int, name: str) -> dict | None:
        """Retrieve a saved named corpus by user_id and name.

        Returns a dict with keys ``name``, ``combined_text``, ``analysis_result``,
        ``created_at``, or *None* if not found.
        """
        logger.info('[DB] Поиск корпуса "%s" для user_id=%s', name, user_id)
        sql = (
            'SELECT name, combined_text, analysis_result, created_at '
            'FROM named_analyses WHERE user_id = ? AND name = ? '
            'ORDER BY id DESC LIMIT 1'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, name))
            row = cur.fetchone()
            if row is None:
                logger.info('[DB] Корпус "%s" не найден для user_id=%s', name, user_id)
                return None
            logger.info('[DB] Корпус "%s" найден для user_id=%s', name, user_id)
            return {
                'name': row[0],
                'combined_text': row[1],
                'analysis_result': row[2],
                'created_at': row[3],
            }
        except Error as e:
            logger.error('[DB] get_named_analysis error: %s', e)
            return None

    def save_named_analysis(self, user_id: int, name: str, combined_text: str,
                            analysis_result: str) -> int | None:
        """Persist a named corpus analysis. Returns the new row id."""
        logger.info('[DB] Сохранение именованного анализа "%s" для user_id=%s', name, user_id)
        sql = (
            'INSERT INTO named_analyses(user_id, name, combined_text, analysis_result) '
            'VALUES(?, ?, ?, ?)'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, name, combined_text, analysis_result))
            self.conn.commit()
            logger.info('[DB] Именованный анализ сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] save_named_analysis error: %s', e)
            return None

    def import_texts_from_directory(self, user_id: int, texts_dir: str = 'texts') -> dict:
        """Import all .txt files from *texts_dir* into ``corpus_texts`` for *user_id*.

        Returns a dict with keys:
          ``imported`` — number of files successfully loaded,
          ``errors``   — number of files that could not be read,
          ``error``    — (optional) directory-level error message.
        """
        logger.info('[DB] Импорт текстов из папки "%s" для user_id=%s', texts_dir, user_id)
        try:
            all_entries = os.listdir(texts_dir)
        except OSError as e:
            logger.error('[DB] Ошибка при чтении папки "%s": %s', texts_dir, e)
            return {'imported': 0, 'errors': 0, 'error': str(e)}

        txt_files = [f for f in all_entries if f.lower().endswith('.txt')]
        logger.info('[DB] Найдено %d .txt файлов в папке "%s"', len(txt_files), texts_dir)

        imported = 0
        errors = 0
        for filename in txt_files:
            filepath = os.path.join(texts_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as fh:
                    text = fh.read()
                self.save_corpus_text(user_id, text)
                imported += 1
                logger.info('[DB] Файл "%s" успешно импортирован', filename)
            except (OSError, UnicodeDecodeError) as e:
                errors += 1
                logger.error('[DB] Ошибка при импорте файла "%s": %s', filename, e)

        logger.info('[DB] Импорт завершён: успешно=%d, ошибок=%d', imported, errors)
        return {'imported': imported, 'errors': errors}

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
        logger.info('[Analyzer] TextAnalyzer инициализирован (%d стоп-слов)', len(self.stop_words))

    def tokenize(self, text):
        tokens = _TOKEN_RE.findall(text.lower())
        logger.info('[Analyzer] Токенизация: %d токенов', len(tokens))
        return tokens

    def lemmatize(self, tokens):
        # Ossetian morphological resources are not available via pymorphy2;
        # return each token in its lowercase form as a stand-in for the lemma.
        logger.info('[Analyzer] Лемматизация: %d токенов → %d уникальных лемм', len(tokens), len(set(tokens)))
        return tokens

    def remove_stopwords(self, tokens):
        cleaned = [token for token in tokens if token not in self.stop_words]
        logger.info('[Analyzer] Удаление стоп-слов: %d → %d токенов', len(tokens), len(cleaned))
        return cleaned

    def get_frequency_distribution(self, tokens):
        freq = dict(Counter(tokens).most_common(50))
        logger.info('[Analyzer] Частотное распределение: топ-%d слов из %d', len(freq), len(tokens))
        return freq

    def get_text_stats(self, text):
        sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]
        tokens = self.tokenize(text)
        stats = {
            'total_words': len(tokens),
            'unique_words': len(set(tokens)),
            'sentences': len(sentences),
            'avg_word_length': sum(len(w) for w in tokens) / len(tokens) if tokens else 0,
            'lexical_diversity': len(set(tokens)) / len(tokens) if tokens else 0,
        }
        logger.info(
            '[Analyzer] Статистика текста: слов=%d, уник.=%d, предл.=%d',
            stats['total_words'], stats['unique_words'], stats['sentences'],
        )
        return stats

    def analyze(self, text):
        logger.info('[Analyzer] Запуск полного анализа текста (%d симв.)', len(text))
        tokens = self.tokenize(text)
        lemmas = self.lemmatize(tokens)
        cleaned = self.remove_stopwords(lemmas)
        result = {
            'stats': self.get_text_stats(text),
            'frequency': self.get_frequency_distribution(cleaned),
            'tokens_count': len(tokens),
            'lemmas_count': len(set(lemmas)),
        }
        logger.info('[Analyzer] Анализ завершён: токенов=%d, уник.лемм=%d', result['tokens_count'], result['lemmas_count'])
        return result

# ---------------------------------------------------------------------------
# Visualiser  (previously visualizer.py)
# ---------------------------------------------------------------------------

class DataVisualizer:
    def plot_word_cloud(self, freq_dict_or_text, title='Word Cloud'):
        """Generate a word cloud image and save to a temp file. Returns file path.

        The caller is responsible for deleting the returned file after use.
        """
        logger.info('[Visualizer] Генерация облака слов: "%s"', title)
        if isinstance(freq_dict_or_text, dict):
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate_from_frequencies(freq_dict_or_text)
        else:
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate(freq_dict_or_text)
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp.close()
        wc.to_file(tmp.name)
        logger.info('[Visualizer] Облако слов сохранено: %s', tmp.name)
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
    saves each message as an individual text in the user's corpus.
    """
    logger.info('[Buffer] Таймер сработал для user_id=%s, chat_id=%s', user_id, chat_id)
    with _buffer_lock:
        texts = _user_buffers.pop(user_id, [])
        _user_timers.pop(user_id, None)

    if not texts:
        logger.info('[Buffer] Буфер пуст для user_id=%s, пропускаем', user_id)
        return

    logger.info('[Buffer] Сообщений в буфере: %d (user_id=%s)', len(texts), user_id)

    # Persist each individual text so /corpus stats stay accurate.
    for i, t in enumerate(texts, 1):
        logger.info('[Buffer] Сохранение текста %d/%d в корпус (user_id=%s)', i, len(texts), user_id)
        db.save_corpus_text(user_id, t)

    combined_text = '\n'.join(texts)
    logger.info('[Buffer] Запуск анализа объединённого текста (%d симв.)', len(combined_text))
    result = analyzer.analyze(combined_text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])
    logger.info('[Buffer] Анализ завершён, отправка результатов в chat_id=%s', chat_id)

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

    bot.send_message(chat_id, reply, parse_mode='Markdown')
    logger.info('[Buffer] Результаты анализа отправлены (user_id=%s)', user_id)


def _receive_corpus_name(message: telebot.types.Message, user_id: int,
                         combined_text: str, result: dict) -> None:
    """Next-step handler: receives the corpus name chosen by the user."""
    logger.info('[Buffer] Получено название корпуса от user_id=%s: "%s"',
                user_id, (message.text or '').strip()[:50])
    if message.text and message.text.strip().lower() in ('/skip', 'skip'):
        logger.info('[Buffer] user_id=%s пропустил сохранение корпуса', user_id)
        bot.reply_to(message, '⏭ Корпус не сохранён.')
        return

    name = (message.text or '').strip()
    if not name:
        logger.info('[Buffer] Пустое название от user_id=%s, сохранение отменено', user_id)
        bot.reply_to(message, '⏭ Название не указано. Корпус не сохранён.')
        return

    db.save_named_analysis(user_id, name, combined_text, json.dumps(result))
    logger.info('[Buffer] Корпус "%s" успешно сохранён для user_id=%s', name, user_id)
    bot.reply_to(message, f'✅ Корпус *{name}* сохранён!', parse_mode='Markdown')

# ---------------------------------------------------------------------------
# Bot handlers  (previously bot.py)
# ---------------------------------------------------------------------------


def _get_text(message: telebot.types.Message) -> str | None:
    """Extract and validate the text argument from the command."""
    parts = message.text.split(maxsplit=1)
    text = parts[1].strip() if len(parts) > 1 else ''
    if not text:
        logger.info('[Handler] Команда без текста от user_id=%s: %s', message.from_user.id, message.text.split()[0])
        bot.reply_to(message, 'Введите текст после команды. Пример:\n/analyze текст для анализа')
        return None
    if len(text) > MAX_TEXT_LENGTH:
        logger.info('[Handler] Текст слишком длинный (%d симв.) от user_id=%s', len(text), message.from_user.id)
        bot.reply_to(
            message,
            f'Текст слишком длинный. Максимальная длина: {MAX_TEXT_LENGTH} символов.',
        )
        return None
    return text


@bot.message_handler(commands=['start'])
def start(message: telebot.types.Message) -> None:
    """Send a welcome message with a command menu keyboard."""
    logger.info('[/start] user_id=%s (@%s)', message.from_user.id, message.from_user.username)

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        telebot.types.KeyboardButton('Статистика и частые слова текста'),
        telebot.types.KeyboardButton('Частотность слов в тексте'),
        telebot.types.KeyboardButton('Облако слов для текста'),
        telebot.types.KeyboardButton('Краткая статистика текста'),
        telebot.types.KeyboardButton('Статистика вашего корпуса'),
        telebot.types.KeyboardButton('Получить текст корпуса по названию'),
        telebot.types.KeyboardButton('Импортировать тексты из папки texts/'),
    )

    bot.reply_to(
        message,
        '👋 Добро пожаловать в *Бот анализа корпуса*!\n\n'
        'Бот анализирует тексты.\n\n'
        'Команды:\n'
        '  /analyze <текст> — статистика + самые частые слова\n'
        '  /frequency <текст> — диаграмма частотности слов\n'
        '  /wordcloud <текст> — облако слов\n'
        '  /stats <текст> — краткая статистика текста\n'
        '  /corpus — статистика вашего корпуса\n'
        '  /load <название> — получить текст сохранённого корпуса\n'
        '  /import_texts — импортировать .txt файлы из папки texts/\n\n'
        '📝 Вы также можете просто отправить одно или несколько сообщений подряд.\n'
        f'Через {COLLECT_WINDOW} {_ru_plural(COLLECT_WINDOW, "секунду", "секунды", "секунд")} '
        'после последнего сообщения бот проанализирует '
        'все тексты вместе и сохранит каждое как отдельный текст в вашем корпусе.\n\n'
        'Введите текст после команды.',
        parse_mode='Markdown',
        reply_markup=markup,
    )


@bot.message_handler(commands=['analyze'])
def analyze(message: telebot.types.Message) -> None:
    """/analyze <text> — run full analysis and return formatted results."""
    logger.info('[/analyze] user_id=%s', message.from_user.id)
    text = _get_text(message)
    if text is None:
        return

    logger.info('[/analyze] Анализ текста (%d симв.) для user_id=%s', len(text), message.from_user.id)
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
    db.save_corpus_text(user_id, text)
    db.insert_analysis((user_id, json.dumps(result['stats'])))
    logger.info('[/analyze] Результаты отправлены user_id=%s', user_id)
    sent = bot.reply_to(message, reply, parse_mode='Markdown')
    bot.send_message(message.chat.id, '📝 Введите название для сохранения корпуса (или /skip, чтобы пропустить):')
    bot.register_next_step_handler(sent, _receive_corpus_name, user_id, text, result)


@bot.message_handler(commands=['frequency'])
def frequency(message: telebot.types.Message) -> None:
    """/frequency <text> — send top word frequencies as a text list."""
    logger.info('[/frequency] user_id=%s', message.from_user.id)
    text = _get_text(message)
    if text is None:
        return

    logger.info('[/frequency] Анализ частотности (%d симв.) для user_id=%s', len(text), message.from_user.id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        logger.info('[/frequency] Нет слов для анализа (user_id=%s)', message.from_user.id)
        bot.reply_to(message, 'Нет слов для частотного анализа.')
        return

    top = sorted(freq_dict.items(), key=lambda x: x[1], reverse=True)[:20]
    lines = ['📊 *Частота слов:*\n\n']
    for word, count in top:
        lines.append(f'  {word}: {count}\n')
    logger.info('[/frequency] Отправка топ-%d слов для user_id=%s', len(top), message.from_user.id)

    user_id = message.from_user.id
    db.save_corpus_text(user_id, text)
    db.insert_analysis((user_id, json.dumps(result['stats'])))
    sent = bot.reply_to(message, ''.join(lines), parse_mode='Markdown')
    bot.send_message(message.chat.id, '📝 Введите название для сохранения корпуса (или /skip, чтобы пропустить):')
    logger.info('[/frequency] Ожидаем название корпуса (user_id=%s)', user_id)
    bot.register_next_step_handler(sent, _receive_corpus_name, user_id, text, result)


@bot.message_handler(commands=['wordcloud'])
def wordcloud(message: telebot.types.Message) -> None:
    """/wordcloud <text> — generate and send a word cloud image."""
    logger.info('[/wordcloud] user_id=%s', message.from_user.id)
    text = _get_text(message)
    if text is None:
        return

    logger.info('[/wordcloud] Генерация облака слов (%d симв.) для user_id=%s', len(text), message.from_user.id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        logger.info('[/wordcloud] Нет слов для облака (user_id=%s)', message.from_user.id)
        bot.reply_to(message, 'Нет слов для создания облака слов.')
        return

    image_path = vis.plot_word_cloud(freq_dict, title='Облако слов')
    try:
        logger.info('[/wordcloud] Отправка изображения %s для user_id=%s', image_path, message.from_user.id)
        with open(image_path, 'rb') as img:
            sent = bot.send_photo(message.chat.id, img, caption='Облако слов')
    finally:
        os.unlink(image_path)
        logger.info('[/wordcloud] Временный файл удалён: %s', image_path)

    user_id = message.from_user.id
    db.save_corpus_text(user_id, text)
    db.insert_analysis((user_id, json.dumps(result['stats'])))
    bot.send_message(message.chat.id, '📝 Введите название для сохранения корпуса (или /skip, чтобы пропустить):')
    logger.info('[/wordcloud] Ожидаем название корпуса (user_id=%s)', user_id)
    bot.register_next_step_handler(sent, _receive_corpus_name, user_id, text, result)


@bot.message_handler(commands=['stats'])
def stats(message: telebot.types.Message) -> None:
    """/stats <text> — return brief text statistics."""
    logger.info('[/stats] user_id=%s', message.from_user.id)
    text = _get_text(message)
    if text is None:
        return

    logger.info('[/stats] Подсчёт статистики (%d симв.) для user_id=%s', len(text), message.from_user.id)
    s = analyzer.get_text_stats(text)
    reply = (
        f'📈 *Статистика текста*\n\n'
        f'  • Слов (всего): {s["total_words"]}\n'
        f'  • Уникальных слов: {s["unique_words"]}\n'
        f'  • Предложений: {s["sentences"]}\n'
        f'  • Средняя длина слова: {s["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {s["lexical_diversity"]:.2%}\n'
    )
    user_id = message.from_user.id
    db.save_corpus_text(user_id, text)
    logger.info('[/stats] Статистика отправлена user_id=%s', user_id)
    sent = bot.reply_to(message, reply, parse_mode='Markdown')
    bot.send_message(message.chat.id, '📝 Введите название для сохранения корпуса (или /skip, чтобы пропустить):')
    logger.info('[/stats] Ожидаем название корпуса (user_id=%s)', user_id)
    bot.register_next_step_handler(sent, _receive_corpus_name, user_id, text, {'stats': s})


@bot.message_handler(commands=['corpus'])
def corpus(message: telebot.types.Message) -> None:
    """/corpus — show corpus statistics for the current user."""
    logger.info('[/corpus] user_id=%s', message.from_user.id)
    user_id = message.from_user.id
    corpus_stats = db.get_corpus_stats(user_id)
    reply = (
        f'📚 *Ваш корпус*\n\n'
        f'  • Текстов сохранено: {corpus_stats["count"]}\n'
        f'  • Всего символов: {corpus_stats["total_chars"]}\n\n'
        f'Отправьте любое текстовое сообщение, чтобы добавить его в корпус.'
    )
    logger.info('[/corpus] Статистика отправлена user_id=%s: текстов=%s', user_id, corpus_stats['count'])
    bot.reply_to(message, reply, parse_mode='Markdown')


def _send_corpus_record(message: telebot.types.Message, name: str, record: dict) -> None:
    """Send the text of a named corpus record to the user."""
    text = record['combined_text']
    created_at = record['created_at']
    header = f'📄 *Корпус: {name}*\n_Сохранён: {created_at}_\n\n'
    full_message = header + text
    if len(full_message) <= 4096:
        bot.reply_to(message, full_message, parse_mode='Markdown')
    else:
        bot.reply_to(message, header, parse_mode='Markdown')
        for i in range(0, len(text), 4096):
            bot.send_message(message.chat.id, text[i:i + 4096])
    logger.info('[/load] Текст корпуса "%s" отправлен user_id=%s (%d симв.)',
                name, message.from_user.id, len(text))


def _receive_load_name(message: telebot.types.Message) -> None:
    """Next-step handler: receives the corpus name to load."""
    name = (message.text or '').strip()
    if not name or name.startswith('/'):
        logger.info('[/load] Пустое или командное название от user_id=%s, загрузка отменена',
                    message.from_user.id)
        bot.reply_to(message, '❌ Название не указано. Загрузка отменена.')
        return

    user_id = message.from_user.id
    logger.info('[/load] Поиск корпуса "%s" для user_id=%s (next-step)', name, user_id)
    record = db.get_named_analysis(user_id, name)
    if record is None:
        bot.reply_to(message, f'❌ Корпус с названием *{name}* не найден.', parse_mode='Markdown')
        return
    _send_corpus_record(message, name, record)


@bot.message_handler(commands=['load'])
def load_corpus(message: telebot.types.Message) -> None:
    """/load <название> — вернуть сохранённый текст корпуса по названию."""
    logger.info('[/load] user_id=%s', message.from_user.id)
    parts = message.text.split(maxsplit=1)
    name = parts[1].strip() if len(parts) > 1 else ''
    if not name:
        sent = bot.reply_to(message, '📝 Введите название корпуса для загрузки:')
        bot.register_next_step_handler(sent, _receive_load_name)
        return

    user_id = message.from_user.id
    logger.info('[/load] Поиск корпуса "%s" для user_id=%s', name, user_id)
    record = db.get_named_analysis(user_id, name)
    if record is None:
        bot.reply_to(
            message,
            f'❌ Корпус с названием *{name}* не найден.',
            parse_mode='Markdown',
        )
        return

    _send_corpus_record(message, name, record)


@bot.message_handler(commands=['import_texts'])
def import_texts(message: telebot.types.Message) -> None:
    """/import_texts — import all .txt files from the texts/ folder into the user's corpus."""
    logger.info('[/import_texts] user_id=%s', message.from_user.id)
    user_id = message.from_user.id

    texts_dir = 'texts'
    result = db.import_texts_from_directory(user_id, texts_dir)

    if 'error' in result:
        bot.reply_to(
            message,
            f'❌ Ошибка при доступе к папке `{texts_dir}`: {result["error"]}',
            parse_mode='Markdown',
        )
        return

    imported = result['imported']
    errors = result['errors']

    if imported == 0 and errors == 0:
        bot.reply_to(
            message,
            f'📂 В папке `{texts_dir}` не найдено .txt файлов.',
            parse_mode='Markdown',
        )
        return

    reply = f'✅ Загружено {imported} текстов'
    if errors:
        reply += f', ошибок: {errors}'
    logger.info('[/import_texts] Импорт завершён для user_id=%s: загружено=%d, ошибок=%d',
                user_id, imported, errors)
    bot.reply_to(message, reply)


@bot.message_handler(content_types=['text'])
def add_to_corpus(message: telebot.types.Message) -> None:
    """Buffer plain-text messages; after COLLECT_WINDOW seconds of inactivity,
    analyse them all together and save each one as an individual text in the
    user's corpus (corpus_texts). Plain messages are texts, not named corpora.
    """
    # Ignore messages that start with '/' (commands not matched by other handlers).
    if message.text.startswith('/'):
        logger.info('[Buffer] Неизвестная команда от user_id=%s: %s', message.from_user.id, message.text.split()[0])
        return
    text = message.text.strip()
    if not text:
        return
    if len(text) > MAX_TEXT_LENGTH:
        logger.info('[Buffer] Текст слишком длинный (%d симв.) от user_id=%s', len(text), message.from_user.id)
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
            logger.info('[Buffer] Сброс таймера для user_id=%s', user_id)
            _user_timers[user_id].cancel()
        _user_buffers.setdefault(user_id, []).append(text)
        buf_len = len(_user_buffers[user_id])
        timer = threading.Timer(COLLECT_WINDOW, _flush_user_buffer, args=(user_id, chat_id))
        timer.daemon = True
        _user_timers[user_id] = timer
        timer.start()

    logger.info('[Buffer] Сообщение добавлено в буфер (user_id=%s, всего в буфере: %d), таймер %ds запущен',
                user_id, buf_len, COLLECT_WINDOW)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _register_commands() -> None:
    """Register bot commands so they appear as menu buttons in Telegram."""
    commands = [
        telebot.types.BotCommand('start',        'Начать работу с ботом'),
        telebot.types.BotCommand('analyze',      'Статистика и частые слова текста'),
        telebot.types.BotCommand('frequency',    'Частотность слов в тексте'),
        telebot.types.BotCommand('wordcloud',    'Облако слов для текста'),
        telebot.types.BotCommand('stats',        'Краткая статистика текста'),
        telebot.types.BotCommand('corpus',       'Статистика вашего корпуса'),
        telebot.types.BotCommand('load',         'Получить текст корпуса по названию'),
        telebot.types.BotCommand('import_texts', 'Импортировать .txt файлы из папки texts/'),
    ]
    bot.set_my_commands(commands)
    logger.info('Команды меню зарегистрированы (%d команд)', len(commands))


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError('TELEGRAM_TOKEN is not set. Please configure it in your .env file.')

    logger.info('Bot is starting...')
    logger.info('Настройки: DB=%s, MAX_TEXT=%d, TOP_WORDS=%d, COLLECT_WINDOW=%ds',
                DB_FILE, MAX_TEXT_LENGTH, TOP_WORDS, COLLECT_WINDOW)
    _register_commands()
    try:
        logger.info('Запуск infinity_polling...')
        bot.infinity_polling()
    finally:
        db.close_connection()
        logger.info('Database connection closed.')


if __name__ == '__main__':
    main()

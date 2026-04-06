import itertools
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import unicodedata
import uuid
from collections import Counter
from sqlite3 import Error

import requests

import telebot

from wordcloud import WordCloud
from dotenv import load_dotenv
load_dotenv()
try:
    from uniparser_ossetic import OsseticAnalyzer as _OsseticAnalyzer
    _UNIPARSER_AVAILABLE = True
except ImportError:
    _UNIPARSER_AVAILABLE = False

try:
    from deep_translator import GoogleTranslator as _GoogleTranslator
    from deep_translator.exceptions import LanguageNotSupportedException as _LangNotSupported
    _DEEP_TRANSLATOR_AVAILABLE = True
except ImportError:
    _DEEP_TRANSLATOR_AVAILABLE = False
    _LangNotSupported = Exception  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Configuration  (previously config.py)
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN: str = os.getenv('TELEGRAM_TOKEN', '')
YANDEX_API_KEY: str = os.getenv('YANDEX_API_KEY', '')
YANDEX_IAM_TOKEN: str = os.getenv('YANDEX_IAM_TOKEN', '')
YANDEX_LLM_MODEL_URI: str = os.getenv('YANDEX_LLM_MODEL_URI', '')
YANDEX_FOLDER_ID: str = os.getenv('YANDEX_FOLDER_ID', '')
DB_FILE: str = os.getenv('DB_FILE', 'corpus_analysis.sqlite3')
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
MAX_TEXT_LENGTH: int = int(os.getenv('MAX_TEXT_LENGTH', '10000'))
TOP_WORDS: int = int(os.getenv('TOP_WORDS', '20'))
COLLECT_WINDOW: int = int(os.getenv('COLLECT_WINDOW', '3'))  # seconds
TEXTS_DIR: str = os.getenv('TEXTS_DIR', 'texts')
SEARCH_MAX_RESULTS: int = int(os.getenv('SEARCH_MAX_RESULTS', '10'))
SEARCH_SENTENCE_DISPLAY_LEN: int = 200   # max chars shown per sentence in search results
SEARCH_CONTEXT_SIZE: int = 2             # sentences of context to show before/after a match
TELEGRAM_MAX_MESSAGE_LEN: int = 4096     # Telegram hard limit for text messages
SHARED_CORPUS_USER_ID: int = 7053276138  # owner of the single shared corpus used by all users

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
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                original_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS yandex_ai_translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                original_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS yandex_ai_explanations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                explanation TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS yandex_ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_text TEXT NOT NULL,
                analysis_result TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        self._create_table(
            '''CREATE TABLE IF NOT EXISTS similarity_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query_text TEXT NOT NULL,
                results TEXT NOT NULL,
                similarity_method TEXT,
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

    def get_corpus_texts(self, user_id: int) -> list[str]:
        """Return all raw corpus texts saved for *user_id*, oldest first."""
        logger.info('[DB] Запрос текстов корпуса для user_id=%s', user_id)
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT text FROM corpus_texts WHERE user_id = ? ORDER BY id ASC',
                (user_id,),
            )
            rows = cur.fetchall()
            logger.info('[DB] Найдено %d текстов для user_id=%s', len(rows), user_id)
            return [row[0] for row in rows]
        except Error as e:
            logger.error('[DB] get_corpus_texts error: %s', e)
            return []

    def get_corpus_texts_with_names(self, user_id: int) -> list[tuple[str, str | None]]:
        """Return all raw corpus texts for *user_id* with their work names when available.

        Each element is a tuple of ``(text, name_or_None)``, ordered oldest first.
        The name is resolved from ``named_analyses`` by matching ``combined_text``
        against the stored corpus text (the most recent matching entry is used).
        """
        logger.info('[DB] Запрос текстов корпуса с названиями для user_id=%s', user_id)
        try:
            cur = self.conn.cursor()
            cur.execute(
                '''SELECT ct.text,
                          (SELECT na.name FROM named_analyses na
                           WHERE na.user_id = ct.user_id AND na.combined_text = ct.text
                           ORDER BY na.id DESC LIMIT 1) AS name
                   FROM corpus_texts ct
                   WHERE ct.user_id = ?
                   ORDER BY ct.id ASC''',
                (user_id,),
            )
            rows = cur.fetchall()
            logger.info('[DB] Найдено %d текстов с названиями для user_id=%s', len(rows), user_id)
            return [(row[0], row[1]) for row in rows]
        except Error as e:
            logger.error('[DB] get_corpus_texts_with_names error: %s', e)
            return []

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

    def search_named_analyses(self, user_id: int, query: str) -> list[dict]:
        """Return all named corpora for *user_id* whose name contains *query* (case-insensitive).

        Each element is a dict with keys ``name``, ``combined_text``,
        ``analysis_result``, ``created_at``.  The most recent entry per name
        is returned.
        """
        logger.info('[DB] Поиск произведений по запросу "%s" для user_id=%s', query, user_id)
        sql = (
            'SELECT name, combined_text, analysis_result, created_at '
            'FROM named_analyses WHERE user_id = ? '
            'ORDER BY id DESC'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id,))
            rows = cur.fetchall()
            q = query.lower()
            seen: set[str] = set()
            results: list[dict] = []
            for row in rows:
                name = row[0]
                if name in seen:
                    continue
                seen.add(name)
                if q in name.lower():
                    results.append({
                        'name': name,
                        'combined_text': row[1],
                        'analysis_result': row[2],
                        'created_at': row[3],
                    })
            logger.info('[DB] Найдено %d произведений по запросу "%s" для user_id=%s',
                        len(results), query, user_id)
            return results
        except Error as e:
            logger.error('[DB] search_named_analyses error: %s', e)
            return []

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

    def save_translation(self, user_id: int, source_lang: str, target_lang: str,
                         original_text: str, translated_text: str) -> int | None:
        """Persist a translation record. Returns the new row id."""
        logger.info('[DB] Сохранение перевода для user_id=%s (%s → %s)', user_id, source_lang, target_lang)
        sql = (
            'INSERT INTO translations(user_id, source_lang, target_lang, original_text, translated_text) '
            'VALUES(?, ?, ?, ?, ?)'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, source_lang, target_lang, original_text, translated_text))
            self.conn.commit()
            logger.info('[DB] Перевод сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] save_translation error: %s', e)
            return None

    def get_translations(self, user_id: int) -> list[dict]:
        """Return all translations for *user_id*, most recent first."""
        logger.info('[DB] Запрос переводов для user_id=%s', user_id)
        sql = (
            'SELECT source_lang, target_lang, original_text, translated_text, created_at '
            'FROM translations WHERE user_id = ? ORDER BY id DESC'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id,))
            rows = cur.fetchall()
            logger.info('[DB] Найдено %d переводов для user_id=%s', len(rows), user_id)
            return [
                {
                    'source_lang': row[0],
                    'target_lang': row[1],
                    'original_text': row[2],
                    'translated_text': row[3],
                    'created_at': row[4],
                }
                for row in rows
            ]
        except Error as e:
            logger.error('[DB] get_translations error: %s', e)
            return []

    def get_yandex_ai_translation_cache(self, original_text: str, source_lang: str,
                                         target_lang: str) -> str | None:
        """Look up a cached Yandex AI translation. Returns translated text or None."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT translated_text FROM yandex_ai_translations '
                'WHERE original_text = ? AND source_lang = ? AND target_lang = ? '
                'ORDER BY id DESC LIMIT 1',
                (original_text, source_lang, target_lang),
            )
            row = cur.fetchone()
            if row:
                logger.info('[DB] Кеш YAI-перевода найден (%s→%s)', source_lang, target_lang)
                return row[0]
            return None
        except Error as e:
            logger.error('[DB] get_yandex_ai_translation_cache error: %s', e)
            return None

    def save_yandex_ai_translation_cache(self, original_text: str, source_lang: str,
                                          target_lang: str, translated_text: str) -> None:
        """Save a Yandex AI translation result to the cache."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'INSERT INTO yandex_ai_translations(source_lang, target_lang, original_text, translated_text) '
                'VALUES(?, ?, ?, ?)',
                (source_lang, target_lang, original_text, translated_text),
            )
            self.conn.commit()
            logger.info('[DB] YAI-перевод закеширован (%s→%s)', source_lang, target_lang)
        except Error as e:
            logger.error('[DB] save_yandex_ai_translation_cache error: %s', e)

    def get_yandex_ai_explanation_cache(self, word: str) -> str | None:
        """Look up a cached word explanation. Returns explanation text or None."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT explanation FROM yandex_ai_explanations WHERE word = ? LIMIT 1',
                (word.lower(),),
            )
            row = cur.fetchone()
            if row:
                logger.info('[DB] Кеш объяснения слова "%s" найден', word)
                return row[0]
            return None
        except Error as e:
            logger.error('[DB] get_yandex_ai_explanation_cache error: %s', e)
            return None

    def save_yandex_ai_explanation_cache(self, word: str, explanation: str) -> None:
        """Save a word explanation to the cache (upsert by word)."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'INSERT INTO yandex_ai_explanations(word, explanation) VALUES(?, ?) '
                'ON CONFLICT(word) DO UPDATE SET explanation=excluded.explanation, '
                'created_at=CURRENT_TIMESTAMP',
                (word.lower(), explanation),
            )
            self.conn.commit()
            logger.info('[DB] Объяснение слова "%s" закешировано', word)
        except Error as e:
            logger.error('[DB] save_yandex_ai_explanation_cache error: %s', e)

    def save_yandex_ai_analysis(self, user_id: int, original_text: str,
                                 analysis_result: str) -> int | None:
        """Save a Yandex AI text analysis result. Returns the new row id."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                'INSERT INTO yandex_ai_analyses(user_id, original_text, analysis_result) '
                'VALUES(?, ?, ?)',
                (user_id, original_text, analysis_result),
            )
            self.conn.commit()
            logger.info('[DB] YAI-анализ сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] save_yandex_ai_analysis error: %s', e)
            return None

    def import_texts_from_directory(self, user_id: int, texts_dir: str = 'texts',
                                    analyzer=None) -> dict:
        """Import all .txt files from *texts_dir* into ``corpus_texts`` for *user_id*.

        Each file is saved as a raw corpus text.  When *analyzer* is provided
        (a :class:`TextAnalyzer` instance), the file is also saved as a named
        corpus entry in ``named_analyses`` using the filename stem (filename
        without the ``.txt`` extension) as the corpus name.

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
                # Save the filename stem as a named corpus entry when an analyzer
                # is available, so texts can be retrieved later by /load <name>.
                if analyzer is not None:
                    name = os.path.splitext(filename)[0]
                    analysis_result = analyzer.analyze(text)
                    self.save_named_analysis(
                        user_id, name, text, json.dumps(analysis_result)
                    )
                    logger.info('[DB] Файл "%s" сохранён как корпус "%s"', filename, name)
                imported += 1
                logger.info('[DB] Файл "%s" успешно импортирован', filename)
            except (OSError, UnicodeDecodeError) as e:
                errors += 1
                logger.error('[DB] Ошибка при импорте файла "%s": %s', filename, e)

        logger.info('[DB] Импорт завершён: успешно=%d, ошибок=%d', imported, errors)
        return {'imported': imported, 'errors': errors}

    def save_similarity_search(self, user_id: int, query_text: str,
                                results: str, similarity_method: str = 'combined') -> int | None:
        """Save a similarity search result to the cache. Returns the new row id."""
        logger.info('[DB] Сохранение поиска похожих для user_id=%s', user_id)
        sql = (
            'INSERT INTO similarity_searches(user_id, query_text, results, similarity_method) '
            'VALUES(?, ?, ?, ?)'
        )
        try:
            cur = self.conn.cursor()
            cur.execute(sql, (user_id, query_text, results, similarity_method))
            self.conn.commit()
            logger.info('[DB] Поиск похожих сохранён, row_id=%s', cur.lastrowid)
            return cur.lastrowid
        except Error as e:
            logger.error('[DB] save_similarity_search error: %s', e)
            return None

# ---------------------------------------------------------------------------
# Text analyser  (previously text_analyzer.py)
# ---------------------------------------------------------------------------

# Common Ossetian (Iron dialect) stopwords — function words, pronouns,
# particles, conjunctions, and frequent auxiliary verb forms.
# Extended with additional function words from the Ossetian National Corpus
# and the uniparser-ossetic grammar base.
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
    # Additional function words from Ossetian corpus / uniparser grammar base
    # Interrogative / relative pronouns and adverbs
    'кæцы', 'кæд', 'кæдæм', 'кæцæй', 'кæй', 'цавæр', 'цыфæндый',
    # Demonstrative pronouns (oblique forms)
    'уымæн', 'уымæ', 'уымæй', 'уыцыты', 'ацытæ', 'уыдонæн',
    # Reflexive / intensifying particles
    'хæдæг', 'хæдæгæй', 'иууылдæр', 'иууыл',
    # Modal particles and discourse markers
    'зæгъгæ', 'æнæмæнг', 'æцæг', 'æгæр', 'раст', 'хорз',
    # Prepositions and postpositions
    'йедтæмæ', 'иннæмæ', 'тыххæй', 'хæццæ', 'сæрмæ',
    # Coordinating / subordinating conjunctions
    'кæнæ', 'æрмæстдæр', 'афтæмæй', 'уæдæ', 'цæмæй',
    # High-frequency verb auxiliaries (copula inflections)
    'ис', 'нæй', 'уыдаид', 'уаид', 'æрцыд', 'æрцыдысты',
    # Numerals used as function words
    'иу', 'дыуæ', 'æртæ',
    # Common adverbs
    'афтæ', 'уæлæ', 'дæлæ', 'ардæм', 'уырдæм', 'æнæхъæн',
}

# Sentence boundary: split on . ! ? followed by whitespace or end-of-string.
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
# Token: sequences of Unicode letters (including æ / Æ) or digits.
_TOKEN_RE = re.compile(r'[^\W\d_]+|\d+', re.UNICODE)


class TextAnalyzer:
    def __init__(self):
        self.stop_words = _OSSETIAN_STOPWORDS
        # Try to initialise the Ossetian Uniparser for real morphological analysis.
        self._uniparser = None
        if _UNIPARSER_AVAILABLE:
            try:
                self._uniparser = _OsseticAnalyzer()
                logger.info('[Analyzer] OsseticAnalyzer (uniparser-ossetic) загружен')
            except Exception as exc:  # noqa: BLE001
                logger.warning('[Analyzer] Не удалось загрузить OsseticAnalyzer: %s — используется fallback', exc)
        else:
            logger.warning('[Analyzer] uniparser-ossetic недоступен — используется fallback-лемматизация')
        logger.info('[Analyzer] TextAnalyzer инициализирован (%d стоп-слов)', len(self.stop_words))

    def tokenize(self, text):
        tokens = _TOKEN_RE.findall(text.lower())
        logger.info('[Analyzer] Токенизация: %d токенов', len(tokens))
        return tokens

    def lemmatize(self, tokens):
        """Return the lemma for each token.

        Uses OsseticAnalyzer (uniparser-ossetic) when available.  Falls back to
        returning each token in lowercase when the parser is not installed or
        fails to analyse a particular token.
        """
        if self._uniparser is None:
            logger.info(
                '[Analyzer] Лемматизация (fallback): %d токенов → %d уникальных лемм',
                len(tokens), len(set(tokens)),
            )
            return tokens

        lemmas = []
        for token in tokens:
            try:
                analyses = self._uniparser.analyze_words(token)
                if analyses and hasattr(analyses[0], 'lemma') and analyses[0].lemma:
                    lemmas.append(analyses[0].lemma.lower())
                else:
                    lemmas.append(token)
            except Exception:  # noqa: BLE001
                lemmas.append(token)
        logger.info(
            '[Analyzer] Лемматизация (uniparser): %d токенов → %d уникальных лемм',
            len(tokens), len(set(lemmas)),
        )
        return lemmas

    def get_morphological_info(self, word: str) -> list[dict]:
        """Return full morphological data for *word* from UniParser.

        Each element in the returned list represents one analysis variant
        (to cover homonyms) and contains:
          - ``word``     – the original word form queried
          - ``lemma``    – base (dictionary) form
          - ``pos``      – part of speech (first tag in the gramm string)
          - ``gramm``    – full grammatical tag string as returned by UniParser
          - ``features`` – list of individual grammatical features (remaining tags)

        Returns an empty list when UniParser is unavailable or the word is unknown.
        """
        if self._uniparser is None:
            logger.info('[Analyzer] Морфоанализ недоступен (uniparser не загружен): %s', word)
            return []
        try:
            analyses = self._uniparser.analyze_words(word)
        except Exception:  # noqa: BLE001
            logger.warning('[Analyzer] Ошибка морфоанализа слова "%s"', word)
            return []

        results = []
        for analysis in analyses:
            lemma = getattr(analysis, 'lemma', '') or ''
            gramm = getattr(analysis, 'gramm', '') or ''
            tags = [t.strip() for t in gramm.split(',') if t.strip()]
            pos = tags[0] if tags else ''
            features = tags[1:] if len(tags) > 1 else []
            results.append({
                'word': word,
                'lemma': lemma.lower(),
                'pos': pos,
                'gramm': gramm,
                'features': features,
            })
        logger.info(
            '[Analyzer] Морфоанализ "%s": %d вариантов, POS=%s',
            word, len(results), results[0]['pos'] if results else '—',
        )
        return results

    def get_pos_distribution(self, text: str) -> dict[str, int]:
        """Return a POS-tag frequency distribution for all tokens in *text*.

        Tokenises *text*, analyses each token with UniParser, and counts the
        first (part-of-speech) tag.  Tokens that can't be analysed are counted
        under the key ``'?'``.

        Returns an empty dict when UniParser is unavailable.
        """
        if self._uniparser is None:
            logger.info('[Analyzer] POS-распределение недоступно (uniparser не загружен)')
            return {}

        tokens = self.tokenize(text)
        pos_counter: Counter = Counter()
        for token in tokens:
            info_list = self.get_morphological_info(token)
            pos = (info_list[0]['pos'] or '?') if info_list else '?'
            pos_counter[pos] += 1
        logger.info(
            '[Analyzer] POS-распределение: %d токенов, %d категорий',
            len(tokens), len(pos_counter),
        )
        return dict(pos_counter.most_common())

    def get_gramm_distribution(self, text: str) -> dict[str, int]:
        """Return a frequency distribution of full grammatical tag strings in *text*.

        Like :meth:`get_pos_distribution` but counts the complete gramm string
        (e.g. ``"N,m,sg,gen"`` rather than just ``"N"``).

        Returns an empty dict when UniParser is unavailable.
        """
        if self._uniparser is None:
            logger.info('[Analyzer] Gramm-распределение недоступно (uniparser не загружен)')
            return {}

        tokens = self.tokenize(text)
        gramm_counter: Counter = Counter()
        for token in tokens:
            info_list = self.get_morphological_info(token)
            gramm = (info_list[0]['gramm'] or '?') if info_list else '?'
            gramm_counter[gramm] += 1
        logger.info(
            '[Analyzer] Gramm-распределение: %d токенов, %d уникальных форм',
            len(tokens), len(gramm_counter),
        )
        return dict(gramm_counter.most_common())

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
# Translator  (machine translation via deep-translator / Google Translate)
# ---------------------------------------------------------------------------

class Translator:
    """Translate text using Google Translate (via deep-translator) with Yandex as fallback.

    Falls back gracefully when the *deep-translator* library is not installed.
    Languages unsupported by Google (e.g. Ossetian) are routed to Yandex Translate API v2.
    Long texts are automatically split into chunks to stay within the API limit.
    """

    _CHUNK_SIZE = 4500  # safe margin below the 5000-char Google Translate limit
    # Languages that Google Translate does not support — route directly to Yandex.
    _YANDEX_LANGUAGES: frozenset[str] = frozenset({'os'})
    # Free widget endpoint — works without any API key.
    _YANDEX_FREE_URL: str = 'https://translate.yandex.net/api/v1/tr.json/translate'
    # Official Cloud Translate API v2 — used only when YANDEX_API_KEY is set.
    _YANDEX_CLOUD_URL: str = 'https://translate.api.cloud.yandex.net/translate/v2/translate'

    def translate(self, text: str, target_lang: str, source_lang: str = 'auto') -> str:
        """Return *text* translated into *target_lang*.

        Routing logic:
        - If source or target is in *_YANDEX_LANGUAGES*, use Yandex directly.
        - Otherwise try Google; on ``LanguageNotSupportedException`` fall back to Yandex.

        Yandex translation uses the free widget endpoint by default (no API key required).
        Set ``YANDEX_API_KEY`` in the environment to use the official Cloud API instead.
        Raises :class:`ValueError` for an unsupported language, and any exception
        propagated from the underlying API call.
        """
        use_yandex = (
            source_lang in self._YANDEX_LANGUAGES
            or target_lang in self._YANDEX_LANGUAGES
        )
        logger.info('[Translator] Перевод текста (%d симв.) на "%s"', len(text), target_lang)
        chunks = self._split_text(text)
        if use_yandex:
            translated_chunks = [self._translate_yandex(chunk, target_lang, source_lang)
                                  for chunk in chunks]
        else:
            if not _DEEP_TRANSLATOR_AVAILABLE:
                raise RuntimeError('deep-translator не установлен')
            try:
                translated_chunks = [self._translate_google(chunk, target_lang, source_lang)
                                     for chunk in chunks]
            except _LangNotSupported:
                logger.warning(
                    '[Translator] Google не поддерживает язык "%s", переключаемся на Yandex',
                    target_lang,
                )
                translated_chunks = [self._translate_yandex(chunk, target_lang, source_lang)
                                     for chunk in chunks]
        result = '\n'.join(translated_chunks)
        logger.info('[Translator] Перевод завершён (%d симв.)', len(result))
        return result

    def _translate_google(self, text: str, target_lang: str, source_lang: str) -> str:
        """Translate a single chunk via Google Translate (deep-translator)."""
        t = _GoogleTranslator(source=source_lang, target=target_lang)
        return t.translate(text) or ''

    def _translate_yandex(self, text: str, target_lang: str, source_lang: str) -> str:
        """Translate a single chunk via Yandex Translate.

        Uses the free widget endpoint by default (no API key required).
        If ``YANDEX_API_KEY`` is configured, the official Cloud Translate API v2
        is used instead for higher reliability and rate limits.
        Raises :class:`RuntimeError` when the request fails.
        """
        if YANDEX_API_KEY:
            return self._translate_yandex_cloud(text, target_lang, source_lang)
        return self._translate_yandex_free(text, target_lang, source_lang)

    def _translate_yandex_free(self, text: str, target_lang: str, source_lang: str) -> str:
        """Translate via the free Yandex widget endpoint (no API key needed)."""
        lang = (
            f'{source_lang}-{target_lang}'
            if source_lang and source_lang != 'auto'
            else target_lang
        )
        params = {
            'id': uuid.uuid4().hex + '-0-0',
            'srv': 'yabrowser',
            'lang': lang,
            'text': text,
        }
        try:
            response = requests.get(
                self._YANDEX_FREE_URL, params=params, timeout=15
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f'Yandex Translate запрос не удался: {exc}') from exc
        data = response.json()
        texts = data.get('text', [])
        if not texts:
            raise RuntimeError('Yandex Translate вернул пустой результат')
        return texts[0]

    def _translate_yandex_cloud(self, text: str, target_lang: str, source_lang: str) -> str:
        """Translate via the official Yandex Cloud Translate API v2 (requires YANDEX_API_KEY)."""
        headers = {
            'Authorization': f'Api-Key {YANDEX_API_KEY}',
            'Content-Type': 'application/json',
        }
        body: dict = {'texts': [text], 'targetLanguageCode': target_lang}
        if YANDEX_FOLDER_ID:
            body['folderId'] = YANDEX_FOLDER_ID
        if source_lang and source_lang != 'auto':
            body['sourceLanguageCode'] = source_lang
        try:
            response = requests.post(
                self._YANDEX_CLOUD_URL, headers=headers, json=body, timeout=15
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f'Yandex Cloud Translate запрос не удался: {exc}') from exc
        data = response.json()
        translations = data.get('translations', [])
        if not translations:
            raise RuntimeError('Yandex Cloud Translate вернул пустой результат')
        translated_text = translations[0].get('text')
        if translated_text is None:
            raise RuntimeError('Yandex Cloud Translate не вернул текст перевода')
        return translated_text

    def get_supported_languages(self) -> dict[str, str]:
        """Return a dict mapping language name to language code."""
        if not _DEEP_TRANSLATOR_AVAILABLE:
            return {}
        return _GoogleTranslator().get_supported_languages(as_dict=True)  # type: ignore[return-value]

    def _split_text(self, text: str) -> list[str]:
        """Split *text* into chunks of at most _CHUNK_SIZE characters,
        preferring paragraph or sentence boundaries."""
        if len(text) <= self._CHUNK_SIZE:
            return [text]
        chunks: list[str] = []
        while text:
            if len(text) <= self._CHUNK_SIZE:
                chunks.append(text)
                break
            # Try to split at a paragraph boundary first, then sentence.
            split_at = text.rfind('\n\n', 0, self._CHUNK_SIZE)
            if split_at == -1:
                split_at = text.rfind('\n', 0, self._CHUNK_SIZE)
            if split_at == -1:
                split_at = text.rfind('. ', 0, self._CHUNK_SIZE)
            if split_at == -1:
                split_at = self._CHUNK_SIZE
            chunks.append(text[:split_at].strip())
            text = text[split_at:].strip()
        return [c for c in chunks if c]

# ---------------------------------------------------------------------------
# Yandex LLM (Yandex.GPT) client
# ---------------------------------------------------------------------------

class YandexLLMClient:
    """Client for Yandex Foundation Models (Yandex.GPT) API.

    Authentication priority:
    1. ``YANDEX_IAM_TOKEN`` — IAM token (short-lived, recommended for production)
    2. ``YANDEX_API_KEY``   — API key (same key used by Yandex Translate)

    Model URI is resolved from ``YANDEX_LLM_MODEL_URI`` if set, otherwise
    constructed from ``YANDEX_FOLDER_ID`` using the ``yandexgpt-lite`` model.
    """

    _URL = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'

    def __init__(self) -> None:
        self._iam_token = YANDEX_IAM_TOKEN
        self._api_key = YANDEX_API_KEY
        self._model_uri = YANDEX_LLM_MODEL_URI
        self._folder_id = YANDEX_FOLDER_ID
        self.available: bool = bool(self._iam_token or self._api_key)
        if self.available:
            logger.info('[YandexLLM] Клиент инициализирован (IAM=%s, API_KEY=%s)',
                        bool(self._iam_token), bool(self._api_key))
        else:
            logger.warning('[YandexLLM] Токен аутентификации не задан — функции ИИ недоступны')

    def _get_model_uri(self) -> str:
        if self._model_uri:
            return self._model_uri
        if self._folder_id:
            return f'gpt://{self._folder_id}/yandexgpt-lite'
        # Fallback: use a public lite model URI placeholder that callers can override.
        return ''

    def complete(self, system_prompt: str, user_text: str,
                 temperature: float = 0.5, max_tokens: int = 2000) -> str:
        """Send a prompt to Yandex.GPT and return the text response.

        Raises :class:`RuntimeError` when authentication or model URI is missing,
        or when the API request fails.
        """
        if not self.available:
            raise RuntimeError(
                'Yandex LLM недоступен: задайте YANDEX_IAM_TOKEN или YANDEX_API_KEY в .env'
            )
        model_uri = self._get_model_uri()
        if not model_uri:
            raise RuntimeError(
                'Yandex LLM: URI модели не задан — укажите YANDEX_LLM_MODEL_URI '
                'или YANDEX_FOLDER_ID в .env'
            )

        headers: dict[str, str] = {'Content-Type': 'application/json'}
        if self._iam_token:
            headers['Authorization'] = f'Bearer {self._iam_token}'
        else:
            headers['Authorization'] = f'Api-Key {self._api_key}'

        body = {
            'modelUri': model_uri,
            'completionOptions': {
                'stream': False,
                'temperature': temperature,
                'maxTokens': str(max_tokens),
            },
            'messages': [
                {'role': 'system', 'text': system_prompt},
                {'role': 'user', 'text': user_text},
            ],
        }
        logger.info('[YandexLLM] Запрос к API (model=%s, max_tokens=%d)', model_uri, max_tokens)
        try:
            response = requests.post(self._URL, headers=headers, json=body, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f'Yandex LLM запрос не удался: {exc}') from exc

        data = response.json()
        try:
            text = data['result']['alternatives'][0]['message']['text']
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f'Yandex LLM неожиданный формат ответа: {data}') from exc
        logger.info('[YandexLLM] Ответ получен (%d симв.)', len(text))
        return text


# ---------------------------------------------------------------------------
# Bot globals
# ---------------------------------------------------------------------------
db = Database(DB_FILE)
analyzer = TextAnalyzer()
vis = DataVisualizer()
translator = Translator()
yandex_llm = YandexLLMClient()
if not TELEGRAM_TOKEN:
    logger.warning('TELEGRAM_TOKEN не задан — бот не сможет подключиться к Telegram.')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ---------------------------------------------------------------------------
# Startup notification
# ---------------------------------------------------------------------------
_STARTUP_NOTIFY_USER_ID: int = 6117734481
_STARTUP_NOTIFY_MESSAGES: list[str] = [
    # English
    'Dear Lali, Yuri asked me to tell you that he loves you!',
    # French
    "Chère Lali, Yuri m'a demandé de te dire qu'il t'aime !",
    # Spanish
    'Querida Lali, Yuri me pidió que te dijera que te ama.',
    # German
    'Liebe Lali, Yuri hat mich gebeten, dir zu sagen, dass er dich liebt!',
    # Italian
    'Cara Lali, Yuri mi ha chiesto di dirti che ti ama!',
    # Portuguese
    'Querida Lali, Yuri pediu para te dizer que te ama!',
    # Dutch
    'Lieve Lali, Yuri vroeg me om te zeggen dat hij van je houdt!',
    # Ukrainian
    'Дорога Лалі, Юрій просив передати, що любить тебе!',
    # Polish
    'Droga Lali, Yuri prosił przekazać, że cię kocha!',
    # Czech
    'Milá Lali, Yuri mě požádal, abych ti řekl, že tě miluje!',
    # Serbian
    'Драга Лали, Јуриј је замолио да ти пренесем да те воли!',
    # Georgian
    'ძვირფასო ლალი, იურიმ მთხოვა გადმომეცა, რომ უყვარხარ!',
    # Armenian
    'Սիրելի Լալի, Յուրին խնդրեց փոխանցել, որ սիրում է քեզ։',
    # Persian
    'لالی عزیز، یوری خواست بگویم که دوستت دارد!',
    # Arabic
    'عزيزتي لالي، طلب يوري أن أخبرك أنه يحبك!',
    # Turkish
    'Sevgili Lali, Yuri senden sana onu sevdiğini söylememi istedi!',
    # Hebrew
    'לאלי היקרה, יורי ביקש למסור שהוא אוהב אותך!',
    # Chinese
    '亲爱的拉莉，尤里让我转告你，他爱你！',
    # Japanese
    '親愛なるラリへ、ユーリが君を愛していると伝えてほしいと言っていました！',
    # Korean
    '사랑하는 라리, 유리가 당신을 사랑한다고 전해달라고 했어요!',
    # Hindi
    'प्रिय लाली, यूरी ने कहा है कि वह तुमसे प्यार करता है!',
    # Swahili
    'Mpenzi Lali, Yuri aliniomba nikuambie kwamba anakupenda!',
    # Latin
    'Cara Lali, Yuri rogavit ut tibi dicam se te amare!',
]


def _get_random_startup_message() -> str:
    """Return a random startup notification message from the multilingual list."""
    return random.choice(_STARTUP_NOTIFY_MESSAGES)

# ---------------------------------------------------------------------------
# Per-user message-collection state (for the 3-second analysis window)
# ---------------------------------------------------------------------------

_user_buffers: dict[int, list[str]] = {}   # user_id -> buffered texts
_user_timers: dict[int, threading.Timer] = {}  # user_id -> active debounce timer
_buffer_lock = threading.Lock()
_auto_collect_enabled: set[int] = set()   # user_ids with auto-collect enabled (off by default)


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


def _escape_html(text: str) -> str:
    """Escape special HTML characters to prevent parse errors."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _is_owner(message: telebot.types.Message) -> bool:
    """Return True if the message was sent by the corpus owner."""
    return message.from_user.id == SHARED_CORPUS_USER_ID


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
        logger.info('[Buffer] Сохранение текста %d/%d в общий корпус (от user_id=%s)', i, len(texts), user_id)
        db.save_corpus_text(SHARED_CORPUS_USER_ID, t)

    combined_text = '\n'.join(texts)
    logger.info('[Buffer] Запуск анализа объединённого текста (%d симв.)', len(combined_text))
    result = analyzer.analyze(combined_text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])
    logger.info('[Buffer] Анализ завершён, отправка результатов в chat_id=%s', chat_id)

    n = len(texts)
    msg_form = _ru_plural(n, 'сообщение', 'сообщения', 'сообщений')
    reply = (
        f'📊 <b>Анализ {n} {msg_form}</b>\n\n'
        f'<b>Статистика:</b>\n'
        f'  • Слов (всего): {stats["total_words"]}\n'
        f'  • Уникальных слов: {stats["unique_words"]}\n'
        f'  • Предложений: {stats["sentences"]}\n'
        f'  • Средняя длина слова: {stats["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {stats["lexical_diversity"]:.2%}\n'
        f'  • Токенов: {result["tokens_count"]}\n'
        f'  • Уникальных лемм: {result["lemmas_count"]}\n\n'
        f'<b>Топ {TOP_WORDS} слов:</b>\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'

    _send_long_message(chat_id, reply, parse_mode='HTML')
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

    db.save_named_analysis(SHARED_CORPUS_USER_ID, name, combined_text,
                           json.dumps({'stats': result['stats'], 'frequency': result.get('frequency', {})}))
    logger.info('[Buffer] Корпус "%s" успешно сохранён в общий корпус (запрос от user_id=%s)', name, user_id)
    bot.reply_to(message, f'✅ Корпус <b>{_escape_html(name)}</b> сохранён!', parse_mode='HTML')

# ---------------------------------------------------------------------------
# Message-splitting helpers
# ---------------------------------------------------------------------------

def _split_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters.

    Tries to break at double newlines, then single newlines, then spaces,
    falling back to a hard character-boundary split as a last resort.
    Returns a list with at least one element.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while len(text) > max_len:
        # Prefer breaking at a double newline.
        split_pos = text.rfind('\n\n', 0, max_len)
        if split_pos > 0:
            chunks.append(text[:split_pos])
            text = text[split_pos + 2:]
            continue
        # Fall back to a single newline.
        split_pos = text.rfind('\n', 0, max_len)
        if split_pos > 0:
            chunks.append(text[:split_pos])
            text = text[split_pos + 1:]
            continue
        # Fall back to a space.
        split_pos = text.rfind(' ', 0, max_len)
        if split_pos > 0:
            chunks.append(text[:split_pos])
            text = text[split_pos + 1:]
            continue
        # Hard split as a last resort.
        chunks.append(text[:max_len])
        text = text[max_len:]

    if text:
        chunks.append(text)
    return chunks


def _send_long_message(
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    *,
    reply_to_message: telebot.types.Message | None = None,
    reply_markup=None,
) -> None:
    """Send *text* to *chat_id*, splitting it into chunks when it exceeds Telegram's limit.

    If *reply_to_message* is provided the first chunk is sent as a reply to that
    message; all subsequent chunks are sent as plain messages.  *reply_markup* is
    attached to the last chunk so that action buttons always appear at the bottom.
    """
    chunks = _split_message(text)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        kwargs: dict = {}
        if parse_mode:
            kwargs['parse_mode'] = parse_mode
        if is_last and reply_markup is not None:
            kwargs['reply_markup'] = reply_markup
        if i == 0 and reply_to_message is not None:
            bot.reply_to(reply_to_message, chunk, **kwargs)
        else:
            bot.send_message(chat_id, chunk, **kwargs)


# ---------------------------------------------------------------------------
# Bot handlers  (previously bot.py)
# ---------------------------------------------------------------------------


@bot.message_handler(commands=['start'])
def start(message: telebot.types.Message) -> None:
    """Send a welcome message with a command menu keyboard."""
    logger.info('[/start] user_id=%s (@%s)', message.from_user.id, message.from_user.username)

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        telebot.types.KeyboardButton('📊 Анализ'),
        telebot.types.KeyboardButton('📈 Частота'),
        telebot.types.KeyboardButton('☁️ Облако'),
        telebot.types.KeyboardButton('📋 Статистика'),
        telebot.types.KeyboardButton('📚 Корпус'),
        telebot.types.KeyboardButton('📂 Загрузить'),
        telebot.types.KeyboardButton('📥 Импорт'),
        telebot.types.KeyboardButton('🔄 Автосбор'),
        telebot.types.KeyboardButton('🔎 Поиск'),
        telebot.types.KeyboardButton('🔍 Похожие'),
        telebot.types.KeyboardButton('🔬 Морфо'),
        telebot.types.KeyboardButton('🌐 Переводчик'),
        telebot.types.KeyboardButton('🤖 Яндекс ИИ'),
    )

    bot.reply_to(
        message,
        '👋 Добро пожаловать в <b>Бот анализа корпуса</b>!\n\n'
        'Бот анализирует общий корпус текстов.\n\n'
        'Как пополнить корпус:\n'
        '  Включите автосбор командой /collect (или кнопкой 🔄 Автосбор) — '
        'по умолчанию он <b>отключён</b>. '
        f'Когда автосбор включён, каждое текстовое сообщение через {COLLECT_WINDOW} '
        f'{_ru_plural(COLLECT_WINDOW, "секунду", "секунды", "секунд")} '
        'после отправки автоматически сохраняется в общий корпус.\n\n'
        'Команды анализа корпуса:\n'
        '  /analyze — статистика + самые частые слова корпуса\n'
        '  /frequency — частотность слов в корпусе\n'
        '  /wordcloud — облако слов корпуса\n'
        '  /stats — краткая статистика корпуса\n'
        '  /corpus — размер общего корпуса\n'
        '  /load [название] — найти произведение по названию и получить текст целиком\n'
        '  /import_texts — импортировать .txt файлы из папки texts/\n'
        '  /collect — включить/выключить автосбор текстовых сообщений\n'
        '  /search &lt;слово&gt; — найти предложения с нужным словом в корпусе\n'
        '  /similar &lt;текст&gt; — найти семантически похожие предложения в корпусе\n'
        '  /morph &lt;слово&gt; — морфологический анализ слова\n'
        '  /morph_stats — статистика частей речи в корпусе\n'
        '  /morph_freq — частота грамматических форм в корпусе\n'
        '  /translate [язык] — перевести корпус на указанный язык\n\n'
        '🤖 <b>Яндекс ИИ</b> — умный перевод и анализ текста без команд:\n'
        '  Нажмите кнопку <b>🤖 Яндекс ИИ</b> и выберите операцию:\n'
        '  ✨ Перевести слово/предложение (Осетинский ↔ Русский)\n'
        '  📚 Объяснить слово\n'
        '  🎯 Анализировать текст',
        parse_mode='HTML',
        reply_markup=markup,
    )


def _get_user_corpus_text(message: telebot.types.Message) -> str | None:
    """Retrieve and join all shared corpus texts.

    Returns the combined text, or *None* if the corpus is empty (and sends an
    appropriate reply to the user in that case).
    """
    texts = db.get_corpus_texts(SHARED_CORPUS_USER_ID)
    if not texts:
        logger.info('[Corpus] Общий корпус пуст')
        bot.reply_to(
            message,
            '📭 Общий корпус пуст. Отправьте несколько текстовых сообщений, '
            'чтобы наполнить его, а затем повторите команду.',
            parse_mode='HTML',
        )
        return None
    return '\n'.join(texts)


@bot.message_handler(commands=['analyze'])
def analyze(message: telebot.types.Message) -> None:
    """/analyze — run full analysis on the user's corpus."""
    logger.info('[/analyze] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/analyze] Анализ корпуса (%d симв.) для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])

    reply = (
        f'📊 <b>Результаты анализа корпуса</b>\n\n'
        f'<b>Статистика:</b>\n'
        f'  • Слов (всего): {stats["total_words"]}\n'
        f'  • Уникальных слов: {stats["unique_words"]}\n'
        f'  • Предложений: {stats["sentences"]}\n'
        f'  • Средняя длина слова: {stats["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {stats["lexical_diversity"]:.2%}\n'
        f'  • Токенов: {result["tokens_count"]}\n'
        f'  • Уникальных лемм: {result["lemmas_count"]}\n\n'
        f'<b>Топ {TOP_WORDS} слов:</b>\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'

    logger.info('[/analyze] Результаты отправлены user_id=%s', user_id)
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)


@bot.message_handler(commands=['frequency'])
def frequency(message: telebot.types.Message) -> None:
    """/frequency — send top word frequencies for the user's corpus."""
    logger.info('[/frequency] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/frequency] Анализ частотности корпуса (%d симв.) для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        logger.info('[/frequency] Нет слов для анализа (user_id=%s)', user_id)
        bot.reply_to(message, 'Нет слов для частотного анализа.')
        return

    top = sorted(freq_dict.items(), key=lambda x: x[1], reverse=True)[:20]
    lines = ['📊 <b>Частота слов корпуса:</b>\n\n']
    for word, count in top:
        lines.append(f'  {word}: {count}\n')
    logger.info('[/frequency] Отправка топ-%d слов для user_id=%s', len(top), user_id)
    _send_long_message(message.chat.id, ''.join(lines), parse_mode='HTML', reply_to_message=message)


@bot.message_handler(commands=['wordcloud'])
def wordcloud(message: telebot.types.Message) -> None:
    """/wordcloud — generate a word cloud from the user's corpus."""
    logger.info('[/wordcloud] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/wordcloud] Генерация облака слов корпуса (%d симв.) для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        logger.info('[/wordcloud] Нет слов для облака (user_id=%s)', user_id)
        bot.reply_to(message, 'Нет слов для создания облака слов.')
        return

    image_path = vis.plot_word_cloud(freq_dict, title='Облако слов корпуса')
    try:
        logger.info('[/wordcloud] Отправка изображения %s для user_id=%s', image_path, user_id)
        with open(image_path, 'rb') as img:
            sent = bot.send_photo(message.chat.id, img, caption='Облако слов корпуса')
    finally:
        os.unlink(image_path)
        logger.info('[/wordcloud] Временный файл удалён: %s', image_path)


@bot.message_handler(commands=['stats'])
def stats(message: telebot.types.Message) -> None:
    """/stats — return brief statistics for the user's corpus."""
    logger.info('[/stats] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/stats] Подсчёт статистики корпуса (%d симв.) для user_id=%s', len(text), user_id)
    s = analyzer.get_text_stats(text)
    reply = (
        f'📈 <b>Статистика корпуса</b>\n\n'
        f'  • Слов (всего): {s["total_words"]}\n'
        f'  • Уникальных слов: {s["unique_words"]}\n'
        f'  • Предложений: {s["sentences"]}\n'
        f'  • Средняя длина слова: {s["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {s["lexical_diversity"]:.2%}\n'
    )
    logger.info('[/stats] Статистика отправлена user_id=%s', user_id)
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)


@bot.message_handler(commands=['corpus'])
def corpus(message: telebot.types.Message) -> None:
    """/corpus — show shared corpus statistics."""
    logger.info('[/corpus] user_id=%s', message.from_user.id)
    corpus_stats = db.get_corpus_stats(SHARED_CORPUS_USER_ID)
    reply = (
        f'📚 <b>Общий корпус</b>\n\n'
        f'  • Текстов сохранено: {corpus_stats["count"]}\n'
        f'  • Всего символов: {corpus_stats["total_chars"]}\n\n'
        f'Отправьте любое текстовое сообщение, чтобы добавить его в корпус.'
    )
    logger.info('[/corpus] Статистика отправлена user_id=%s: текстов=%s', message.from_user.id, corpus_stats['count'])
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)


def _send_corpus_record(message: telebot.types.Message, name: str, record: dict) -> None:
    """Send the text of a named corpus record to the user."""
    text = record['combined_text']
    created_at = record['created_at']
    header = f'📄 <b>Корпус: {_escape_html(name)}</b>\n<i>Сохранён: {_escape_html(str(created_at))}</i>\n\n'
    _send_long_message(message.chat.id, header + text, parse_mode='HTML',
                       reply_to_message=message)
    logger.info('[/load] Текст корпуса "%s" отправлен user_id=%s (%d симв.)',
                name, message.from_user.id, len(text))


def _receive_load_name(message: telebot.types.Message) -> None:
    """Next-step handler: receives the work name to search and load."""
    query = (message.text or '').strip()
    if not query or query.startswith('/'):
        logger.info('[/load] Пустой запрос от user_id=%s, загрузка отменена',
                    message.from_user.id)
        bot.reply_to(message, '❌ Название не указано. Загрузка отменена.')
        return

    user_id = SHARED_CORPUS_USER_ID
    logger.info('[/load] Поиск произведения "%s" для user_id=%s (next-step)', query, user_id)
    results = db.search_named_analyses(user_id, query)
    if not results:
        bot.reply_to(message,
                     f'❌ Произведение по запросу <b>{_escape_html(query)}</b> не найдено.',
                     parse_mode='HTML')
        return
    if len(results) == 1:
        record = results[0]
        _send_corpus_record(message, record['name'], record)
        return
    # Multiple matches — list them and ask the user to be more specific
    names_list = '\n'.join(f'  • {_escape_html(r["name"])}' for r in results)
    bot.reply_to(
        message,
        f'🔍 Найдено несколько произведений по запросу <b>{_escape_html(query)}</b>:\n\n'
        f'{names_list}\n\n'
        f'Уточните название и попробуйте снова.',
        parse_mode='HTML',
    )


@bot.message_handler(commands=['load'])
def load_corpus(message: telebot.types.Message) -> None:
    """/load [название] — найти произведение по названию и показать текст целиком."""
    logger.info('[/load] user_id=%s', message.from_user.id)
    parts = message.text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ''
    if not query:
        sent = bot.reply_to(message, '🔍 Введите название произведения для поиска:')
        bot.register_next_step_handler(sent, _receive_load_name)
        return

    user_id = SHARED_CORPUS_USER_ID
    logger.info('[/load] Поиск произведения "%s" для user_id=%s', query, user_id)
    results = db.search_named_analyses(user_id, query)
    if not results:
        bot.reply_to(
            message,
            f'❌ Произведение по запросу <b>{_escape_html(query)}</b> не найдено.',
            parse_mode='HTML',
        )
        return
    if len(results) == 1:
        record = results[0]
        _send_corpus_record(message, record['name'], record)
        return
    names_list = '\n'.join(f'  • {_escape_html(r["name"])}' for r in results)
    bot.reply_to(
        message,
        f'🔍 Найдено несколько произведений по запросу <b>{_escape_html(query)}</b>:\n\n'
        f'{names_list}\n\n'
        f'Уточните название и попробуйте снова.',
        parse_mode='HTML',
    )


# ---------------------------------------------------------------------------
# Diacritical-insensitive search helpers for Ossetian
# ---------------------------------------------------------------------------

# Mapping of Ossetian diacritical variants to a canonical Cyrillic form.
# "æ" (ash) is the standard IPA letter for the Ossetian "а" vowel, and both
# "а" (Cyrillic/Latin a) and "æ" represent the same phoneme in different
# transcription traditions.  Likewise "ӕ" (Cyrillic small letter ae) is the
# same sound written with a different Unicode codepoint.
# All variants are mapped to lowercase "а" because _normalize_ossetian()
# lowercases text before applying this mapping.
_OSSETIAN_CHAR_MAP: dict[str, str] = {
    'æ':  'а',   # U+00E6 LATIN SMALL LETTER AE  → Cyrillic а
    'Æ':  'а',   # U+00C6 LATIN CAPITAL LETTER AE → Cyrillic а (lowercased before lookup)
    'ӕ':  'а',   # U+04D5 CYRILLIC SMALL LETTER AE
    'Ӕ':  'а',   # U+04D4 CYRILLIC CAPITAL LETTER AE (lowercased before lookup)
}


def _normalize_ossetian(text: str) -> str:
    """Return *text* lowercased and with Ossetian diacritical variants collapsed.

    Characters that represent the same Ossetian phoneme in different
    transcription systems are mapped to a single canonical form so that
    e.g. "æе" and "ае" compare equal.
    """
    text = unicodedata.normalize('NFC', text.lower())
    return text.translate(str.maketrans(_OSSETIAN_CHAR_MAP))


def _word_matches_flexible(word_norm: str, sentence: str) -> bool:
    """Return True if *word_norm* (already normalized) appears as a word in *sentence*.

    Matching is:
    - case-insensitive
    - diacritical-insensitive (uses :func:`_normalize_ossetian`)
    - whole-word (surrounded by non-word characters or sentence boundaries)
    """
    sentence_norm = _normalize_ossetian(sentence)
    # Use word-boundary regex so "ае" does not match inside a longer word.
    pattern = r'(?<!\w)' + re.escape(word_norm) + r'(?!\w)'
    return bool(re.search(pattern, sentence_norm))


# ---------------------------------------------------------------------------
# Semantic similarity helpers (no ML/AI — token-based methods only)
# ---------------------------------------------------------------------------

SIMILAR_TOP_N: int = 10   # number of top results to return
SIMILAR_MIN_SCORE: float = 0.05  # minimum combined score to include a result


def _tokenize_for_similarity(text: str) -> list[str]:
    """Tokenize *text* for similarity comparison.

    Lowercases, applies Ossetian normalisation, then extracts word tokens
    while discarding stop-words.
    """
    normalized = _normalize_ossetian(text)
    tokens = _TOKEN_RE.findall(normalized)
    return [t for t in tokens if t not in _OSSETIAN_STOPWORDS and len(t) > 1]


def _jaccard_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Return Jaccard similarity between two token lists."""
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _ngram_similarity(tokens_a: list[str], tokens_b: list[str], n: int = 2) -> float:
    """Return n-gram Jaccard similarity between two token lists."""
    def get_ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
        return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}

    ngrams_a = get_ngrams(tokens_a, n)
    ngrams_b = get_ngrams(tokens_b, n)
    if not ngrams_a and not ngrams_b:
        return 0.0
    intersection = len(ngrams_a & ngrams_b)
    union = len(ngrams_a | ngrams_b)
    return intersection / union if union > 0 else 0.0


def _cosine_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Return TF-based cosine similarity between two token lists."""
    freq_a = Counter(tokens_a)
    freq_b = Counter(tokens_b)
    if not freq_a or not freq_b:
        return 0.0
    dot = sum(freq_a[w] * freq_b[w] for w in freq_a if w in freq_b)
    norm_a = math.sqrt(sum(v ** 2 for v in freq_a.values()))
    norm_b = math.sqrt(sum(v ** 2 for v in freq_b.values()))
    return dot / (norm_a * norm_b) if norm_a * norm_b > 0 else 0.0


def _combined_similarity(tokens_query: list[str], tokens_candidate: list[str]) -> float:
    """Return a combined similarity score using Jaccard, bigrams, trigrams and cosine.

    Weights are tuned for short-to-medium Ossetian corpus sentences:
      40 % cosine (captures word-frequency overlap)
      30 % Jaccard (fast token-set overlap)
      20 % bigram similarity (phrase structure)
      10 % trigram similarity (longer phrase structure)
    """
    jaccard = _jaccard_similarity(tokens_query, tokens_candidate)
    bigram = _ngram_similarity(tokens_query, tokens_candidate, n=2)
    trigram = _ngram_similarity(tokens_query, tokens_candidate, n=3)
    cosine = _cosine_similarity(tokens_query, tokens_candidate)
    return 0.40 * cosine + 0.30 * jaccard + 0.20 * bigram + 0.10 * trigram


def _do_similar(message: telebot.types.Message, query: str) -> None:
    """Core logic for /similar — find semantically similar sentences without AI."""
    user_id = message.from_user.id
    logger.info('[/similar] Запрос "%s" от user_id=%s', query[:80], user_id)

    texts_with_names = db.get_corpus_texts_with_names(SHARED_CORPUS_USER_ID)
    if not texts_with_names:
        logger.info('[/similar] Общий корпус пуст (user_id=%s)', user_id)
        bot.reply_to(
            message,
            '📭 Общий корпус пуст. Отправьте несколько текстовых сообщений, '
            'чтобы наполнить его, а затем повторите команду.',
        )
        return

    query_tokens = _tokenize_for_similarity(query)
    if not query_tokens:
        bot.reply_to(
            message,
            '❌ Запрос содержит только стоп-слова или пустой. '
            'Введите более содержательный текст.',
        )
        return

    scored: list[tuple[float, str, str | None]] = []  # (score, sentence, name)

    for text, name in texts_with_names:
        sentences = [s.strip() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]
        for sentence in sentences:
            candidate_tokens = _tokenize_for_similarity(sentence)
            if not candidate_tokens:
                continue
            score = _combined_similarity(query_tokens, candidate_tokens)
            if score >= SIMILAR_MIN_SCORE:
                scored.append((score, sentence, name))

    logger.info('[/similar] Оценено %d предложений, запрос="%s" (user_id=%s)',
                len(scored), query[:80], user_id)

    if not scored:
        bot.reply_to(
            message,
            f'🔍 Похожих текстов для запроса <b>{_escape_html(query)}</b> не найдено.',
            parse_mode='HTML',
        )
        return

    # Sort by score descending; keep top N.
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:SIMILAR_TOP_N]

    # Persist the search for analytics.
    results_json = json.dumps(
        [{'score': round(s, 4), 'sentence': t, 'name': n} for s, t, n in top],
        ensure_ascii=False,
    )
    db.save_similarity_search(user_id, query, results_json, similarity_method='combined')

    reply = (
        f'🔍 <b>Семантически похожие тексты</b>\n'
        f'Запрос: <i>{_escape_html(query)}</i>\n'
        f'Найдено: {len(scored)} предложений, показаны топ-{len(top)}\n\n'
    )
    for i, (score, sentence, name) in enumerate(top, 1):
        display = (
            sentence if len(sentence) <= SEARCH_SENTENCE_DISPLAY_LEN
            else sentence[:SEARCH_SENTENCE_DISPLAY_LEN - 3] + '...'
        )
        work_label = f'📚 <i>{_escape_html(name)}</i>\n' if name else ''
        reply += (
            f'<b>{i}.</b> {work_label}'
            f'<code>{score:.2%}</code> {_escape_html(display)}\n\n'
        )

    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)
    logger.info('[/similar] Результаты отправлены user_id=%s (%d совпадений)', user_id, len(top))


def _do_search(message: telebot.types.Message, word: str) -> None:
    """Core search logic shared by /search command and button_search."""
    user_id = message.from_user.id
    texts_with_names = db.get_corpus_texts_with_names(SHARED_CORPUS_USER_ID)
    if not texts_with_names:
        logger.info('[/search] Общий корпус пуст (user_id=%s)', user_id)
        bot.reply_to(
            message,
            '📭 Общий корпус пуст. Отправьте несколько текстовых сообщений, '
            'чтобы наполнить его, а затем повторите команду.',
        )
        return

    word_norm = _normalize_ossetian(word)
    matches = []  # list of (sentence_text, text_idx, sent_idx, name_or_None)

    for text_idx, (text, name) in enumerate(texts_with_names):
        sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]
        for sent_idx, sentence in enumerate(sentences):
            if _word_matches_flexible(word_norm, sentence):
                matches.append((sentence, text_idx, sent_idx, name))
                if len(matches) >= SEARCH_MAX_RESULTS:
                    break
        if len(matches) >= SEARCH_MAX_RESULTS:
            break

    logger.info('[/search] Слово "%s": найдено %d предложений (user_id=%s)', word, len(matches), user_id)

    if not matches:
        if word_norm in _OSSETIAN_STOPWORDS:
            bot.reply_to(
                message,
                f'🔍 Слово <b>{_escape_html(word)}</b> является стоп-словом и встречается очень часто.\n'
                f'Попробуйте поиск другого слова.',
                parse_mode='HTML',
            )
        else:
            bot.reply_to(
                message,
                f'🔍 Слово <b>{_escape_html(word)}</b> не найдено в вашем корпусе.',
                parse_mode='HTML',
            )
        return

    reply = f'🔍 <b>Результаты поиска: "{_escape_html(word)}"</b>\nНайдено предложений: {len(matches)}\n\n'
    markup = telebot.types.InlineKeyboardMarkup()
    for i, (sentence, text_idx, sent_idx, name) in enumerate(matches, 1):
        display = sentence if len(sentence) <= SEARCH_SENTENCE_DISPLAY_LEN else sentence[:SEARCH_SENTENCE_DISPLAY_LEN - 3] + '...'
        work_label = f'📚 <i>{_escape_html(name)}</i>\n' if name else ''
        reply += f'<b>{i}.</b> {work_label}{_escape_html(display)}\n\n'
        callback_data = f'srch:{text_idx}:{sent_idx}'
        markup.add(
            telebot.types.InlineKeyboardButton(
                f'📄 Открыть текст #{i}',
                callback_data=callback_data,
            )
        )

    _send_long_message(message.chat.id, reply, parse_mode='HTML',
                       reply_to_message=message, reply_markup=markup)


def _receive_search_word(message: telebot.types.Message) -> None:
    """Next-step handler: receives the search word entered by the user."""
    word = unicodedata.normalize('NFC', (message.text or '').strip())
    if not word or word.startswith('/'):
        logger.info('[Button/🔎] Пустой или командный ввод от user_id=%s, поиск отменён',
                    message.from_user.id)
        bot.reply_to(message, '❌ Слово не введено. Поиск отменён.')
        return

    logger.info('[Button/🔎] Поиск слова "%s" для user_id=%s (next-step)', word, message.from_user.id)
    _do_search(message, word)


@bot.message_handler(commands=['search'])
def search_word(message: telebot.types.Message) -> None:
    """/search <word> — find sentences containing the word in the user's corpus."""
    logger.info('[/search] user_id=%s', message.from_user.id)
    parts = message.text.split(maxsplit=1)
    word = parts[1].strip() if len(parts) > 1 else ''

    if not word:
        bot.reply_to(message, '🔍 Укажите слово для поиска. Пример: /search слово')
        return

    _do_search(message, word)


def _receive_similar_text(message: telebot.types.Message) -> None:
    """Next-step handler: receives the query text for similarity search."""
    query = unicodedata.normalize('NFC', (message.text or '').strip())
    if not query or query.startswith('/'):
        logger.info('[Button/🔍] Пустой или командный ввод от user_id=%s, поиск похожих отменён',
                    message.from_user.id)
        bot.reply_to(message, '❌ Текст не введён. Поиск похожих отменён.')
        return

    logger.info('[Button/🔍] Поиск похожих для "%s" (user_id=%s, next-step)',
                query[:80], message.from_user.id)
    _do_similar(message, query)


@bot.message_handler(commands=['similar'])
def similar_texts(message: telebot.types.Message) -> None:
    """/similar <текст> — найти семантически похожие предложения в корпусе."""
    logger.info('[/similar] user_id=%s', message.from_user.id)
    parts = message.text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ''

    if not query:
        sent = bot.reply_to(message, '🔍 Введите текст для поиска похожих предложений:')
        bot.register_next_step_handler(sent, _receive_similar_text)
        return

    _do_similar(message, query)


# ---------------------------------------------------------------------------
# Morphological analysis helpers and command handlers
# ---------------------------------------------------------------------------

def _do_morph(message: telebot.types.Message, word: str) -> None:
    """Core morphological analysis logic shared by /morph and the '🔬 Морфо' button."""
    user_id = message.from_user.id
    logger.info('[/morph] Морфоанализ слова "%s" для user_id=%s', word, user_id)

    if analyzer._uniparser is None:
        bot.reply_to(
            message,
            '⚠️ Морфологический анализ недоступен: uniparser-ossetic не установлен.',
        )
        return

    info_list = analyzer.get_morphological_info(word)
    if not info_list:
        bot.reply_to(
            message,
            f'🔬 Слово <b>{_escape_html(word)}</b> не распознано морфологическим анализатором.',
            parse_mode='HTML',
        )
        return

    lines = [f'🔬 <b>Морфологический анализ:</b> {_escape_html(word)}\n']
    for i, info in enumerate(info_list, 1):
        if len(info_list) > 1:
            lines.append(f'<b>Вариант {i}:</b>')
        lines.append(f'  Лемма: {_escape_html(info["lemma"])}')
        lines.append(f'  Часть речи: {_escape_html(info["pos"])}')
        if info['features']:
            lines.append(f'  Признаки: {_escape_html(", ".join(info["features"]))}')
        lines.append(f'  Граммемы: {_escape_html(info["gramm"])}')
        if i < len(info_list):
            lines.append('')
    logger.info('[/morph] Результат морфоанализа отправлен user_id=%s', user_id)
    _send_long_message(message.chat.id, '\n'.join(lines), parse_mode='HTML',
                       reply_to_message=message)


def _receive_morph_word(message: telebot.types.Message) -> None:
    """Next-step handler: receives the word to morphologically analyse."""
    word = unicodedata.normalize('NFC', (message.text or '').strip())
    if not word or word.startswith('/'):
        logger.info('[Button/🔬] Пустой ввод от user_id=%s, морфоанализ отменён',
                    message.from_user.id)
        bot.reply_to(message, '❌ Слово не введено. Морфоанализ отменён.')
        return
    logger.info('[Button/🔬] Морфоанализ "%s" для user_id=%s (next-step)',
                word, message.from_user.id)
    _do_morph(message, word)


@bot.message_handler(commands=['morph'])
def morph(message: telebot.types.Message) -> None:
    """/morph <word> — morphological analysis of a single word."""
    logger.info('[/morph] user_id=%s', message.from_user.id)
    parts = message.text.split(maxsplit=1)
    word = parts[1].strip() if len(parts) > 1 else ''
    if not word:
        bot.reply_to(message,
                     '🔬 Укажите слово для морфологического анализа. Пример: /morph слово')
        return
    _do_morph(message, word)


@bot.message_handler(commands=['morph_stats'])
def morph_stats(message: telebot.types.Message) -> None:
    """/morph_stats — POS distribution statistics for the shared corpus."""
    logger.info('[/morph_stats] user_id=%s', message.from_user.id)

    if analyzer._uniparser is None:
        bot.reply_to(
            message,
            '⚠️ Морфологический анализ недоступен: uniparser-ossetic не установлен.',
        )
        return

    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/morph_stats] Анализ POS-распределения корпуса (%d симв.) для user_id=%s',
                len(text), user_id)
    pos_dist = analyzer.get_pos_distribution(text)
    if not pos_dist:
        bot.reply_to(message, '📊 Нет данных для морфологической статистики.')
        return

    total = sum(pos_dist.values())
    top_pos = list(pos_dist.items())[:15]
    lines = ['📊 <b>Статистика частей речи в корпусе</b>\n']
    for pos, count in top_pos:
        pct = count / total * 100
        pos_label = _escape_html(pos if pos else '?')
        lines.append(f'  {pos_label}: {count} ({pct:.1f}%)')
    lines.append(f'\n<i>Всего токенов проанализировано: {total}</i>')
    logger.info('[/morph_stats] POS-статистика отправлена user_id=%s (%d категорий)',
                user_id, len(pos_dist))
    _send_long_message(message.chat.id, '\n'.join(lines), parse_mode='HTML',
                       reply_to_message=message)


@bot.message_handler(commands=['morph_freq'])
def morph_freq(message: telebot.types.Message) -> None:
    """/morph_freq — frequency distribution of grammatical forms in the shared corpus."""
    logger.info('[/morph_freq] user_id=%s', message.from_user.id)

    if analyzer._uniparser is None:
        bot.reply_to(
            message,
            '⚠️ Морфологический анализ недоступен: uniparser-ossetic не установлен.',
        )
        return

    text = _get_user_corpus_text(message)
    if text is None:
        return

    user_id = message.from_user.id
    logger.info('[/morph_freq] Анализ gramm-распределения корпуса (%d симв.) для user_id=%s',
                len(text), user_id)
    gramm_dist = analyzer.get_gramm_distribution(text)
    if not gramm_dist:
        bot.reply_to(message, '📊 Нет данных для анализа грамматических форм.')
        return

    top_forms = list(gramm_dist.items())[:20]
    total = sum(gramm_dist.values())
    lines = ['📊 <b>Частота грамматических форм в корпусе</b> (топ 20)\n']
    for gramm, count in top_forms:
        pct = count / total * 100
        lines.append(f'  {_escape_html(gramm)}: {count} ({pct:.1f}%)')
    lines.append(f'\n<i>Всего токенов: {total}, уникальных форм: {len(gramm_dist)}</i>')
    logger.info('[/morph_freq] Грамматические формы отправлены user_id=%s (%d форм)',
                user_id, len(gramm_dist))
    _send_long_message(message.chat.id, '\n'.join(lines), parse_mode='HTML',
                       reply_to_message=message)


@bot.callback_query_handler(func=lambda call: call.data.startswith('srch:'))
def search_open_text_menu(call: telebot.types.CallbackQuery) -> None:
    """Show work title and viewing options when the user taps 'Open text'."""
    logger.info('[callback/srch] user_id=%s, data=%s', call.from_user.id, call.data)

    parts = call.data.split(':')
    if len(parts) != 3:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    try:
        text_idx = int(parts[1])
        sent_idx = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    user_id = call.from_user.id
    texts_with_names = db.get_corpus_texts_with_names(SHARED_CORPUS_USER_ID)

    if text_idx >= len(texts_with_names):
        bot.answer_callback_query(call.id, '❌ Текст не найден в корпусе.')
        return

    _text, name = texts_with_names[text_idx]
    title_line = (
        f'📚 <b>{_escape_html(name)}</b>\n\n'
        if name
        else f'📄 <b>Текст {text_idx + 1}</b>\n\n'
    )
    menu_text = title_line + 'Выберите режим просмотра:'
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton(
            '📖 Показать контекст',
            callback_data=f'srch_ctx:{text_idx}:{sent_idx}',
        ),
        telebot.types.InlineKeyboardButton(
            '📕 Полный текст',
            callback_data=f'srch_full:{text_idx}',
        ),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, menu_text, parse_mode='HTML', reply_markup=markup)
    logger.info('[callback/srch] Меню просмотра отправлено (user_id=%s, текст=%d, предл.=%d)',
                user_id, text_idx, sent_idx)


@bot.callback_query_handler(func=lambda call: call.data.startswith('srch_ctx:'))
def search_show_context(call: telebot.types.CallbackQuery) -> None:
    """Show sentence context for a search result."""
    logger.info('[callback/srch_ctx] user_id=%s, data=%s', call.from_user.id, call.data)

    parts = call.data.split(':')
    if len(parts) != 3:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    try:
        text_idx = int(parts[1])
        sent_idx = int(parts[2])
    except ValueError:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    user_id = call.from_user.id
    texts_with_names = db.get_corpus_texts_with_names(SHARED_CORPUS_USER_ID)

    if text_idx >= len(texts_with_names):
        bot.answer_callback_query(call.id, '❌ Текст не найден в корпусе.')
        return

    text, name = texts_with_names[text_idx]
    sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]

    if sent_idx >= len(sentences):
        bot.answer_callback_query(call.id, '❌ Предложение не найдено.')
        return

    # Show SEARCH_CONTEXT_SIZE sentences before and after for context.
    context_before = SEARCH_CONTEXT_SIZE
    context_after = SEARCH_CONTEXT_SIZE
    start = max(0, sent_idx - context_before)
    end = min(len(sentences), sent_idx + context_after + 1)
    context_sentences = sentences[start:end]
    relative_idx = sent_idx - start  # position of the matching sentence in the slice

    context_lines = []
    for i, sent in enumerate(context_sentences):
        context_lines.append(f'▶ {sent}' if i == relative_idx else sent)
    context_text = '\n'.join(context_lines)

    title_line = (
        f'📚 <b>{_escape_html(name)}</b>\n'
        if name
        else f'📄 <b>Текст {text_idx + 1}</b>\n'
    )
    header = (
        title_line
        + f'📖 <b>Контекст</b> (предложение {sent_idx + 1} из {len(sentences)})\n\n'
    )

    bot.answer_callback_query(call.id)
    _send_long_message(call.message.chat.id, header + context_text, parse_mode='HTML')
    logger.info('[callback/srch_ctx] Контекст отправлен (user_id=%s, текст=%d, предл.=%d)',
                user_id, text_idx, sent_idx)


@bot.callback_query_handler(func=lambda call: call.data.startswith('srch_full:'))
def search_show_full_text(call: telebot.types.CallbackQuery) -> None:
    """Show the full text of a work."""
    logger.info('[callback/srch_full] user_id=%s, data=%s', call.from_user.id, call.data)

    parts = call.data.split(':')
    if len(parts) != 2:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    try:
        text_idx = int(parts[1])
    except ValueError:
        bot.answer_callback_query(call.id, '❌ Неверный формат данных.')
        return

    user_id = call.from_user.id
    texts_with_names = db.get_corpus_texts_with_names(SHARED_CORPUS_USER_ID)

    if text_idx >= len(texts_with_names):
        bot.answer_callback_query(call.id, '❌ Текст не найден в корпусе.')
        return

    text, name = texts_with_names[text_idx]
    title_line = (
        f'📚 <b>{_escape_html(name)}</b>\n'
        if name
        else f'📄 <b>Текст {text_idx + 1}</b>\n'
    )
    header = title_line + '📕 <b>Полный текст</b>\n\n'

    bot.answer_callback_query(call.id)
    _send_long_message(call.message.chat.id, header + text, parse_mode='HTML')
    logger.info('[callback/srch_full] Полный текст отправлен (user_id=%s, текст=%d)',
                user_id, text_idx)


@bot.message_handler(commands=['import_texts'])
def import_texts(message: telebot.types.Message) -> None:
    """/import_texts — import all .txt files from the texts/ folder into the shared corpus (owner only)."""
    logger.info('[/import_texts] user_id=%s', message.from_user.id)
    if not _is_owner(message):
        logger.info('[/import_texts] Отказ: user_id=%s не является владельцем корпуса', message.from_user.id)
        bot.reply_to(message, '❌ Только владелец корпуса может импортировать тексты.')
        return

    texts_dir = TEXTS_DIR
    result = db.import_texts_from_directory(SHARED_CORPUS_USER_ID, texts_dir, analyzer=analyzer)

    if 'error' in result:
        bot.reply_to(
            message,
            f'❌ Ошибка при доступе к папке <code>{_escape_html(texts_dir)}</code>: {_escape_html(result["error"])}',
            parse_mode='HTML',
        )
        return

    imported = result['imported']
    errors = result['errors']

    if imported == 0 and errors == 0:
        bot.reply_to(
            message,
            f'📂 В папке <code>{_escape_html(texts_dir)}</code> не найдено .txt файлов.',
            parse_mode='HTML',
        )
        return

    reply = f'✅ Загружено {imported} {_ru_plural(imported, "текст", "текста", "текстов")}'
    if errors:
        reply += f', {errors} {_ru_plural(errors, "ошибка", "ошибки", "ошибок")}'
    logger.info('[/import_texts] Импорт завершён для user_id=%s: загружено=%d, ошибок=%d',
                message.from_user.id, imported, errors)
    bot.reply_to(message, reply)


# ---------------------------------------------------------------------------
# Keyboard button handlers
# ---------------------------------------------------------------------------

@bot.message_handler(func=lambda m: m.text == '📊 Анализ')
def button_analyze(message: telebot.types.Message) -> None:
    """Handle '📊 Анализ' button – run full analysis on the user's corpus."""
    logger.info('[Button/📊] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return
    user_id = message.from_user.id
    logger.info('[Button/📊] Анализ корпуса %d симв. для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    stats_data = result['stats']
    freq = dict(itertools.islice(result['frequency'].items(), TOP_WORDS))
    reply = (
        f'📊 <b>Результаты анализа корпуса</b>\n\n'
        f'<b>Статистика:</b>\n'
        f'  • Слов (всего): {stats_data["total_words"]}\n'
        f'  • Уникальных слов: {stats_data["unique_words"]}\n'
        f'  • Предложений: {stats_data["sentences"]}\n'
        f'  • Средняя длина слова: {stats_data["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {stats_data["lexical_diversity"]:.2%}\n'
        f'  • Токенов: {result["tokens_count"]}\n'
        f'  • Уникальных лемм: {result["lemmas_count"]}\n\n'
        f'<b>Топ {TOP_WORDS} слов:</b>\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)
def button_frequency(message: telebot.types.Message) -> None:
    """Handle '📈 Частота' button – show word frequencies for the user's corpus."""
    logger.info('[Button/📈] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return
    user_id = message.from_user.id
    logger.info('[Button/📈] Частота корпуса %d симв. для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        bot.reply_to(message, 'Нет слов для частотного анализа.')
        return
    top = sorted(freq_dict.items(), key=lambda x: x[1], reverse=True)[:20]
    lines = ['📊 <b>Частота слов корпуса:</b>\n\n']
    for word, count in top:
        lines.append(f'  {word}: {count}\n')
    _send_long_message(message.chat.id, ''.join(lines), parse_mode='HTML',
                       reply_to_message=message)


@bot.message_handler(func=lambda m: m.text == '☁️ Облако')
def button_wordcloud(message: telebot.types.Message) -> None:
    """Handle '☁️ Облако' button – generate a word cloud from the user's corpus."""
    logger.info('[Button/☁️] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return
    user_id = message.from_user.id
    logger.info('[Button/☁️] Облако корпуса %d симв. для user_id=%s', len(text), user_id)
    result = analyzer.analyze(text)
    freq_dict = result['frequency']
    if not freq_dict:
        bot.reply_to(message, 'Нет слов для создания облака слов.')
        return
    image_path = vis.plot_word_cloud(freq_dict, title='Облако слов корпуса')
    try:
        with open(image_path, 'rb') as img:
            bot.send_photo(message.chat.id, img, caption='Облако слов корпуса')
    finally:
        os.unlink(image_path)


@bot.message_handler(func=lambda m: m.text == '📋 Статистика')
def button_stats(message: telebot.types.Message) -> None:
    """Handle '📋 Статистика' button – show brief statistics for the user's corpus."""
    logger.info('[Button/📋] user_id=%s', message.from_user.id)
    text = _get_user_corpus_text(message)
    if text is None:
        return
    user_id = message.from_user.id
    logger.info('[Button/📋] Статистика корпуса %d симв. для user_id=%s', len(text), user_id)
    s = analyzer.get_text_stats(text)
    reply = (
        f'📈 <b>Статистика корпуса</b>\n\n'
        f'  • Слов (всего): {s["total_words"]}\n'
        f'  • Уникальных слов: {s["unique_words"]}\n'
        f'  • Предложений: {s["sentences"]}\n'
        f'  • Средняя длина слова: {s["avg_word_length"]:.2f}\n'
        f'  • Лексическое разнообразие: {s["lexical_diversity"]:.2%}\n'
    )
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)


@bot.message_handler(func=lambda m: m.text == '📚 Корпус')
def button_corpus(message: telebot.types.Message) -> None:
    """Handle '📚 Корпус' button – show shared corpus statistics."""
    logger.info('[Button/📚] user_id=%s', message.from_user.id)
    requesting_user_id = message.from_user.id
    corpus_stats = db.get_corpus_stats(SHARED_CORPUS_USER_ID)
    collect_status = '🟢 включён' if requesting_user_id in _auto_collect_enabled else '🔴 отключён'
    reply = (
        f'📚 <b>Общий корпус</b>\n\n'
        f'  • Текстов сохранено: {corpus_stats["count"]}\n'
        f'  • Всего символов: {corpus_stats["total_chars"]}\n\n'
        f'Автосбор: {collect_status}\n'
        f'Используйте /collect или кнопку 🔄 Автосбор, чтобы включить/выключить автоматическое '
        f'сохранение текстовых сообщений в корпус.'
    )
    _send_long_message(message.chat.id, reply, parse_mode='HTML', reply_to_message=message)


@bot.message_handler(func=lambda m: m.text == '📂 Загрузить')
def button_load(message: telebot.types.Message) -> None:
    """Handle '📂 Загрузить' button – prompt for corpus name, then load it."""
    logger.info('[Button/📂] user_id=%s', message.from_user.id)
    sent = bot.send_message(message.chat.id, '🔍 Введите название произведения для поиска:')
    bot.register_next_step_handler(sent, _receive_load_name)


@bot.message_handler(func=lambda m: m.text == '🔎 Поиск')
def button_search(message: telebot.types.Message) -> None:
    """Handle '🔎 Поиск' button – prompt for a search word, then run search."""
    logger.info('[Button/🔎] user_id=%s', message.from_user.id)
    sent = bot.send_message(message.chat.id, '🔎 Введите слово для поиска:')
    bot.register_next_step_handler(sent, _receive_search_word)


@bot.message_handler(func=lambda m: m.text == '🔍 Похожие')
def button_similar(message: telebot.types.Message) -> None:
    """Handle '🔍 Похожие' button – prompt for text, then find similar sentences."""
    logger.info('[Button/🔍] user_id=%s', message.from_user.id)
    sent = bot.send_message(message.chat.id, '🔍 Введите текст для поиска похожих предложений:')
    bot.register_next_step_handler(sent, _receive_similar_text)


@bot.message_handler(func=lambda m: m.text == '🔬 Морфо')
def button_morph(message: telebot.types.Message) -> None:
    """Handle '🔬 Морфо' button – prompt for a word, then run morphological analysis."""
    logger.info('[Button/🔬] user_id=%s', message.from_user.id)
    sent = bot.send_message(message.chat.id, '🔬 Введите слово для морфологического анализа:')
    bot.register_next_step_handler(sent, _receive_morph_word)


@bot.message_handler(func=lambda m: m.text == '📥 Импорт')
def button_import(message: telebot.types.Message) -> None:
    """Handle '📥 Импорт' button – import .txt files from the texts/ directory (owner only)."""
    logger.info('[Button/📥] user_id=%s', message.from_user.id)
    if not _is_owner(message):
        logger.info('[Button/📥] Отказ: user_id=%s не является владельцем корпуса', message.from_user.id)
        bot.reply_to(message, '❌ Только владелец корпуса может импортировать тексты.')
        return
    texts_dir = TEXTS_DIR
    result = db.import_texts_from_directory(SHARED_CORPUS_USER_ID, texts_dir, analyzer=analyzer)
    if 'error' in result:
        bot.reply_to(message,
                     f'❌ Ошибка при доступе к папке <code>{_escape_html(texts_dir)}</code>: {_escape_html(result["error"])}',
                     parse_mode='HTML')
        return
    imported = result['imported']
    errors = result['errors']
    if imported == 0 and errors == 0:
        bot.reply_to(message,
                     f'📂 В папке <code>{_escape_html(texts_dir)}</code> не найдено .txt файлов.',
                     parse_mode='HTML')
        return
    reply = f'✅ Загружено {imported} {_ru_plural(imported, "текст", "текста", "текстов")}'
    if errors:
        reply += f', {errors} {_ru_plural(errors, "ошибка", "ошибки", "ошибок")}'
    bot.reply_to(message, reply)


@bot.message_handler(commands=['collect'])
def toggle_collect(message: telebot.types.Message) -> None:
    """/collect — toggle auto-collect mode on/off (owner only)."""
    if not _is_owner(message):
        logger.info('[/collect] Отказ: user_id=%s не является владельцем корпуса', message.from_user.id)
        bot.reply_to(message, '❌ Только владелец корпуса может управлять автосбором.')
        return
    user_id = message.from_user.id
    if user_id in _auto_collect_enabled:
        _auto_collect_enabled.discard(user_id)
        logger.info('[/collect] Автосбор отключён для user_id=%s', user_id)
        bot.reply_to(
            message,
            '🔴 <b>Автосбор отключён.</b>\n'
            'Текстовые сообщения больше не будут автоматически сохраняться в корпус.',
            parse_mode='HTML',
        )
    else:
        _auto_collect_enabled.add(user_id)
        logger.info('[/collect] Автосбор включён для user_id=%s', user_id)
        bot.reply_to(
            message,
            '🟢 <b>Автосбор включён.</b>\n'
            f'Теперь каждое текстовое сообщение автоматически сохраняется в общий корпус '
            f'(через {COLLECT_WINDOW} {_ru_plural(COLLECT_WINDOW, "секунду", "секунды", "секунд")} после последнего).',
            parse_mode='HTML',
        )


@bot.message_handler(func=lambda m: m.text == '🔄 Автосбор')
def button_toggle_collect(message: telebot.types.Message) -> None:
    """Handle '🔄 Автосбор' button – toggle auto-collect mode."""
    logger.info('[Button/🔄] user_id=%s', message.from_user.id)
    toggle_collect(message)


# ---------------------------------------------------------------------------
# Yandex AI handlers
# ---------------------------------------------------------------------------

# Callback data constants for the Yandex AI menu.
_YAI_CB_TRANSLATE = 'yai:translate'
_YAI_CB_EXPLAIN   = 'yai:explain'
_YAI_CB_ANALYZE   = 'yai:analyze'
_YAI_CB_TRANS_OS_RU = 'yai:trans:os_ru'
_YAI_CB_TRANS_RU_OS = 'yai:trans:ru_os'


def _send_yandex_ai_menu(chat_id: int) -> None:
    """Send the Yandex AI inline menu to *chat_id*."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            '✨ Перевести слово/предложение', callback_data=_YAI_CB_TRANSLATE,
        ),
    )
    markup.add(
        telebot.types.InlineKeyboardButton(
            '📚 Объяснить слово', callback_data=_YAI_CB_EXPLAIN,
        ),
        telebot.types.InlineKeyboardButton(
            '🎯 Анализ текста', callback_data=_YAI_CB_ANALYZE,
        ),
    )
    bot.send_message(
        chat_id,
        '🤖 <b>Яндекс ИИ</b> — выберите операцию:',
        parse_mode='HTML',
        reply_markup=markup,
    )


@bot.message_handler(func=lambda m: m.text == '🤖 Яндекс ИИ')
def button_yandex_ai(message: telebot.types.Message) -> None:
    """Handle '🤖 Яндекс ИИ' button – show AI operations menu."""
    logger.info('[Button/🤖] user_id=%s', message.from_user.id)
    _send_yandex_ai_menu(message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == _YAI_CB_TRANSLATE)
def callback_yai_translate(call: telebot.types.CallbackQuery) -> None:
    """Show translation direction selection for Yandex AI translate."""
    bot.answer_callback_query(call.id)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton(
            '✨ Осетинский → Русский', callback_data=_YAI_CB_TRANS_OS_RU,
        ),
        telebot.types.InlineKeyboardButton(
            '✨ Русский → Осетинский', callback_data=_YAI_CB_TRANS_RU_OS,
        ),
    )
    bot.send_message(
        call.message.chat.id,
        '🌐 Выберите направление перевода:',
        reply_markup=markup,
    )


@bot.callback_query_handler(
    func=lambda call: call.data in (_YAI_CB_TRANS_OS_RU, _YAI_CB_TRANS_RU_OS),
)
def callback_yai_translate_direction(call: telebot.types.CallbackQuery) -> None:
    """Handle translation direction selection for Yandex AI translate."""
    bot.answer_callback_query(call.id)
    if call.data == _YAI_CB_TRANS_OS_RU:
        source_lang, target_lang = 'os', 'ru'
    else:
        source_lang, target_lang = 'ru', 'os'
    sent = bot.send_message(call.message.chat.id, '✏️ Введите слово или предложение для перевода:')
    bot.register_next_step_handler(sent, _receive_yai_translate_text, source_lang, target_lang)


def _receive_yai_translate_text(message: telebot.types.Message,
                                  source_lang: str, target_lang: str) -> None:
    """Next-step handler: translates word or sentence entered by the user."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or '').strip()

    if not text or text.startswith('/'):
        logger.info('[YAI/translate] Пустой ввод от user_id=%s, перевод отменён', user_id)
        bot.reply_to(message, '❌ Текст не введён. Перевод отменён.')
        return

    logger.info('[YAI/translate] Перевод "%s" (%s→%s) для user_id=%s',
                text[:50], source_lang, target_lang, user_id)

    # Check cache first.
    cached = db.get_yandex_ai_translation_cache(text, source_lang, target_lang)
    if cached:
        logger.info('[YAI/translate] Кеш попадание для user_id=%s', user_id)
        _send_yai_translation_result(chat_id, text, cached, source_lang, target_lang)
        return

    try:
        translated = translator.translate(text, target_lang=target_lang, source_lang=source_lang)
    except Exception as exc:  # noqa: BLE001
        logger.error('[YAI/translate] Ошибка перевода для user_id=%s: %s', user_id, exc)
        bot.send_message(chat_id, '❌ Не удалось выполнить перевод. Попробуйте ещё раз.')
        return

    # Save to cache.
    db.save_yandex_ai_translation_cache(text, source_lang, target_lang, translated)

    _send_yai_translation_result(chat_id, text, translated, source_lang, target_lang)

    # Also persist to the user translations history.
    db.save_translation(
        user_id=user_id,
        source_lang=source_lang,
        target_lang=target_lang,
        original_text=text,
        translated_text=translated,
    )

    # For a single word, also show morphological analysis if available.
    words = text.split()
    if len(words) == 1 and analyzer._uniparser is not None:
        info_list = analyzer.get_morphological_info(text)
        if info_list:
            lines = [f'\n🔬 <b>Морфологический анализ:</b>']
            for i, info in enumerate(info_list, 1):
                if len(info_list) > 1:
                    lines.append(f'<b>Вариант {i}:</b>')
                lines.append(f'  Лемма: {_escape_html(info["lemma"])}')
                lines.append(f'  Часть речи: {_escape_html(info["pos"])}')
                if info['features']:
                    lines.append(f'  Признаки: {_escape_html(", ".join(info["features"]))}')
            morph_msg = '\n'.join(lines)
            if morph_msg:
                _send_long_message(chat_id, morph_msg, parse_mode='HTML')


def _send_yai_translation_result(chat_id: int, original: str, translated: str,
                                   source_lang: str, target_lang: str) -> None:
    """Send a formatted translation result to the chat."""
    if source_lang == 'os' and target_lang == 'ru':
        direction_label = 'ос → рус'
    elif source_lang == 'ru' and target_lang == 'os':
        direction_label = 'рус → ос'
    else:
        direction_label = f'{source_lang} → {target_lang}'

    words = original.split()
    is_word = len(words) == 1
    kind_label = 'слова' if is_word else 'предложения'

    header = f'🤖 <b>Перевод {kind_label}</b> ({direction_label}):\n\n'
    body = f'📝 Оригинал: <i>{_escape_html(original)}</i>\n🔄 Перевод: <b>{_escape_html(translated)}</b>'
    full_reply = header + body
    _send_long_message(chat_id, full_reply, parse_mode='HTML')


@bot.callback_query_handler(func=lambda call: call.data == _YAI_CB_EXPLAIN)
def callback_yai_explain(call: telebot.types.CallbackQuery) -> None:
    """Prompt the user to enter a word for explanation."""
    bot.answer_callback_query(call.id)
    sent = bot.send_message(call.message.chat.id, '📚 Введите слово для объяснения:')
    bot.register_next_step_handler(sent, _receive_yai_explain_text)


def _get_corpus_examples_for_word(word: str, max_examples: int = 3) -> list[str]:
    """Return up to *max_examples* sentences from the shared corpus containing *word*.

    Matching uses the same flexible (case- and diacritical-insensitive,
    whole-word) logic as the /search command.
    """
    word_norm = _normalize_ossetian(word)
    examples: list[str] = []
    texts = db.get_corpus_texts(SHARED_CORPUS_USER_ID)
    for text in texts:
        sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s]
        for sentence in sentences:
            if _word_matches_flexible(word_norm, sentence):
                examples.append(sentence.strip())
                if len(examples) >= max_examples:
                    return examples
    return examples


def _receive_yai_explain_text(message: telebot.types.Message) -> None:
    """Next-step handler: explains the word entered by the user using Yandex.GPT."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    word = (message.text or '').strip()

    if not word or word.startswith('/'):
        logger.info('[YAI/explain] Пустой ввод от user_id=%s, объяснение отменено', user_id)
        bot.reply_to(message, '❌ Слово не введено. Операция отменена.')
        return

    # Only single words are accepted for explanation.
    if len(word.split()) > 1:
        bot.reply_to(
            message,
            '⚠️ Введите одно слово для объяснения (не предложение).',
        )
        return

    logger.info('[YAI/explain] Объяснение слова "%s" для user_id=%s', word, user_id)

    # Collect corpus examples before checking cache so they can be shown even
    # when the AI explanation comes from cache.
    corpus_examples = _get_corpus_examples_for_word(word)
    logger.info(
        '[YAI/explain] Найдено %d примеров из корпуса для слова "%s"',
        len(corpus_examples), word,
    )

    # Check cache first.
    cached = db.get_yandex_ai_explanation_cache(word)
    if cached:
        logger.info('[YAI/explain] Кеш попадание для слова "%s" (user_id=%s)', word, user_id)
        _send_yai_explanation_result(chat_id, word, cached, corpus_examples)
        return

    if not yandex_llm.available:
        bot.send_message(
            chat_id,
            '⚠️ Функция объяснения недоступна: задайте <b>YANDEX_IAM_TOKEN</b> или '
            '<b>YANDEX_API_KEY</b> и <b>YANDEX_FOLDER_ID</b> в файле <code>.env</code>.',
            parse_mode='HTML',
        )
        return

    # Include morphological info in the prompt when available.
    morph_context = ''
    if analyzer._uniparser is not None:
        info_list = analyzer.get_morphological_info(word)
        if info_list:
            info = info_list[0]
            morph_context = (
                f'\nМорфологический анализ: лемма={info["lemma"]}, '
                f'часть речи={info["pos"]}, граммемы={info["gramm"]}'
            )

    # Include real corpus sentences in the prompt when available.
    corpus_context = ''
    if corpus_examples:
        examples_text = '\n'.join(f'  • {s}' for s in corpus_examples)
        corpus_context = (
            f'\n\nПримеры использования из осетинского корпуса текстов:\n{examples_text}'
        )

    system_prompt = (
        'Ты — лингвистический помощник, специализирующийся на осетинском языке. '
        'Отвечай на русском языке. Структурируй ответ по пронумерованным пунктам.'
    )
    user_prompt = (
        f'Объясни слово на осетинском языке: «{word}»{morph_context}{corpus_context}\n\n'
        'Дай:\n'
        '1) Определение слова на русском языке\n'
        '2) Контекст и особенности использования в осетинском языке'
    )

    bot.send_message(chat_id, '⏳ Генерирую объяснение...')
    try:
        explanation = yandex_llm.complete(system_prompt, user_prompt)
    except RuntimeError as exc:
        logger.error('[YAI/explain] Ошибка LLM для user_id=%s: %s', user_id, exc)
        bot.send_message(
            chat_id,
            f'❌ Не удалось получить объяснение: {exc}',
        )
        return

    # Cache and send.
    db.save_yandex_ai_explanation_cache(word, explanation)
    _send_yai_explanation_result(chat_id, word, explanation, corpus_examples)


def _send_yai_explanation_result(
    chat_id: int,
    word: str,
    explanation: str,
    corpus_examples: list[str] | None = None,
) -> None:
    """Send a formatted word explanation to the chat.

    The AI-generated *explanation* is always sent first.  When *corpus_examples*
    are provided a separate section with real corpus sentences is appended.
    """
    header = f'📚 <b>Объяснение слова:</b> <i>{_escape_html(word)}</i>\n\n'
    full_reply = header + _escape_html(explanation)

    # Build optional corpus-examples block.
    corpus_block = ''
    if corpus_examples:
        lines = ['\n\n📖 <b>Примеры из корпуса:</b>']
        for i, sentence in enumerate(corpus_examples, 1):
            lines.append(f'  <b>{i}.</b> {_escape_html(sentence)}')
        corpus_block = '\n'.join(lines)

    if len(full_reply) + len(corpus_block) <= TELEGRAM_MAX_MESSAGE_LEN:
        bot.send_message(chat_id, full_reply + corpus_block, parse_mode='HTML')
    else:
        _send_long_message(chat_id, full_reply, parse_mode='HTML')
        if corpus_block:
            _send_long_message(chat_id, corpus_block.strip(), parse_mode='HTML')


@bot.callback_query_handler(func=lambda call: call.data == _YAI_CB_ANALYZE)
def callback_yai_analyze(call: telebot.types.CallbackQuery) -> None:
    """Prompt the user to enter a text for AI analysis."""
    bot.answer_callback_query(call.id)
    sent = bot.send_message(call.message.chat.id, '🎯 Введите текст для анализа:')
    bot.register_next_step_handler(sent, _receive_yai_analyze_text)


def _receive_yai_analyze_text(message: telebot.types.Message) -> None:
    """Next-step handler: analyses the text entered by the user using Yandex.GPT."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or '').strip()

    if not text or text.startswith('/'):
        logger.info('[YAI/analyze] Пустой ввод от user_id=%s, анализ отменён', user_id)
        bot.reply_to(message, '❌ Текст не введён. Анализ отменён.')
        return

    logger.info('[YAI/analyze] Анализ текста (%d симв.) для user_id=%s', len(text), user_id)

    if not yandex_llm.available:
        bot.send_message(
            chat_id,
            '⚠️ Функция анализа недоступна: задайте <b>YANDEX_IAM_TOKEN</b> или '
            '<b>YANDEX_API_KEY</b> и <b>YANDEX_FOLDER_ID</b> в файле <code>.env</code>.',
            parse_mode='HTML',
        )
        return

    system_prompt = (
        'Ты — эксперт по осетинскому языку и лингвистическому анализу текста. '
        'Отвечай на русском языке. Будь структурированным и информативным.'
    )
    user_prompt = (
        f'Проанализируй следующий текст:\n\n«{text}»\n\n'
        'Предоставь:\n'
        '1) Главная идея текста\n'
        '2) Ключевые слова\n'
        '3) Предложенный перевод на русский язык с пояснением\n'
        '4) Лингвистические особенности (если есть)'
    )

    bot.send_message(chat_id, '⏳ Анализирую текст...')
    try:
        analysis = yandex_llm.complete(system_prompt, user_prompt, max_tokens=3000)
    except RuntimeError as exc:
        logger.error('[YAI/analyze] Ошибка LLM для user_id=%s: %s', user_id, exc)
        bot.send_message(
            chat_id,
            f'❌ Не удалось выполнить анализ: {exc}',
        )
        return

    # Save analysis to database.
    db.save_yandex_ai_analysis(user_id, text, analysis)

    header = '🎯 <b>Анализ текста (Яндекс ИИ):</b>\n\n'
    full_reply = header + analysis
    _send_long_message(chat_id, full_reply, parse_mode='HTML')
    logger.info('[YAI/analyze] Анализ отправлен user_id=%s', user_id)


# ---------------------------------------------------------------------------
# Translation helpers and command handlers
# ---------------------------------------------------------------------------

# Callback data constants for translation direction selection.
_TRANS_CB_OS_RU = 'trans:os_ru'
_TRANS_CB_RU_OS = 'trans:ru_os'


def _send_translate_direction_keyboard(chat_id: int) -> None:
    """Send an inline keyboard asking the user to choose a translation direction."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton(
            '✨ Осетинский → Русский', callback_data=_TRANS_CB_OS_RU,
        ),
        telebot.types.InlineKeyboardButton(
            '✨ Русский → Осетинский', callback_data=_TRANS_CB_RU_OS,
        ),
    )
    bot.send_message(chat_id, '🌐 Выберите направление перевода:', reply_markup=markup)


def _receive_translate_text(message: telebot.types.Message, source_lang: str,
                             target_lang: str) -> None:
    """Next-step handler: receives the text to translate and sends the result."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or '').strip()

    if not text or text.startswith('/'):
        logger.info('[Translator] Пустой или командный ввод от user_id=%s, перевод отменён',
                    user_id)
        bot.reply_to(message, '❌ Текст не введён. Перевод отменён.')
        return

    if not _DEEP_TRANSLATOR_AVAILABLE:
        bot.send_message(
            chat_id,
            '⚠️ Машинный перевод недоступен: библиотека <b>deep-translator</b> не установлена.',
            parse_mode='HTML',
        )
        return

    logger.info('[Translator] Перевод текста (%d симв.) %s→%s для user_id=%s',
                len(text), source_lang, target_lang, user_id)

    try:
        translated = translator.translate(text, target_lang=target_lang, source_lang=source_lang)
    except Exception as exc:  # noqa: BLE001
        logger.error('[Translator] Ошибка перевода для user_id=%s: %s', user_id, exc)
        bot.send_message(
            chat_id,
            '❌ Не удалось выполнить перевод. Попробуйте ещё раз.',
        )
        return

    # Persist translation to database.
    db.save_translation(
        user_id=user_id,
        source_lang=source_lang,
        target_lang=target_lang,
        original_text=text,
        translated_text=translated,
    )

    if source_lang == 'os' and target_lang == 'ru':
        direction_label = 'ос → рус'
    elif source_lang == 'ru' and target_lang == 'os':
        direction_label = 'рус → ос'
    else:
        direction_label = f'{source_lang} → {target_lang}'
    header = f'🌐 <b>Перевод</b> ({direction_label}):\n\n'
    full_reply = header + translated
    _send_long_message(chat_id, full_reply, parse_mode='HTML')
    logger.info('[Translator] Перевод отправлен user_id=%s', user_id)


@bot.callback_query_handler(func=lambda call: call.data in (_TRANS_CB_OS_RU, _TRANS_CB_RU_OS))
def callback_translate_direction(call: telebot.types.CallbackQuery) -> None:
    """Handle translation direction selection from inline keyboard."""
    bot.answer_callback_query(call.id)
    if call.data == _TRANS_CB_OS_RU:
        source_lang, target_lang = 'os', 'ru'
    else:
        source_lang, target_lang = 'ru', 'os'

    sent = bot.send_message(call.message.chat.id, '✏️ Введите текст для перевода:')
    bot.register_next_step_handler(
        sent, _receive_translate_text, source_lang, target_lang,
    )


@bot.message_handler(commands=['translate'])
def translate_corpus(message: telebot.types.Message) -> None:
    """/translate — choose translation direction (Ossetian ↔ Russian)."""
    logger.info('[/translate] user_id=%s', message.from_user.id)
    _send_translate_direction_keyboard(message.chat.id)


@bot.message_handler(func=lambda m: m.text == '🌐 Переводчик')
def button_translate(message: telebot.types.Message) -> None:
    """Handle '🌐 Переводчик' button – show translation direction selection."""
    logger.info('[Button/🌐] user_id=%s', message.from_user.id)
    _send_translate_direction_keyboard(message.chat.id)


@bot.message_handler(func=lambda m: bool(m.text) and not m.text.startswith('/') and len(m.text.split()) == 1)
def explain_single_word(message: telebot.types.Message) -> None:
    """Automatically explain a single word sent as a plain message via Yandex.GPT."""
    _receive_yai_explain_text(message)


@bot.message_handler(content_types=['text'])
def add_to_corpus(message: telebot.types.Message) -> None:
    """Buffer plain-text messages; after COLLECT_WINDOW seconds of inactivity,
    analyse them all together and save each one as an individual text in the
    user's corpus (corpus_texts). Plain messages are texts, not named corpora.

    This handler only runs when auto-collect is enabled for the user via /collect.
    """
    # Ignore messages that start with '/' (commands not matched by other handlers).
    if message.text.startswith('/'):
        logger.info('[Buffer] Неизвестная команда от user_id=%s: %s', message.from_user.id, message.text.split()[0])
        return
    text = message.text.strip()
    if not text:
        return

    # Only the corpus owner may add texts.
    if not _is_owner(message):
        logger.debug('[Buffer] user_id=%s не является владельцем, сообщение проигнорировано', message.from_user.id)
        return

    user_id = message.from_user.id

    # Auto-collect is disabled by default; silently skip if the user hasn't enabled it.
    if user_id not in _auto_collect_enabled:
        logger.debug('[Buffer] Автосбор отключён для user_id=%s, сообщение проигнорировано', user_id)
        return

    if len(text) > MAX_TEXT_LENGTH:
        logger.info('[Buffer] Текст слишком длинный (%d симв.) от user_id=%s', len(text), user_id)
        bot.reply_to(
            message,
            f'Текст слишком длинный. Максимальная длина: {MAX_TEXT_LENGTH} символов.',
        )
        return

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
        telebot.types.BotCommand('analyze',      'Статистика и частые слова корпуса'),
        telebot.types.BotCommand('frequency',    'Частотность слов в корпусе'),
        telebot.types.BotCommand('wordcloud',    'Облако слов корпуса'),
        telebot.types.BotCommand('stats',        'Краткая статистика корпуса'),
        telebot.types.BotCommand('corpus',       'Статистика вашего корпуса'),
        telebot.types.BotCommand('load',         'Найти произведение по названию и получить текст'),
        telebot.types.BotCommand('import_texts', 'Импортировать .txt файлы из папки texts/'),
        telebot.types.BotCommand('collect',      'Включить/выключить автосбор текстовых сообщений'),
        telebot.types.BotCommand('search',       'Поиск слова в корпусе с примерами предложений'),
        telebot.types.BotCommand('similar',      'Поиск семантически похожих предложений в корпусе'),
        telebot.types.BotCommand('morph',        'Морфологический анализ слова'),
        telebot.types.BotCommand('morph_stats',  'Статистика частей речи в корпусе'),
        telebot.types.BotCommand('morph_freq',   'Частота грамматических форм в корпусе'),
        telebot.types.BotCommand('translate',    'Перевести корпус на указанный язык'),
    ]
    bot.set_my_commands(commands)
    logger.info('Команды меню зарегистрированы (%d команд)', len(commands))


def _send_startup_notification() -> None:
    """Send a one-time startup notification to the configured user."""
    try:
        bot.send_message(_STARTUP_NOTIFY_USER_ID, _get_random_startup_message())
        logger.info('Startup notification sent to user_id=%d', _STARTUP_NOTIFY_USER_ID)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Failed to send startup notification to user_id=%d: %s',
                       _STARTUP_NOTIFY_USER_ID, exc)


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError('TELEGRAM_TOKEN is not set. Please configure it in your .env file.')

    logger.info('Bot is starting...')
    logger.info('Настройки: DB=%s, MAX_TEXT=%d, TOP_WORDS=%d, COLLECT_WINDOW=%ds',
                DB_FILE, MAX_TEXT_LENGTH, TOP_WORDS, COLLECT_WINDOW)
    _register_commands()
    _send_startup_notification()
    try:
        logger.info('Запуск infinity_polling...')
        bot.infinity_polling()
    finally:
        with _buffer_lock:
            for timer in _user_timers.values():
                timer.cancel()
            _user_timers.clear()
        db.close_connection()
        logger.info('Database connection closed.')


if __name__ == '__main__':
    main()

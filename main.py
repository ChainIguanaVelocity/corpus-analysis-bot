"""Ирон корпусы анализы бот — single-file entry point.

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
from collections import Counter
from sqlite3 import Error

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from wordcloud import WordCloud

# ---------------------------------------------------------------------------
# Configuration  (previously config.py)
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_TOKEN: str = os.getenv('TELEGRAM_TOKEN', '')
DB_FILE: str = os.getenv('DB_FILE', 'corpus_analysis.sqlite3')
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
MAX_TEXT_LENGTH: int = int(os.getenv('MAX_TEXT_LENGTH', '10000'))
TOP_WORDS: int = int(os.getenv('TOP_WORDS', '20'))

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
            # check_same_thread=False is safe here because all async bot
            # handler coroutines run in a single event-loop thread.
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

def _save_figure(fig):
    """Save a matplotlib figure to a temporary PNG file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    fig.savefig(tmp.name, format='png', bbox_inches='tight')
    tmp.close()
    return tmp.name


class DataVisualizer:
    def plot_frequency_distribution(self, freq_dict, title='Word Frequency Distribution'):
        """Plot top-N word frequencies and save to a temp file. Returns file path."""
        top = dict(list(sorted(freq_dict.items(), key=lambda x: x[1], reverse=True))[:20])
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.bar(top.keys(), top.values(), color='steelblue')
        ax.set_title(title)
        ax.set_xlabel('Word')
        ax.set_ylabel('Frequency')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        path = _save_figure(fig)
        plt.close(fig)
        return path

    def plot_word_cloud(self, freq_dict_or_text, title='Word Cloud'):
        """Generate a word cloud image and save to a temp file. Returns file path."""
        if isinstance(freq_dict_or_text, dict):
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate_from_frequencies(freq_dict_or_text)
        else:
            wc = WordCloud(width=800, height=400, background_color='white', max_words=200)
            wc = wc.generate(freq_dict_or_text)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(wc, interpolation='bilinear')
        ax.axis('off')
        ax.set_title(title)
        plt.tight_layout()
        path = _save_figure(fig)
        plt.close(fig)
        return path

# ---------------------------------------------------------------------------
# Bot globals
# ---------------------------------------------------------------------------
analyzer = TextAnalyzer()
db = Database(DB_FILE)
vis = DataVisualizer()

# ---------------------------------------------------------------------------
# Bot handlers  (previously bot.py)
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message explaining available commands."""
    await update.message.reply_text(
        '👋 Хæрзбон! *Ирон корпусы анализы бот*-мæ хæрзбон!\n\n'
        'Бот ирон æвзаджы текстытæ анализ кæны.\n\n'
        'Фæрæзтæ:\n'
        '  /analyze <текст> — бæрæггæнæнтæ + хъуыддагдæр дзырдтæ\n'
        '  /frequency <текст> — дзырдты частотæйы диаграммæ\n'
        '  /wordcloud <текст> — дзырдты облакæ\n'
        '  /stats <текст> — текстæн лæмæгъ статистикæ\n\n'
        'Командæйы фæстæ текст ныффыс.',
        parse_mode='Markdown',
    )


async def _get_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Extract and validate the text argument from the command."""
    text = ' '.join(context.args).strip()
    if not text:
        await update.message.reply_text('Командæйы фæстæ текст ныффыс. Æппæлæг:\n/analyze Ирон æвзаг')
        return None
    if len(text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(
            f'Текст æппынæддæр рæсугъд у. Хистæр бæрц: {MAX_TEXT_LENGTH} знаджы.'
        )
        return None
    return text


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyze <text> — run full analysis and return formatted results."""
    text = await _get_text(update, context)
    if text is None:
        return

    result = analyzer.analyze(text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])

    reply = (
        f'📊 *Анализы хъуыддæгтæ*\n\n'
        f'*Статистикæ:*\n'
        f'  • Дзырдтæ (иугай): {stats["total_words"]}\n'
        f'  • Нæмыгдæттон дзырдтæ: {stats["unique_words"]}\n'
        f'  • Хъуырытæ: {stats["sentences"]}\n'
        f'  • Дзырды æнцон дæргъ: {stats["avg_word_length"]:.2f}\n'
        f'  • Лексикалон æнтысгæ: {stats["lexical_diversity"]:.2%}\n'
        f'  • Токентæ: {result["tokens_count"]}\n'
        f'  • Нæмыгдæттон леммæтæ: {result["lemmas_count"]}\n\n'
        f'*Хистæр {TOP_WORDS} дзырды:*\n'
    )
    for word, count in freq.items():
        reply += f'  {word}: {count}\n'

    user_id = update.effective_user.id
    db.insert_analysis((user_id, json.dumps(result['stats'])))
    await update.message.reply_text(reply, parse_mode='Markdown')


async def frequency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/frequency <text> — send a frequency distribution bar chart."""
    text = await _get_text(update, context)
    if text is None:
        return

    freq_dict = analyzer.analyze(text)['frequency']
    if not freq_dict:
        await update.message.reply_text('Частотæйы анализæн дзырдтæ нæй.')
        return

    image_path = vis.plot_frequency_distribution(freq_dict, title='Дзырдты частотæ')
    try:
        with open(image_path, 'rb') as img:
            await update.message.reply_photo(photo=img, caption='Дзырдты частотæ')
    finally:
        os.unlink(image_path)


async def wordcloud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wordcloud <text> — generate and send a word cloud image."""
    text = await _get_text(update, context)
    if text is None:
        return

    freq_dict = analyzer.analyze(text)['frequency']
    if not freq_dict:
        await update.message.reply_text('Дзырдты облакæ аразынæн дзырдтæ нæй.')
        return

    image_path = vis.plot_word_cloud(freq_dict, title='Дзырдты облакæ')
    try:
        with open(image_path, 'rb') as img:
            await update.message.reply_photo(photo=img, caption='Дзырдты облакæ')
    finally:
        os.unlink(image_path)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats <text> — return brief text statistics."""
    text = await _get_text(update, context)
    if text is None:
        return

    s = analyzer.get_text_stats(text)
    reply = (
        f'📈 *Текстæн статистикæ*\n\n'
        f'  • Дзырдтæ (иугай): {s["total_words"]}\n'
        f'  • Нæмыгдæттон дзырдтæ: {s["unique_words"]}\n'
        f'  • Хъуырытæ: {s["sentences"]}\n'
        f'  • Дзырды æнцон дæргъ: {s["avg_word_length"]:.2f}\n'
        f'  • Лексикалон æнтысгæ: {s["lexical_diversity"]:.2%}\n'
    )
    await update.message.reply_text(reply, parse_mode='Markdown')

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError('TELEGRAM_TOKEN is not set. Please configure it in your .env file.')

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('analyze', analyze))
    app.add_handler(CommandHandler('frequency', frequency))
    app.add_handler(CommandHandler('wordcloud', wordcloud))
    app.add_handler(CommandHandler('stats', stats))

    logger.info('Bot is starting...')
    try:
        app.run_polling()
    finally:
        db.close_connection()
        logger.info('Database connection closed.')


if __name__ == '__main__':
    main()

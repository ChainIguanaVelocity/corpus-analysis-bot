import logging
import os
import json
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from text_analyzer import TextAnalyzer
from visualizer import DataVisualizer
from database import Database
from config import TELEGRAM_TOKEN, DB_FILE, LOG_LEVEL, MAX_TEXT_LENGTH, TOP_WORDS

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

analyzer = TextAnalyzer()
db = Database(DB_FILE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message explaining available commands."""
    await update.message.reply_text(
        '👋 Welcome to *Corpus Analysis Bot*!\n\n'
        'Available commands:\n'
        '  /analyze <text> — full text analysis (stats + top words)\n'
        '  /frequency <text> — word frequency chart\n'
        '  /wordcloud <text> — word cloud image\n'
        '  /stats <text> — text statistics summary\n\n'
        'Pass the text you want to analyse right after the command.',
        parse_mode='Markdown',
    )


async def _get_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Extract and validate the text argument from the command."""
    text = ' '.join(context.args).strip()
    if not text:
        await update.message.reply_text('Please provide text after the command. Example:\n/analyze Hello world')
        return None
    if len(text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(
            f'Text is too long. Maximum allowed length is {MAX_TEXT_LENGTH} characters.'
        )
        return None
    return text


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyze <text> — run full analysis and return JSON-formatted results."""
    text = await _get_text(update, context)
    if text is None:
        return

    result = analyzer.analyze(text)
    stats = result['stats']
    freq = dict(list(result['frequency'].items())[:TOP_WORDS])

    reply = (
        f'📊 *Analysis Results*\n\n'
        f'*Statistics:*\n'
        f'  • Total words: {stats["total_words"]}\n'
        f'  • Unique words: {stats["unique_words"]}\n'
        f'  • Sentences: {stats["sentences"]}\n'
        f'  • Avg word length: {stats["avg_word_length"]:.2f}\n'
        f'  • Lexical diversity: {stats["lexical_diversity"]:.2%}\n'
        f'  • Tokens: {result["tokens_count"]}\n'
        f'  • Unique lemmas: {result["lemmas_count"]}\n\n'
        f'*Top {TOP_WORDS} words:*\n'
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

    result = analyzer.analyze(text)
    freq_dict = result['frequency']

    if not freq_dict:
        await update.message.reply_text('No words found for frequency analysis.')
        return

    vis = DataVisualizer(None)
    image_path = vis.plot_frequency_distribution(freq_dict, title='Word Frequency Distribution')

    try:
        with open(image_path, 'rb') as img:
            await update.message.reply_photo(photo=img, caption='Word Frequency Distribution')
    finally:
        os.unlink(image_path)


async def wordcloud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wordcloud <text> — generate and send a word cloud image."""
    text = await _get_text(update, context)
    if text is None:
        return

    result = analyzer.analyze(text)
    freq_dict = result['frequency']

    if not freq_dict:
        await update.message.reply_text('Not enough words to generate a word cloud.')
        return

    vis = DataVisualizer(None)
    image_path = vis.plot_word_cloud(freq_dict, title='Word Cloud')

    try:
        with open(image_path, 'rb') as img:
            await update.message.reply_photo(photo=img, caption='Word Cloud')
    finally:
        os.unlink(image_path)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats <text> — return brief text statistics."""
    text = await _get_text(update, context)
    if text is None:
        return

    s = analyzer.get_text_stats(text)

    reply = (
        f'📈 *Text Statistics*\n\n'
        f'  • Total words: {s["total_words"]}\n'
        f'  • Unique words: {s["unique_words"]}\n'
        f'  • Sentences: {s["sentences"]}\n'
        f'  • Avg word length: {s["avg_word_length"]:.2f}\n'
        f'  • Lexical diversity: {s["lexical_diversity"]:.2%}\n'
    )
    await update.message.reply_text(reply, parse_mode='Markdown')


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

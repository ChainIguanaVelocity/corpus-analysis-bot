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
    """/analyze <text> — run full analysis and return JSON-formatted results."""
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

    result = analyzer.analyze(text)
    freq_dict = result['frequency']

    if not freq_dict:
        await update.message.reply_text('Частотæйы анализæн дзырдтæ нæй.')
        return

    vis = DataVisualizer(None)
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

    result = analyzer.analyze(text)
    freq_dict = result['frequency']

    if not freq_dict:
        await update.message.reply_text('Дзырдты облакæ аразынæн дзырдтæ нæй.')
        return

    vis = DataVisualizer(None)
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

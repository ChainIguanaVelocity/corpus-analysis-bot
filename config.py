import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')

# Database
DB_FILE = os.getenv('DB_FILE', 'corpus_analysis.sqlite3')

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'app.log')

# Analysis limits
MAX_TEXT_LENGTH = int(os.getenv('MAX_TEXT_LENGTH', '10000'))
TOP_WORDS = int(os.getenv('TOP_WORDS', '20'))

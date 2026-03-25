# Ирон корпусы анализы бот / Ossetian Corpus Analysis Bot

A Telegram bot for linguistic and statistical analysis of **Ossetian (Iron dialect)** text corpora. Supports word frequency analysis, stopword removal, visualisations (bar charts & word clouds), and per-user analysis history stored in SQLite.

---

## Features

- **Text Analysis** (`/analyze`) — tokenisation, Ossetian stopword removal, frequency distribution, and a full statistics summary.
- **Frequency Chart** (`/frequency`) — returns a bar chart of the top-20 most frequent words.
- **Word Cloud** (`/wordcloud`) — generates a word cloud image from the input text.
- **Statistics** (`/stats`) — word count, unique words, sentence count, average word length, and lexical diversity.
- **Persistent Storage** — analysis results are saved to a local SQLite database.

---

## Requirements

- Python 3.10+
- A Telegram Bot token (create one via [@BotFather](https://t.me/BotFather))

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/ChainIguanaVelocity/corpus-analysis-bot.git
cd corpus-analysis-bot

# 2. Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
TELEGRAM_TOKEN=your-telegram-bot-token-here   # required
DB_FILE=corpus_analysis.sqlite3               # optional
LOG_LEVEL=INFO                                # optional
MAX_TEXT_LENGTH=10000                         # optional
TOP_WORDS=20                                  # optional
```

> **Never commit `.env` to version control** — it is already listed in `.gitignore`.

---

## Running the Bot

```bash
python main.py
```

The bot will start polling Telegram for updates. Press `Ctrl+C` to stop.

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Display the welcome message and list of commands |
| `/analyze <text>` | Full analysis: statistics + top-N frequent words |
| `/frequency <text>` | Bar chart of word frequencies |
| `/wordcloud <text>` | Word cloud image |
| `/stats <text>` | Brief text statistics summary |

**Example:**
```
/analyze Ирон æвзаг — алы рæстæджы дæр зæрдæмæ цæуы.
```

---

## Project Structure

```
corpus-analysis-bot/
├── main.py            # Entry point
├── bot.py             # Telegram bot command handlers (python-telegram-bot v20)
├── text_analyzer.py   # Text tokenization, lemmatization, frequency analysis
├── visualizer.py      # Chart and word cloud generation (matplotlib / wordcloud)
├── database.py        # SQLite database wrapper
├── config.py          # Configuration loaded from environment variables
├── requirements.txt   # Python dependencies
├── .env.example       # Environment variable template
└── .gitignore
```

---

## Dependencies

| Library | Purpose |
|---------|---------|
| `python-telegram-bot` | Telegram Bot API client |
| `matplotlib` / `seaborn` | Charts |
| `wordcloud` | Word cloud image generation |
| `python-dotenv` | `.env` file loading |

---

## Contributing

1. Fork the repository.
2. Create a new branch for your feature or fix.
3. Make your changes and commit them.
4. Push your branch and open a pull request.

---

## License

This project is licensed under the MIT License.
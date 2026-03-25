# Corpus Analysis Bot

A Telegram bot for linguistic and statistical analysis of text corpora. Supports word frequency analysis, lemmatization, stopword removal, visualizations (bar charts & word clouds), and per-user analysis history stored in SQLite.

---

## Features

- **Text Analysis** (`/analyze`) — tokenization, lemmatization (Russian & English via pymorphy2), frequency distribution, and a full statistics summary.
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

## Web Dashboard

A lightweight status dashboard starts automatically alongside the bot at:

```
http://localhost:5000
```

It shows:

- **Bot status** (running / stopped) and the time it started
- **Total analyses** performed (persisted in SQLite)
- **20 most recent analysis records** with statistics for each

The dashboard auto-refreshes every 30 seconds.

A JSON status endpoint is also available at `http://localhost:5000/api/status`.

You can change the host/port in `.env`:

```dotenv
DASHBOARD_HOST=127.0.0.1  # set to 0.0.0.0 to expose on all interfaces
DASHBOARD_PORT=5000        # port number
```

> **Production note:** the built-in Flask development server is used by default.
> For production deployments, run the app behind a WSGI server such as Gunicorn:
> ```bash
> gunicorn "dashboard:app"
> ```

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
/analyze Привет! Это тестовый текст для анализа корпуса.
```

---

## Project Structure

```
corpus-analysis-bot/
├── main.py            # Entry point (starts bot + dashboard)
├── bot.py             # Telegram bot command handlers (python-telegram-bot v20)
├── dashboard.py       # Flask web dashboard
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
| `pymorphy3` | Russian morphological analyser + tokenization (Python 3.10+ compatible) |
| `matplotlib` / `seaborn` | Charts |
| `wordcloud` | Word cloud image generation |
| `python-dotenv` | `.env` file loading |
| `flask` | Web dashboard |

---

## Contributing

1. Fork the repository.
2. Create a new branch for your feature or fix.
3. Make your changes and commit them.
4. Push your branch and open a pull request.

---

## License

This project is licensed under the MIT License.
"""Entry point for the Corpus Analysis Bot.

Usage:
    python main.py

Requires TELEGRAM_TOKEN to be set in the .env file or as an environment variable.
The web dashboard starts automatically at http://localhost:5000 (configurable via
DASHBOARD_HOST and DASHBOARD_PORT in .env).
"""
import threading

from bot import main as bot_main
from config import DASHBOARD_HOST, DASHBOARD_PORT
from dashboard import run_dashboard, set_bot_running

if __name__ == '__main__':
    dashboard_thread = threading.Thread(
        target=run_dashboard,
        args=(DASHBOARD_HOST, DASHBOARD_PORT),
        daemon=True,
        name='dashboard',
    )
    dashboard_thread.start()

    set_bot_running(True)
    try:
        bot_main()
    finally:
        set_bot_running(False)

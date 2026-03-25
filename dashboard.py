"""Web dashboard for the Corpus Analysis Bot.

Provides a lightweight Flask UI that shows:
  - Bot running status
  - Total number of analyses performed
  - 20 most recent analysis records stored in SQLite
  - Per-word frequency stats for each record

Run alongside the bot via ``main.py``.  Access at http://localhost:5000 by default.

Note: the built-in Flask development server is used for simplicity.  For
production deployments, run the app behind a WSGI server such as Gunicorn::

    gunicorn "dashboard:app"
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string

from config import DASHBOARD_HOST, DASHBOARD_PORT, DB_FILE

app = Flask(__name__)

# Shared state updated by the bot process
_bot_state: dict = {"running": False, "started_at": None}
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# State helpers (called from main.py)
# ---------------------------------------------------------------------------

def set_bot_running(running: bool) -> None:
    """Mark the bot as running or stopped.  Thread-safe."""
    with _state_lock:
        _bot_state["running"] = running
        _bot_state["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if running else None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_summary():
    """Return total analysis count and last-20 records."""
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM analyses")
        total = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT id, user_id, analysis_result, created_at "
            "FROM analyses ORDER BY id DESC LIMIT 20"
        )
        rows = []
        for row in cur.fetchall():
            try:
                stats = json.loads(row["analysis_result"])
            except (json.JSONDecodeError, TypeError):
                stats = {}
            rows.append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "created_at": row["created_at"],
                    "stats": stats,
                }
            )
        conn.close()
        return total, rows
    except sqlite3.Error:
        return 0, []


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Corpus Analysis Bot — Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f4f6f9; color: #222; }
    header { background: #2c3e50; color: #fff; padding: 1rem 2rem;
             display: flex; align-items: center; gap: 1rem; }
    header h1 { font-size: 1.4rem; }
    .badge { padding: .25rem .75rem; border-radius: 999px; font-size: .85rem;
             font-weight: 600; }
    .badge-on  { background: #27ae60; color: #fff; }
    .badge-off { background: #e74c3c; color: #fff; }
    main { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
             gap: 1rem; margin-bottom: 2rem; }
    .card { background: #fff; border-radius: 8px; padding: 1.25rem 1.5rem;
            box-shadow: 0 1px 4px rgba(0,0,0,.1); }
    .card .label { font-size: .8rem; text-transform: uppercase; color: #888; }
    .card .value { font-size: 2rem; font-weight: 700; margin-top: .25rem; }
    table { width: 100%; border-collapse: collapse; background: #fff;
            border-radius: 8px; overflow: hidden;
            box-shadow: 0 1px 4px rgba(0,0,0,.1); }
    th { background: #2c3e50; color: #fff; text-align: left;
         padding: .75rem 1rem; font-size: .85rem; }
    td { padding: .65rem 1rem; border-bottom: 1px solid #eee;
         font-size: .9rem; vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #f9fbfc; }
    .mono { font-family: monospace; font-size: .8rem; color: #555; }
    footer { text-align: center; margin: 3rem 0 1rem; font-size: .8rem; color: #aaa; }
  </style>
</head>
<body>
<header>
  <h1>📊 Corpus Analysis Bot</h1>
  <span class="badge {{ 'badge-on' if bot_running else 'badge-off' }}">
    {{ '● Running' if bot_running else '○ Stopped' }}
  </span>
  {% if started_at %}
  <span style="font-size:.85rem;opacity:.7">since {{ started_at }}</span>
  {% endif %}
</header>
<main>
  <div class="cards">
    <div class="card">
      <div class="label">Total Analyses</div>
      <div class="value">{{ total }}</div>
    </div>
    <div class="card">
      <div class="label">Records Shown</div>
      <div class="value">{{ records|length }}</div>
    </div>
  </div>

  {% if records %}
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>User ID</th>
        <th>Created At</th>
        <th>Words</th>
        <th>Unique</th>
        <th>Sentences</th>
        <th>Avg Len</th>
        <th>Diversity</th>
      </tr>
    </thead>
    <tbody>
      {% for r in records %}
      <tr>
        <td class="mono">{{ r.id }}</td>
        <td class="mono">{{ r.user_id }}</td>
        <td class="mono">{{ r.created_at }}</td>
        {% set s = r.stats %}
        <td>{{ s.get('total_words', '—') }}</td>
        <td>{{ s.get('unique_words', '—') }}</td>
        <td>{{ s.get('sentences', '—') }}</td>
        <td>{{ '%.2f'|format(s.get('avg_word_length', 0)) if s.get('avg_word_length') is not none else '—' }}</td>
        <td>{{ '%.1f%%'|format(s.get('lexical_diversity', 0) * 100) if s.get('lexical_diversity') is not none else '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#888;text-align:center;padding:2rem">No analyses recorded yet.</p>
  {% endif %}
</main>
<footer>Auto-refreshes every 30 s &nbsp;·&nbsp; Corpus Analysis Bot Dashboard</footer>
<script>setTimeout(() => location.reload(), 30000);</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    total, records = _fetch_summary()
    with _state_lock:
        running = _bot_state["running"]
        started_at = _bot_state["started_at"]
    return render_template_string(
        _TEMPLATE,
        bot_running=running,
        started_at=started_at,
        total=total,
        records=records,
    )


@app.route("/api/status")
def api_status():
    total, _ = _fetch_summary()
    with _state_lock:
        state = dict(_bot_state)
    state["total_analyses"] = total
    return jsonify(state)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def run_dashboard(host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> None:
    """Start the Flask development server (blocking).  Call from a daemon thread."""
    app.run(host=host, port=port, use_reloader=False, debug=False)

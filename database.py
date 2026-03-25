import sqlite3
from sqlite3 import Error


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
        self.create_table(
            '''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT
            )'''
        )
        self.create_table(
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

    def create_table(self, table_creation_sql):
        """Create a table from the provided SQL statement."""
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

    def insert_user(self, user_data):
        """Insert a new user record. user_data = (username, email)."""
        sql = 'INSERT INTO users(username, email) VALUES(?, ?)'
        try:
            cur = self.conn.cursor()
            cur.execute(sql, user_data)
            self.conn.commit()
            return cur.lastrowid
        except Error as e:
            print(f'insert_user error: {e}')
            return None

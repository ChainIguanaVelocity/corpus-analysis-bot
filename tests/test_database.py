"""Unit tests for Database."""
import os
import tempfile
import pytest
from database import Database


@pytest.fixture()
def db(tmp_path):
    db_file = str(tmp_path / 'test.sqlite3')
    database = Database(db_file)
    yield database
    database.close_connection()


class TestDatabaseInit:
    def test_creates_file(self, tmp_path):
        db_file = str(tmp_path / 'new.sqlite3')
        db = Database(db_file)
        assert os.path.exists(db_file)
        db.close_connection()

    def test_connection_established(self, db):
        assert db.conn is not None


class TestInsertAnalysis:
    def test_returns_row_id(self, db):
        row_id = db.insert_analysis((1, '{"total_words": 5}'))
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_sequential_ids(self, db):
        id1 = db.insert_analysis((1, 'result_a'))
        id2 = db.insert_analysis((2, 'result_b'))
        assert id2 > id1

    def test_data_persists(self, db):
        db.insert_analysis((42, 'persisted_result'))
        cur = db.conn.cursor()
        cur.execute('SELECT user_id, analysis_result FROM analyses WHERE user_id=42')
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 42
        assert row[1] == 'persisted_result'


class TestInsertUser:
    def test_returns_row_id(self, db):
        row_id = db.insert_user(('alice', 'alice@example.com'))
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_data_persists(self, db):
        db.insert_user(('bob', 'bob@example.com'))
        cur = db.conn.cursor()
        cur.execute("SELECT username, email FROM users WHERE username='bob'")
        row = cur.fetchone()
        assert row == ('bob', 'bob@example.com')

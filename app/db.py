import os
import sqlite3
from collections.abc import Generator


DATABASE_PATH = os.getenv("DATABASE_PATH", "learner.db")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL CHECK (language IN ('de', 'el')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS phrases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL CHECK (language IN ('de', 'el')),
                source_title TEXT NOT NULL,
                video_path TEXT,
                text TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                segment_kind TEXT NOT NULL DEFAULT 'translation',
                duration_seconds REAL NOT NULL DEFAULT 0,
                processing_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase_id INTEGER NOT NULL REFERENCES phrases(id),
                kind TEXT NOT NULL DEFAULT 'word_order',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_id INTEGER NOT NULL REFERENCES exercises(id),
                answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS session_items (
                session_id INTEGER NOT NULL REFERENCES training_sessions(id),
                phrase_id INTEGER NOT NULL REFERENCES phrases(id),
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, phrase_id, kind)
            );
            CREATE TABLE IF NOT EXISTS session_candidates (
                session_id INTEGER NOT NULL REFERENCES training_sessions(id),
                phrase_id INTEGER NOT NULL REFERENCES phrases(id),
                PRIMARY KEY (session_id, phrase_id)
            );
            CREATE TABLE IF NOT EXISTS translations (
                phrase_id INTEGER PRIMARY KEY REFERENCES phrases(id),
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'yandex',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dictionary_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL CHECK (language IN ('de', 'el')),
                word TEXT NOT NULL,
                translation TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(language, word)
            );
            CREATE TABLE IF NOT EXISTS dictionary_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL CHECK (language IN ('de', 'el')),
                to_russian_count INTEGER NOT NULL,
                from_russian_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dictionary_session_items (
                session_id INTEGER NOT NULL REFERENCES dictionary_sessions(id),
                word_id INTEGER NOT NULL REFERENCES dictionary_words(id),
                direction TEXT NOT NULL CHECK (direction IN ('to_russian', 'from_russian')),
                issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, word_id, direction)
            );
            CREATE TABLE IF NOT EXISTS dictionary_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES dictionary_sessions(id),
                word_id INTEGER NOT NULL REFERENCES dictionary_words(id),
                direction TEXT NOT NULL CHECK (direction IN ('to_russian', 'from_russian')),
                answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS history_review_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL CHECK (language IN ('de', 'el')),
                history_limit INTEGER NOT NULL,
                requested_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS history_review_items (
                session_id INTEGER NOT NULL REFERENCES history_review_sessions(id),
                exercise_id INTEGER NOT NULL REFERENCES exercises(id),
                position INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, exercise_id)
            );
            CREATE TABLE IF NOT EXISTS history_review_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES history_review_sessions(id),
                exercise_id INTEGER NOT NULL REFERENCES exercises(id),
                answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(phrases)").fetchall()
        }
        if "video_path" not in columns:
            connection.execute("ALTER TABLE phrases ADD COLUMN video_path TEXT")
        if "segment_kind" not in columns:
            connection.execute(
                "ALTER TABLE phrases ADD COLUMN segment_kind TEXT NOT NULL DEFAULT 'translation'"
            )
        if "duration_seconds" not in columns:
            connection.execute(
                "ALTER TABLE phrases ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0"
            )
        if "processing_version" not in columns:
            connection.execute(
                "ALTER TABLE phrases ADD COLUMN processing_version INTEGER NOT NULL DEFAULT 0"
            )
        session_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(training_sessions)"
            ).fetchall()
        }
        if "listening_count" not in session_columns:
            connection.execute(
                "ALTER TABLE training_sessions ADD COLUMN listening_count INTEGER NOT NULL DEFAULT 3"
            )
        if "translation_count" not in session_columns:
            connection.execute(
                "ALTER TABLE training_sessions ADD COLUMN translation_count INTEGER NOT NULL DEFAULT 5"
            )
        if "listening_length" not in session_columns:
            connection.execute(
                "ALTER TABLE training_sessions ADD COLUMN listening_length TEXT NOT NULL DEFAULT 'medium'"
            )
        if "review_count" not in session_columns:
            connection.execute(
                "ALTER TABLE training_sessions ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"
            )
        dictionary_session_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(dictionary_sessions)"
            ).fetchall()
        }
        if "review_count" not in dictionary_session_columns:
            connection.execute(
                "ALTER TABLE dictionary_sessions ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"
            )


def get_connection() -> Generator[sqlite3.Connection, None, None]:
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()

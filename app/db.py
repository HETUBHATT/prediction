import os
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(os.getenv("ACADEMIC_DB_PATH", "data/academic.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
 department TEXT NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8),
 phone TEXT, attendance REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS faculty (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
 department TEXT NOT NULL, phone TEXT, faculty_code_hash TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS academic_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 subject TEXT NOT NULL, semester INTEGER NOT NULL, internal_marks REAL NOT NULL DEFAULT 0,
 test_marks REAL NOT NULL DEFAULT 0, previous_semester_marks REAL NOT NULL DEFAULT 0,
 percentile_12th REAL NOT NULL DEFAULT 0, UNIQUE(student_id, subject, semester)
);
CREATE TABLE IF NOT EXISTS attendance (
 id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 subject TEXT NOT NULL, classes_held INTEGER NOT NULL, classes_attended INTEGER NOT NULL, date TEXT,
 UNIQUE(student_id, subject, date)
);
CREATE TABLE IF NOT EXISTS assignments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 subject TEXT NOT NULL, title TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL DEFAULT 'pending',
 score REAL, UNIQUE(student_id, subject, title)
);
CREATE TABLE IF NOT EXISTS tests (
 id INTEGER PRIMARY KEY AUTOINCREMENT, faculty_id INTEGER REFERENCES faculty(id), subject TEXT NOT NULL,
 title TEXT NOT NULL, total_marks REAL NOT NULL, scheduled_at TEXT, question_paper TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
 student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, answer_paper TEXT NOT NULL,
 file_name TEXT, file_path TEXT, content_type TEXT, file_size INTEGER, annotations_json TEXT NOT NULL DEFAULT '[]',
 submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, score REAL, feedback TEXT, status TEXT NOT NULL DEFAULT 'submitted',
 UNIQUE(test_id, student_id)
);
CREATE TABLE IF NOT EXISTS results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 subject TEXT NOT NULL, semester INTEGER NOT NULL, marks REAL NOT NULL, total_marks REAL NOT NULL DEFAULT 100,
 grade TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 1, published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with connection() as conn:
        conn.executescript(SCHEMA)
        faculty_columns = {row[1] for row in conn.execute('PRAGMA table_info(faculty)').fetchall()}
        if 'faculty_code_hash' not in faculty_columns:
            conn.execute('ALTER TABLE faculty ADD COLUMN faculty_code_hash TEXT')
        conn.execute(
            'UPDATE faculty SET faculty_code_hash=? WHERE faculty_code_hash IS NULL',
            [hashlib.sha256(b'2124').hexdigest()],
        )
        columns = {row[1] for row in conn.execute('PRAGMA table_info(submissions)').fetchall()}
        for name, definition in (
            ('file_name', 'TEXT'),
            ('file_path', 'TEXT'),
            ('content_type', 'TEXT'),
            ('file_size', 'INTEGER'),
            ('annotations_json', "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in columns:
                conn.execute(f'ALTER TABLE submissions ADD COLUMN {name} {definition}')

def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

def execute(sql: str, params: Iterable[Any] = ()) -> dict[str, Any]:
    with connection() as conn:
        cursor = conn.execute(sql, tuple(params))
        return {"id": cursor.lastrowid, "affected": cursor.rowcount}

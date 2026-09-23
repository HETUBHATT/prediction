import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
if ENV_FILE.is_file():
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL is not set. Configure the PostgreSQL connection string before starting the API.')
IntegrityError = psycopg.IntegrityError

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        department TEXT NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8),
        phone TEXT, attendance DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS faculty (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        department TEXT NOT NULL, phone TEXT, faculty_code_hash TEXT, password_hash TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS academic_records (
        id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        subject TEXT NOT NULL, semester INTEGER NOT NULL, internal_marks DOUBLE PRECISION NOT NULL DEFAULT 0,
        test_marks DOUBLE PRECISION NOT NULL DEFAULT 0, previous_semester_marks DOUBLE PRECISION NOT NULL DEFAULT 0,
        percentile_12th DOUBLE PRECISION NOT NULL DEFAULT 0, UNIQUE(student_id, subject, semester))""",
    """CREATE TABLE IF NOT EXISTS attendance (
        id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        subject TEXT NOT NULL, classes_held INTEGER NOT NULL, classes_attended INTEGER NOT NULL, date TEXT,
        UNIQUE(student_id, subject, date))""",
    """CREATE TABLE IF NOT EXISTS assignments (
        id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        subject TEXT NOT NULL, title TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL DEFAULT 'pending',
        score DOUBLE PRECISION, submission_file_name TEXT, submission_file_path TEXT,
        submission_content_type TEXT, submission_file_size INTEGER, submitted_at TIMESTAMPTZ,
        UNIQUE(student_id, subject, title))""",
    """CREATE TABLE IF NOT EXISTS tests (
        id SERIAL PRIMARY KEY, faculty_id INTEGER REFERENCES faculty(id), subject TEXT NOT NULL,
        title TEXT NOT NULL, total_marks DOUBLE PRECISION NOT NULL, scheduled_at TEXT, question_paper TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY, test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, answer_paper TEXT NOT NULL,
        file_name TEXT, file_path TEXT, content_type TEXT, file_size INTEGER,
        annotations_json TEXT NOT NULL DEFAULT '[]', manual_marks_json TEXT NOT NULL DEFAULT '[]', submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        score DOUBLE PRECISION, feedback TEXT, review_mode TEXT NOT NULL DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'submitted',
        UNIQUE(test_id, student_id))""",
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        token TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('admin', 'faculty', 'student')),
        account_id INTEGER NOT NULL, email TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS results (
        id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        subject TEXT NOT NULL, semester INTEGER NOT NULL, marks DOUBLE PRECISION NOT NULL,
        total_marks DOUBLE PRECISION NOT NULL DEFAULT 100, grade TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 1,
        published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
)


def _postgres_sql(sql: str) -> str:
    return re.sub(r'\?', '%s', sql)


@contextmanager
def connection():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def init_db():
    with connection() as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute('ALTER TABLE faculty ADD COLUMN IF NOT EXISTS faculty_code_hash TEXT')
        conn.execute('ALTER TABLE faculty ADD COLUMN IF NOT EXISTS password_hash TEXT')
        conn.execute('ALTER TABLE students ADD COLUMN IF NOT EXISTS password_hash TEXT')
        conn.execute('ALTER TABLE submissions ADD COLUMN IF NOT EXISTS file_name TEXT')
        conn.execute('ALTER TABLE submissions ADD COLUMN IF NOT EXISTS file_path TEXT')
        conn.execute('ALTER TABLE submissions ADD COLUMN IF NOT EXISTS content_type TEXT')
        conn.execute('ALTER TABLE submissions ADD COLUMN IF NOT EXISTS file_size INTEGER')
        conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS annotations_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS manual_marks_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS review_mode TEXT NOT NULL DEFAULT 'manual'")
        conn.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS submission_file_name TEXT')
        conn.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS submission_file_path TEXT')
        conn.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS submission_content_type TEXT')
        conn.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS submission_file_size INTEGER')
        conn.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ')


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        return list(conn.execute(_postgres_sql(sql), tuple(params)).fetchall())


def execute(sql: str, params: Iterable[Any] = ()) -> dict[str, Any]:
    with connection() as conn:
        if sql.lstrip().upper().startswith('INSERT') and 'AUTH_SESSIONS' not in sql.upper():
            row = conn.execute(_postgres_sql(sql) + ' RETURNING id', tuple(params)).fetchone()
            return {'id': row['id'], 'affected': 1}
        cursor = conn.execute(_postgres_sql(sql), tuple(params))
        return {'id': None, 'affected': cursor.rowcount}

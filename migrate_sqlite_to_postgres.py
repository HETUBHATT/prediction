import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.db import DATABASE_URL, init_db

SQLITE_PATH = Path(os.getenv('SQLITE_PATH', 'data/academic.db'))
TABLES = {
    'students': ('id', 'name', 'email', 'department', 'semester', 'phone', 'attendance', 'created_at'),
    'faculty': ('id', 'name', 'email', 'department', 'phone', 'faculty_code_hash', 'created_at'),
    'academic_records': ('id', 'student_id', 'subject', 'semester', 'internal_marks', 'test_marks', 'previous_semester_marks', 'percentile_12th'),
    'attendance': ('id', 'student_id', 'subject', 'classes_held', 'classes_attended', 'date'),
    'assignments': ('id', 'student_id', 'subject', 'title', 'due_date', 'status', 'score'),
    'tests': ('id', 'faculty_id', 'subject', 'title', 'total_marks', 'scheduled_at', 'question_paper', 'created_at'),
    'submissions': ('id', 'test_id', 'student_id', 'answer_paper', 'file_name', 'file_path', 'content_type', 'file_size', 'annotations_json', 'submitted_at', 'score', 'feedback', 'status'),
    'results': ('id', 'student_id', 'subject', 'semester', 'marks', 'total_marks', 'grade', 'published', 'published_at'),
}


def main():
    if not SQLITE_PATH.is_file():
        raise SystemExit(f'SQLite database not found: {SQLITE_PATH}')
    init_db()
    source = sqlite3.connect(SQLITE_PATH)
    source.row_factory = sqlite3.Row
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as target:
        for table, columns in TABLES.items():
            rows = source.execute(f'SELECT {", ".join(columns)} FROM {table}').fetchall()
            if not rows:
                continue
            placeholders = ', '.join(['%s'] * len(columns))
            names = ', '.join(columns)
            statement = f'INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING'
            with target.cursor() as cursor:
                cursor.executemany(statement, [tuple(row[column] for column in columns) for row in rows])
            target.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
            )
            print(f'{table}: {len(rows)} rows processed')
    source.close()
    print('SQLite to PostgreSQL migration complete')


if __name__ == '__main__':
    main()

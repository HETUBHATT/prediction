# Predictive Student Academic Performance API

FastAPI backend for student management, academic records, attendance, assignments, online tests, results, risk prediction, analytics, and report exports.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

Open Swagger UI at <http://127.0.0.1:4009/docs>.

The API uses PostgreSQL. Set `DATABASE_URL` before starting the app, for example:

```powershell
$env:DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/prediction"
python -m app.main
```

## Keeping data consistent across clones

Git commits source code, not PostgreSQL rows or uploaded files. For every device to see the same students, faculty, assignments, tests, and submissions, configure every clone with the same hosted PostgreSQL `DATABASE_URL`. Copy `.env.example` to `.env`, fill in the real connection string and credentials, and never commit `.env`.

For local development on another device, create a private database backup on the original device and restore it on the new database:

```powershell
pg_dump --format=custom --file=backups/prediction.dump "$env:DATABASE_URL"
pg_restore --clean --if-exists --dbname="$env:DATABASE_URL" backups/prediction.dump
```

Uploaded question papers and submissions are stored under `UPLOAD_DIR` and are not included in a Git commit. Copy that directory to the same path on the new device, or use shared object storage and set `UPLOAD_DIR` to a shared mounted location. The database backup alone preserves metadata but cannot restore missing files.

Direct password login is used for administrators, faculty, and students. Set the administrator credentials and session lifetime in `.env`:

```powershell
$env:ADMIN_EMAILS = "admin@eduvista.com"
$env:ADMIN_PASSWORD = "<strong-admin-password>"
$env:SESSION_TTL_SECONDS = "28800"
```

New student and faculty accounts must have passwords of at least eight characters. Existing records created before this change have no password hash and must be assigned a password through an account migration or administrative reset before they can sign in.

SMTP is no longer required for authentication. It may still be configured for other application email workflows by setting the Gmail values in `.env`:

```powershell
$env:SMTP_USERNAME = "Nmcbca2010@gmail.com"
$env:SMTP_FROM = "Nmcbca2010@gmail.com"
$env:SMTP_PASSWORD = "<gmail-app-password>"
```

The app uses Gmail SMTP on `smtp.gmail.com` port `587` with STARTTLS. Do not use the normal Gmail account password or commit `.env`.

Uploaded submission files remain on disk under `data/submissions` by default. Set `UPLOAD_DIR` to change that location.

To migrate the existing SQLite database into PostgreSQL, create the PostgreSQL database first, set `DATABASE_URL`, and run:

```powershell
$env:DATABASE_URL = "postgresql://postgres:password@localhost:5432/academic"
python migrate_sqlite_to_postgres.py
```

## Main endpoint groups

- `/login`, `/students`, `/faculty`, `/faculty/login`
- `/attendance`, `/assignments`
- `/tests`, `/submissions`, `/results`
- `/predictions`, `/analytics`
- `/reports`

`POST /login` returns a bearer session token for API clients:

```json
POST /login
{"role":"faculty","email":"faculty@example.com","password":"<password>"}
```

Send the returned `auth_token` as `Authorization: Bearer <token>`. Sessions are persisted in PostgreSQL and expire after `SESSION_TTL_SECONDS`. The legacy OTP endpoints return `410 Gone` and are retained only so older clients fail clearly.

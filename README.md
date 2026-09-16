# Predictive Student Academic Performance API

FastAPI backend for student management, academic records, attendance, assignments, online tests, results, risk prediction, analytics, and report exports.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Open Swagger UI at <http://127.0.0.1:4009/docs>.

The API uses PostgreSQL. Set `DATABASE_URL` before starting the app, for example:

```powershell
$env:DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/prediction"
python app/main.py
```

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

Use `POST /login` and choose either `student` or `faculty`. Faculty accounts use the static code `2124` with the officially registered email:

```json
POST /login
{"role":"faculty","email":"faculty@example.com","faculty_code":"2124"}
```

Use the returned `faculty_id` and `faculty_code=2124` on faculty submission endpoints. No bearer token is required.

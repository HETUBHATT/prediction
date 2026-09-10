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

SQLite data is stored in `data/academic.db`. Set `ACADEMIC_DB_PATH` to use another database path.

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

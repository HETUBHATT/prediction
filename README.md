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

- `/students`, `/faculty`
- `/attendance`, `/assignments`
- `/tests`, `/submissions`, `/results`
- `/predictions`, `/analytics`
- `/reports`

from io import BytesIO
import csv
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import uuid4
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
try:
    from .db import IntegrityError, execute, init_db, query
    from .schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, FacultyLogin, LoginRequest, ManualReviewSave, PenAnnotation, ResultCreate, StudentCreate, StudentUpdate, TestCreate
    from .services import grade, predict
except ImportError:
    from db import IntegrityError, execute, init_db, query
    from schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, FacultyLogin, LoginRequest, ManualReviewSave, PenAnnotation, ResultCreate, StudentCreate, StudentUpdate, TestCreate
    from services import grade, predict

app = FastAPI(title='Predictive Student Academic Performance API', version='1.0.0', description='Student academic management, assessment, results, analytics, and ML risk prediction.')
FRONTEND_DIR = Path(__file__).resolve().parent.parent / 'frontend'
app.mount('/frontend', StaticFiles(directory=FRONTEND_DIR), name='frontend')
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', 'data/submissions'))
MAX_SUBMISSION_SIZE = 10 * 1024 * 1024
ALLOWED_FILES = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
}
0
@app.on_event('startup')
def startup():
    init_db()

@app.get('/', include_in_schema=False)
def home():
    return RedirectResponse('/dashboard')

@app.get('/dashboard', include_in_schema=False)
def dashboard():
    return FileResponse(FRONTEND_DIR / 'dashboard.html')

def one_or_404(sql: str, params=(), label='Resource'):
    rows = query(sql, params)
    if not rows: raise HTTPException(404, f'{label} not found')
    return rows[0]

def create(sql: str, params, fetch_sql: str, label='Resource'):
    try:
        result = execute(sql, params)
    except IntegrityError as exc:
        raise HTTPException(409, f'{label} conflicts with an existing record') from exc
    return one_or_404(fetch_sql, [result['id']], label)

def hash_faculty_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

def faculty_from_credentials(faculty_id: int, faculty_code: str) -> dict:
    faculty = one_or_404('SELECT id,name,email,department,phone,faculty_code_hash FROM faculty WHERE id=?', [faculty_id], 'Faculty')
    if not faculty['faculty_code_hash'] or not hmac.compare_digest(faculty['faculty_code_hash'], hash_faculty_code(faculty_code)):
        raise HTTPException(401, 'Invalid faculty credentials')
    faculty.pop('faculty_code_hash', None)
    return faculty

@app.get('/health', tags=['System'])
def health():
    return {'status': 'ok', 'service': 'academic-performance-api'}

@app.post('/students', tags=['Student Management'])
def add_student(payload: StudentCreate):
    return create('INSERT INTO students(name,email,department,semester,phone,attendance) VALUES(?,?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM students WHERE id=?', 'Student')

@app.get('/students', tags=['Student Management'])
def view_student_list(department: str | None = None, semester: int | None = Query(None, ge=1, le=8)):
    sql, params = 'SELECT * FROM students WHERE 1=1', []
    if department: sql += ' AND department=?'; params.append(department)
    if semester: sql += ' AND semester=?'; params.append(semester)
    return query(sql + ' ORDER BY name', params)

@app.get('/students/{student_id}', tags=['Student Management'])
def view_student(student_id: int):
    return one_or_404('SELECT * FROM students WHERE id=?', [student_id], 'Student')

@app.put('/students/{student_id}', tags=['Student Management'])
def update_student(student_id: int, payload: StudentUpdate):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student')
    try: execute('UPDATE students SET name=?,email=?,department=?,semester=?,phone=?,attendance=? WHERE id=?', [*payload.model_dump().values(), student_id])
    except IntegrityError as exc: raise HTTPException(409, 'Email already belongs to another student') from exc
    return view_student(student_id)

@app.delete('/students/{student_id}', tags=['Student Management'])
def delete_student(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); execute('DELETE FROM students WHERE id=?', [student_id]); return {'message': 'Student deleted'}

@app.post('/faculty', tags=['Faculty Management'])
def add_faculty(payload: FacultyCreate):
    values = [payload.name, payload.email.lower(), payload.department, payload.phone, hash_faculty_code(payload.faculty_code)]
    return create('INSERT INTO faculty(name,email,department,phone,faculty_code_hash) VALUES(?,?,?,?,?)', values, 'SELECT id,name,email,department,phone,created_at FROM faculty WHERE id=?', 'Faculty')

@app.post('/faculty/login', tags=['Faculty Authentication'])
def faculty_login(payload: FacultyLogin):
    faculty_rows = query('SELECT id,email,faculty_code_hash FROM faculty WHERE lower(email)=lower(?)', [payload.email])
    faculty = faculty_rows[0] if faculty_rows else None
    if not faculty or not faculty['faculty_code_hash'] or not hmac.compare_digest(faculty['faculty_code_hash'], hash_faculty_code(payload.faculty_code)):
        raise HTTPException(401, 'Invalid faculty email or code')
    return {'role': 'faculty', 'faculty_id': faculty['id'], 'email': faculty['email']}

@app.post('/login', tags=['Authentication'])
def login(payload: LoginRequest):
    if payload.role == 'faculty':
        if payload.faculty_code != '2124':
            raise HTTPException(401, 'Faculty login requires code 2124')
        faculty = query('SELECT id,email,faculty_code_hash FROM faculty WHERE lower(email)=lower(?)', [payload.email])
        if not faculty or not hmac.compare_digest(faculty[0]['faculty_code_hash'] or '', hash_faculty_code(payload.faculty_code)):
            raise HTTPException(401, 'Invalid faculty email or code')
        return {'role': 'faculty', 'faculty_id': faculty[0]['id'], 'email': faculty[0]['email']}
    student = one_or_404('SELECT id,name,email FROM students WHERE lower(email)=lower(?)', [payload.email], 'Student')
    return {'role': 'student', 'student_id': student['id'], 'name': student['name'], 'email': student['email']}

@app.get('/faculty', tags=['Faculty Management'])
def view_faculty_list(department: str | None = None):
    fields = 'id,name,email,department,phone,created_at'
    return query(f'SELECT {fields} FROM faculty WHERE department=? ORDER BY name' if department else f'SELECT {fields} FROM faculty ORDER BY name', [department] if department else [])

@app.delete('/faculty/{faculty_id}', tags=['Faculty Management'])
def delete_faculty(faculty_id: int):
    one_or_404('SELECT id FROM faculty WHERE id=?', [faculty_id], 'Faculty'); execute('DELETE FROM faculty WHERE id=?', [faculty_id]); return {'message': 'Faculty deleted'}

@app.post('/academic-records', tags=['Academic Data Entry'])
def add_academic_record(payload: AcademicRecordCreate):
    one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student')
    return create('INSERT INTO academic_records(student_id,subject,semester,internal_marks,test_marks,previous_semester_marks,percentile_12th) VALUES(?,?,?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM academic_records WHERE id=?', 'Academic record')

@app.get('/students/{student_id}/academic-records', tags=['Academic Data Entry'])
def view_academic_records(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT * FROM academic_records WHERE student_id=? ORDER BY semester,subject', [student_id])

@app.post('/attendance', tags=['Attendance Management'])
def record_attendance(payload: AttendanceCreate):
    if payload.classes_attended > payload.classes_held: raise HTTPException(422, 'Classes attended cannot exceed classes held')
    one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student')
    return create('INSERT INTO attendance(student_id,subject,classes_held,classes_attended,date) VALUES(?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM attendance WHERE id=?', 'Attendance record')

@app.get('/attendance/student/{student_id}', tags=['Attendance Management'])
def view_attendance(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT subject,SUM(classes_held) classes_held,SUM(classes_attended) classes_attended,ROUND((SUM(classes_attended)*100.0/SUM(classes_held))::numeric,2) attendance_percentage FROM attendance WHERE student_id=? GROUP BY subject', [student_id])

@app.post('/assignments', tags=['Assignment Tracking'])
def add_assignment(payload: AssignmentCreate):
    one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student'); return create('INSERT INTO assignments(student_id,subject,title,due_date,status,score) VALUES(?,?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM assignments WHERE id=?', 'Assignment')

@app.get('/assignments/student/{student_id}', tags=['Assignment Tracking'])
def view_assignments(student_id: int, status: str | None = None):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT * FROM assignments WHERE student_id=? AND status=?' if status else 'SELECT * FROM assignments WHERE student_id=?', [student_id, status] if status else [student_id])

@app.post('/tests', tags=['Online Test Management'])
def schedule_test(payload: TestCreate):
    if payload.faculty_id: one_or_404('SELECT id FROM faculty WHERE id=?', [payload.faculty_id], 'Faculty')
    return create('INSERT INTO tests(faculty_id,subject,title,total_marks,scheduled_at,question_paper) VALUES(?,?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM tests WHERE id=?', 'Test')

@app.get('/tests', tags=['Online Test Management'])
def list_tests(subject: str | None = None):
    return query('SELECT * FROM tests WHERE subject=? ORDER BY created_at DESC' if subject else 'SELECT * FROM tests ORDER BY created_at DESC', [subject] if subject else [])

def faculty_test(test_id: int, faculty_id: int):
    one_or_404('SELECT id FROM faculty WHERE id=?', [faculty_id], 'Faculty')
    test = one_or_404('SELECT id,faculty_id FROM tests WHERE id=?', [test_id], 'Test')
    if test['faculty_id'] is not None and test['faculty_id'] != faculty_id:
        raise HTTPException(403, 'Only the assigned faculty can review this submission')
    return test

@app.post('/tests/{test_id}/submissions', tags=['Online Test Management'])
async def upload_test_paper(test_id: int, student_id: int = Form(...), file: UploadFile = File(...)):
    one_or_404('SELECT id FROM tests WHERE id=?', [test_id], 'Test')
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ALLOWED_FILES or file.content_type != ALLOWED_FILES[suffix]:
        raise HTTPException(415, 'Submission must be a PDF, JPG, JPEG, or PNG file')
    contents = await file.read(MAX_SUBMISSION_SIZE + 1)
    if len(contents) > MAX_SUBMISSION_SIZE:
        raise HTTPException(413, 'Submission file must be 10 MB or smaller')
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f'{uuid4().hex}{suffix}'
    stored_path = UPLOAD_DIR / stored_name
    stored_path.write_bytes(contents)
    try:
        result = execute(
            'INSERT INTO submissions(test_id,student_id,answer_paper,file_name,file_path,content_type,file_size) VALUES(?,?,?,?,?,?,?)',
            [test_id, student_id, stored_name, file.filename, str(stored_path), file.content_type, len(contents)],
        )
    except IntegrityError as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(409, 'Student already submitted this test') from exc
    return one_or_404('SELECT id,test_id,student_id,file_name,content_type,file_size,submitted_at,status FROM submissions WHERE id=?', [result['id']], 'Submission')

@app.get('/submissions', tags=['Faculty Submission Review'])
def list_submissions(faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$'), test_id: int | None = None, student_id: int | None = None):
    faculty_from_credentials(faculty_id, faculty_code)
    if test_id: faculty_test(test_id, faculty_id)
    sql = 'SELECT s.id,s.test_id,s.student_id,s.file_name,s.content_type,s.file_size,s.submitted_at,s.score,s.feedback,s.review_mode,s.status FROM submissions s JOIN tests t ON t.id=s.test_id WHERE (t.faculty_id=? OR t.faculty_id IS NULL)'
    params = [faculty_id]
    if test_id: sql += ' AND s.test_id=?'; params.append(test_id)
    if student_id: sql += ' AND s.student_id=?'; params.append(student_id)
    return query(sql + ' ORDER BY s.submitted_at DESC', params)

@app.get('/submissions/{submission_id}', tags=['Faculty Submission Review'])
def review_submission(submission_id: int, faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$')):
    submission = one_or_404('SELECT s.*,t.faculty_id FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, faculty_code)
    faculty_test(submission['test_id'], faculty_id)
    submission['annotations'] = json.loads(submission.pop('annotations_json') or '[]')
    submission['marks'] = json.loads(submission.pop('manual_marks_json') or '[]')
    return submission

@app.get('/submissions/{submission_id}/file', tags=['Faculty Submission Review'], response_class=FileResponse)
def download_submission(submission_id: int, faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$')):
    submission = one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, faculty_code)
    faculty_test(submission['test_id'], faculty_id)
    if not submission['file_path'] or not Path(submission['file_path']).is_file():
        raise HTTPException(404, 'Submission file not found')
    return FileResponse(submission['file_path'], media_type=submission['content_type'], filename=submission['file_name'])

@app.post('/submissions/{submission_id}/annotations', tags=['Faculty Submission Review'])
def annotate_submission(submission_id: int, payload: PenAnnotation, faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$')):
    submission = one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, faculty_code)
    faculty_test(submission['test_id'], faculty_id)
    annotations = json.loads(submission['annotations_json'] or '[]')
    annotations.append(payload.model_dump())
    execute('UPDATE submissions SET annotations_json=?,status=? WHERE id=?', [json.dumps(annotations), 'in_review', submission_id])
    return {'submission_id': submission_id, 'annotations': annotations, 'status': 'in_review'}

@app.put('/submissions/{submission_id}/manual-review', tags=['Faculty Submission Review'])
def save_manual_review(submission_id: int, payload: ManualReviewSave, faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$')):
    submission = one_or_404('SELECT s.*,t.total_marks FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, faculty_code)
    faculty_test(submission['test_id'], faculty_id)
    if payload.score is not None and payload.score > submission['total_marks']:
        raise HTTPException(422, 'Score cannot exceed total marks')
    annotations = [annotation.model_dump() for annotation in payload.annotations]
    marks = [mark.model_dump() for mark in payload.marks]
    status = 'corrected' if payload.score is not None else 'in_review'
    execute(
        'UPDATE submissions SET annotations_json=?,manual_marks_json=?,score=?,feedback=?,review_mode=?,status=? WHERE id=?',
        [json.dumps(annotations), json.dumps(marks), payload.score, payload.feedback, payload.review_mode, status, submission_id],
    )
    saved = one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')
    saved['annotations'] = json.loads(saved.pop('annotations_json') or '[]')
    saved['marks'] = json.loads(saved.pop('manual_marks_json') or '[]')
    return saved

@app.post('/submissions/{submission_id}/correct', tags=['Automatic Test Evaluation'])
def correct_test(submission_id: int, payload: CorrectionRequest, faculty_id: int = Query(..., gt=0), faculty_code: str = Query(..., pattern=r'^2124$')):
    submission = one_or_404('SELECT s.*,t.total_marks FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, faculty_code)
    faculty_test(submission['test_id'], faculty_id)
    if payload.score > submission['total_marks']: raise HTTPException(422, 'Score cannot exceed total marks')
    execute('UPDATE submissions SET score=?,feedback=?,status=? WHERE id=?', [payload.score, payload.feedback, 'corrected', submission_id])
    return one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')

@app.post('/results', tags=['Result Generation'])
def generate_result(payload: ResultCreate):
    one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student')
    values = [payload.student_id, payload.subject, payload.semester, payload.marks, payload.total_marks, grade(payload.marks, payload.total_marks)]
    return create('INSERT INTO results(student_id,subject,semester,marks,total_marks,grade) VALUES(?,?,?,?,?,?)', values, 'SELECT * FROM results WHERE id=?', 'Result')

@app.get('/results/student/{student_id}', tags=['Result Generation'])
def view_result(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT * FROM results WHERE student_id=? ORDER BY semester,subject', [student_id])

@app.get('/predictions/student/{student_id}', tags=['Machine Learning Prediction'])
def predict_student_risk(student_id: int):
    try: return predict(student_id)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc

@app.get('/analytics/overview', tags=['Dashboard Analytics'])
def dashboard_overview(department: str | None = None):
    students = query('SELECT * FROM students WHERE department=?' if department else 'SELECT * FROM students', [department] if department else [])
    risks = []
    for student in students:
        try: risks.append(predict(student['id'])['risk'])
        except ValueError: pass
    return {'total_students': len(students), 'total_faculty': query('SELECT COUNT(*) count FROM faculty')[0]['count'], 'total_tests': query('SELECT COUNT(*) count FROM tests')[0]['count'], 'risk_counts': {level: risks.count(level) for level in ('Low', 'Medium', 'High')}, 'average_marks': query('SELECT ROUND((AVG(marks / total_marks * 100))::numeric,2) average FROM results')[0]['average']}

@app.get('/analytics/at-risk-students', tags=['Dashboard Analytics'])
def at_risk_students(risk: str = Query('High', pattern='^(Low|Medium|High)$')):
    output = []
    for student in query('SELECT * FROM students ORDER BY name'):
        result = predict(student['id'])
        if result['risk'] == risk: output.append({'student': student, 'prediction': result})
    return output

@app.get('/analytics/student/{student_id}', tags=['Dashboard Analytics'])
def student_dashboard(student_id: int):
    student = view_student(student_id); return {'student': student, 'attendance': view_attendance(student_id), 'assignments': view_assignments(student_id), 'results': view_result(student_id), 'prediction': predict_student_risk(student_id)}

@app.get('/reports/student/{student_id}', tags=['Reports'])
def student_performance_report(student_id: int):
    student = view_student(student_id); return {'student': student, 'results': view_result(student_id), 'academic_records': view_academic_records(student_id), 'attendance': view_attendance(student_id), 'prediction': predict_student_risk(student_id)}

@app.get('/reports/risk', tags=['Reports'])
def risk_report(): return at_risk_students('High') + at_risk_students('Medium')

@app.get('/reports/attendance', tags=['Reports'])
def attendance_report(): return query('SELECT s.id student_id,s.name,s.department,ROUND((COALESCE(SUM(a.classes_attended)*100.0/NULLIF(SUM(a.classes_held),0),s.attendance))::numeric,2) attendance_percentage FROM students s LEFT JOIN attendance a ON a.student_id=s.id GROUP BY s.id ORDER BY attendance_percentage')

@app.get('/reports/export.csv', tags=['Reports'])

def export_results_csv():
    rows = query('SELECT r.*,s.name student_name,s.department FROM results r JOIN students s ON s.id=r.student_id ORDER BY s.name,r.semester')
    stream = BytesIO(); text = stream
    import io
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()) if rows else ['message']); writer.writeheader(); writer.writerows(rows or [{'message': 'No results available'}]); stream.write(output.getvalue().encode()); stream.seek(0)
    return StreamingResponse(stream, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=results.csv'})


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=4009)

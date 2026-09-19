from io import BytesIO
import csv
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import socket
import time
from pathlib import Path
from uuid import uuid4
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

try:
    from .db import IntegrityError, execute, init_db, query
    from .smtp_service import send_email, send_otp_email
    from .schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, FacultyLogin, LoginRequest, ManualReviewSave, OTPRequest, OTPVerification, PenAnnotation, ResultCreate, SemesterAssignmentCreate, StudentCreate, StudentSignupRequest, StudentUpdate, TestCreate
    from .services import grade, predict
except ImportError:
    from db import IntegrityError, execute, init_db, query
    from smtp_service import send_email, send_otp_email
    from schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, FacultyLogin, LoginRequest, ManualReviewSave, OTPRequest, OTPVerification, PenAnnotation, ResultCreate, SemesterAssignmentCreate, StudentCreate, StudentSignupRequest, StudentUpdate, TestCreate
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
ADMIN_EMAILS = {email.strip().lower() for email in os.getenv('ADMIN_EMAILS', 'admin@eduvista.com').split(',') if email.strip()}
SESSION_TTL_SECONDS = int(os.getenv('SESSION_TTL_SECONDS', str(8 * 60 * 60)))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')


def generate_faculty_code() -> str:
    return f'FAC-{secrets.token_hex(3).upper()}'


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 210_000)
    return f'pbkdf2_sha256$210000${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or not encoded.startswith('pbkdf2_sha256$'):
        return False
    try:
        _, rounds, salt_hex, digest_hex = encoded.split('$')
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def make_student_name_from_email(email: str) -> str:
    return email.split('@', 1)[0].replace('.', ' ').replace('_', ' ').title()


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

def session_user(token: str) -> dict:
    rows = query('SELECT token,role,account_id,email,expires_at FROM auth_sessions WHERE token=? AND expires_at > CURRENT_TIMESTAMP', [token])
    if not rows:
        raise HTTPException(401, 'Authentication has expired')
    row = rows[0]
    return {'role': row['role'], 'account_id': row['account_id'], 'email': row['email'], 'token': row['token']}


def current_user(request: Request) -> dict:
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        raise HTTPException(401, 'Authentication required')
    return session_user(header[7:].strip())


def require_roles(request: Request, *roles: str) -> dict:
    user = current_user(request)
    if user['role'] not in roles:
        raise HTTPException(403, 'You do not have permission to perform this action')
    return user


def faculty_from_credentials(faculty_id: int, auth_token: str) -> dict:
    user = session_user(auth_token)
    if user['role'] != 'faculty' or user['account_id'] != faculty_id:
        raise HTTPException(403, 'Faculty access is not authorized for this account')
    return one_or_404('SELECT id,name,email,department,phone FROM faculty WHERE id=?', [faculty_id], 'Faculty')


@app.middleware('http')
async def enforce_api_access(request: Request, call_next):
    path = request.url.path
    public = path in {'/health', '/login', '/auth/signup', '/auth/request-otp', '/auth/verify-otp'} or path.startswith('/frontend') or path in {'/', '/dashboard', '/student/signup', '/student/portal'} or path.startswith('/docs') or path.startswith('/openapi')
    if public:
        return await call_next(request)
    try:
        user = current_user(request)
        admin_only = (path == '/faculty' and request.method != 'GET') or path.startswith('/faculty/') or path.startswith('/analytics/overview') or path.startswith('/analytics/at-risk') or path.startswith('/reports/risk') or path.startswith('/reports/attendance') or path.startswith('/reports/export')
        if admin_only and user['role'] not in {'admin', 'faculty'}:
            raise HTTPException(403, 'This service is not available for your role')
        if path == '/faculty' and request.method == 'POST' and user['role'] != 'admin':
            raise HTTPException(403, 'Only administrators can manage faculty')
        if path.startswith('/faculty/') and user['role'] != 'admin':
            raise HTTPException(403, 'Only administrators can manage faculty')
        if user['role'] == 'student' and request.method not in {'GET', 'POST'}:
            raise HTTPException(403, 'Students cannot modify this resource')
        if user['role'] == 'student':
            parts = [part for part in path.split('/') if part]
            if path == '/students' or path.startswith('/faculty') or path.startswith('/analytics/overview') or path.startswith('/analytics/at-risk') or path.startswith('/reports/risk') or path.startswith('/reports/attendance') or path.startswith('/reports/export'):
                raise HTTPException(403, 'This service is not available for students')
            if path.startswith('/submissions') and request.method != 'GET':
                raise HTTPException(403, 'This service is not available for students')
            if len(parts) >= 2 and parts[1].isdigit() and parts[0] in {'students', 'predictions', 'results', 'attendance', 'assignments', 'analytics', 'reports'} and int(parts[1]) != user['account_id']:
                raise HTTPException(403, 'You can only access your own academic data')
            if request.method == 'POST' and not path.startswith('/tests/') and path != '/attendance' and not path.startswith('/student/'):
                raise HTTPException(403, 'Students cannot create this resource')
        request.state.user = user
    except HTTPException as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    return await call_next(request)


def admin_access(email: str) -> bool:
    return email.lower() in ADMIN_EMAILS

@app.get('/health', tags=['System'])
def health():
    return {'status': 'ok', 'service': 'academic-performance-api'}

@app.post('/students', tags=['Student Management'])
def add_student(payload: StudentCreate, request: Request):
    require_roles(request, 'admin', 'faculty')
    values = payload.model_dump()
    values['password_hash'] = hash_password(values.pop('password'))
    return create('INSERT INTO students(name,email,department,semester,phone,attendance,password_hash) VALUES(?,?,?,?,?,?,?)', values.values(), 'SELECT id,name,email,department,semester,phone,attendance FROM students WHERE id=?', 'Student')

@app.get('/students', tags=['Student Management'])
def view_student_list(department: str | None = None, semester: int | None = Query(None, ge=1, le=8)):
    sql, params = 'SELECT * FROM students WHERE 1=1', []
    if department: sql += ' AND department=?'; params.append(department)
    if semester: sql += ' AND semester=?'; params.append(semester)
    return query(sql + ' ORDER BY name', params)

@app.get('/students/{student_id}', tags=['Student Management'])
def view_student(student_id: int):
    return one_or_404('SELECT id,name,email,department,semester,phone,attendance,created_at FROM students WHERE id=?', [student_id], 'Student')

@app.put('/students/{student_id}', tags=['Student Management'])
def update_student(student_id: int, payload: StudentUpdate):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student')
    values = payload.model_dump()
    password = values.pop('password')
    try:
        if password:
            execute('UPDATE students SET name=?,email=?,department=?,semester=?,phone=?,attendance=?,password_hash=? WHERE id=?', [*values.values(), hash_password(password), student_id])
        else:
            execute('UPDATE students SET name=?,email=?,department=?,semester=?,phone=?,attendance=? WHERE id=?', [*values.values(), student_id])
    except IntegrityError as exc:
        raise HTTPException(409, 'Email already belongs to another student') from exc
    return view_student(student_id)

@app.delete('/students/{student_id}', tags=['Student Management'])
def delete_student(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); execute('DELETE FROM students WHERE id=?', [student_id]); return {'message': 'Student deleted'}

@app.post('/faculty', tags=['Faculty Management'])
def add_faculty(payload: FacultyCreate, request: Request):
    require_roles(request, 'admin')
    values = [payload.name, payload.email.lower(), payload.department, payload.phone, hash_password(payload.password)]
    faculty = create('INSERT INTO faculty(name,email,department,phone,password_hash) VALUES(?,?,?,?,?)', values, 'SELECT id,name,email,department,phone,created_at FROM faculty WHERE id=?', 'Faculty')
    return faculty

@app.post('/login', tags=['Authentication'])
def login(payload: LoginRequest):
    email = payload.email.strip().lower()
    if payload.role == 'admin':
        if not ADMIN_PASSWORD or not admin_access(email) or not hmac.compare_digest(payload.password, ADMIN_PASSWORD):
            raise HTTPException(401, 'Invalid credentials')
        account_id = 0
        name = 'Administrator'
    else:
        table = 'faculty' if payload.role == 'faculty' else 'students'
        account = one_or_404(f'SELECT id,name,email,password_hash FROM {table} WHERE lower(email)=lower(?)', [email], 'Account')
        if not verify_password(payload.password, account['password_hash']):
            raise HTTPException(401, 'Invalid credentials')
        account_id = account['id']
        name = account['name']
    token = secrets.token_urlsafe(48)
    execute('INSERT INTO auth_sessions(token,role,account_id,email,expires_at) VALUES(?,?,?, ?, CURRENT_TIMESTAMP + (? * INTERVAL \'1 second\'))', [token, payload.role, account_id, email, SESSION_TTL_SECONDS])
    return {'role': payload.role, 'account_id': account_id, 'name': name, 'email': email, 'auth_token': token, 'expires_in': SESSION_TTL_SECONDS}

def user_for_otp(role: str, email: str):
    normalized = email.lower()
    if role == 'admin':
        if not admin_access(normalized):
            raise HTTPException(404, 'Admin account not found')
        return {'id': 0, 'name': 'Administrator', 'email': normalized}
    if role == 'faculty':
        return one_or_404('SELECT id,email FROM faculty WHERE lower(email)=lower(?)', [normalized], 'Faculty account')
    match = query('SELECT id,name,email FROM students WHERE lower(email)=lower(?)', [normalized])
    if match:
        return match[0]
    return {'id': 0, 'email': normalized, 'name': make_student_name_from_email(normalized)}


def authenticated_user(role: str, account: dict):
    if role == 'admin':
        return {'role': 'admin', 'name': 'Administrator', 'email': account['email']}
    if role == 'faculty':
        return {'role': 'faculty', 'faculty_id': account['id'], 'email': account['email']}
    return {'role': 'student', 'student_id': account['id'], 'name': account['name'], 'email': account['email']}

@app.post('/auth/request-otp', tags=['Authentication'])
def request_otp(payload: OTPRequest):
    raise HTTPException(410, 'Code verification is disabled. Use direct password login.')


@app.post('/auth/signup', tags=['Authentication'])
def signup_student(payload: StudentSignupRequest):
    email = payload.email.lower()
    existing = query('SELECT id FROM students WHERE lower(email)=lower(?)', [email])
    if existing:
        raise HTTPException(409, 'A student account already exists for this email')
    try:
        student = create(
            'INSERT INTO students(name,email,department,semester,phone,attendance,password_hash) VALUES(?,?,?,?,?,?,?)',
            [payload.name, email, payload.department, payload.semester, payload.phone, 0, hash_password(payload.password)],
            'SELECT id,name,email,department,semester,phone,attendance FROM students WHERE id=?',
            'Student'
        )
    except HTTPException:
        raise
    return {'message': 'Student account created. You can sign in with your password.', 'student': student}

@app.post('/auth/verify-otp', tags=['Authentication'])
def verify_otp(payload: OTPVerification):
    raise HTTPException(410, 'Code verification is disabled. Use direct password login.')

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
def record_attendance(payload: AttendanceCreate, request: Request):
    user = current_user(request)
    if user['role'] == 'student' and payload.student_id != user['account_id']:
        raise HTTPException(403, 'Students can only mark their own attendance')
    if payload.classes_attended > payload.classes_held: raise HTTPException(422, 'Classes attended cannot exceed classes held')
    one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student')
    return create('INSERT INTO attendance(student_id,subject,classes_held,classes_attended,date) VALUES(?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM attendance WHERE id=?', 'Attendance record')

@app.get('/attendance/student/{student_id}', tags=['Attendance Management'])
def view_attendance(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT subject,SUM(classes_held) classes_held,SUM(classes_attended) classes_attended,ROUND((SUM(classes_attended)*100.0/SUM(classes_held))::numeric,2) attendance_percentage FROM attendance WHERE student_id=? GROUP BY subject', [student_id])

@app.post('/assignments', tags=['Assignment Tracking'])
def add_assignment(payload: AssignmentCreate):
    student = one_or_404('SELECT id,semester FROM students WHERE id=?', [payload.student_id], 'Student')
    if payload.subject and student['semester']:
        pass
    return create('INSERT INTO assignments(student_id,subject,title,due_date,status,score) VALUES(?,?,?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM assignments WHERE id=?', 'Assignment')


@app.post('/assignments/semester', tags=['Assignment Tracking'])
def add_semester_assignments(payload: SemesterAssignmentCreate):
    rows = query('SELECT id FROM students WHERE semester=? ORDER BY name', [payload.semester])
    created = []
    for student in rows:
        try:
            created.append(create(
                'INSERT INTO assignments(student_id,subject,title,due_date,status,score) VALUES(?,?,?,?,?,?)',
                [student['id'], payload.subject, payload.title, payload.due_date, payload.status, payload.score],
                'SELECT * FROM assignments WHERE id=?',
                'Assignment'
            ))
        except IntegrityError:
            continue
    return {'semester': payload.semester, 'students': len(rows), 'assigned': len(created), 'message': f'Assignment scheduled for {len(created)} student(s) in semester {payload.semester}'}


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
async def upload_test_paper(test_id: int, request: Request, student_id: int = Form(...), file: UploadFile = File(...)):
    user = current_user(request)
    if user['role'] == 'student' and student_id != user['account_id']:
        raise HTTPException(403, 'Students can only submit their own papers')
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

@app.get('/tests/{test_id}/submissions', tags=['Online Test Management'])
def list_test_submissions(test_id: int, request: Request, student_id: int | None = Query(None)):
    user = current_user(request)
    if user['role'] == 'student':
        sid = student_id or user['account_id']
        if sid != user['account_id']:
            raise HTTPException(403, 'You can only view your own submissions')
        return query('SELECT id,test_id,student_id,file_name,content_type,file_size,submitted_at,score,feedback,status FROM submissions WHERE test_id=? AND student_id=?', [test_id, sid])
    return query('SELECT id,test_id,student_id,file_name,content_type,file_size,submitted_at,score,feedback,status FROM submissions WHERE test_id=?', [test_id])


@app.get('/submissions', tags=['Faculty Submission Review'])
def list_submissions(faculty_id: int = Query(..., gt=0), auth_token: str = Query(...), test_id: int | None = None, student_id: int | None = None):
    faculty_from_credentials(faculty_id, auth_token)
    if test_id: faculty_test(test_id, faculty_id)
    sql = 'SELECT s.id,s.test_id,s.student_id,s.file_name,s.content_type,s.file_size,s.submitted_at,s.score,s.feedback,s.review_mode,s.status FROM submissions s JOIN tests t ON t.id=s.test_id WHERE (t.faculty_id=? OR t.faculty_id IS NULL)'
    params = [faculty_id]
    if test_id: sql += ' AND s.test_id=?'; params.append(test_id)
    if student_id: sql += ' AND s.student_id=?'; params.append(student_id)
    return query(sql + ' ORDER BY s.submitted_at DESC', params)

@app.get('/submissions/{submission_id}', tags=['Faculty Submission Review'])
def review_submission(submission_id: int, faculty_id: int = Query(..., gt=0), auth_token: str = Query(...)):
    submission = one_or_404('SELECT s.*,t.faculty_id FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, auth_token)
    faculty_test(submission['test_id'], faculty_id)
    submission['annotations'] = json.loads(submission.pop('annotations_json') or '[]')
    submission['marks'] = json.loads(submission.pop('manual_marks_json') or '[]')
    return submission

@app.get('/submissions/{submission_id}/file', tags=['Faculty Submission Review'], response_class=FileResponse)
def download_submission(submission_id: int, faculty_id: int = Query(..., gt=0), auth_token: str = Query(...)):
    submission = one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, auth_token)
    faculty_test(submission['test_id'], faculty_id)
    if not submission['file_path'] or not Path(submission['file_path']).is_file():
        raise HTTPException(404, 'Submission file not found')
    return FileResponse(submission['file_path'], media_type=submission['content_type'], filename=submission['file_name'])

@app.post('/submissions/{submission_id}/annotations', tags=['Faculty Submission Review'])
def annotate_submission(submission_id: int, payload: PenAnnotation, faculty_id: int = Query(..., gt=0), auth_token: str = Query(...)):
    submission = one_or_404('SELECT * FROM submissions WHERE id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, auth_token)
    faculty_test(submission['test_id'], faculty_id)
    annotations = json.loads(submission['annotations_json'] or '[]')
    annotations.append(payload.model_dump())
    execute('UPDATE submissions SET annotations_json=?,status=? WHERE id=?', [json.dumps(annotations), 'in_review', submission_id])
    return {'submission_id': submission_id, 'annotations': annotations, 'status': 'in_review'}

@app.put('/submissions/{submission_id}/manual-review', tags=['Faculty Submission Review'])
def save_manual_review(submission_id: int, payload: ManualReviewSave, faculty_id: int = Query(..., gt=0), auth_token: str = Query(...)):
    submission = one_or_404('SELECT s.*,t.total_marks FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, auth_token)
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
def correct_test(submission_id: int, payload: CorrectionRequest, faculty_id: int = Query(..., gt=0), auth_token: str = Query(...)):
    submission = one_or_404('SELECT s.*,t.total_marks FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
    faculty_from_credentials(faculty_id, auth_token)
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
    try:
        prediction = predict(student_id)
        subjects = {row['subject'] for row in query('SELECT subject FROM academic_records WHERE student_id=? UNION SELECT subject FROM attendance WHERE student_id=? UNION SELECT subject FROM results WHERE student_id=?', [student_id, student_id, student_id])}
        subject_risks = []
        for subject in sorted(subjects):
            record = query('SELECT AVG(internal_marks) internal_marks,AVG(test_marks) test_marks,AVG(previous_semester_marks) previous_semester_marks FROM academic_records WHERE student_id=? AND subject=?', [student_id, subject])[0]
            attendance = query('SELECT COALESCE(SUM(classes_attended),0) attended,COALESCE(SUM(classes_held),0) held FROM attendance WHERE student_id=? AND subject=?', [student_id, subject])[0]
            attendance_pct = round(attendance['attended'] / attendance['held'] * 100, 2) if attendance['held'] else None
            test_marks = round(float(record['test_marks']), 2) if record['test_marks'] is not None else None
            internal_marks = round(float(record['internal_marks']), 2) if record['internal_marks'] is not None else None
            reasons = []
            if attendance_pct is not None and attendance_pct < 75: reasons.append('attendance below 75%')
            if test_marks is not None and test_marks < 50: reasons.append('test marks below 50%')
            if internal_marks is not None and internal_marks < 50: reasons.append('internal marks below 50%')
            if reasons:
                subject_risks.append({'subject': subject, 'risk': 'High' if len(reasons) > 1 else 'Medium', 'attendance': attendance_pct, 'internal_marks': internal_marks, 'test_marks': test_marks, 'reason': '; '.join(reasons), 'tip': 'Attend more classes and schedule a faculty support session.' if attendance_pct is not None and attendance_pct < 75 else 'Review weak topics and complete practice work before the next assessment.'})
        prediction['subject_risks'] = subject_risks
        return prediction
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


@app.get('/student/signup', include_in_schema=False)
def student_signup_page():
    return FileResponse(FRONTEND_DIR / 'student-signup.html')

@app.get('/student/portal', include_in_schema=False)
def student_portal_page():
    return FileResponse(FRONTEND_DIR / 'student-portal.html')

@app.get('/student/me', tags=['Student Portal'])
def student_me(request: Request):
    user = require_roles(request, 'student')
    return one_or_404('SELECT id,name,email,department,semester,phone,attendance,created_at FROM students WHERE id=?', [user['account_id']], 'Student')

@app.get('/student/me/dashboard', tags=['Student Portal'])
def student_me_dashboard(request: Request):
    user = require_roles(request, 'student')
    sid = user['account_id']
    student = one_or_404('SELECT id,name,email,department,semester,phone,attendance,created_at FROM students WHERE id=?', [sid], 'Student')
    attendance = query('SELECT subject,SUM(classes_held) classes_held,SUM(classes_attended) classes_attended,ROUND((SUM(classes_attended)*100.0/SUM(classes_held))::numeric,2) attendance_percentage FROM attendance WHERE student_id=? GROUP BY subject', [sid])
    results = query('SELECT * FROM results WHERE student_id=? ORDER BY semester,subject', [sid])
    assignments = query('SELECT * FROM assignments WHERE student_id=? ORDER BY due_date DESC LIMIT 5', [sid])
    tests = query('SELECT t.id,t.subject,t.title,t.total_marks,t.scheduled_at,s.score,s.status,s.submitted_at FROM tests t LEFT JOIN submissions s ON s.test_id=t.id AND s.student_id=? ORDER BY t.created_at DESC LIMIT 10', [sid])
    try:
        prediction = predict_student_risk(sid)
    except HTTPException:
        prediction = None
    return {'student': student, 'attendance': attendance, 'results': results, 'assignments': assignments, 'tests': tests, 'prediction': prediction}

@app.get('/student/me/report', tags=['Student Portal'])
def student_me_report(request: Request):
    user = require_roles(request, 'student')
    sid = user['account_id']
    student = one_or_404('SELECT id,name,email,department,semester,phone,attendance,created_at FROM students WHERE id=?', [sid], 'Student')
    results = query('SELECT * FROM results WHERE student_id=? ORDER BY semester,subject', [sid])
    attendance = query('SELECT subject,SUM(classes_held) classes_held,SUM(classes_attended) classes_attended,ROUND((SUM(classes_attended)*100.0/SUM(classes_held))::numeric,2) attendance_percentage FROM attendance WHERE student_id=? GROUP BY subject', [sid])
    academic_records = query('SELECT * FROM academic_records WHERE student_id=? ORDER BY semester,subject', [sid])
    try:
        prediction = predict_student_risk(sid)
    except HTTPException:
        prediction = None

    tips = []
    overall_att = student.get('attendance', 0) or 0
    if overall_att < 75:
        tips.append({'category': 'Attendance', 'message': f'Your overall attendance is {overall_att}%. Aim for at least 75% to avoid academic penalties.'})
    elif overall_att < 85:
        tips.append({'category': 'Attendance', 'message': f'Good attendance at {overall_att}%. Maintaining above 85% will strengthen your academic standing.'})
    else:
        tips.append({'category': 'Attendance', 'message': f'Excellent attendance at {overall_att}%! Keep it up.'})

    if results:
        avg_pct = sum(r['marks'] / r['total_marks'] * 100 for r in results) / len(results)
        if avg_pct < 50:
            tips.append({'category': 'Performance', 'message': f'Your average score is {avg_pct:.1f}%. Focus on weak subjects and seek faculty guidance.'})
        elif avg_pct < 70:
            tips.append({'category': 'Performance', 'message': f'Your average score is {avg_pct:.1f}%. Consistent revision and practice tests can push you above 70%.'})
        else:
            tips.append({'category': 'Performance', 'message': f'Strong average score of {avg_pct:.1f}%. Challenge yourself with advanced topics.'})

    if prediction and prediction.get('subject_risks'):
        for sr in prediction['subject_risks']:
            tips.append({'category': f"Subject Risk — {sr['subject']}", 'message': sr.get('tip', 'Review this subject carefully.')})

    pending_assignments = query("SELECT COUNT(*) count FROM assignments WHERE student_id=? AND status='pending'", [sid])[0]['count']
    if pending_assignments:
        tips.append({'category': 'Assignments', 'message': f'You have {pending_assignments} pending assignment(s). Submit them before the due date to avoid grade penalties.'})

    return {'student': student, 'results': results, 'academic_records': academic_records, 'attendance': attendance, 'prediction': prediction, 'tips': tips}

@app.post('/student/me/submit-test', tags=['Student Portal'])
async def student_submit_test(request: Request, test_id: int = Form(...), file: UploadFile = File(...)):
    user = require_roles(request, 'student')
    student_id = user['account_id']
    one_or_404('SELECT id FROM tests WHERE id=?', [test_id], 'Test')
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
        raise HTTPException(409, 'You have already submitted this test') from exc
    return one_or_404('SELECT id,test_id,student_id,file_name,content_type,file_size,submitted_at,status FROM submissions WHERE id=?', [result['id']], 'Submission')

if __name__ == '__main__':
    configured_port = os.getenv('APP_PORT')
    port = int(configured_port or '4009')
    if not configured_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                port = 4010
                print('Port 4009 is already in use; starting the debug server on port 4010.')
    uvicorn.run(app, host='127.0.0.1', port=port)

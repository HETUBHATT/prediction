from io import BytesIO
import csv
import sqlite3
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
from fastapi.responses import FileResponse ,StreamingResponse
try:
    from .db import execute, init_db, query
    from .schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, ResultCreate, StudentCreate, StudentUpdate, SubmissionCreate, TestCreate
    from .services import grade, predict
except ImportError:
    from db import execute, init_db, query
    from schemas import AcademicRecordCreate, AssignmentCreate, AttendanceCreate, CorrectionRequest, FacultyCreate, ResultCreate, StudentCreate, StudentUpdate, SubmissionCreate, TestCreate
    from services import grade, predict

app = FastAPI(title='Predictive Student Academic Performance API', version='1.0.0', description='Student academic management, assessment, results, analytics, and ML risk prediction.')
BASE_DIR = Path(__file__).resolve().parent
@app.get("/addstudent.html", include_in_schema=False)
def add_student_page():
    return FileResponse(BASE_DIR / "static" / "addstudent.html")
@app.on_event('startup')
def startup():
    init_db()

def one_or_404(sql: str, params=(), label='Resource'):
    rows = query(sql, params)
    if not rows: raise HTTPException(404, f'{label} not found')
    return rows[0]

def create(sql: str, params, fetch_sql: str, label='Resource'):
    try:
        result = execute(sql, params)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, f'{label} conflicts with an existing record') from exc
    return one_or_404(fetch_sql, [result['id']], label)

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
    except sqlite3.IntegrityError as exc: raise HTTPException(409, 'Email already belongs to another student') from exc
    return view_student(student_id)

@app.delete('/students/{student_id}', tags=['Student Management'])
def delete_student(student_id: int):
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); execute('DELETE FROM students WHERE id=?', [student_id]); return {'message': 'Student deleted'}

@app.post('/faculty', tags=['Faculty Management'])
def add_faculty(payload: FacultyCreate):
    return create('INSERT INTO faculty(name,email,department,phone) VALUES(?,?,?,?)', payload.model_dump().values(), 'SELECT * FROM faculty WHERE id=?', 'Faculty')

@app.get('/faculty', tags=['Faculty Management'])
def view_faculty_list(department: str | None = None):
    return query('SELECT * FROM faculty WHERE department=? ORDER BY name' if department else 'SELECT * FROM faculty ORDER BY name', [department] if department else [])

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
    one_or_404('SELECT id FROM students WHERE id=?', [student_id], 'Student'); return query('SELECT subject,SUM(classes_held) classes_held,SUM(classes_attended) classes_attended,ROUND(SUM(classes_attended)*100.0/SUM(classes_held),2) attendance_percentage FROM attendance WHERE student_id=? GROUP BY subject', [student_id])

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

def upload_test_paper(test_id: int, payload: SubmissionCreate):
    if payload.test_id != test_id: raise HTTPException(422, 'Path test_id and body test_id must match')
    one_or_404('SELECT id FROM tests WHERE id=?', [test_id], 'Test'); one_or_404('SELECT id FROM students WHERE id=?', [payload.student_id], 'Student')
    return create('INSERT INTO submissions(test_id,student_id,answer_paper) VALUES(?,?,?)', [test_id, payload.student_id, payload.answer_paper], 'SELECT * FROM submissions WHERE id=?', 'Submission')

@app.get('/submissions', tags=['Online Test Management'])
def list_submissions(test_id: int | None = None, student_id: int | None = None):
    sql, params = 'SELECT * FROM submissions WHERE 1=1', []
    if test_id: sql += ' AND test_id=?'; params.append(test_id)
    if student_id: sql += ' AND student_id=?'; params.append(student_id)
    return query(sql + ' ORDER BY submitted_at DESC', params)

@app.post('/submissions/{submission_id}/correct', tags=['Automatic Test Evaluation'])
def correct_test(submission_id: int, payload: CorrectionRequest):
    submission = one_or_404('SELECT s.*,t.total_marks FROM submissions s JOIN tests t ON t.id=s.test_id WHERE s.id=?', [submission_id], 'Submission')
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
    return {'total_students': len(students), 'total_faculty': query('SELECT COUNT(*) count FROM faculty')[0]['count'], 'total_tests': query('SELECT COUNT(*) count FROM tests')[0]['count'], 'risk_counts': {level: risks.count(level) for level in ('Low', 'Medium', 'High')}, 'average_marks': query('SELECT ROUND(AVG(marks / total_marks * 100),2) average FROM results')[0]['average']}

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
def attendance_report(): return query('SELECT s.id student_id,s.name,s.department,ROUND(COALESCE(SUM(a.classes_attended)*100.0/NULLIF(SUM(a.classes_held),0),s.attendance),2) attendance_percentage FROM students s LEFT JOIN attendance a ON a.student_id=s.id GROUP BY s.id ORDER BY attendance_percentage')

@app.get('/reports/export.csv', tags=['Reports'])

def export_results_csv():
    rows = query('SELECT r.*,s.name student_name,s.department FROM results r JOIN students s ON s.id=r.student_id ORDER BY s.name,r.semester')
    stream = BytesIO(); text = stream
    import io
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()) if rows else ['message']); writer.writeheader(); writer.writerows(rows or [{'message': 'No results available'}]); stream.write(output.getvalue().encode()); stream.seek(0)
    return StreamingResponse(stream, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=results.csv'})


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=4009)

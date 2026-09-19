from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class StudentCreate(BaseModel):
    name: str = Field(min_length=2)
    email: str
    department: str
    semester: int = Field(ge=1, le=8)
    phone: str | None = None
    attendance: float = Field(default=0, ge=0, le=100)
    password: str = Field(min_length=8, max_length=128)

class StudentUpdate(StudentCreate):
    password: str | None = Field(default=None, min_length=8, max_length=128)

class FacultyCreate(BaseModel):
    name: str = Field(min_length=2)
    email: str
    department: str
    phone: str | None = None
    password: str = Field(min_length=8, max_length=128)

class FacultyLogin(BaseModel):
    email: str
    otp: str = Field(pattern=r'^\d{6}$')

class LoginRequest(BaseModel):
    role: Literal['student', 'faculty', 'admin']
    email: str
    password: str = Field(min_length=1, max_length=128)

class OTPRequest(BaseModel):
    role: Literal['student', 'faculty', 'admin']
    email: str

class OTPVerification(BaseModel):
    role: Literal['student', 'faculty', 'admin']
    email: str
    otp: str = Field(pattern=r'^(?:\d{4}|\d{6})$')

class StudentSignupRequest(BaseModel):
    name: str = Field(min_length=2)
    email: str
    department: str
    semester: int = Field(ge=1, le=8)
    phone: str | None = None
    password: str = Field(min_length=8, max_length=128)

class SemesterAssignmentCreate(BaseModel):
    semester: int = Field(ge=1, le=8)
    subject: str
    title: str
    due_date: str | None = None
    status: Literal['pending', 'submitted', 'graded'] = 'pending'
    score: float | None = Field(default=None, ge=0, le=100)

class AcademicRecordCreate(BaseModel):
    student_id: int
    subject: str
    semester: int = Field(ge=1, le=8)
    internal_marks: float = Field(ge=0, le=100)
    test_marks: float = Field(ge=0, le=100)
    previous_semester_marks: float = Field(default=0, ge=0, le=100)
    percentile_12th: float = Field(default=0, ge=0, le=100)

class AttendanceCreate(BaseModel):
    student_id: int
    subject: str
    classes_held: int = Field(gt=0)
    classes_attended: int = Field(ge=0)
    date: str | None = None

class AssignmentCreate(BaseModel):
    student_id: int
    subject: str
    title: str
    due_date: str | None = None
    status: Literal['pending', 'submitted', 'graded'] = 'pending'
    score: float | None = Field(default=None, ge=0, le=100)

class TestCreate(BaseModel):
    faculty_id: int | None = None
    subject: str
    title: str
    total_marks: float = Field(gt=0)
    scheduled_at: str | None = None
    question_paper: str | None = None

class SubmissionCreate(BaseModel):
    test_id: int
    student_id: int
    answer_paper: str = Field(min_length=1)

class AnnotationPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

class PenAnnotation(BaseModel):
    page: int = Field(ge=1)
    points: list[AnnotationPoint] = Field(default_factory=list)
    color: str = Field(default='#d92d20', pattern=r'^#[0-9a-fA-F]{6}$')
    width: float = Field(default=2, gt=0, le=20)
    kind: Literal['pen', 'correct', 'wrong', 'comment'] = 'pen'
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    text: str | None = Field(default=None, max_length=500)

class ManualMark(BaseModel):
    question: int = Field(ge=1)
    result: Literal['correct', 'wrong', 'clear'] = 'clear'
    marks: float = Field(default=0, ge=0)

class ManualReviewSave(BaseModel):
    annotations: list[PenAnnotation] = Field(default_factory=list)
    marks: list[ManualMark] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0)
    feedback: str | None = Field(default=None, max_length=4000)
    review_mode: Literal['manual'] = 'manual'

class CorrectionRequest(BaseModel):
    score: float = Field(ge=0)
    feedback: str | None = None

class ResultCreate(BaseModel):
    student_id: int
    subject: str
    semester: int = Field(ge=1, le=8)
    marks: float = Field(ge=0)
    total_marks: float = Field(gt=0, default=100)

class Recommendation(BaseModel):
    category: str
    message: str

class APIResponse(BaseModel):
    message: str
    data: Any | None = None

from typing import Any
import numpy as np
try:
    from sklearn.ensemble import RandomForestClassifier
except ImportError:
    RandomForestClassifier = None
try:
    from .db import query
except ImportError:
    from db import query

_MODEL = None
if RandomForestClassifier:
    _MODEL = RandomForestClassifier(n_estimators=40, random_state=42, max_depth=4)
    _MODEL.fit(np.array([[95, 90, 92, 90], [82, 78, 80, 75], [60, 55, 58, 50], [35, 40, 42, 30], [72, 68, 70, 65]]), ['Low', 'Low', 'Medium', 'High', 'Medium'])

def student_features(student_id: int) -> tuple[list[float], dict[str, Any]]:
    student = query('SELECT * FROM students WHERE id = ?', [student_id])
    if not student:
        raise ValueError('Student not found')
    records = query('SELECT internal_marks, test_marks, previous_semester_marks, percentile_12th FROM academic_records WHERE student_id = ?', [student_id])
    attendance = query('SELECT COALESCE(SUM(classes_attended), 0) attended, COALESCE(SUM(classes_held), 0) held FROM attendance WHERE student_id = ?', [student_id])[0]
    if records:
        values = np.array([[r['internal_marks'], r['test_marks'], r['previous_semester_marks'], r['percentile_12th']] for r in records], dtype=float).mean(axis=0)
    else:
        values = np.array([student[0]['attendance'], student[0]['attendance'], student[0]['attendance'], 80.0])
    attendance_pct = (attendance['attended'] / attendance['held'] * 100) if attendance['held'] else student[0]['attendance']
    features = [float(values[0]), float(values[1]), float(values[2]), float(values[3])]
    return features, {'attendance': round(attendance_pct, 2), 'student': student[0]}

def predict(student_id: int) -> dict[str, Any]:
    features, context = student_features(student_id)
    if _MODEL:
        risk = str(_MODEL.predict([features])[0])
        probability = _MODEL.predict_proba([features])[0]
    else:
        score = sum(features[:3]) / 3
        risk = 'Low' if score >= 80 else 'Medium' if score >= 55 else 'High'
        probability = [0.8 if level == risk else 0.1 for level in ('High', 'Low', 'Medium')]
    recommendations = []
    if context['attendance'] < 80: recommendations.append({'category': 'Attendance', 'message': 'Attend minimum 80% of lectures.'})
    if features[1] < 50: recommendations.append({'category': 'Academics', 'message': 'Complete pending assignments and schedule a faculty mentoring session.'})
    if not recommendations: recommendations.append({'category': 'Performance', 'message': 'Maintain current study and attendance habits.'})
    return {'student_id': student_id, 'risk': risk, 'confidence': round(float(max(probability)), 3), 'features': {'internal_marks': features[0], 'test_marks': features[1], 'previous_semester_marks': features[2], 'percentile_12th': features[3], 'attendance': context['attendance']}, 'recommendations': recommendations}

def grade(marks: float, total: float) -> str:
    pct = marks / total * 100
    return 'A+' if pct >= 90 else 'A' if pct >= 80 else 'B' if pct >= 70 else 'C' if pct >= 60 else 'D' if pct >= 50 else 'F'

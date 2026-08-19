import uuid

import pytest
from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models import GradeRelease, GradeReleaseItem, GradingResult, Role, Status, User
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_confirm_results_contract import _confirm, _confirmable_case, _readiness


def _released_wrong_question() -> tuple[str, str, str]:
    with _confirmable_case() as case:
        result = case.db.get(GradingResult, case.result_id)
        assert result is not None
        result.score = max(result.max_score - 1, 0)
        result.error_type = "concept"
        result.student_feedback = "需要重新检查概念与计算过程。"
        case.db.commit()
        reviewed = TestClient(app).put(
            f"/api/student-answers/{case.answer_id}/review",
            json={
                "decision": "accepted",
                "final_error_type": "concept",
                "final_feedback": "需要重新检查概念与计算过程。",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        readiness = _readiness(case)
        confirmed = _confirm(
            case,
            key=f"teacher-practice-{uuid.uuid4()}",
            review_hash=str(readiness["review_hash"]),
        )
        assert confirmed.status_code == 201, confirmed.text
        return confirmed.json()["grade_release_id"], case.batch_id, str(case.answer_id)


def test_teacher_wrong_questions_uses_latest_formal_release_and_real_filters() -> None:
    release_id, batch_id, answer_id = _released_wrong_question()
    response = TestClient(app).get("/api/teacher/wrong-questions?page=1&page_size=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["summary"]["total_wrong_questions"] == 1
    assert payload["summary"]["affected_students"] == 1
    item = payload["items"][0]
    assert item["grade_release_id"] == release_id
    assert item["grading_batch_id"] == batch_id
    assert item["student_answer_id"] == answer_id
    assert item["score_rate"] < 1
    assert item["error_type"] == "concept"
    assert item["feedback"] == "需要重新检查概念与计算过程。"
    assert item["student_name"]
    assert item["question_content"]

    by_class = TestClient(app).get(
        "/api/teacher/wrong-questions",
        params={"class_id": item["class_id"], "error_type": "concept"},
    )
    assert by_class.status_code == 200 and by_class.json()["total"] == 1
    searched = TestClient(app).get(
        "/api/teacher/wrong-questions", params={"search": item["student_name"]}
    )
    assert searched.status_code == 200 and searched.json()["total"] == 1
    missing = TestClient(app).get(
        "/api/teacher/wrong-questions", params={"search": "不存在的合成关键字"}
    )
    assert missing.status_code == 200 and missing.json()["total"] == 0

    with SessionLocal() as db:
        release_v1 = db.get(GradeRelease, uuid.UUID(release_id))
        assert release_v1 is not None
        source = db.scalar(
            select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == release_v1.id)
        )
        assert source is not None
        release_v2 = GradeRelease(
            owner_id=release_v1.owner_id,
            assignment_id=release_v1.assignment_id,
            class_id=release_v1.class_id,
            version=release_v1.version + 1,
            status="released",
            release_mode=release_v1.release_mode,
            released_at=release_v1.released_at,
            created_by=release_v1.created_by,
        )
        db.add(release_v2)
        db.flush()
        db.add(
            GradeReleaseItem(
                grade_release_id=release_v2.id,
                student_id=source.student_id,
                submission_id=source.submission_id,
                score_snapshot_id=source.score_snapshot_id,
                status="included",
            )
        )
        db.commit()
        release_v2_id = str(release_v2.id)

    latest = TestClient(app).get("/api/teacher/wrong-questions")
    assert latest.status_code == 200
    assert latest.json()["items"][0]["grade_release_id"] == release_v2_id
    assert latest.json()["items"][0]["grade_release_version"] == 2


@pytest.mark.parametrize("role_name", ["student", "admin"])
def test_non_teacher_account_cannot_read_teacher_wrong_questions(role_name: str) -> None:
    with SessionLocal() as db:
        role = Role(name=role_name, description=f"合成{role_name}")
        user = User(
            username=f"practice-{role_name}",
            email=f"practice-{role_name}@ahamark.local",
            display_name=f"合成{role_name}",
            password_hash=hash_password("secure-pass-123"),
            status=Status.active,
        )
        user.roles.append(role)
        db.add(user)
        db.commit()

    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"username": f"practice-{role_name}", "password": "secure-pass-123"},
    )
    assert login.status_code == 200
    response = client.get("/api/teacher/wrong-questions")
    assert response.status_code == 403
    assert response.json()["code"] == "TEACHER_ROLE_REQUIRED"

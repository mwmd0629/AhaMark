import io

from app.db.session import SessionLocal
from app.main import app
from app.models import AuditLog, ClassStudent, Student
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select

client = TestClient(app)


def create_class(name: str = "高一（1）班") -> dict[str, object]:
    response = client.post("/api/classes", json={"name": name, "grade": "高一", "subject": "数学"})
    assert response.status_code == 201
    return response.json()


def test_class_crud_pagination_archive_restore_and_audit() -> None:
    item = create_class()
    assert client.get("/api/classes?page=1&page_size=1&status=active").json()["total"] == 1
    assert (
        client.patch(f"/api/classes/{item['id']}", json={"subject": "物理"}).json()["subject"]
        == "物理"
    )
    assert client.post(f"/api/classes/{item['id']}/archive").json()["status"] == "archived"
    assert client.post(f"/api/classes/{item['id']}/archive").status_code == 200
    assert client.post(f"/api/classes/{item['id']}/restore").json()["status"] == "active"
    with SessionLocal() as db:
        assert (db.scalar(select(func.count()).select_from(AuditLog)) or 0) >= 4


def test_student_reuse_duplicate_membership_and_soft_remove() -> None:
    first, second = create_class("一班"), create_class("二班")
    payload = {"name": "测试学生", "student_number": "0012"}
    student = client.post(f"/api/classes/{first['id']}/students", json=payload)
    assert student.status_code == 201 and student.json()["student_number"] == "0012"
    assert client.post(f"/api/classes/{first['id']}/students", json=payload).status_code == 409
    reused = client.post(f"/api/classes/{second['id']}/students", json=payload)
    assert reused.status_code == 201 and reused.json()["id"] == student.json()["id"]
    assert (
        client.delete(f"/api/classes/{first['id']}/students/{student.json()['id']}").status_code
        == 200
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Student)) == 1
        assert db.scalar(select(func.count()).select_from(ClassStudent)) == 2


def test_group_rejects_student_from_other_class() -> None:
    first, second = create_class("甲班"), create_class("乙班")
    student = client.post(
        f"/api/classes/{first['id']}/students", json={"name": "甲", "student_number": "A01"}
    ).json()
    group = client.post(f"/api/classes/{second['id']}/groups", json={"name": "提高组"}).json()
    response = client.put(
        f"/api/groups/{group['id']}/members", json={"student_ids": [student["id"]]}
    )
    assert response.status_code == 409 and response.json()["code"] == "STUDENT_NOT_IN_CLASS"


def test_csv_preview_confirm_errors_and_idempotency() -> None:
    item = create_class()
    content = (
        "\ufeff姓名,学号,邮箱\r\n"
        "张同学,0001,test@example.com\r\n"
        "李同学,0002,bad\r\n"
        "重复,0001,ok@example.com\r\n"
    )
    preview = client.post(
        f"/api/classes/{item['id']}/imports",
        files={"file": ("students.csv", content.encode(), "text/csv")},
    )
    assert preview.status_code == 201
    data = preview.json()
    assert data["total_rows"] == 3 and data["valid_rows"] == 1 and data["invalid_rows"] == 2
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Student)) == 0
    first = client.post(f"/api/imports/{data['id']}/confirm").json()
    second = client.post(f"/api/imports/{data['id']}/confirm").json()
    assert first["status"] == second["status"] == "confirmed"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Student)) == 1


def test_xlsx_preview_preserves_leading_zero() -> None:
    item = create_class()
    book = Workbook()
    sheet = book.active
    sheet.append(["姓名", "学号"])
    sheet.append(["测试学生", "0007"])
    output = io.BytesIO()
    book.save(output)
    response = client.post(
        f"/api/classes/{item['id']}/imports",
        files={
            "file": (
                "students.xlsx",
                output.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert (
        response.status_code == 201
        and response.json()["rows"][0]["data"]["student_number"] == "0007"
    )


def test_import_empty_and_missing_headers() -> None:
    item = create_class()
    assert (
        client.post(
            f"/api/classes/{item['id']}/imports", files={"file": ("empty.csv", b"", "text/csv")}
        ).status_code
        == 422
    )
    response = client.post(
        f"/api/classes/{item['id']}/imports",
        files={"file": ("bad.csv", "姓名\n甲".encode(), "text/csv")},
    )
    assert response.status_code == 422 and response.json()["code"] == "IMPORT_HEADERS_INVALID"

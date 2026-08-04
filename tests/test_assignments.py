import io
import uuid
from decimal import Decimal

from app.api.assignments import detail
from app.db.session import SessionLocal, engine, get_db
from app.main import app
from app.models import (
    ArchiveStatus,
    Assignment,
    AssignmentClass,
    AssignmentGenerationJob,
    AuditLog,
    FileStatus,
    PaperPage,
    PaperVersion,
    Question,
    SchoolClass,
    StoredFile,
    User,
)
from app.storage.base import ObjectMetadata
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, select

client = TestClient(app)


def actor_and_db():
    db = SessionLocal()
    client.get("/api/classes")
    actor = db.scalar(select(User).where(User.email == "demo-teacher@ahamark.local"))
    assert actor
    return actor, db


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[str] = []

    def ensure_bucket(self) -> None:
        pass

    def put(self, key: str, data: io.BytesIO, size: int, content_type: str) -> ObjectMetadata:
        self.objects[key] = data.read()
        return ObjectMetadata(key, size, content_type)

    def stat(self, key: str) -> ObjectMetadata:
        return ObjectMetadata(key, len(self.objects[key]), None)

    def get(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.objects[key])

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.objects.pop(key, None)

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return f"https://fake.invalid/{key}"


def active_class(db, actor_id, name="八年级一班"):
    item = SchoolClass(owner_id=actor_id, name=name, status=ArchiveStatus.active)
    db.add(item)
    db.commit()
    return item


def create(client, class_id):
    response = client.post(
        "/api/assignments",
        json={
            "title": "函数练习",
            "subject": "数学",
            "grade": "八年级",
            "total_score": 10,
            "class_ids": [str(class_id)],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_assignment_crud_conflict_archive_copy_and_audit():
    actor, db = actor_and_db()
    cls = active_class(db, actor.id)
    item = create(client, cls.id)
    assert client.get("/api/assignments").json()["total"] == 1
    changed = client.patch(
        f"/api/assignments/{item['id']}", json={"title": "新标题", "updated_at": item["updated_at"]}
    )
    assert changed.status_code == 200
    conflict = client.patch(
        f"/api/assignments/{item['id']}", json={"title": "覆盖", "updated_at": item["updated_at"]}
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "EDIT_CONFLICT"
    copied = client.post(f"/api/assignments/{item['id']}/copy")
    assert copied.status_code == 201 and copied.json()["status"] == "draft"
    assert client.post(f"/api/assignments/{item['id']}/archive").json()["status"] == "archived"
    assert client.post(f"/api/assignments/{item['id']}/restore").json()["status"] == "draft"
    assert db.query(AuditLog).filter(AuditLog.resource_type == "assignment").count() >= 4


def test_class_ownership_active_and_duplicates():
    actor, db = actor_and_db()
    cls = active_class(db, actor.id)
    assert (
        client.post(
            "/api/assignments", json={"title": "重复", "class_ids": [str(cls.id), str(cls.id)]}
        ).status_code
        == 422
    )
    cls.status = ArchiveStatus.archived
    db.commit()
    assert (
        client.post("/api/assignments", json={"title": "归档", "class_ids": [str(cls.id)]}).json()[
            "code"
        ]
        == "CLASS_NOT_ACTIVE"
    )
    other = User(email="other@example.com", password_hash="x", display_name="Other")
    db.add(other)
    db.commit()
    foreign = active_class(db, other.id, "外部班级")
    assert (
        client.post(
            "/api/assignments", json={"title": "越权", "class_ids": [str(foreign.id)]}
        ).status_code
        == 403
    )


def test_file_pages_question_region_rubric_and_publish():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    cls = active_class(db, actor.id)
    item = create(client, cls.id)
    aid = item["id"]
    png = io.BytesIO()
    Image.new("RGB", (100, 200), "white").save(png, "PNG")
    upload = client.post(
        f"/api/assignments/{aid}/files",
        files={
            "file": (
                "paper.png",
                png.getvalue(),
                "image/png",
            )
        },
    )
    assert upload.status_code == 201
    detail = client.get(f"/api/assignments/{aid}").json()
    page = detail["paper_version"]["pages"][0]
    assert (page["width"], page["height"]) == (100, 200)
    assert page["file_name"] == "paper.png"
    assert (
        client.patch(f"/api/assignments/{aid}/pages/{page['id']}", json={"rotation": 90}).json()[
            "rotation"
        ]
        == 90
    )
    question = client.post(
        f"/api/assignments/{aid}/questions",
        json={
            "question_number": "1",
            "question_type": "calculation",
            "max_score": 10,
            "content_text": "计算",
            "difficulty": "medium",
            "knowledge_points": ["一次函数"],
        },
    ).json()
    good = client.post(
        f"/api/assignments/{aid}/questions/{question['id']}/regions",
        json={"paper_page_id": page["id"], "x": 0.1, "y": 0.2, "width": 0.8, "height": 0.3},
    )
    assert good.status_code == 201 and good.json()["source"] == "manual"
    bad = client.post(
        f"/api/assignments/{aid}/questions/{question['id']}/regions",
        json={"paper_page_id": page["id"], "x": -0.1, "y": 0, "width": 1, "height": 1},
    )
    assert bad.status_code == 422
    before = client.get(f"/api/assignments/{aid}/publish-check").json()
    assert not before["ready"]
    rubric = client.put(
        f"/api/assignments/{aid}/rubrics/{question['id']}",
        json={"standard_answer": "答案", "items": [{"title": "正确", "points": 10}]},
    )
    assert rubric.status_code == 200
    readiness = client.get(f"/api/assignments/{aid}/manual-publish-readiness")
    assert readiness.status_code == 200
    ready = readiness.json()
    assert ready["ready"] is True
    assert ready["class_ids"] == [str(cls.id)]
    assert len(ready["state_hash"]) == 64

    missing_confirmation = client.post(
        f"/api/assignments/{aid}/manual-publish",
        json={
            "state_hash": ready["state_hash"],
            "expected_assignment_updated_at": ready["expected_assignment_updated_at"],
        },
    )
    assert missing_confirmation.status_code == 422

    current = client.get(f"/api/assignments/{aid}").json()
    changed = client.patch(
        f"/api/assignments/{aid}",
        json={"title": "发布前改名", "updated_at": current["updated_at"]},
    )
    assert changed.status_code == 200
    stale = client.post(
        f"/api/assignments/{aid}/manual-publish",
        json={
            "state_hash": ready["state_hash"],
            "expected_assignment_updated_at": ready["expected_assignment_updated_at"],
            "explicit_confirmation": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "PUBLISH_STATE_STALE"

    ready = client.get(f"/api/assignments/{aid}/manual-publish-readiness").json()
    published = client.post(
        f"/api/assignments/{aid}/manual-publish",
        json={
            "state_hash": ready["state_hash"],
            "expected_assignment_updated_at": ready["expected_assignment_updated_at"],
            "explicit_confirmation": True,
        },
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    # The structured/AI review endpoint remains protected by its own readiness contract.
    assert client.post(f"/api/assignments/{aid}/publish").status_code == 422
    app.dependency_overrides.pop(get_storage, None)


def test_delete_draft_file_removes_object_pages_and_renumbers_remaining_pages():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    try:
        aid = create(client, active_class(db, actor.id, "删除文件测试班").id)["id"]
        file_ids: list[str] = []
        for name in ("first.png", "second.png"):
            image = io.BytesIO()
            Image.new("RGB", (100, 200), "white").save(image, "PNG")
            response = client.post(
                f"/api/assignments/{aid}/files",
                files={"file": (name, image.getvalue(), "image/png")},
            )
            assert response.status_code == 201, response.text
            file_ids.append(response.json()["id"])

        assert len(fake.objects) == 2
        deleted = client.delete(f"/api/assignments/{aid}/files/{file_ids[0]}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"id": file_ids[0], "pages_deleted": 1}
        assert len(fake.objects) == 1
        pages = client.get(f"/api/assignments/{aid}").json()["paper_version"]["pages"]
        assert [(page["file_name"], page["page_number"]) for page in pages] == [("second.png", 1)]
        repeated = client.delete(f"/api/assignments/{aid}/files/{file_ids[0]}")
        assert repeated.status_code == 404
        assert repeated.json()["code"] == "FILE_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_delete_file_does_not_touch_object_when_pending_marker_commit_fails():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    try:
        aid = create(client, active_class(db, actor.id, "删除标记失败班").id)["id"]
        image = io.BytesIO()
        Image.new("RGB", (100, 200), "white").save(image, "PNG")
        uploaded = client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("prepare.png", image.getvalue(), "image/png")},
        ).json()
        storage_key = next(iter(fake.objects))

        def failing_db():
            with SessionLocal() as session:
                original_commit = session.commit

                def fail_first_commit() -> None:
                    session.commit = original_commit  # type: ignore[method-assign]
                    raise RuntimeError("synthetic pending-marker commit failure")

                session.commit = fail_first_commit  # type: ignore[method-assign]
                yield session

        app.dependency_overrides[get_db] = failing_db
        failed = client.delete(f"/api/assignments/{aid}/files/{uploaded['id']}")
        assert failed.status_code == 503
        assert failed.json()["code"] == "FILE_DELETE_PREPARE_FAILED"
        assert fake.delete_calls == []
        assert storage_key in fake.objects

        db.expire_all()
        stored = db.get(StoredFile, uuid.UUID(uploaded["id"]))
        assert stored is not None and stored.status == FileStatus.ready
        assert db.scalar(select(PaperPage).where(PaperPage.stored_file_id == stored.id)) is not None
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_storage, None)


def test_delete_file_storage_failure_is_recoverable_and_retry_is_idempotent():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    class FailingDeleteStorage(FakeStorage):
        fail_delete = True

        def delete(self, key: str) -> None:
            self.delete_calls.append(key)
            if self.fail_delete:
                raise RuntimeError("synthetic object delete failure")
            self.objects.pop(key, None)

    fake = FailingDeleteStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    try:
        aid = create(client, active_class(db, actor.id, "对象删除重试班").id)["id"]
        image = io.BytesIO()
        Image.new("RGB", (100, 200), "white").save(image, "PNG")
        uploaded = client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("retry.png", image.getvalue(), "image/png")},
        ).json()
        storage_key = next(iter(fake.objects))

        failed = client.delete(f"/api/assignments/{aid}/files/{uploaded['id']}")
        assert failed.status_code == 503
        assert failed.json()["code"] == "STORAGE_UNAVAILABLE"
        assert fake.delete_calls == [storage_key]
        assert storage_key in fake.objects
        db.expire_all()
        stored = db.get(StoredFile, uuid.UUID(uploaded["id"]))
        assert stored is not None and stored.status == FileStatus.pending
        assert db.scalar(select(PaperPage).where(PaperPage.stored_file_id == stored.id)) is not None

        fake.fail_delete = False
        retried = client.delete(f"/api/assignments/{aid}/files/{uploaded['id']}")
        assert retried.status_code == 200, retried.text
        assert fake.delete_calls == [storage_key, storage_key]
        assert storage_key not in fake.objects
        db.expire_all()
        assert stored.status == FileStatus.deleted
        assert db.scalar(select(PaperPage).where(PaperPage.stored_file_id == stored.id)) is None
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_delete_file_object_success_then_finalize_commit_failure_can_retry():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    try:
        aid = create(client, active_class(db, actor.id, "数据库收尾重试班").id)["id"]
        image = io.BytesIO()
        Image.new("RGB", (100, 200), "white").save(image, "PNG")
        uploaded = client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("finalize.png", image.getvalue(), "image/png")},
        ).json()
        storage_key = next(iter(fake.objects))

        def failing_db():
            with SessionLocal() as session:
                original_commit = session.commit
                commit_count = 0

                def fail_second_commit() -> None:
                    nonlocal commit_count
                    commit_count += 1
                    if commit_count == 2:
                        raise RuntimeError("synthetic final commit failure")
                    original_commit()

                session.commit = fail_second_commit  # type: ignore[method-assign]
                yield session

        app.dependency_overrides[get_db] = failing_db
        failed = client.delete(f"/api/assignments/{aid}/files/{uploaded['id']}")
        assert failed.status_code == 503
        assert failed.json()["code"] == "FILE_DELETE_FINALIZE_FAILED"
        assert fake.delete_calls == [storage_key]
        assert storage_key not in fake.objects
        db.expire_all()
        stored = db.get(StoredFile, uuid.UUID(uploaded["id"]))
        assert stored is not None and stored.status == FileStatus.pending
        assert db.scalar(select(PaperPage).where(PaperPage.stored_file_id == stored.id)) is not None

        app.dependency_overrides.pop(get_db, None)
        retried = client.delete(f"/api/assignments/{aid}/files/{uploaded['id']}")
        assert retried.status_code == 200, retried.text
        assert fake.delete_calls == [storage_key, storage_key]
        db.expire_all()
        assert stored.status == FileStatus.deleted
        assert db.scalar(select(PaperPage).where(PaperPage.stored_file_id == stored.id)) is None
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_storage, None)


def test_manual_publish_cannot_bypass_an_ai_generation_job():
    actor, db = actor_and_db()
    item = create(client, active_class(db, actor.id, "AI 作业班").id)
    db.add(
        AssignmentGenerationJob(
            owner_id=actor.id,
            assignment_id=uuid.UUID(item["id"]),
            generation=1,
            status="completed",
            current_stage="completed",
            progress=100,
            idempotency_key=f"test-ai-review-{item['id']}",
            request_fingerprint="a" * 64,
            source_snapshot_hash="b" * 64,
            provider_mode="unavailable",
            provider_config_version="test-unavailable-v1",
            prompt_version="test-v1",
            schema_version="test-v1",
        )
    )
    db.commit()

    response = client.get(f"/api/assignments/{item['id']}/manual-publish-readiness")
    assert response.status_code == 409
    assert response.json()["code"] == "AI_REVIEW_REQUIRED"


def test_upload_rejections():
    actor, db = actor_and_db()
    from app.storage.dependencies import get_storage

    app.dependency_overrides[get_storage] = lambda: FakeStorage()
    aid = create(client, active_class(db, actor.id).id)["id"]
    assert (
        client.post(
            f"/api/assignments/{aid}/files", files={"file": ("empty.pdf", b"", "application/pdf")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        ).status_code
        == 415
    )
    assert (
        client.post(
            f"/api/assignments/{aid}/files",
            files={"file": ("../fake.pdf", b"not pdf", "application/pdf")},
        ).status_code
        == 415
    )
    app.dependency_overrides.pop(get_storage, None)


def test_large_assignment_detail_uses_bounded_query_count() -> None:
    actor, db = actor_and_db()
    school_class = active_class(db, actor.id, "容量详情班")
    assignment = Assignment(
        owner_id=actor.id,
        title="100题容量作业",
        total_score=Decimal("100"),
    )
    db.add(assignment)
    db.flush()
    db.add(AssignmentClass(assignment_id=assignment.id, class_id=school_class.id))
    paper = PaperVersion(
        assignment_id=assignment.id,
        version=1,
        created_by=actor.id,
    )
    db.add(paper)
    db.flush()
    assignment.active_paper_version_id = paper.id
    db.add_all(
        [
            Question(
                paper_version_id=paper.id,
                question_number=str(index),
                display_order=index,
                question_type="single_choice",
                content_text=f"合成容量题 {index}",
                max_score=Decimal("1"),
            )
            for index in range(1, 101)
        ]
    )
    db.commit()
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        response = detail(db, assignment)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert len(response["paper_version"]["questions"]) == 100
    assert len(statements) <= 12

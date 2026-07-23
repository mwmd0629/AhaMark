"""Seed isolated 150/200/250-page paper fixtures for Fake OCR orchestration."""

import hashlib
import io
import json

from PIL import Image, ImageDraw

from app.cli.seed_capacity_demo import MARKER, uid
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    FileStatus,
    PaperPage,
    PaperVersion,
    StoredFile,
    User,
    VersionStatus,
)
from app.storage.dependencies import get_storage

SCALES = (150, 200, 250)


def png_fixture() -> bytes:
    image = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "1. Synthetic printed OCR orchestration fixture", fill="black")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def seed_scale(pages: int, content: bytes) -> dict[str, object]:
    teacher_id = uid("teacher-1")
    assignment_id = uid(f"fake-ocr-assignment-{pages}")
    paper_id = uid(f"fake-ocr-paper-{pages}")
    stored_id = uid(f"fake-ocr-source-{pages}")
    key = f"capacity/{MARKER}/fake-ocr-{pages}.png"
    storage = get_storage()
    storage.put(key, io.BytesIO(content), len(content), "image/png")
    with SessionLocal.begin() as db:
        teacher = db.get(User, teacher_id)
        if teacher is None:
            raise RuntimeError("capacity teacher fixture missing")
        assignment = db.get(Assignment, assignment_id)
        if assignment is None:
            assignment = Assignment(
                id=assignment_id,
                owner_id=teacher.id,
                title=f"Fake OCR Capacity {pages}",
                subject="Synthetic",
                grade="S8",
            )
            db.add(assignment)
            db.flush()
        paper = db.get(PaperVersion, paper_id)
        if paper is None:
            paper = PaperVersion(
                id=paper_id,
                assignment_id=assignment.id,
                version=1,
                status=VersionStatus.ready,
                source_type="upload",
                created_by=teacher.id,
            )
            db.add(paper)
            db.flush()
        assignment.active_paper_version_id = paper.id
        stored = db.get(StoredFile, stored_id)
        if stored is None:
            stored = StoredFile(
                id=stored_id,
                owner_id=teacher.id,
                storage_key=key,
                original_name=f"fake-ocr-{pages}.png",
                content_type="image/png",
                size=len(content),
                checksum=hashlib.sha256(content).hexdigest(),
                status=FileStatus.ready,
            )
            db.add(stored)
            db.flush()
        created = 0
        for page_number in range(1, pages + 1):
            page_id = uid(f"fake-ocr-page-{pages}-{page_number}")
            if db.get(PaperPage, page_id) is None:
                db.add(
                    PaperPage(
                        id=page_id,
                        paper_version_id=paper.id,
                        stored_file_id=stored.id,
                        page_number=page_number,
                        source_page_number=1,
                    )
                )
                created += 1
        return {
            "pages": pages,
            "assignment_id": str(assignment.id),
            "paper_version_id": str(paper.id),
            "created_pages": created,
            "source_key": key,
        }


def main() -> None:
    content = png_fixture()
    print(
        json.dumps(
            {
                "marker": MARKER,
                "fixtures": [seed_scale(pages, content) for pages in SCALES],
            }
        )
    )


if __name__ == "__main__":
    main()

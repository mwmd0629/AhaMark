import argparse
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.api.auth import normalize_email
from app.assignment_generation.textbook_sources import text_signals
from app.db.session import SessionLocal
from app.models import TextbookLibrary, TextbookLibraryQuestion, User

BOOKS = {
    "book1": ("math-analysis-lecture-book1", "数学分析讲义", "第1册"),
    "book2": ("math-analysis-lecture-book2", "数学分析讲义", "第二册"),
}


def _exercise_label(value: str) -> str:
    if value.startswith("exercise-"):
        return f"习题 {value.removeprefix('exercise-')}"
    if value.startswith("comprehensive-"):
        return f"综合习题 {value.removeprefix('comprehensive-')}"
    return value


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("题库 JSON 缺少 questions 数组")
    return payload, raw


def import_question_bank(path: Path, owner_email: str) -> dict[str, int]:
    payload, raw = _load(path)
    source_hash = hashlib.sha256(raw).hexdigest()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in payload["questions"]:
        if not isinstance(item, dict) or item.get("book") not in BOOKS:
            raise ValueError("题库包含未知教材分册")
        required = {
            "id",
            "display_number",
            "exercise",
            "text",
            "source_pdf_pages",
            "printed_pages",
            "ocr_confidence",
            "status",
            "review_warnings",
        }
        if not required.issubset(item):
            raise ValueError(f"题库候选缺少字段：{item.get('id', '<unknown>')}")
        if item["status"] not in {"suggested", "manual_required"}:
            raise ValueError(f"题库候选状态不安全：{item['id']}")
        grouped[item["book"]].append(item)
    if set(grouped) != set(BOOKS):
        raise ValueError("题库必须同时包含两册教材")

    created_libraries = 0
    created_questions = 0
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == normalize_email(owner_email)))
        if owner is None:
            raise ValueError("教师账号不存在")
        for book, items in grouped.items():
            source_key, title, volume = BOOKS[book]
            library = db.scalar(
                select(TextbookLibrary).where(
                    TextbookLibrary.owner_id == owner.id,
                    TextbookLibrary.source_key == source_key,
                )
            )
            if library is not None:
                if library.source_content_hash != source_hash:
                    raise ValueError(f"{title} {volume} 已存在不同版本，请先人工核对")
                continue
            library = TextbookLibrary(
                owner_id=owner.id,
                source_key=source_key,
                title=title,
                volume_label=volume,
                source_content_hash=source_hash,
                status="ready",
                question_count=len(items),
                metadata_={
                    "schema_version": payload.get("schema_version"),
                    "generated_at": payload.get("generated_at"),
                    "question_only": True,
                    "requires_teacher_review": True,
                    "math_ocr_reliable": False,
                },
            )
            db.add(library)
            db.flush()
            for item in items:
                pages = [int(value) for value in item["source_pdf_pages"]]
                printed_pages = [int(value) for value in item["printed_pages"]]
                text = str(item["text"]).strip()
                if not text or not pages:
                    raise ValueError(f"题库候选内容或页码为空：{item['id']}")
                db.add(
                    TextbookLibraryQuestion(
                        library_id=library.id,
                        source_key=str(item["id"]),
                        detected_number=str(item["display_number"]),
                        exercise_label=_exercise_label(str(item["exercise"])),
                        pdf_page_number=min(pages),
                        printed_page_number=min(printed_pages) if printed_pages else None,
                        content_text=text,
                        signals=sorted(text_signals(text)),
                        content_hash=str(item["content_hash"]),
                        ocr_confidence=Decimal(str(item["ocr_confidence"])),
                        status=str(item["status"]),
                        warning_codes=list(item["review_warnings"]),
                        metadata_={
                            "source_pdf_pages": pages,
                            "printed_pages": printed_pages,
                            "requires_teacher_review": True,
                            "math_ocr_reliable": False,
                        },
                    )
                )
            created_libraries += 1
            created_questions += len(items)
        db.commit()
    return {
        "created_libraries": created_libraries,
        "created_questions": created_questions,
        "total_questions": sum(len(items) for items in grouped.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入教师私有的教材题目候选库")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--owner-email", required=True)
    args = parser.parse_args()
    result = import_question_bank(args.input, args.owner_email)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

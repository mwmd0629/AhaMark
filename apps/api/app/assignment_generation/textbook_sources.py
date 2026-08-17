"""Pre-index textbooks and create one suggestion-only source match per solution."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentSourceFileAnalysis,
    AssignmentTextbookLibrarySelection,
    PaperPage,
    Question,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
    ReferenceAnswerSourceBinding,
    ReferenceAnswerSourceRegion,
    TextbookContentIndex,
    TextbookLibrary,
    TextbookLibraryQuestion,
    TextbookSourceMatchCandidate,
)
from app.recognition.question_numbers import normalize_question_number


class TextbookSourceMatchError(RuntimeError):
    pass


TEXTBOOK_INDEX_POLICY = "questions-only-v1"
_SPACE = re.compile(r"\s+")
_LEXEME = re.compile(r"[a-z]+\d*|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2,8}", re.I)
_SECTION = re.compile(r"(?:§\s*)?(\d{1,2}\.\d{1,2})(?:\.\d{1,2})?")
_EXERCISE = re.compile(r"(?:习题|练习)\s*(\d{1,2}(?:\.\d{1,2})?)")
_CHAPTER = re.compile(r"第\s*(\d{1,2})\s*章")
_CONTENTS_HEADING = re.compile(r"^\s*目录\s*$")
_ANSWER_HEADING = re.compile(r"^\s*(?:答案|解答|参考答案|提示)\s*[:：]?\s*$")
_SECTION_HEADING = re.compile(r"^\s*§\s*\d{1,2}\.\d{1,2}(?:\.\d{1,2})?")
_CHAPTER_HEADING = re.compile(r"^\s*第\s*\d{1,2}\s*章(?:\s|$)")
_TEXTBOOK_QUESTION_ANCHOR = re.compile(
    r"^\s*(?:第\s*)?\d{1,3}(?:\s*[（(]\s*\d{1,3}\s*[）)])?"
    r"(?:\s*[.．、:：)]\s*|\s+)\S"
)
_COMMON = {
    "证明",
    "求出",
    "求下列",
    "函数",
    "方程",
    "其中",
    "所以",
    "因此",
    "可得",
    "解答",
    "答案",
}


def _normalized_text(text: str) -> str:
    return _SPACE.sub("", text).lower()


def text_signals(text: str) -> set[str]:
    compact = _normalized_text(text)
    signals = {
        token.lower() for token in _LEXEME.findall(text) if len(token) >= 2 and token not in _COMMON
    }
    signals.update(
        f"g:{compact[index : index + 4]}"
        for index in range(max(0, len(compact) - 3))
        if not compact[index : index + 4].isdigit()
    )
    return signals


def signal_overlap(solution_signals: set[str], source_signals: set[str]) -> tuple[float, list[str]]:
    shared = solution_signals & source_signals
    if not solution_signals or not source_signals or len(shared) < 3:
        return 0.0, []
    containment = len(shared) / min(len(solution_signals), len(source_signals))
    union = len(solution_signals | source_signals)
    jaccard = len(shared) / union if union else 0.0
    score = min(0.79, 0.65 * containment + 0.35 * jaccard)
    visible = sorted(signal for signal in shared if not signal.startswith("g:"))[:12]
    if len(visible) < 3:
        visible.extend(sorted(shared - set(visible))[: 3 - len(visible)])
    return score, visible[:12]


def solution_overlap(solution: str, source: str) -> tuple[float, list[str]]:
    return signal_overlap(text_signals(solution), text_signals(source))


@dataclass(frozen=True)
class _Window:
    page: PaperPage
    anchor: RecognitionBlock | None
    blocks: tuple[RecognitionBlock, ...]
    text: str


def _question_anchor_number(block: RecognitionBlock) -> str | None:
    text = (block.text or "").strip()
    if not text or float(block.x) > 0.20 or not _TEXTBOOK_QUESTION_ANCHOR.match(text):
        return None
    return normalize_question_number(text)


def _page_windows(page: PaperPage, blocks: list[RecognitionBlock]) -> list[_Window]:
    ordered = sorted(
        blocks,
        key=lambda row: (float(row.y), float(row.x), row.display_order, row.id),
    )
    anchors = [row for row in ordered if _question_anchor_number(row) is not None]
    if not anchors:
        return []
    windows: list[_Window] = []
    for index, anchor in enumerate(anchors):
        next_y = float(anchors[index + 1].y) if index + 1 < len(anchors) else 1.0
        selected = tuple(
            row for row in ordered if float(row.y) >= float(anchor.y) and float(row.y) < next_y
        )
        windows.append(
            _Window(
                page=page,
                anchor=anchor,
                blocks=selected,
                text="\n".join(
                    (row.text or "").strip() for row in selected if (row.text or "").strip()
                ),
            )
        )
    return windows


def _exercise_question_windows(
    page: PaperPage,
    blocks: list[RecognitionBlock],
    active_exercise: str | None,
) -> tuple[list[_Window], str | None, str | None]:
    ordered = sorted(
        blocks,
        key=lambda row: (float(row.y), float(row.x), row.display_order, row.id),
    )
    texts = [(row, (row.text or "").strip()) for row in ordered if (row.text or "").strip()]
    if any(_CONTENTS_HEADING.fullmatch(text) for _row, text in texts):
        return [], None, None
    exercise_match = None
    for _row, text in texts:
        exercise_match = _EXERCISE.search(text)
        if exercise_match is not None:
            break
    if exercise_match is not None:
        active_exercise = f"习题 {exercise_match.group(1)}"
    elif any(
        float(row.y) > 0.12 and (_SECTION_HEADING.match(text) or _CHAPTER_HEADING.match(text))
        for row, text in texts
    ):
        active_exercise = None
    if active_exercise is None:
        return [], None, None
    page_exercise = active_exercise
    answer_row = next(
        (row for row, text in texts if _ANSWER_HEADING.fullmatch(text)),
        None,
    )
    question_blocks = (
        [row for row in ordered if float(row.y) < float(answer_row.y)]
        if answer_row is not None
        else ordered
    )
    windows = _page_windows(page, question_blocks)
    return windows, page_exercise, None if answer_row is not None else active_exercise


def _labels(
    page_blocks: list[RecognitionBlock],
) -> tuple[str | None, str | None, str | None, int | None]:
    text = "\n".join((row.text or "").strip() for row in page_blocks if (row.text or "").strip())
    chapter_match = _CHAPTER.search(text)
    section_match = _SECTION.search(text)
    exercise_match = _EXERCISE.search(text)
    printed_page = next(
        (
            int((row.text or "").strip())
            for row in page_blocks
            if float(row.y) <= 0.15 and re.fullmatch(r"\d{1,4}", (row.text or "").strip())
        ),
        None,
    )
    return (
        f"第 {chapter_match.group(1)} 章" if chapter_match else None,
        f"§{section_match.group(1)}" if section_match else None,
        f"习题 {exercise_match.group(1)}" if exercise_match else None,
        printed_page,
    )


def _trusted_blocks(
    db: Session,
    assignment: Assignment,
    stored_file_id: uuid.UUID,
) -> tuple[list[PaperPage], dict[uuid.UUID, list[RecognitionBlock]]]:
    recognition = db.scalar(
        select(RecognitionJob)
        .where(
            RecognitionJob.paper_version_id == assignment.active_paper_version_id,
            RecognitionJob.status == RecognitionStatus.completed,
        )
        .order_by(RecognitionJob.created_at.desc(), RecognitionJob.id.desc())
    )
    if recognition is None:
        raise TextbookSourceMatchError("TEXTBOOK_RECOGNITION_REQUIRED")
    pages = list(
        db.scalars(
            select(PaperPage)
            .where(
                PaperPage.paper_version_id == assignment.active_paper_version_id,
                PaperPage.stored_file_id == stored_file_id,
                PaperPage.status != "excluded",
            )
            .order_by(PaperPage.source_page_number, PaperPage.page_number, PaperPage.id)
        )
    )
    if not pages:
        raise TextbookSourceMatchError("TEXTBOOK_PAGES_REQUIRED")
    page_ids = {page.id for page in pages}
    all_blocks = list(
        db.scalars(
            select(RecognitionBlock)
            .where(
                RecognitionBlock.recognition_job_id == recognition.id,
                RecognitionBlock.paper_page_id.in_(page_ids),
                RecognitionBlock.status == "recognized",
                RecognitionBlock.text.is_not(None),
            )
            .order_by(RecognitionBlock.paper_page_id, RecognitionBlock.y, RecognitionBlock.x)
        )
    )
    trusted = [
        block
        for block in all_blocks
        if block.source.startswith("pdf_text:")
        or (
            recognition.provider not in {"fake", "unavailable"}
            and not block.source.startswith("fake:")
        )
    ]
    if not trusted:
        raise TextbookSourceMatchError("TEXTBOOK_TRUSTED_TEXT_REQUIRED")
    return pages, {
        page.id: [block for block in trusted if block.paper_page_id == page.id] for page in pages
    }


def build_textbook_index(
    db: Session,
    *,
    assignment: Assignment,
    revision: AssignmentDraftRevision,
    textbook: AssignmentSourceFileAnalysis,
) -> list[TextbookContentIndex]:
    if textbook.analysis_status != "confirmed" or textbook.teacher_confirmed_role != "textbook":
        raise TextbookSourceMatchError("TEXTBOOK_ROLE_NOT_CONFIRMED")
    latest_version = int(
        db.scalar(
            select(func.coalesce(func.max(TextbookContentIndex.index_version), 0)).where(
                TextbookContentIndex.source_file_analysis_id == textbook.id
            )
        )
        or 0
    )
    existing = list(
        db.scalars(
            select(TextbookContentIndex).where(
                TextbookContentIndex.source_file_analysis_id == textbook.id,
                TextbookContentIndex.index_version == latest_version,
                TextbookContentIndex.index_policy == TEXTBOOK_INDEX_POLICY,
                TextbookContentIndex.source_snapshot_hash == revision.source_snapshot_hash,
            )
        )
    )
    if existing:
        return existing
    pages, by_page = _trusted_blocks(db, assignment, textbook.stored_file_id)
    version = latest_version + 1
    created: list[TextbookContentIndex] = []
    active_exercise: str | None = None
    for page in pages:
        labels = _labels(by_page[page.id])
        windows, page_exercise, active_exercise = _exercise_question_windows(
            page,
            by_page[page.id],
            active_exercise,
        )
        for window in windows:
            signals = sorted(text_signals(window.text))
            if len(signals) < 3:
                continue
            chapter, section, _exercise, printed_page = labels
            assert window.anchor is not None
            source_key = str(window.anchor.id)
            row = TextbookContentIndex(
                owner_id=assignment.owner_id,
                assignment_id=assignment.id,
                draft_revision_id=revision.id,
                paper_version_id=assignment.active_paper_version_id,
                source_file_analysis_id=textbook.id,
                source_page_id=page.id,
                source_recognition_block_id=window.anchor.id,
                source_key=source_key,
                index_version=version,
                index_policy=TEXTBOOK_INDEX_POLICY,
                detected_number=_question_anchor_number(window.anchor),
                chapter_label=chapter,
                section_label=section,
                exercise_label=page_exercise,
                pdf_page_number=page.source_page_number or page.page_number,
                printed_page_number=printed_page,
                signals=signals,
                recognition_block_ids=[str(block.id) for block in window.blocks[:50]],
                content_hash=hashlib.sha256("\n".join(signals).encode("utf-8")).hexdigest(),
                source_snapshot_hash=revision.source_snapshot_hash,
            )
            db.add(row)
            created.append(row)
    if not created:
        raise TextbookSourceMatchError("TEXTBOOK_TRUSTED_TEXT_REQUIRED")
    db.flush()
    return created


def _latest_indexes(
    db: Session, textbooks: list[AssignmentSourceFileAnalysis]
) -> list[TextbookContentIndex]:
    rows: list[TextbookContentIndex] = []
    for textbook in textbooks:
        version = int(
            db.scalar(
                select(func.coalesce(func.max(TextbookContentIndex.index_version), 0)).where(
                    TextbookContentIndex.source_file_analysis_id == textbook.id,
                    TextbookContentIndex.index_policy == TEXTBOOK_INDEX_POLICY,
                )
            )
            or 0
        )
        if version:
            rows.extend(
                db.scalars(
                    select(TextbookContentIndex).where(
                        TextbookContentIndex.source_file_analysis_id == textbook.id,
                        TextbookContentIndex.index_version == version,
                        TextbookContentIndex.index_policy == TEXTBOOK_INDEX_POLICY,
                    )
                )
            )
    return rows


def binding_solution_text(db: Session, binding: ReferenceAnswerSourceBinding) -> str | None:
    anchor = db.get(RecognitionBlock, binding.source_recognition_block_id)
    if anchor is None:
        return None
    recognition = db.get(RecognitionJob, anchor.recognition_job_id)
    if recognition is None or recognition.provider in {"fake", "unavailable"}:
        trusted_prefixes: tuple[str, ...] = ("pdf_text:",)
    else:
        trusted_prefixes = ("pdf_text:", "rapidocr:", "tesseract:")
    regions = list(
        db.scalars(
            select(ReferenceAnswerSourceRegion)
            .where(ReferenceAnswerSourceRegion.binding_id == binding.id)
            .order_by(ReferenceAnswerSourceRegion.display_order)
        )
    )
    rows: list[tuple[int, RecognitionBlock]] = []
    for region in regions:
        page = db.get(PaperPage, region.paper_page_id)
        if page is None:
            continue
        blocks = db.scalars(
            select(RecognitionBlock)
            .where(
                RecognitionBlock.recognition_job_id == anchor.recognition_job_id,
                RecognitionBlock.paper_page_id == region.paper_page_id,
                RecognitionBlock.id != anchor.id,
                RecognitionBlock.status == "recognized",
                RecognitionBlock.text.is_not(None),
                RecognitionBlock.x >= region.x,
                RecognitionBlock.y >= region.y,
                RecognitionBlock.x + RecognitionBlock.width <= region.x + region.width,
                RecognitionBlock.y + RecognitionBlock.height <= region.y + region.height,
            )
            .order_by(RecognitionBlock.display_order, RecognitionBlock.y, RecognitionBlock.x)
        )
        rows.extend(
            (page.page_number, block)
            for block in blocks
            if block.source.startswith(trusted_prefixes) and (block.text or "").strip()
        )
    rows.sort(key=lambda item: (item[0], item[1].display_order, item[1].y, item[1].x))
    text = "\n".join((block.text or "").strip() for _page, block in rows)
    return text or None


def _create_top_match(
    db: Session,
    *,
    assignment: Assignment,
    revision: AssignmentDraftRevision,
    solution: str,
    indexes: list[TextbookContentIndex],
    library_questions: list[TextbookLibraryQuestion] | None = None,
    question: Question | None = None,
    answer: AssignmentAnswerDraftCandidate | None = None,
    binding: ReferenceAnswerSourceBinding | None = None,
) -> TextbookSourceMatchCandidate | None:
    solution_hash = hashlib.sha256(solution.encode("utf-8")).hexdigest()
    if question is not None:
        current_filter = TextbookSourceMatchCandidate.question_id == question.id
    elif binding is not None:
        current_filter = TextbookSourceMatchCandidate.source_reference_binding_id == binding.id
    else:
        raise ValueError("question or source binding is required")
    confirmed = db.scalar(
        select(TextbookSourceMatchCandidate.id).where(
            TextbookSourceMatchCandidate.draft_revision_id == revision.id,
            current_filter,
            TextbookSourceMatchCandidate.status == "confirmed",
        )
    )
    if confirmed is not None:
        return None
    existing = db.scalar(
        select(TextbookSourceMatchCandidate).where(
            TextbookSourceMatchCandidate.draft_revision_id == revision.id,
            current_filter,
            TextbookSourceMatchCandidate.status == "suggested",
            TextbookSourceMatchCandidate.solution_content_hash == solution_hash,
            TextbookSourceMatchCandidate.source_snapshot_hash == revision.source_snapshot_hash,
        )
    )
    if existing is not None:
        return existing
    solution_signals = text_signals(solution)
    sources: list[TextbookContentIndex | TextbookLibraryQuestion] = [
        *indexes,
        *(library_questions or []),
    ]
    scored = [(*signal_overlap(solution_signals, set(index.signals)), index) for index in sources]
    scored = [item for item in scored if item[0] >= 0.08]
    if not scored:
        return None
    score, shared, best = max(
        scored,
        key=lambda item: (
            item[0],
            -(item[2].pdf_page_number),
            item[2].detected_number or "",
        ),
    )
    for old in db.scalars(
        select(TextbookSourceMatchCandidate)
        .where(
            TextbookSourceMatchCandidate.draft_revision_id == revision.id,
            current_filter,
            TextbookSourceMatchCandidate.status == "suggested",
        )
        .with_for_update()
    ):
        old.status = "superseded"
    max_version = db.scalar(
        select(func.max(TextbookSourceMatchCandidate.match_version)).where(
            TextbookSourceMatchCandidate.draft_revision_id == revision.id,
            current_filter,
        )
    )
    version = (max_version or 0) + 1
    library_question = best if isinstance(best, TextbookLibraryQuestion) else None
    content_index = best if isinstance(best, TextbookContentIndex) else None
    row = TextbookSourceMatchCandidate(
        owner_id=assignment.owner_id,
        assignment_id=assignment.id,
        draft_revision_id=revision.id,
        paper_version_id=assignment.active_paper_version_id,
        question_id=question.id if question is not None else None,
        answer_candidate_id=answer.id if answer is not None else None,
        source_reference_binding_id=binding.id if binding is not None else None,
        source_file_analysis_id=(
            content_index.source_file_analysis_id if content_index is not None else None
        ),
        source_page_id=content_index.source_page_id if content_index is not None else None,
        source_recognition_block_id=(
            content_index.source_recognition_block_id if content_index is not None else None
        ),
        library_question_id=library_question.id if library_question is not None else None,
        detected_number=best.detected_number,
        chapter_label=content_index.chapter_label if content_index is not None else None,
        section_label=content_index.section_label if content_index is not None else None,
        exercise_label=best.exercise_label,
        pdf_page_number=best.pdf_page_number,
        printed_page_number=best.printed_page_number,
        match_version=version,
        rank=1,
        confidence=Decimal(str(round(score, 5))),
        matching_method=(
            "library_solution_overlap_v1"
            if library_question is not None
            else "preindexed_solution_overlap_v2"
        ),
        solution_content_hash=solution_hash,
        source_snapshot_hash=revision.source_snapshot_hash,
        evidence={
            "textbook_content_index_id": str(content_index.id) if content_index else None,
            "textbook_library_question_id": (
                str(library_question.id) if library_question else None
            ),
            "shared_signals": shared,
        },
        warning_codes=[
            "SOLUTION_SIMILARITY_REQUIRES_TEACHER_CONFIRMATION",
            "MATH_EQUIVALENCE_NOT_VERIFIED",
        ],
    )
    db.add(row)
    db.flush()
    return row


def auto_match_available_solutions(
    db: Session,
    *,
    assignment: Assignment,
    revision: AssignmentDraftRevision,
) -> dict[str, int]:
    textbooks = list(
        db.scalars(
            select(AssignmentSourceFileAnalysis).where(
                AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                AssignmentSourceFileAnalysis.analysis_status == "confirmed",
                AssignmentSourceFileAnalysis.teacher_confirmed_role == "textbook",
            )
        )
    )
    for textbook in textbooks:
        try:
            build_textbook_index(
                db,
                assignment=assignment,
                revision=revision,
                textbook=textbook,
            )
        except TextbookSourceMatchError:
            continue
    indexes = _latest_indexes(db, textbooks)
    library_questions = list(
        db.scalars(
            select(TextbookLibraryQuestion)
            .join(
                AssignmentTextbookLibrarySelection,
                AssignmentTextbookLibrarySelection.library_id == TextbookLibraryQuestion.library_id,
            )
            .join(TextbookLibrary, TextbookLibrary.id == TextbookLibraryQuestion.library_id)
            .where(
                AssignmentTextbookLibrarySelection.assignment_id == assignment.id,
                AssignmentTextbookLibrarySelection.owner_id == assignment.owner_id,
                TextbookLibraryQuestion.status == "suggested",
                TextbookLibrary.status == "ready",
            )
        )
    )
    if not indexes and not library_questions:
        return {"indexed_textbooks": 0, "created": 0}
    created = 0
    answers = list(
        db.scalars(
            select(AssignmentAnswerDraftCandidate)
            .where(
                AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
                AssignmentAnswerDraftCandidate.status.in_(
                    ["suggested", "manual_required", "accepted", "modified"]
                ),
            )
            .order_by(
                AssignmentAnswerDraftCandidate.question_id,
                AssignmentAnswerDraftCandidate.candidate_version.desc(),
            )
        )
    )
    latest_answers: dict[object, AssignmentAnswerDraftCandidate] = {}
    for answer in answers:
        latest_answers.setdefault(answer.question_id, answer)
    for answer in latest_answers.values():
        question = db.get(Question, answer.question_id)
        solution = (answer.normalized_content or answer.raw_content or "").strip()
        if question is not None and solution:
            created += int(
                _create_top_match(
                    db,
                    assignment=assignment,
                    revision=revision,
                    solution=solution,
                    indexes=indexes,
                    library_questions=library_questions,
                    question=question,
                    answer=answer,
                )
                is not None
            )
    bindings = list(
        db.scalars(
            select(ReferenceAnswerSourceBinding).where(
                ReferenceAnswerSourceBinding.draft_revision_id == revision.id,
                ReferenceAnswerSourceBinding.question_id.is_(None),
                ReferenceAnswerSourceBinding.status.in_(["suggested", "confirmed"]),
            )
        )
    )
    for binding in bindings:
        binding_solution = binding_solution_text(db, binding)
        if binding_solution:
            created += int(
                _create_top_match(
                    db,
                    assignment=assignment,
                    revision=revision,
                    solution=binding_solution,
                    indexes=indexes,
                    library_questions=library_questions,
                    binding=binding,
                )
                is not None
            )
    selected_library_count = len({row.library_id for row in library_questions})
    return {
        "indexed_textbooks": len(textbooks) + selected_library_count,
        "created": created,
    }


def find_textbook_source_matches(
    db: Session,
    *,
    assignment: Assignment,
    revision: AssignmentDraftRevision,
    question: Question,
    textbook: AssignmentSourceFileAnalysis,
    limit: int = 1,
) -> list[TextbookSourceMatchCandidate]:
    del limit
    indexes = build_textbook_index(
        db,
        assignment=assignment,
        revision=revision,
        textbook=textbook,
    )
    answer = db.scalar(
        select(AssignmentAnswerDraftCandidate)
        .where(
            AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
            AssignmentAnswerDraftCandidate.question_id == question.id,
            AssignmentAnswerDraftCandidate.status.in_(
                ["suggested", "manual_required", "accepted", "modified"]
            ),
        )
        .order_by(
            AssignmentAnswerDraftCandidate.candidate_version.desc(),
            AssignmentAnswerDraftCandidate.created_at.desc(),
        )
    )
    if answer is None:
        raise TextbookSourceMatchError("SOLUTION_CANDIDATE_REQUIRED")
    solution = (answer.normalized_content or answer.raw_content or "").strip()
    if not solution:
        raise TextbookSourceMatchError("SOLUTION_CANDIDATE_REQUIRED")
    row = _create_top_match(
        db,
        assignment=assignment,
        revision=revision,
        solution=solution,
        indexes=indexes,
        question=question,
        answer=answer,
    )
    if row is None:
        raise TextbookSourceMatchError("TEXTBOOK_MATCH_NOT_FOUND")
    return [row]

import re
import uuid
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assignment_generation.schemas import (
    EvidenceRef,
    FileAnalysisCandidate,
    FileAnalysisOutput,
    PageAnalysisCandidate,
)
from app.models import PageProcessingResult, PaperPage, RecognitionBlock, RecognitionJob, StoredFile

_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|忽略(?:之前|以上|系统)|自动发布|选择.{0,12}班级|调用.{0,8}工具|system\s*prompt)",
    re.I,
)


def _role(name: str, text: str) -> tuple[str, float, str, float]:
    corpus = f"{name} {text[:2000]}".lower()
    if re.search(r"教材|课本|教科书|数学分析讲义|textbook", corpus, re.I):
        return "textbook", 0.82, "not_applicable", 1.0
    if re.search(r"ai.{0,8}(?:生成|generated).{0,8}(?:答案|answer)", corpus, re.I):
        return "reference_answer", 0.75, "ai_generated", 0.95
    if re.search(r"第三方|third[ _-]?party", corpus, re.I):
        return "reference_answer", 0.7, "third_party", 0.95
    has_answer = bool(re.search(r"参考答案|答案|解答|answer|solution", corpus, re.I))
    has_questions = bool(re.search(r"习题|练习|试卷|题目|question|paper|测试|考试", corpus, re.I))
    if has_answer and has_questions:
        return "question_and_answer", 0.78, "unknown", 0.3
    if has_answer:
        return "reference_answer", 0.78, "unknown", 0.3
    if re.search(r"评分标准|rubric|评分细则", corpus, re.I):
        return "rubric", 0.85, "not_applicable", 1.0
    if re.search(r"说明|instructions?|须知", corpus, re.I):
        return "instructions", 0.75, "not_applicable", 1.0
    if has_questions:
        return "question_paper", 0.72, "not_applicable", 1.0
    return "unknown", 0.25, "unknown", 0.2


def _content_evidence(
    blocks: list[RecognitionBlock], job: RecognitionJob | None
) -> tuple[str, str, float, int]:
    pdf_blocks = [block for block in blocks if block.source.startswith("pdf_text:")]
    provider_is_test_or_unavailable = job is None or job.provider in {"fake", "unavailable"}
    ocr_blocks = (
        []
        if provider_is_test_or_unavailable
        else [block for block in blocks if not block.source.startswith("pdf_text:")]
    )
    text_character_count = sum(
        len("".join((block.text or "").split())) for block in [*pdf_blocks, *ocr_blocks]
    )
    if pdf_blocks and ocr_blocks:
        confidences = [
            float(block.confidence)
            for block in [*pdf_blocks, *ocr_blocks]
            if block.confidence is not None
        ]
        return "mixed", "mixed", min(confidences, default=0.5), text_character_count
    if pdf_blocks:
        return "text", "pdf_text", 1.0, text_character_count
    if ocr_blocks:
        confidences = [
            float(block.confidence) for block in ocr_blocks if block.confidence is not None
        ]
        return "scanned", "ocr", min(confidences, default=0.5), text_character_count
    return "unknown", "unavailable", 0.0, 0


def _file_content_evidence(
    page_evidence: list[tuple[str, str, float, int]],
) -> tuple[str, str, float]:
    if not page_evidence or any(
        mode == "unknown" for mode, _source, _confidence, _count in page_evidence
    ):
        return "unknown", "unavailable", 0.0
    modes = {mode for mode, _source, _confidence, _count in page_evidence}
    sources = {source for _mode, source, _confidence, _count in page_evidence}
    confidence = min(item[2] for item in page_evidence)
    if "mixed" in modes or len(modes) > 1:
        return "mixed", "mixed", confidence
    return next(iter(modes)), next(iter(sources)), confidence


def collect_file_analysis(db: Session, pages: list[PaperPage]) -> FileAnalysisOutput:
    grouped: dict[uuid.UUID, list[PaperPage]] = defaultdict(list)
    for page in pages:
        grouped[page.stored_file_id].append(page)
    files = (
        {
            row.id: row
            for row in db.scalars(select(StoredFile).where(StoredFile.id.in_(grouped))).all()
        }
        if grouped
        else {}
    )
    latest_jobs: dict[uuid.UUID, RecognitionJob] = {}
    for page in pages:
        job = db.scalar(
            select(RecognitionJob)
            .where(RecognitionJob.paper_version_id == page.paper_version_id)
            .order_by(RecognitionJob.created_at.desc())
            .limit(1)
        )
        if job:
            latest_jobs[page.paper_version_id] = job
    blocks_by_page: dict[uuid.UUID, list[RecognitionBlock]] = defaultdict(list)
    for job in latest_jobs.values():
        for block in db.scalars(
            select(RecognitionBlock)
            .where(RecognitionBlock.recognition_job_id == job.id)
            .order_by(RecognitionBlock.display_order)
        ).all():
            blocks_by_page[block.paper_page_id].append(block)
    results: dict[uuid.UUID, PageProcessingResult] = {}
    for page in pages:
        job = latest_jobs.get(page.paper_version_id)
        if job:
            row = db.scalar(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job.id,
                    PageProcessingResult.paper_page_id == page.id,
                )
            )
            if row:
                results[page.id] = row
    content_by_page = {
        page.id: _content_evidence(
            blocks_by_page.get(page.id, []), latest_jobs.get(page.paper_version_id)
        )
        for page in pages
    }
    checksum_first: dict[str, uuid.UUID] = {}
    name_page_signatures: dict[tuple[str, int], uuid.UUID] = {}
    roles_by_file: dict[uuid.UUID, str] = {}
    file_rows: list[FileAnalysisCandidate] = []
    page_rows: list[PageAnalysisCandidate] = []
    injection_evidence: list[EvidenceRef] = []
    variant_counts: Counter[str] = Counter()
    for file_id, file_pages in grouped.items():
        stored = files[file_id]
        text = " ".join(
            (block.text or "") for page in file_pages for block in blocks_by_page.get(page.id, [])
        )[:8000]
        role, role_conf, answer_source, source_conf = _role(stored.original_name, text)
        content_mode, text_source, content_confidence = _file_content_evidence(
            [content_by_page[page.id] for page in file_pages]
        )
        warnings: list[str] = []
        if role == "unknown" or role_conf < 0.7:
            warnings.append("FILE_ROLE_REVIEW_REQUIRED")
        if content_mode == "unknown":
            warnings.append("TEXT_SOURCE_UNAVAILABLE")
        duplicate = checksum_first.get(stored.checksum)
        if duplicate:
            warnings.append("DUPLICATE_FILE")
        else:
            checksum_first[stored.checksum] = stored.id
        normalized_name = re.sub(
            r"(?:答案|answer|试卷|paper|[_\-\s])", "", stored.original_name.lower()
        )
        signature = (normalized_name, len(file_pages))
        if not duplicate and signature in name_page_signatures:
            duplicate = name_page_signatures[signature]
            warnings.append("PROBABLE_DUPLICATE_FILE")
        else:
            name_page_signatures[signature] = stored.id
        if duplicate and roles_by_file.get(duplicate) not in {None, role}:
            warnings.append("FILE_ROLE_CONFLICT_REVIEW_REQUIRED")
        roles_by_file[stored.id] = role
        evidence = [
            EvidenceRef(
                kind="file_name",
                reference_id=str(stored.id),
                summary=(
                    "文件用途无法可靠判断，需要教师选择"
                    if any(
                        code in {"FILE_ROLE_REVIEW_REQUIRED", "FILE_ROLE_CONFLICT_REVIEW_REQUIRED"}
                        for code in warnings
                    )
                    else "文件用途由受控文件名与 OCR 线索自动识别，可由教师修改"
                ),
            )
        ]
        if _INJECTION.search(f"{stored.original_name} {text}"):
            injection_evidence.append(
                EvidenceRef(
                    kind="file",
                    reference_id=str(stored.id),
                    summary="检测到类似越权指令的文档文字；已作为不可信数据隔离",
                )
            )
        match = re.search(
            r"(?:^|[^a-z])([ab])(?:卷|版|variant)(?:$|[^a-z])", f" {stored.original_name.lower()} "
        )
        variant = f"possible_variant_{match.group(1)}" if match else None
        if variant:
            variant_counts[variant] += 1
        file_rows.append(
            FileAnalysisCandidate(
                stored_file_id=str(stored.id),
                detected_mime_type=stored.content_type,
                checksum=stored.checksum,
                page_count=len(file_pages),
                content_mode=content_mode,
                text_source=text_source,
                content_mode_confidence=content_confidence,
                suggested_role=role,
                role_confidence=role_conf,
                suggested_answer_source=answer_source,
                answer_source_confidence=source_conf,
                duplicate_of_file_id=str(duplicate) if duplicate else None,
                evidence=evidence,
                warning_codes=warnings,
            )
        )
        ordered = sorted(file_pages, key=lambda p: p.page_number)
        source_numbers = [p.source_page_number for p in ordered if p.source_page_number is not None]
        missing = any(b != a + 1 for a, b in zip(source_numbers, source_numbers[1:], strict=False))
        for page in ordered:
            result = results.get(page.id)
            params = dict(result.processing_parameters) if result else {}
            page_content_mode, page_text_source, page_content_confidence, text_count = (
                content_by_page[page.id]
            )
            blank = (
                float(params.get("blank_probability", 0))
                if params.get("blank_probability") is not None
                else None
            )
            quality = (
                float(result.quality_score) if result and result.quality_score is not None else None
            )
            codes: list[str] = []
            status = "ready"
            if page.status == "pending_conversion":
                status = "pending_conversion"
                codes.append("PENDING_CONVERSION")
            elif result and result.status.value == "failed":
                error_code = (result.error_code or "").upper()
                if "UNSUPPORTED" in error_code:
                    status = "unsupported"
                    codes.append("UNSUPPORTED_FILE")
                elif "CORRUPT" in error_code or "INVALID" in error_code:
                    status = "corrupted"
                    codes.append("CORRUPT_FILE")
                else:
                    status = "processing_failed"
                    codes.append("LOW_QUALITY_PAGE")
            elif blank is not None and blank >= 0.95:
                status = "blank"
                codes.append("BLANK_PAGE")
            elif quality is not None and quality < 0.5:
                status = "low_quality"
                codes.append("LOW_QUALITY_PAGE")
            if missing:
                codes.append("POSSIBLE_MISSING_PAGE")
            if page_content_mode == "unknown":
                codes.append("TEXT_SOURCE_UNAVAILABLE")
            page_rows.append(
                PageAnalysisCandidate(
                    paper_page_id=str(page.id),
                    stored_file_id=str(stored.id),
                    status=status,
                    content_mode=page_content_mode,
                    text_source=page_text_source,
                    content_mode_confidence=page_content_confidence,
                    text_character_count=text_count,
                    quality_score=quality,
                    blank_probability=blank,
                    missing_page_suspected=missing,
                    low_quality=status == "low_quality",
                    corrupted=status == "corrupted",
                    variant_label=variant or "unknown",
                    metrics={
                        k: v
                        for k, v in params.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    },
                    evidence=[
                        EvidenceRef(
                            kind="page",
                            reference_id=str(page.id),
                            summary="复用现有页面状态与最新识别质量指标",
                        )
                    ],
                    warning_codes=codes,
                )
            )
    mixed = (
        variant_counts.get("possible_variant_a", 0) > 0
        and variant_counts.get("possible_variant_b", 0) > 0
    )
    if mixed:
        for page_candidate in page_rows:
            page_candidate.mixed_document_suspected = True
            page_candidate.warning_codes.append("MULTIPLE_VARIANTS_SUSPECTED")
    return FileAnalysisOutput(
        files=file_rows,
        pages=page_rows,
        prompt_injection_detected=bool(injection_evidence),
        prompt_injection_evidence=injection_evidence,
    )

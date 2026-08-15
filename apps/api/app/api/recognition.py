import io
import uuid
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.core.request_id import celery_request_headers
from app.db.session import get_db
from app.failure_recovery import recovery_fault_checkpoint
from app.models import (
    Assignment,
    CandidateStatus,
    FormulaRegion,
    PageProcessingResult,
    PageRecognitionStatus,
    PaperPage,
    PaperVersion,
    Question,
    QuestionCandidate,
    QuestionCandidateRegion,
    QuestionRegion,
    RecognitionBlock,
    RecognitionCorrection,
    RecognitionJob,
    RecognitionStatus,
    StoredFile,
    VersionStatus,
    now_utc,
)
from app.recognition.formula import formula_provider_from_settings
from app.recognition.math_structure import apply_math_risk_status, detect_math_structure_risks
from app.recognition.page_quality import assess_page_quality, measure_page_quality
from app.recognition.pipeline import (
    DefaultDocumentConverter,
    PageArtifact,
    PillowPreprocessor,
    ProviderBlock,
    QuestionAnchor,
    RecognitionError,
    derivative_key,
    derive_question_regions,
    extract_pdf_text_layer,
    fuse_text_sources,
    parse_hierarchical_question_number,
    provider_from_settings,
    read_all,
    safe_provider_readiness,
    store_artifact,
    text_for_question_region,
)
from app.recognition.text_integrity import (
    CharacterEncodingCorruptionError,
    ensure_text_fields_integrity,
    text_quality_statistics,
)
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/assignments/{assignment_id}/recognition", tags=["recognition"])


def question_source_kind(source: str) -> str:
    if source.startswith("pdf_text:"):
        return "pdf_text"
    if source.startswith("mixed:"):
        return "mixed"
    return "ocr"


def _page_quality(parameters: dict[str, Any]) -> dict[str, Any]:
    raw = parameters.get("page_quality")
    if not isinstance(raw, dict):
        return {"level": "review_required", "issues": []}
    level = raw.get("level")
    if level not in {"good", "review_required", "rescan_required"}:
        level = "review_required"
    allowed_issues = {
        "low_resolution",
        "blur",
        "low_contrast",
        "shadow",
        "skew",
        "crop_risk",
    }
    issues = raw.get("issues")
    public_issues = (
        [item for item in issues if isinstance(item, str) and item in allowed_issues]
        if isinstance(issues, list)
        else []
    )
    return {"level": level, "issues": public_issues}


def _quality_score(parameters: dict[str, Any], field: str) -> Decimal:
    page_quality = parameters.get("page_quality")
    metrics = page_quality.get("metrics") if isinstance(page_quality, dict) else None
    value = metrics.get(field) if isinstance(metrics, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Decimal("0")
    return Decimal(str(max(0.0, min(1.0, float(value))))).quantize(Decimal("0.000001"))


def _public_processing_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    output = dict(parameters)
    raw_quality = parameters.get("page_quality")
    public_quality = _page_quality(parameters)
    output["page_quality"] = {
        "version": raw_quality.get("version") if isinstance(raw_quality, dict) else None,
        **public_quality,
    }
    output["math_structure"] = _math_structure_risks(parameters)
    return output


def _math_structure_risks(parameters: dict[str, Any]) -> dict[str, Any]:
    raw = parameters.get("math_structure")
    allowed_codes = {
        "FORMULA_REVIEW_REQUIRED",
        "MATH_LAYOUT_REVIEW_REQUIRED",
        "READING_ORDER_CONFLICT",
    }
    if not isinstance(raw, dict):
        return {"version": None, "risk_codes": [], "evidence": []}
    risk_codes = raw.get("risk_codes")
    evidence = raw.get("evidence")
    public_codes: list[str] = []
    public_evidence: list[dict[str, Any]] = []
    if isinstance(risk_codes, (list, tuple)) and isinstance(evidence, (list, tuple)):
        for code, item in zip(risk_codes, evidence, strict=False):
            if not isinstance(code, str) or code not in allowed_codes or not isinstance(item, dict):
                continue
            indexes = item.get("block_indexes")
            region = item.get("region")
            if not (
                isinstance(indexes, (list, tuple))
                and all(
                    isinstance(index, int) and not isinstance(index, bool) and index >= 0
                    for index in indexes
                )
                and isinstance(region, (list, tuple))
                and len(region) == 4
                and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in region
                )
            ):
                continue
            normalized_region = [float(value) for value in region]
            x, y, width, height = normalized_region
            if (
                min(normalized_region) < 0
                or width <= 0
                or height <= 0
                or x + width > 1
                or y + height > 1
            ):
                continue
            public_codes.append(code)
            public_evidence.append(
                {"block_indexes": [int(index) for index in indexes], "region": normalized_region}
            )
    return {
        "version": raw.get("version") if isinstance(raw.get("version"), str) else None,
        "risk_codes": public_codes,
        "evidence": public_evidence,
    }


Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


@dataclass(frozen=True)
class PreparedRecognitionPage:
    page: PaperPage
    original_storage_key: str
    rendered: PageArtifact
    processed: PageArtifact
    thumbnail: PageArtifact
    blocks: list[ProviderBlock]
    processing_parameters: dict[str, object]


@dataclass(frozen=True)
class StoredRecognitionPage:
    prepared: PreparedRecognitionPage
    rendered_storage_key: str
    processed_storage_key: str
    thumbnail_storage_key: str


@dataclass(frozen=True)
class RecognitionAttemptClaim:
    attempt: int
    transitioned_to_running: bool


class StartRecognition(BaseModel):
    paper_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


class CandidatePatch(BaseModel):
    temporary_number: str | None = Field(None, max_length=80)
    question_type: str | None = None
    content_text: str | None = None
    content_latex: str | None = None
    suggested_score: Decimal | None = Field(None, gt=0)
    status: Literal["accepted", "edited", "rejected"] | None = None


class ConfirmInput(BaseModel):
    candidate_ids: list[uuid.UUID]


class PageAdjustment(BaseModel):
    rotation: Literal[0, 90, 180, 270] = 0
    crop: dict[str, float] | None = None

    @model_validator(mode="after")
    def valid_crop(self) -> "PageAdjustment":
        if self.crop:
            x, y = self.crop.get("x", -1), self.crop.get("y", -1)
            w, h = self.crop.get("width", 0), self.crop.get("height", 0)
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
                raise ValueError("裁切区域必须位于 0..1 页面坐标内")
        return self


def context(
    db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID
) -> tuple[Assignment, PaperVersion]:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == actor_id)
    )
    if not assignment:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    version = (
        db.get(PaperVersion, assignment.active_paper_version_id)
        if assignment.active_paper_version_id
        else None
    )
    if not version:
        raise ApiProblem(409, "RECOGNITION_JOB_STATE_CONFLICT", "作业尚无试卷版本")
    return assignment, version


def owned_job(
    db: Session, actor_id: uuid.UUID, assignment_id: uuid.UUID, job_id: uuid.UUID
) -> RecognitionJob:
    job = db.scalar(
        select(RecognitionJob).where(
            RecognitionJob.id == job_id,
            RecognitionJob.owner_id == actor_id,
            RecognitionJob.assignment_id == assignment_id,
        )
    )
    if not job:
        raise ApiProblem(404, "RECOGNITION_JOB_NOT_FOUND", "识别任务不存在")
    return job


def ensure_recognition_results_ready(job: RecognitionJob) -> None:
    if job.status != RecognitionStatus.completed:
        raise ApiProblem(409, "RECOGNITION_RESULTS_NOT_READY", "识别结果尚未完整就绪")


def job_json(db: Session, job: RecognitionJob) -> dict[str, Any]:
    pages = list(
        db.scalars(
            select(PageProcessingResult).where(PageProcessingResult.recognition_job_id == job.id)
        ).all()
    )
    results_ready = job.status == RecognitionStatus.completed
    return {
        "id": str(job.id),
        "assignment_id": str(job.assignment_id),
        "paper_version_id": str(job.paper_version_id),
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "provider": job.provider,
        "provider_version": job.provider_version,
        "config_version": job.config_version,
        "attempt": job.attempt,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "page_summary": {
            "total": len(pages),
            "completed": sum(x.status == PageRecognitionStatus.completed for x in pages)
            if results_ready
            else 0,
            "failed": sum(x.status == PageRecognitionStatus.failed for x in pages)
            if results_ready
            else 0,
            "stale": sum(x.status == PageRecognitionStatus.stale for x in pages)
            if results_ready
            else 0,
        },
    }


def dispatch_recognition_job(db: Session, job: RecognitionJob) -> None:
    """Publish only the durable job identifier; DB remains the user-visible truth."""
    try:
        from workers.tasks.ocr import run_recognition

        run_recognition.apply_async(args=[str(job.id)], headers=celery_request_headers())
    except Exception as exc:
        job.status = RecognitionStatus.failed
        job.error_code = "WORKER_UNAVAILABLE"
        job.error_message = f"识别任务无法发送到 Worker：{type(exc).__name__}"
        job.failed_at = now_utc()
        db.commit()
        raise ApiProblem(503, "WORKER_UNAVAILABLE", "Redis 或 Celery Worker 不可用") from exc


def _preflight_recognition_page(
    db: Session,
    storage: ObjectStorage,
    job: RecognitionJob,
    page: PaperPage,
    converter: DefaultDocumentConverter,
    preprocessor: PillowPreprocessor,
    provider: Any,
    available: bool,
    unavailable_reason: str | None,
) -> PreparedRecognitionPage:
    source = db.get(StoredFile, page.stored_file_id)
    if not source:
        raise RecognitionError("PAGE_CONVERSION_FAILED", "页面原文件不存在")
    source_content = read_all(storage.get(source.storage_key))
    source_page = page.source_page_number or 1
    original = converter.convert(source_content, source.content_type, source_page)
    quality_metrics = measure_page_quality(original)
    previous_result = db.scalar(
        select(PageProcessingResult).where(
            PageProcessingResult.recognition_job_id == job.id,
            PageProcessingResult.paper_page_id == page.id,
        )
    )
    previous_parameters = previous_result.processing_parameters if previous_result else {}
    params: dict[str, object] = {
        "rotation": previous_parameters.get("rotation", 0),
        "denoise": True,
        "contrast": True,
    }
    if previous_parameters.get("crop") is not None:
        params["crop"] = previous_parameters["crop"]
    processed = preprocessor.process(original, params)
    thumb_image = __import__("PIL.Image", fromlist=["Image"]).open(io.BytesIO(processed.content))
    thumb_image.thumbnail((360, 480))
    thumb_buffer = io.BytesIO()
    thumb_image.save(thumb_buffer, "PNG")
    thumbnail = PageArtifact(thumb_buffer.getvalue(), thumb_image.width, thumb_image.height)
    text_layer_error: str | None = None
    try:
        text_layer_blocks = (
            extract_pdf_text_layer(source_content, source_page)
            if source.content_type == "application/pdf"
            else []
        )
    except RecognitionError as exc:
        if exc.code != "PDF_TEXT_EXTRACTION_FAILED":
            raise
        text_layer_blocks = []
        text_layer_error = exc.code
    text_character_count = sum(
        len("".join((block.text or "").split())) for block in text_layer_blocks
    )
    text_layer_sufficient = text_character_count >= 20
    provider_blocks = (
        provider.recognize(processed)
        if available and (not text_layer_sufficient or provider.name == "rapidocr")
        else []
    )
    fusion = fuse_text_sources(text_layer_blocks, provider_blocks)
    adopted_blocks = fusion.adopted_blocks
    if not adopted_blocks:
        unavailable_code = (
            "RECOGNITION_PROVIDER_UNAVAILABLE"
            if not available and not text_layer_blocks
            else "TRUSTED_TEXT_SOURCE_UNAVAILABLE"
        )
        raise RecognitionError(unavailable_code, unavailable_reason or "没有可可靠采用的文字来源")
    adopted_text_blocks = [block for block in adopted_blocks if (block.text or "").strip()]
    reliable_text = (
        bool(adopted_text_blocks)
        and all((block.source or "").startswith("pdf_text:") for block in adopted_text_blocks)
        and fusion.missing_region_count == 0
        and fusion.source_conflict_count == 0
    )
    quality_assessment = assess_page_quality(quality_metrics, reliable_text)
    quality_blocks = (
        [
            replace(block, status="manual_required")
            if block.status in {"adopted", "manual_required"}
            else block
            for block in fusion.blocks
        ]
        if quality_assessment.manual_required
        else fusion.blocks
    )
    structure_assessment = detect_math_structure_risks(quality_blocks)
    structured_blocks = apply_math_risk_status(quality_blocks, structure_assessment)
    adopted_blocks = [
        block for block in structured_blocks if block.status in {"adopted", "manual_required"}
    ]
    try:
        ensure_text_fields_integrity(
            [
                (f"recognition_blocks[{order}]", block.text)
                for order, block in enumerate(adopted_blocks)
            ]
        )
    except CharacterEncodingCorruptionError as exc:
        raise RecognitionError(exc.code, "识别文字存在损坏，请重新识别或人工核对") from exc
    sources = sorted(
        {block.source or f"{provider.name}:{provider.version}" for block in structured_blocks}
    )
    quality_stats = text_quality_statistics(
        [block.text for block in adopted_blocks],
        sources=[block.source or f"{provider.name}:{provider.version}" for block in adopted_blocks],
        confidences=[block.confidence for block in adopted_blocks],
        block_types=[block.block_type for block in adopted_blocks],
    )
    return PreparedRecognitionPage(
        page=page,
        original_storage_key=source.storage_key,
        rendered=original,
        processed=processed,
        thumbnail=thumbnail,
        blocks=structured_blocks,
        processing_parameters={
            **params,
            "text_character_count": text_character_count,
            "text_layer_sufficient": text_layer_sufficient,
            "recognition_sources": sources,
            "text_layer_error": text_layer_error,
            "text_quality": quality_stats,
            "page_quality": {
                "version": quality_metrics.algorithm_version,
                "level": quality_assessment.grade,
                "issues": list(quality_assessment.issues),
                "metrics": asdict(quality_metrics),
            },
            "math_structure": asdict(structure_assessment),
            **fusion.metrics,
        },
    )


def _mark_recognition_failed(
    db: Session,
    job_id: uuid.UUID,
    expected_attempt: int,
    error_code: str,
    error_message: str,
) -> bool:
    db.rollback()
    job = db.scalar(select(RecognitionJob).where(RecognitionJob.id == job_id).with_for_update())
    if not job or job.status != RecognitionStatus.running or job.attempt != expected_attempt:
        db.rollback()
        return False
    job.status = RecognitionStatus.failed
    job.stage = "failed"
    job.error_code = error_code
    job.error_message = error_message
    job.failed_at = now_utc()
    db.commit()
    return True


def _claim_recognition_attempt(
    db: Session,
    job_id: uuid.UUID,
    *,
    allow_running_resume: bool = False,
) -> RecognitionAttemptClaim | None:
    job = db.scalar(select(RecognitionJob).where(RecognitionJob.id == job_id).with_for_update())
    if not job:
        db.rollback()
        return None
    if job.status in {RecognitionStatus.completed, RecognitionStatus.partially_completed}:
        db.rollback()
        return None
    if job.status == RecognitionStatus.running and not allow_running_resume:
        db.rollback()
        return None
    transitioned_to_running = job.status != RecognitionStatus.running
    job.status = RecognitionStatus.running
    job.stage = "preflight"
    job.progress = 0
    job.error_code = None
    job.error_message = None
    job.failed_at = None
    if transitioned_to_running:
        job.started_at = now_utc()
    job.attempt += 1
    claim = RecognitionAttemptClaim(job.attempt, transitioned_to_running)
    db.commit()
    return claim


def _delete_artifacts(storage: ObjectStorage, keys: list[str]) -> None:
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            pass


def _has_protected_recognition_results(db: Session, job: RecognitionJob) -> bool:
    candidates = list(
        db.scalars(
            select(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job.id)
        ).all()
    )
    return (
        any(
            candidate.status in {CandidateStatus.edited, CandidateStatus.accepted}
            or candidate.confirmed_question_id is not None
            for candidate in candidates
        )
        or bool(
            db.scalar(
                select(func.count())
                .select_from(RecognitionCorrection)
                .where(RecognitionCorrection.recognition_job_id == job.id)
            )
        )
        or bool(
            db.scalar(
                select(func.count())
                .select_from(FormulaRegion)
                .where(FormulaRegion.recognition_job_id == job.id)
            )
        )
    )


def _ensure_recognition_retry_allowed(db: Session, job: RecognitionJob) -> None:
    if _has_protected_recognition_results(db, job):
        raise ApiProblem(
            409,
            "RECOGNITION_RETRY_REQUIRES_NEW_JOB",
            "识别结果已被教师编辑或确认，请创建新任务后再识别",
        )


def _structure_recognition_candidates(
    db: Session, job: RecognitionJob, pages: list[PaperPage]
) -> None:
    question_number_blocks = list(
        db.scalars(
            select(RecognitionBlock)
            .join(PaperPage, PaperPage.id == RecognitionBlock.paper_page_id)
            .where(
                RecognitionBlock.recognition_job_id == job.id,
                RecognitionBlock.block_type == "question_number",
                RecognitionBlock.status.in_(["adopted", "manual_required"]),
            )
            .order_by(PaperPage.page_number, RecognitionBlock.y, RecognitionBlock.display_order)
        ).all()
    )
    derived_regions = derive_question_regions(
        [page.id for page in pages],
        [
            QuestionAnchor(block.id, block.paper_page_id, float(block.y))
            for block in question_number_blocks
        ],
    )
    content_blocks = [
        (
            item.paper_page_id,
            ProviderBlock(
                item.block_type,
                item.text,
                item.latex,
                float(item.confidence) if item.confidence is not None else None,
                (float(item.x), float(item.y), float(item.width), float(item.height)),
                source=item.source,
            ),
        )
        for item in db.scalars(
            select(RecognitionBlock)
            .join(PaperPage, PaperPage.id == RecognitionBlock.paper_page_id)
            .where(
                RecognitionBlock.recognition_job_id == job.id,
                RecognitionBlock.status.in_(["adopted", "manual_required"]),
            )
            .order_by(PaperPage.page_number, RecognitionBlock.y, RecognitionBlock.display_order)
        ).all()
    ]
    source_evidence_blocks = [
        (
            item.paper_page_id,
            ProviderBlock(
                item.block_type,
                item.text,
                item.latex,
                float(item.confidence) if item.confidence is not None else None,
                (float(item.x), float(item.y), float(item.width), float(item.height)),
                status=item.status,
                source=item.source,
            ),
        )
        for item in db.scalars(
            select(RecognitionBlock)
            .join(PaperPage, PaperPage.id == RecognitionBlock.paper_page_id)
            .where(
                RecognitionBlock.recognition_job_id == job.id,
                RecognitionBlock.status.in_(["adopted", "manual_required", "source_conflict"]),
            )
            .order_by(PaperPage.page_number, RecognitionBlock.y, RecognitionBlock.display_order)
        ).all()
    ]
    seen_numbers: dict[str, int] = {}
    for candidate_order, block in enumerate(question_number_blocks, 1):
        detected_number = parse_hierarchical_question_number(block.text or "") or str(
            candidate_order
        )
        occurrence = seen_numbers.get(detected_number, 0) + 1
        seen_numbers[detected_number] = occurrence
        temporary_number = (
            detected_number if occurrence == 1 else f"{detected_number} [重复 {occurrence}]"
        )
        candidate_regions = derived_regions.get(block.id, [])
        region_sources = {
            item.source
            for paper_page_id, item in source_evidence_blocks
            for region in candidate_regions
            if item.source
            and paper_page_id == region.paper_page_id
            and region.x <= item.region[0] + item.region[2] / 2 <= region.x + region.width
            and region.y <= item.region[1] + item.region[3] / 2 <= region.y + region.height
        }
        has_pdf_text = any(source.startswith("pdf_text:") for source in region_sources)
        has_ocr = any(source.startswith("rapidocr:") for source in region_sources)
        candidate_source = (
            "mixed:conservative_fusion"
            if has_pdf_text and has_ocr
            else (sorted(region_sources)[0] if region_sources else block.source)
        )
        candidate = QuestionCandidate(
            recognition_job_id=job.id,
            paper_version_id=job.paper_version_id,
            temporary_number=temporary_number,
            question_type="other",
            content_text=text_for_question_region(content_blocks, candidate_regions) or block.text,
            confidence=block.confidence,
            source=candidate_source,
        )
        db.add(candidate)
        db.flush()
        for region in candidate_regions:
            db.add(
                QuestionCandidateRegion(
                    question_candidate_id=candidate.id,
                    paper_page_id=region.paper_page_id,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    confidence=block.confidence,
                )
            )


def run_recognition_job(
    db: Session,
    storage: ObjectStorage,
    job_id: uuid.UUID,
    *,
    allow_running_resume: bool = False,
    claimed_attempt: RecognitionAttemptClaim | None = None,
) -> None:
    claim = claimed_attempt or _claim_recognition_attempt(
        db, job_id, allow_running_resume=allow_running_resume
    )
    if claim is None:
        return
    attempt = claim.attempt
    job = db.get(RecognitionJob, job_id)
    if not job:
        return
    if _has_protected_recognition_results(db, job):
        _mark_recognition_failed(
            db,
            job.id,
            attempt,
            "RECOGNITION_RETRY_REQUIRES_NEW_JOB",
            "识别结果已被教师编辑或确认，请创建新任务后再识别",
        )
        return
    try:
        if claim.transitioned_to_running:
            recovery_fault_checkpoint("recognition-running")
        settings = get_settings()
        provider = provider_from_settings(settings)
        available, reason = safe_provider_readiness(provider)
        converter = DefaultDocumentConverter(settings)
        preprocessor = PillowPreprocessor()
        pages = list(
            db.scalars(
                select(PaperPage)
                .where(PaperPage.paper_version_id == job.paper_version_id)
                .order_by(PaperPage.page_number)
            ).all()
        )
    except Exception:
        _mark_recognition_failed(
            db, job.id, attempt, "RECOGNITION_FAILED", "识别初始化失败，请重试"
        )
        return
    if not pages:
        _mark_recognition_failed(
            db, job.id, attempt, "PAGE_CONVERSION_FAILED", "试卷中没有可识别页面"
        )
        return
    prepared_pages: list[PreparedRecognitionPage] = []
    try:
        for page in pages:
            prepared_pages.append(
                _preflight_recognition_page(
                    db,
                    storage,
                    job,
                    page,
                    converter,
                    preprocessor,
                    provider,
                    available,
                    reason,
                )
            )
    except RecognitionError as exc:
        _mark_recognition_failed(db, job.id, attempt, exc.code, str(exc))
        return
    except Exception:
        _mark_recognition_failed(db, job.id, attempt, "RECOGNITION_FAILED", "页面识别失败，请重试")
        return

    uploaded_keys: list[str] = []
    stored_pages: list[StoredRecognitionPage] = []
    try:
        for prepared in prepared_pages:
            rendered_key = derivative_key(job.owner_id, job.id, prepared.page.id, "rendered")
            processed_key = derivative_key(job.owner_id, job.id, prepared.page.id, "processed")
            thumbnail_key = derivative_key(job.owner_id, job.id, prepared.page.id, "thumbnail")
            for key, artifact in (
                (rendered_key, prepared.rendered),
                (processed_key, prepared.processed),
                (thumbnail_key, prepared.thumbnail),
            ):
                uploaded_keys.append(key)
                store_artifact(storage, key, artifact)
            stored_pages.append(
                StoredRecognitionPage(
                    prepared=prepared,
                    rendered_storage_key=rendered_key,
                    processed_storage_key=processed_key,
                    thumbnail_storage_key=thumbnail_key,
                )
            )
    except Exception:
        _delete_artifacts(storage, uploaded_keys)
        _mark_recognition_failed(
            db, job.id, attempt, "ARTIFACT_STORAGE_FAILED", "识别产物保存失败，请重试"
        )
        return

    old_artifact_keys: list[str] = []
    try:
        old_artifact_keys = [
            key
            for result in db.scalars(
                select(PageProcessingResult).where(
                    PageProcessingResult.recognition_job_id == job.id
                )
            ).all()
            for key in (
                result.rendered_storage_key,
                result.processed_storage_key,
                result.thumbnail_storage_key,
            )
            if key
        ]
        locked_job = db.scalar(
            select(RecognitionJob).where(RecognitionJob.id == job.id).with_for_update()
        )
        if (
            not locked_job
            or locked_job.status != RecognitionStatus.running
            or locked_job.attempt != attempt
        ):
            db.rollback()
            _delete_artifacts(storage, uploaded_keys)
            return
        db.execute(delete(QuestionCandidate).where(QuestionCandidate.recognition_job_id == job.id))
        db.execute(delete(RecognitionBlock).where(RecognitionBlock.recognition_job_id == job.id))
        db.execute(
            delete(PageProcessingResult).where(PageProcessingResult.recognition_job_id == job.id)
        )
        for stored in stored_pages:
            prepared = stored.prepared
            page = prepared.page
            page.width = prepared.processed.width
            page.height = prepared.processed.height
            page.preview_storage_key = stored.rendered_storage_key
            page.thumbnail_storage_key = stored.thumbnail_storage_key
            db.add(
                PageProcessingResult(
                    recognition_job_id=job.id,
                    paper_page_id=page.id,
                    status=PageRecognitionStatus.completed,
                    stage="completed",
                    progress=100,
                    original_storage_key=prepared.original_storage_key,
                    rendered_storage_key=stored.rendered_storage_key,
                    processed_storage_key=stored.processed_storage_key,
                    thumbnail_storage_key=stored.thumbnail_storage_key,
                    width=prepared.processed.width,
                    height=prepared.processed.height,
                    applied_rotation=cast(int, prepared.processing_parameters.get("rotation", 0)),
                    crop_region=cast(
                        dict[str, Any] | None, prepared.processing_parameters.get("crop")
                    ),
                    quality_score=_quality_score(prepared.processing_parameters, "quality_score"),
                    blur_score=_quality_score(prepared.processing_parameters, "sharpness_score"),
                    shadow_score=_quality_score(prepared.processing_parameters, "shadow_score"),
                    processing_parameters=prepared.processing_parameters,
                )
            )
            for order, recognized_block in enumerate(prepared.blocks, 1):
                x, y, width, height = recognized_block.region
                db.add(
                    RecognitionBlock(
                        recognition_job_id=job.id,
                        paper_page_id=page.id,
                        block_type=recognized_block.block_type,
                        display_order=order,
                        text=recognized_block.text,
                        latex=recognized_block.latex,
                        confidence=recognized_block.confidence,
                        language="zh-Hans",
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        source=recognized_block.source or f"{provider.name}:{provider.version}",
                        character_boxes=recognized_block.character_boxes,
                        status=recognized_block.status,
                    )
                )
        db.flush()
        locked_job.stage = "structuring"
        _structure_recognition_candidates(db, locked_job, pages)
        locked_job.stage = "completed"
        locked_job.progress = 100
        locked_job.completed_at = now_utc()
        locked_job.failed_at = None
        locked_job.status = RecognitionStatus.completed
        db.commit()
    except RecognitionError as exc:
        db.rollback()
        _delete_artifacts(storage, uploaded_keys)
        _mark_recognition_failed(db, job.id, attempt, exc.code, str(exc))
        return
    except Exception:
        db.rollback()
        _delete_artifacts(storage, uploaded_keys)
        _mark_recognition_failed(
            db,
            job.id,
            attempt,
            "RECOGNITION_PERSISTENCE_FAILED",
            "识别结果保存失败，请重试",
        )
        return
    _delete_artifacts(storage, old_artifact_keys)


@router.get("/providers")
def providers(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
    )
    if not assignment:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    settings = get_settings()
    provider = provider_from_settings(settings)
    available, reason = safe_provider_readiness(provider)
    has_pdf_source = bool(
        assignment.active_paper_version_id
        and db.scalar(
            select(func.count())
            .select_from(PaperPage)
            .join(StoredFile, StoredFile.id == PaperPage.stored_file_id)
            .where(
                PaperPage.paper_version_id == assignment.active_paper_version_id,
                StoredFile.content_type == "application/pdf",
            )
        )
    )
    formula_provider = formula_provider_from_settings(settings)
    formula_available, formula_reason = formula_provider.available()
    return {
        "provider": provider.name,
        "version": provider.version,
        "available": available,
        "can_start": available or has_pdf_source,
        "demo": provider.is_demo,
        "reason": reason,
        "pdf_text": {
            "available": True,
            "reason": "PDF 优先读取内嵌文字层；无文字层的页面才需要 OCR",
        },
        "formula": {
            "provider": formula_provider.name,
            "available": formula_available,
            "reason": formula_reason,
        },
    }


@router.post("/jobs", status_code=201)
def create_job(
    assignment_id: uuid.UUID,
    data: StartRecognition,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = Query(False),
) -> dict[str, Any]:
    assignment, version = context(db, actor.id, assignment_id)
    if version.id != data.paper_version_id or version.status not in {
        VersionStatus.draft,
        VersionStatus.processing,
        VersionStatus.ready,
    }:
        raise ApiProblem(409, "RECOGNITION_JOB_STATE_CONFLICT", "当前试卷版本不能启动识别")
    existing = db.scalar(
        select(RecognitionJob).where(
            RecognitionJob.owner_id == actor.id,
            RecognitionJob.idempotency_key == data.idempotency_key,
        )
    )
    if existing:
        return job_json(db, existing)
    provider = provider_from_settings(get_settings())
    available, reason = safe_provider_readiness(provider)
    page_file_ids = set(
        db.scalars(
            select(PaperPage.stored_file_id).where(PaperPage.paper_version_id == version.id)
        ).all()
    )
    has_pdf_source = bool(
        page_file_ids
        and db.scalar(
            select(func.count())
            .select_from(StoredFile)
            .where(StoredFile.id.in_(page_file_ids), StoredFile.content_type == "application/pdf")
        )
    )
    if not available and not has_pdf_source:
        raise ApiProblem(503, "RECOGNITION_PROVIDER_UNAVAILABLE", reason or "识别器不可用")
    job = RecognitionJob(
        owner_id=actor.id,
        assignment_id=assignment.id,
        paper_version_id=version.id,
        provider=provider.name,
        provider_version=provider.version,
        config_version=get_settings().recognition_config_version,
        idempotency_key=data.idempotency_key,
    )
    db.add(job)
    audit(db, actor.id, "recognition.job.create", "recognition_job", job.id)
    db.commit()
    if run_now:
        run_recognition_job(db, storage, job.id)
    else:
        dispatch_recognition_job(db, job)
    return job_json(db, job)


@router.get("/jobs/{job_id}")
def get_job(assignment_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return job_json(db, owned_job(db, actor.id, assignment_id, job_id))


@router.get("/jobs/{job_id}/pages")
def get_pages(
    assignment_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> list[dict[str, Any]]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    if job.status != RecognitionStatus.completed:
        return []
    rows = db.scalars(
        select(PageProcessingResult).where(PageProcessingResult.recognition_job_id == job.id)
    ).all()
    return [
        {
            "id": str(x.id),
            "paper_page_id": str(x.paper_page_id),
            "status": x.status,
            "stage": x.stage,
            "progress": x.progress,
            "width": x.width,
            "height": x.height,
            "quality_score": str(x.quality_score) if x.quality_score is not None else None,
            "blur_score": str(x.blur_score) if x.blur_score is not None else None,
            "shadow_score": str(x.shadow_score) if x.shadow_score is not None else None,
            "quality": _page_quality(x.processing_parameters),
            "math_structure": _math_structure_risks(x.processing_parameters),
            "error_code": x.error_code,
            "error_message": x.error_message,
            "rendered_url": storage.presigned_get(x.rendered_storage_key, 300)
            if x.rendered_storage_key
            else None,
            "processed_url": storage.presigned_get(x.processed_storage_key, 300)
            if x.processed_storage_key
            else None,
            "thumbnail_url": storage.presigned_get(x.thumbnail_storage_key, 300)
            if x.thumbnail_storage_key
            else None,
            "processing_parameters": _public_processing_parameters(x.processing_parameters),
        }
        for x in rows
    ]


@router.get("/jobs/{job_id}/blocks")
def get_blocks(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    ensure_recognition_results_ready(job)
    query = select(RecognitionBlock).where(RecognitionBlock.recognition_job_id == job.id)
    if page_id:
        query = query.where(RecognitionBlock.paper_page_id == page_id)
    return [
        {
            "id": str(x.id),
            "paper_page_id": str(x.paper_page_id),
            "block_type": x.block_type,
            "text": x.text,
            "latex": x.latex,
            "confidence": str(x.confidence) if x.confidence is not None else None,
            "region": {
                "x": str(x.x),
                "y": str(x.y),
                "width": str(x.width),
                "height": str(x.height),
            },
            "source": x.source,
            "character_boxes": x.character_boxes,
            "status": x.status,
        }
        for x in db.scalars(query.order_by(RecognitionBlock.display_order)).all()
    ]


def candidate_json(db: Session, x: QuestionCandidate) -> dict[str, Any]:
    regions = db.scalars(
        select(QuestionCandidateRegion).where(QuestionCandidateRegion.question_candidate_id == x.id)
    ).all()
    quality_stats = text_quality_statistics(
        [x.content_text, x.content_latex],
        sources=[x.source],
        confidences=[float(x.confidence) if x.confidence is not None else None],
        block_types=["formula"] if x.content_latex else [],
    )
    return {
        "id": str(x.id),
        "temporary_number": x.temporary_number,
        "question_type": x.question_type,
        "content_text": x.content_text,
        "content_latex": x.content_latex,
        "suggested_score": str(x.suggested_score) if x.suggested_score is not None else None,
        "confidence": str(x.confidence) if x.confidence is not None else None,
        "status": x.status,
        "source": x.source,
        "quality_stats": quality_stats,
        "confirmed_question_id": str(x.confirmed_question_id) if x.confirmed_question_id else None,
        "regions": [
            {
                "paper_page_id": str(r.paper_page_id),
                "x": str(r.x),
                "y": str(r.y),
                "width": str(r.width),
                "height": str(r.height),
            }
            for r in regions
        ],
    }


def _rectangles_overlap(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return min(first_x + first_width, second_x + second_width) > max(first_x, second_x) and min(
        first_y + first_height, second_y + second_height
    ) > max(first_y, second_y)


def _candidate_intersects_math_risk(
    candidate: QuestionCandidate,
    regions: list[QuestionCandidateRegion],
    page_results: dict[uuid.UUID, PageProcessingResult],
    risk_code: str,
) -> bool:
    for region in regions:
        if region.question_candidate_id != candidate.id:
            continue
        result = page_results.get(region.paper_page_id)
        if result is None:
            continue
        risks = _math_structure_risks(result.processing_parameters)
        for code, evidence in zip(risks["risk_codes"], risks["evidence"], strict=False):
            risk_region = evidence["region"]
            if code == risk_code and _rectangles_overlap(
                (float(region.x), float(region.y), float(region.width), float(region.height)),
                (
                    float(risk_region[0]),
                    float(risk_region[1]),
                    float(risk_region[2]),
                    float(risk_region[3]),
                ),
            ):
                return True
    return False


def _candidate_content_changed(
    db: Session, job_id: uuid.UUID, candidate: QuestionCandidate
) -> bool:
    corrections = list(
        db.scalars(
            select(RecognitionCorrection)
            .where(
                RecognitionCorrection.recognition_job_id == job_id,
                RecognitionCorrection.target_type == "candidate",
                RecognitionCorrection.target_id == candidate.id,
                RecognitionCorrection.field.in_({"content_text", "content_latex"}),
            )
            .order_by(RecognitionCorrection.created_at, RecognitionCorrection.id)
        ).all()
    )
    baseline: dict[str, str | None] = {}
    for correction in corrections:
        baseline.setdefault(correction.field, correction.original_value)
    return any(
        baseline[field]
        != (str(getattr(candidate, field)) if getattr(candidate, field) is not None else None)
        for field in baseline
    )


@router.get("/jobs/{job_id}/candidates")
def get_candidates(
    assignment_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor
) -> list[dict[str, Any]]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    ensure_recognition_results_ready(job)
    return [
        candidate_json(db, x)
        for x in db.scalars(
            select(QuestionCandidate)
            .where(QuestionCandidate.recognition_job_id == job.id)
            .order_by(QuestionCandidate.created_at)
        ).all()
    ]


@router.patch("/jobs/{job_id}/candidates/{candidate_id}")
def patch_candidate(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    data: CandidatePatch,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    ensure_recognition_results_ready(job)
    candidate = db.scalar(
        select(QuestionCandidate).where(
            QuestionCandidate.id == candidate_id, QuestionCandidate.recognition_job_id == job.id
        )
    )
    if not candidate:
        raise ApiProblem(404, "QUESTION_CANDIDATE_INVALID", "候选题目不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        old = getattr(candidate, field)
        db.add(
            RecognitionCorrection(
                recognition_job_id=job.id,
                target_type="candidate",
                target_id=candidate.id,
                field=field,
                original_value=str(old) if old is not None else None,
                corrected_value=str(value) if value is not None else None,
                actor_id=actor.id,
            )
        )
        setattr(candidate, field, value)
    if data.status is None:
        candidate.status = CandidateStatus.edited
    audit(db, actor.id, "recognition.candidate.update", "question_candidate", candidate.id)
    db.commit()
    return candidate_json(db, candidate)


@router.post("/jobs/{job_id}/confirm")
def confirm(
    assignment_id: uuid.UUID, job_id: uuid.UUID, data: ConfirmInput, db: Db, actor: Actor
) -> dict[str, Any]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    ensure_recognition_results_ready(job)
    candidates = list(
        db.scalars(
            select(QuestionCandidate).where(
                QuestionCandidate.recognition_job_id == job.id,
                QuestionCandidate.id.in_(data.candidate_ids),
            )
        ).all()
    )
    if len(candidates) != len(set(data.candidate_ids)):
        raise ApiProblem(422, "QUESTION_CANDIDATE_INVALID", "候选题目集合无效")
    if any("[重复 " in candidate.temporary_number for candidate in candidates):
        raise ApiProblem(
            422,
            "QUESTION_NUMBER_CONFLICT",
            "检测到重复题号，请教师先修改题号再确认",
        )
    candidate_regions = list(
        db.scalars(
            select(QuestionCandidateRegion).where(
                QuestionCandidateRegion.question_candidate_id.in_([item.id for item in candidates])
            )
        ).all()
    )
    candidate_page_ids = {region.paper_page_id for region in candidate_regions}
    page_results = list(
        db.scalars(
            select(PageProcessingResult).where(
                PageProcessingResult.recognition_job_id == job.id,
                PageProcessingResult.paper_page_id.in_(candidate_page_ids),
            )
        ).all()
    )
    result_page_ids = {result.paper_page_id for result in page_results}
    if (
        not candidate_page_ids
        or candidate_page_ids != result_page_ids
        or any(result.status != PageRecognitionStatus.completed for result in page_results)
    ):
        raise ApiProblem(
            409,
            "RECOGNITION_RESULTS_NOT_READY",
            "识别结果缺少完整页面区域，请重新识别后再确认",
        )
    if any(
        _page_quality(result.processing_parameters)["level"] == "rescan_required"
        for result in page_results
    ):
        raise ApiProblem(
            409,
            "RECOGNITION_PAGE_RESCAN_REQUIRED",
            "页面无法可靠读取，请重新拍摄或扫描后再确认",
        )
    page_results_by_page = {result.paper_page_id: result for result in page_results}
    if any(
        candidate.confirmed_question_id is None
        and _candidate_intersects_math_risk(
            candidate,
            candidate_regions,
            page_results_by_page,
            "READING_ORDER_CONFLICT",
        )
        and not _candidate_content_changed(db, job.id, candidate)
        for candidate in candidates
    ):
        raise ApiProblem(
            409,
            "READING_ORDER_CONFLICT",
            "页面疑似多栏，请先核对并修改题目内容后再确认",
        )
    existing_order = (
        db.scalar(
            select(func.max(Question.display_order)).where(
                Question.paper_version_id == job.paper_version_id
            )
        )
        or 0
    )
    created = []
    for offset, candidate in enumerate(candidates, 1):
        if candidate.confirmed_question_id:
            created.append(str(candidate.confirmed_question_id))
            continue
        if candidate.status == CandidateStatus.rejected:
            continue
        source_kind = question_source_kind(candidate.source)
        question = Question(
            paper_version_id=job.paper_version_id,
            question_number=candidate.temporary_number,
            display_order=existing_order + offset,
            question_type=candidate.question_type,
            content_text=candidate.content_text,
            content_latex=candidate.content_latex,
            max_score=candidate.suggested_score,
            source=source_kind,
        )
        db.add(question)
        db.flush()
        for region in db.scalars(
            select(QuestionCandidateRegion).where(
                QuestionCandidateRegion.question_candidate_id == candidate.id
            )
        ).all():
            db.add(
                QuestionRegion(
                    question_id=question.id,
                    paper_page_id=region.paper_page_id,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    source=source_kind,
                    confidence=region.confidence,
                )
            )
        candidate.confirmed_question_id = question.id
        candidate.status = CandidateStatus.accepted
        created.append(str(question.id))
    audit(
        db,
        actor.id,
        "recognition.candidates.confirm",
        "recognition_job",
        job.id,
        {"created_question_ids": created},
    )
    db.commit()
    return {"created_question_ids": created}


@router.post("/jobs/{job_id}/retry")
def retry_job(
    assignment_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> dict[str, Any]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    if job.status in {RecognitionStatus.running, RecognitionStatus.queued}:
        raise ApiProblem(409, "RECOGNITION_JOB_ALREADY_RUNNING", "识别任务正在运行")
    _ensure_recognition_retry_allowed(db, job)
    job.status = RecognitionStatus.queued
    job.stage = "queued"
    job.progress = 0
    job.error_code = None
    job.error_message = None
    job.completed_at = None
    job.failed_at = None
    db.commit()
    dispatch_recognition_job(db, job)
    return job_json(db, job)


@router.post("/jobs/{job_id}/pages/{page_id}/retry")
def retry_page(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    page_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    result = db.scalar(
        select(PageProcessingResult).where(
            PageProcessingResult.recognition_job_id == job.id,
            PageProcessingResult.paper_page_id == page_id,
        )
    )
    if not result:
        raise ApiProblem(404, "PAGE_CONVERSION_FAILED", "页面处理记录不存在")
    if job.status in {RecognitionStatus.running, RecognitionStatus.queued}:
        raise ApiProblem(409, "RECOGNITION_JOB_ALREADY_RUNNING", "识别任务正在运行")
    _ensure_recognition_retry_allowed(db, job)
    job.status = RecognitionStatus.queued
    job.stage = "queued"
    job.progress = 0
    job.error_code = None
    job.error_message = None
    job.completed_at = None
    job.failed_at = None
    db.commit()
    dispatch_recognition_job(db, job)
    return job_json(db, job)


@router.post("/jobs/{job_id}/pages/{page_id}/adjust")
def adjust_page(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    page_id: uuid.UUID,
    data: PageAdjustment,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    job = owned_job(db, actor.id, assignment_id, job_id)
    ensure_recognition_results_ready(job)
    result = db.scalar(
        select(PageProcessingResult).where(
            PageProcessingResult.recognition_job_id == job.id,
            PageProcessingResult.paper_page_id == page_id,
        )
    )
    if not result:
        raise ApiProblem(404, "PAGE_CONVERSION_FAILED", "页面处理记录不存在")
    result.applied_rotation = data.rotation
    result.crop_region = data.crop
    result.processing_parameters = {
        **result.processing_parameters,
        "rotation": data.rotation,
        "crop": data.crop,
    }
    result.status = PageRecognitionStatus.stale
    audit(db, actor.id, "recognition.page.adjust", "paper_page", page_id)
    db.commit()
    return {
        "paper_page_id": str(page_id),
        "status": result.status,
        "processing_parameters": result.processing_parameters,
    }

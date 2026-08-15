import uuid
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    Assignment,
    AssignmentStatus,
    FormulaRecognitionCandidate,
    FormulaRegion,
    PageProcessingResult,
    PaperPage,
    RecognitionJob,
    RecognitionStatus,
    now_utc,
)
from app.recognition.formula import (
    crop_formula_region,
    formula_provider_from_settings,
    normalize_latex,
    recognize_formula_safely,
    select_top_candidate,
)
from app.recognition.pipeline import PageArtifact, RecognitionError
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/assignments/{assignment_id}/recognition/jobs/{job_id}/formulas",
    tags=["formula-recognition"],
)
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
UNREADABLE_REASONS = (
    "severe_overwriting_or_occlusion",
    "crop_incomplete",
    "blurred_or_too_faint",
    "subscript_ambiguous",
    "ruled_paper_line_ambiguous",
    "other_image_quality_issue",
)


class FormulaRegionInput(BaseModel):
    paper_page_id: uuid.UUID
    region_kind: Literal["inline", "display", "unknown"] = "unknown"
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_page(self) -> "FormulaRegionInput":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("公式区域必须位于页面内")
        return self


class FormulaDispositionInput(BaseModel):
    action: Literal["accept", "reject"]
    explicit_confirmation: bool
    edited_latex: str | None = Field(default=None, max_length=20_000)


class FormulaRegionGeometryInput(BaseModel):
    region_kind: Literal["inline", "display", "unknown"] = "unknown"
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_page(self) -> "FormulaRegionGeometryInput":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("公式区域必须位于页面内")
        return self


class FormulaUnreadableInput(BaseModel):
    reason: Literal[
        "severe_overwriting_or_occlusion",
        "crop_incomplete",
        "blurred_or_too_faint",
        "subscript_ambiguous",
        "ruled_paper_line_ambiguous",
        "other_image_quality_issue",
    ]
    explicit_confirmation: bool


def _context(
    db: Session,
    actor_id: uuid.UUID,
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[Assignment, RecognitionJob]:
    assignment_query = select(Assignment).where(
        Assignment.id == assignment_id, Assignment.owner_id == actor_id
    )
    job_query = select(RecognitionJob).where(
        RecognitionJob.id == job_id,
        RecognitionJob.assignment_id == assignment_id,
        RecognitionJob.owner_id == actor_id,
    )
    if lock:
        assignment_query = assignment_query.with_for_update()
        job_query = job_query.with_for_update()
    assignment = db.scalar(assignment_query)
    job = db.scalar(job_query)
    if assignment is None or job is None:
        raise ApiProblem(404, "FORMULA_CONTEXT_NOT_FOUND", "公式识别上下文不存在")
    if assignment.status != AssignmentStatus.draft:
        raise ApiProblem(409, "ASSIGNMENT_LOCKED", "只能处理草稿作业中的公式")
    if assignment.active_paper_version_id != job.paper_version_id:
        raise ApiProblem(409, "FORMULA_SOURCE_STALE", "公式识别任务不是当前试卷版本")
    if job.status != RecognitionStatus.completed:
        raise ApiProblem(409, "RECOGNITION_RESULTS_NOT_READY", "识别结果尚未完整就绪")
    return assignment, job


def _region(
    db: Session,
    job: RecognitionJob,
    region_id: uuid.UUID,
    *,
    lock: bool = False,
) -> FormulaRegion:
    query = select(FormulaRegion).where(
        FormulaRegion.id == region_id,
        FormulaRegion.recognition_job_id == job.id,
    )
    if lock:
        query = query.with_for_update()
    item = db.scalar(query)
    if item is None:
        raise ApiProblem(404, "FORMULA_REGION_NOT_FOUND", "公式区域不存在")
    return item


def _candidate_json(item: FormulaRecognitionCandidate) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "rank": item.candidate_rank,
        "latex": item.latex,
        "confidence": str(item.confidence) if item.confidence is not None else None,
        "warning_codes": item.warning_codes,
        "status": item.status,
    }


def _overlaps_existing_region(
    data: FormulaRegionGeometryInput | FormulaRegionInput,
    row: FormulaRegion,
) -> bool:
    intersection_width = max(
        0.0,
        min(data.x + data.width, float(row.x) + float(row.width)) - max(data.x, float(row.x)),
    )
    intersection_height = max(
        0.0,
        min(data.y + data.height, float(row.y) + float(row.height)) - max(data.y, float(row.y)),
    )
    intersection = intersection_width * intersection_height
    smaller_area = min(data.width * data.height, float(row.width) * float(row.height))
    return smaller_area > 0 and intersection / smaller_area >= 0.90


def _region_json(
    db: Session,
    item: FormulaRegion,
    *,
    include_alternatives: bool,
) -> dict[str, Any]:
    candidates = list(
        db.scalars(
            select(FormulaRecognitionCandidate)
            .where(FormulaRecognitionCandidate.formula_region_id == item.id)
            .order_by(FormulaRecognitionCandidate.candidate_rank)
        ).all()
    )
    candidates.sort(
        key=lambda candidate: (
            0 if candidate.status == "accepted" else 1 if candidate.status != "rejected" else 2,
            candidate.candidate_rank,
        )
    )
    visible = candidates if include_alternatives else candidates[:1]
    return {
        "id": str(item.id),
        "paper_page_id": str(item.paper_page_id),
        "region_kind": item.region_kind,
        "region": {
            "x": str(item.x),
            "y": str(item.y),
            "width": str(item.width),
            "height": str(item.height),
        },
        "status": item.status,
        "has_alternatives": len(candidates) > 1,
        "candidates": [_candidate_json(candidate) for candidate in visible],
    }


@router.post("/regions", status_code=201)
def create_region(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    data: FormulaRegionInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    _, job = _context(db, actor.id, assignment_id, job_id, lock=True)
    page = db.scalar(
        select(PaperPage).where(
            PaperPage.id == data.paper_page_id,
            PaperPage.paper_version_id == job.paper_version_id,
        )
    )
    if page is None:
        raise ApiProblem(404, "PAGE_NOT_FOUND", "公式页面不存在")
    existing = db.scalars(
        select(FormulaRegion).where(
            FormulaRegion.recognition_job_id == job.id,
            FormulaRegion.paper_page_id == page.id,
            FormulaRegion.status != "rejected",
        )
    ).all()
    for row in existing:
        if _overlaps_existing_region(data, row):
            raise ApiProblem(409, "FORMULA_REGION_OVERLAP", "该公式区域已经存在")
    order = (
        db.scalar(
            select(func.max(FormulaRegion.display_order)).where(
                FormulaRegion.recognition_job_id == job.id,
                FormulaRegion.paper_page_id == page.id,
            )
        )
        or 0
    ) + 1
    item = FormulaRegion(
        recognition_job_id=job.id,
        paper_page_id=page.id,
        display_order=order,
        region_kind=data.region_kind,
        x=data.x,
        y=data.y,
        width=data.width,
        height=data.height,
        detection_source="teacher_explicit",
        status="manual_required",
    )
    db.add(item)
    db.flush()
    audit(db, actor.id, "formula.region.create", "formula_region", item.id)
    db.commit()
    return _region_json(db, item, include_alternatives=False)


@router.patch("/regions/{region_id}")
def update_region_geometry(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    region_id: uuid.UUID,
    data: FormulaRegionGeometryInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    _, job = _context(db, actor.id, assignment_id, job_id, lock=True)
    item = _region(db, job, region_id, lock=True)
    if item.status == "confirmed":
        raise ApiProblem(409, "FORMULA_ALREADY_CONFIRMED", "已确认公式不能重新框选")
    others = db.scalars(
        select(FormulaRegion).where(
            FormulaRegion.recognition_job_id == job.id,
            FormulaRegion.paper_page_id == item.paper_page_id,
            FormulaRegion.id != item.id,
            FormulaRegion.status != "rejected",
        )
    ).all()
    if any(_overlaps_existing_region(data, row) for row in others):
        raise ApiProblem(409, "FORMULA_REGION_OVERLAP", "该公式区域已经存在")
    previous_region = {
        "x": str(item.x),
        "y": str(item.y),
        "width": str(item.width),
        "height": str(item.height),
    }
    db.execute(
        delete(FormulaRecognitionCandidate).where(
            FormulaRecognitionCandidate.formula_region_id == item.id
        )
    )
    item.region_kind = data.region_kind
    item.x = data.x
    item.y = data.y
    item.width = data.width
    item.height = data.height
    item.status = "manual_required"
    audit(
        db,
        actor.id,
        "formula.region.redraw",
        "formula_region",
        item.id,
        {"previous_region": previous_region},
    )
    db.commit()
    return _region_json(db, item, include_alternatives=False)


@router.get("/regions")
def list_regions(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Db,
    actor: Actor,
    include_alternatives: Annotated[bool, Query()] = False,
) -> list[dict[str, Any]]:
    _, job = _context(db, actor.id, assignment_id, job_id)
    rows = db.scalars(
        select(FormulaRegion)
        .join(PaperPage, PaperPage.id == FormulaRegion.paper_page_id)
        .where(FormulaRegion.recognition_job_id == job.id)
        .order_by(PaperPage.page_number, FormulaRegion.display_order)
    ).all()
    return [_region_json(db, item, include_alternatives=include_alternatives) for item in rows]


@router.post("/regions/{region_id}/recognize")
def recognize_region(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    region_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    settings = get_settings()
    _, job = _context(db, actor.id, assignment_id, job_id)
    item = _region(db, job, region_id)
    if item.status == "confirmed":
        raise ApiProblem(409, "FORMULA_ALREADY_CONFIRMED", "已确认公式不能重新识别")
    page_result = db.scalar(
        select(PageProcessingResult).where(
            PageProcessingResult.recognition_job_id == job.id,
            PageProcessingResult.paper_page_id == item.paper_page_id,
        )
    )
    if page_result is None or not page_result.processed_storage_key:
        raise ApiProblem(409, "FORMULA_SOURCE_NOT_READY", "公式页面尚未处理完成")
    source_key = page_result.processed_storage_key
    source_width = page_result.width or 0
    source_height = page_result.height or 0
    region_snapshot = (
        item.updated_at,
        float(item.x),
        float(item.y),
        float(item.width),
        float(item.height),
        item.region_kind,
    )
    try:
        stream = storage.get(source_key)
        try:
            content = stream.read(settings.formula_recognition_max_image_bytes * 4 + 1)
        finally:
            stream.close()
        if len(content) > settings.formula_recognition_max_image_bytes * 4:
            raise RecognitionError("FORMULA_SOURCE_TOO_LARGE", "公式来源页面超过读取限制")
        page = PageArtifact(content, source_width, source_height)
        artifact = crop_formula_region(
            page,
            region_snapshot[1:5],
            max_pixels=settings.formula_recognition_max_pixels,
            max_bytes=settings.formula_recognition_max_image_bytes,
        )
        artifact = type(artifact)(artifact.page, artifact.region, region_snapshot[5])
        outcome = recognize_formula_safely(formula_provider_from_settings(settings), artifact)
        candidates = list(outcome.candidates)
    except RecognitionError as exc:
        status_code = 503 if exc.code.startswith("FORMULA_PROVIDER_") else 422
        details = {}
        if exc.code == "FORMULA_IMAGE_QUALITY_BLOCKED":
            details["allowed_unreadable_reasons"] = list(UNREADABLE_REASONS)
        raise ApiProblem(status_code, exc.code, str(exc), details) from exc
    ordered = sorted(
        candidates,
        key=lambda candidate: candidate.confidence if candidate.confidence is not None else -1,
        reverse=True,
    )
    if select_top_candidate(ordered) is None:
        raise ApiProblem(422, "FORMULA_NO_CANDIDATE", "未识别出可供教师核对的公式")
    _, locked_job = _context(db, actor.id, assignment_id, job_id, lock=True)
    item = _region(db, locked_job, region_id, lock=True)
    current_snapshot = (
        item.updated_at,
        float(item.x),
        float(item.y),
        float(item.width),
        float(item.height),
        item.region_kind,
    )
    if current_snapshot != region_snapshot:
        raise ApiProblem(409, "FORMULA_REGION_STALE", "公式区域已改变，请重新识别")
    if item.status == "confirmed":
        raise ApiProblem(409, "FORMULA_ALREADY_CONFIRMED", "公式已由教师确认")
    db.execute(
        delete(FormulaRecognitionCandidate).where(
            FormulaRecognitionCandidate.formula_region_id == item.id
        )
    )
    for rank, candidate in enumerate(ordered, 1):
        warnings = list(candidate.warning_codes)
        if candidate.confidence is None:
            warnings.append("UNCALIBRATED_CONFIDENCE")
        db.add(
            FormulaRecognitionCandidate(
                formula_region_id=item.id,
                candidate_rank=rank,
                latex=candidate.latex,
                normalized_latex=normalize_latex(candidate.latex),
                provider=candidate.provider,
                provider_version=candidate.provider_version,
                confidence=candidate.confidence,
                warning_codes=sorted(set(warnings)),
                status="manual_required",
            )
        )
    item.status = "manual_required"
    audit(
        db,
        actor.id,
        "formula.region.recognize",
        "formula_region",
        item.id,
        {
            "candidate_count": len(ordered),
            "provider": ordered[0].provider,
            "quality_warning_codes": list(outcome.quality.warning_codes),
            "preprocessed_variant_used": outcome.used_preprocessed_variant,
            "preprocessing_agreed": outcome.preprocessing_agreed,
        },
    )
    db.commit()
    return _region_json(db, item, include_alternatives=False)


@router.post("/regions/{region_id}/unreadable")
def mark_region_unreadable(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    region_id: uuid.UUID,
    data: FormulaUnreadableInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    if not data.explicit_confirmation:
        raise ApiProblem(422, "EXPLICIT_CONFIRMATION_REQUIRED", "请明确确认无法可靠识别")
    _, job = _context(db, actor.id, assignment_id, job_id, lock=True)
    item = _region(db, job, region_id, lock=True)
    if item.status == "confirmed":
        raise ApiProblem(409, "FORMULA_ALREADY_CONFIRMED", "已确认公式不能标记为无法识别")
    db.execute(
        update(FormulaRecognitionCandidate)
        .where(FormulaRecognitionCandidate.formula_region_id == item.id)
        .values(status="rejected", updated_at=now_utc())
    )
    item.status = "rejected"
    audit(
        db,
        actor.id,
        "formula.region.mark_unreadable",
        "formula_region",
        item.id,
        {"reason": data.reason, "explicit_confirmation": True},
    )
    db.commit()
    return {
        **_region_json(db, item, include_alternatives=False),
        "unreadable_reason": data.reason,
    }


@router.post("/regions/{region_id}/candidates/{candidate_id}/disposition")
def dispose_candidate(
    assignment_id: uuid.UUID,
    job_id: uuid.UUID,
    region_id: uuid.UUID,
    candidate_id: uuid.UUID,
    data: FormulaDispositionInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    if not data.explicit_confirmation:
        raise ApiProblem(422, "EXPLICIT_CONFIRMATION_REQUIRED", "请明确确认本次公式处理")
    _, job = _context(db, actor.id, assignment_id, job_id, lock=True)
    item = _region(db, job, region_id, lock=True)
    candidate = db.scalar(
        select(FormulaRecognitionCandidate)
        .where(
            FormulaRecognitionCandidate.id == candidate_id,
            FormulaRecognitionCandidate.formula_region_id == item.id,
        )
        .with_for_update()
    )
    if candidate is None:
        raise ApiProblem(404, "FORMULA_CANDIDATE_NOT_FOUND", "公式候选不存在")
    if data.action == "accept":
        latex = (data.edited_latex if data.edited_latex is not None else candidate.latex).strip()
        if not latex:
            raise ApiProblem(422, "FORMULA_LATEX_REQUIRED", "确认公式不能为空")
        candidate.latex = latex
        candidate.normalized_latex = normalize_latex(latex)
        candidate.status = "accepted"
        candidate.updated_at = now_utc()
        db.execute(
            update(FormulaRecognitionCandidate)
            .where(
                FormulaRecognitionCandidate.formula_region_id == item.id,
                FormulaRecognitionCandidate.id != candidate.id,
            )
            .values(status="rejected", updated_at=now_utc())
        )
        item.status = "confirmed"
    else:
        candidate.status = "rejected"
        remaining = db.scalar(
            select(func.count())
            .select_from(FormulaRecognitionCandidate)
            .where(
                FormulaRecognitionCandidate.formula_region_id == item.id,
                FormulaRecognitionCandidate.id != candidate.id,
                FormulaRecognitionCandidate.status != "rejected",
            )
        )
        item.status = "rejected" if not remaining else "manual_required"
    audit(
        db,
        actor.id,
        f"formula.candidate.{data.action}",
        "formula_candidate",
        candidate.id,
        {"formula_region_id": str(item.id), "edited": data.edited_latex is not None},
    )
    db.commit()
    return _region_json(db, item, include_alternatives=False)

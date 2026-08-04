import hashlib
import io
import json
import re
import unicodedata
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Annotated, Any, Literal

from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.core.config import get_settings
from app.core.request_id import celery_request_headers
from app.db.session import get_db
from app.grading.providers import grade_objective, provider_from_settings
from app.models import (
    Assignment,
    AssignmentClass,
    AssignmentParticipantSnapshot,
    AssignmentStatus,
    ClassStudent,
    FileStatus,
    GradeRelease,
    GradeReleaseItem,
    GradingBatch,
    GradingCollaborator,
    GradingCriterionResult,
    GradingEvidence,
    GradingJob,
    GradingQuestionAssignment,
    GradingResult,
    MembershipStatus,
    Question,
    QuestionKnowledgePoint,
    QuestionRecognitionEvidence,
    QuestionRubric,
    QuestionStatus,
    RecognitionRevision,
    RubricItem,
    RubricVersion,
    SchoolClass,
    ScoreRevision,
    StoredFile,
    Student,
    StudentAnswer,
    StudentAnswerRegion,
    Submission,
    SubmissionFileMatch,
    SubmissionPage,
    SubmissionProcessingJob,
    SubmissionRecognitionBlock,
    SubmissionRecognitionJob,
    SubmissionScoreSnapshot,
    TeacherReview,
    User,
    now_utc,
)
from app.recognition.answer_providers import (
    provider_from_settings as recognition_provider_from_settings,
)
from app.recognition.submission import mark_submission_stale
from app.results.services import serialize_grade_release_mutation
from app.security.files import UnsafeFile, inspect_upload, safe_filename
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["grading"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]

_CURRENT_RECOGNITION_STATUSES = {"recognized", "requires_review", "confirmed"}


def _normalized_consistency_answer(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _criterion_signature(db: Session, result_id: uuid.UUID) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(item.rubric_item_id), str(item.awarded_points))
        for item in db.scalars(
            select(GradingCriterionResult)
            .where(GradingCriterionResult.grading_result_id == result_id)
            .order_by(GradingCriterionResult.rubric_item_id)
        )
    )


def _consistency_differs(db: Session, answer: StudentAnswer, result: GradingResult) -> bool:
    submission = db.get(Submission, answer.submission_id)
    if submission is None:
        return False
    peers = db.execute(
        select(StudentAnswer, GradingResult)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .join(GradingResult, GradingResult.student_answer_id == StudentAnswer.id)
        .where(
            Submission.grading_batch_id == submission.grading_batch_id,
            Submission.owner_id == submission.owner_id,
            StudentAnswer.question_id == answer.question_id,
            GradingResult.rubric_version_id == result.rubric_version_id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
    ).all()
    normalized = _normalized_consistency_answer(_effective_answer_content(answer))
    comparable = [
        (peer, peer_result)
        for peer, peer_result in peers
        if _normalized_consistency_answer(_effective_answer_content(peer)) == normalized
    ]
    if len(comparable) < 2:
        return False
    scores = {str(peer_result.score) for _, peer_result in comparable}
    criteria = {_criterion_signature(db, peer_result.id) for _, peer_result in comparable}
    return len(scores) > 1 or len(criteria) > 1


def _needs_boundary_recheck(
    score: Decimal | None,
    confidence: Decimal | None,
    maximum: Decimal,
) -> bool:
    if score is None:
        return False
    threshold = Decimal(str(get_settings().grading_auto_accept_confidence))
    return score in {Decimal("0"), maximum} or (
        confidence is not None and threshold - Decimal("0.08") <= confidence <= threshold
    )


def _apply_consistency_quality_flags(items: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in items:
        for answer_item in item["answers"]:
            result_item = answer_item.get("result")
            if result_item is None:
                continue
            key = (
                str(answer_item["question"]["id"]),
                _normalized_consistency_answer(answer_item.get("effective_text")),
                str(result_item["rubric_version_id"]),
            )
            groups.setdefault(key, []).append(answer_item)
    for group in groups.values():
        if len(group) < 2:
            continue
        scores = {item["result"]["score"] for item in group}
        criterion_signatures = {
            tuple(
                (criterion["rubric_item_id"], criterion["awarded_points"])
                for criterion in answer_item["criteria"]
            )
            for answer_item in group
        }
        if len(scores) > 1 or len(criterion_signatures) > 1:
            for answer_item in group:
                answer_item["result"]["quality_flags"].append("CONSISTENCY_REVIEW_REQUIRED")


class BatchInput(BaseModel):
    class_id: uuid.UUID
    name: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=2000)


class JointPoolInput(BaseModel):
    name: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=2000)


class MatchInput(BaseModel):
    student_id: uuid.UUID


class AnswerInput(BaseModel):
    question_id: uuid.UUID
    recognized_text: str | None = None
    recognized_latex: str | None = None
    recognition_confidence: Decimal | None = Field(None, ge=0, le=1)
    recognition_provider: str = "manual"
    recognition_provider_version: str = "none"
    is_blank: bool = False


class AnswerPatch(BaseModel):
    corrected_text: str | None = None
    corrected_latex: str | None = None


class AnswerRegionInput(BaseModel):
    submission_page_id: uuid.UUID
    x: Decimal = Field(ge=0, le=1)
    y: Decimal = Field(ge=0, le=1)
    width: Decimal = Field(gt=0, le=1)
    height: Decimal = Field(gt=0, le=1)
    source: Literal["manual", "template", "ocr"] = "manual"
    confidence: Decimal | None = Field(None, ge=0, le=1)
    confirmed: bool = True


class ReviewInput(BaseModel):
    decision: Literal["accepted", "modified", "rejected", "manual_scored", "needs_more_information"]
    final_score: Decimal | None = Field(None, ge=0)
    final_feedback: str | None = None
    final_error_type: str | None = None
    review_notes: str | None = None
    reason: str | None = None
    criterion_scores: dict[uuid.UUID, Decimal] = Field(default_factory=dict)
    expected_review_version: int | None = Field(default=None, ge=1)


class CollaboratorInput(BaseModel):
    email: EmailStr


class QuestionAssignmentInput(BaseModel):
    assignee_id: uuid.UUID | None = None


class CodexSuggestionInput(BaseModel):
    score: Decimal = Field(ge=0)
    reasoning: str = Field(min_length=1, max_length=4000)
    criterion_scores: dict[uuid.UUID, Decimal] = Field(default_factory=dict)


class RecognitionStartInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)
    provider_kind: Literal[
        "printed_text", "handwriting_text", "math_formula", "multimodal_document"
    ] = "printed_text"


class PageOrderInput(BaseModel):
    page_ids: list[uuid.UUID] = Field(min_length=1)


class MovePagesInput(BaseModel):
    target_submission_id: uuid.UUID
    page_ids: list[uuid.UUID] = Field(min_length=1)


class SplitSubmissionInput(BaseModel):
    page_ids: list[uuid.UUID] = Field(min_length=1)


class MergeSubmissionInput(BaseModel):
    source_submission_id: uuid.UUID


class BulkAcceptInput(BaseModel):
    answer_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RegradeInput(BaseModel):
    question_id: uuid.UUID | None = None
    submission_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    only_unreviewed: bool = False
    only_stale: bool = False


class ConfirmResultsInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)
    expected_review_hash: str = Field(min_length=64, max_length=64)


class ReopenSubmissionInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalized_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


def owned_assignment(db: Session, owner: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    item = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id, Assignment.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    return item


def owned_batch(db: Session, owner: uuid.UUID, batch_id: uuid.UUID) -> GradingBatch:
    item = db.scalar(
        select(GradingBatch).where(GradingBatch.id == batch_id, GradingBatch.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    return item


def owned_submission(db: Session, owner: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    item = db.scalar(
        select(Submission).where(Submission.id == submission_id, Submission.owner_id == owner)
    )
    if item is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    return item


def _active_collaborator(
    db: Session, assignment_id: uuid.UUID, user_id: uuid.UUID
) -> GradingCollaborator | None:
    collaborator = db.scalar(
        select(GradingCollaborator).where(
            GradingCollaborator.assignment_id == assignment_id,
            GradingCollaborator.user_id == user_id,
            GradingCollaborator.status == "active",
        )
    )
    if collaborator is None or not _is_active_teacher(db, user_id):
        return None
    return collaborator


def _is_active_teacher(db: Session, user_id: uuid.UUID) -> bool:
    user = db.get(User, user_id)
    return bool(
        user is not None
        and user.status == "active"
        and "teacher" in {role.name for role in user.roles}
    )


def _reviewable_batch(
    db: Session, actor_id: uuid.UUID, batch_id: uuid.UUID
) -> tuple[GradingBatch, bool, set[uuid.UUID]]:
    batch = db.get(GradingBatch, batch_id)
    if batch is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    if batch.owner_id == actor_id:
        return batch, True, set()
    if _active_collaborator(db, batch.assignment_id, actor_id) is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    assigned = set(
        db.scalars(
            select(GradingQuestionAssignment.question_id).where(
                GradingQuestionAssignment.grading_batch_id == batch.id,
                GradingQuestionAssignment.assignee_id == actor_id,
            )
        ).all()
    )
    if not assigned:
        raise ApiProblem(403, "GRADING_SCOPE_EMPTY", "尚未分配可批改的题目")
    return batch, False, assigned


def _require_question_scope(
    db: Session,
    actor_id: uuid.UUID,
    batch: GradingBatch,
    question_id: uuid.UUID,
) -> bool:
    if batch.owner_id == actor_id:
        return True
    if _active_collaborator(db, batch.assignment_id, actor_id) is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    assigned = db.scalar(
        select(GradingQuestionAssignment.id).where(
            GradingQuestionAssignment.grading_batch_id == batch.id,
            GradingQuestionAssignment.question_id == question_id,
            GradingQuestionAssignment.assignee_id == actor_id,
        )
    )
    if assigned is None:
        raise ApiProblem(403, "GRADING_SCOPE_FORBIDDEN", "该题未分配给当前教师")
    return False


def _effective_answer_content(answer: StudentAnswer) -> str:
    corrected = (
        answer.corrected_text if answer.corrected_text is not None else answer.corrected_latex
    )
    if corrected is not None:
        return corrected.strip()
    return (answer.recognized_text or answer.recognized_latex or "").strip()


def _answer_evidence_context(
    db: Session, answer: StudentAnswer
) -> tuple[str | None, list[StudentAnswerRegion], QuestionRecognitionEvidence | None]:
    confirmed_regions = list(
        db.scalars(
            select(StudentAnswerRegion).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status == "confirmed",
            )
        ).all()
    )
    if not confirmed_regions:
        return "CONFIRMED_REGION_MISSING", [], None
    evidence = db.scalar(
        select(QuestionRecognitionEvidence)
        .where(QuestionRecognitionEvidence.student_answer_id == answer.id)
        .order_by(
            QuestionRecognitionEvidence.recognition_version.desc(),
            QuestionRecognitionEvidence.created_at.desc(),
        )
    )
    if evidence is None:
        return "CURRENT_RECOGNITION_EVIDENCE_MISSING", confirmed_regions, None
    if evidence.stale_at is not None or evidence.status == "stale":
        return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
    if evidence.status not in _CURRENT_RECOGNITION_STATUSES or not evidence.block_sources:
        return "CURRENT_RECOGNITION_EVIDENCE_MISSING", confirmed_regions, evidence
    regions_by_id = {str(region.id): region for region in confirmed_regions}
    current_regions: list[StudentAnswerRegion] = []
    seen_region_ids: set[uuid.UUID] = set()
    for source in evidence.block_sources:
        if not isinstance(source, dict):
            return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
        block_id, region_id = source.get("block_id"), source.get("region_id")
        lineage_version_value = source.get("block_recognition_version")
        region_version_value = source.get("region_version")
        if (
            not isinstance(lineage_version_value, int)
            or isinstance(lineage_version_value, bool)
            or not isinstance(region_version_value, int)
            or isinstance(region_version_value, bool)
        ):
            return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
        try:
            block_uuid = uuid.UUID(str(block_id))
            page_uuid = uuid.UUID(str(source.get("page_id")))
            lineage_job_uuid = uuid.UUID(str(source.get("block_recognition_job_id")))
        except (TypeError, ValueError):
            return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
        region = regions_by_id.get(str(region_id))
        block = db.get(SubmissionRecognitionBlock, block_uuid)
        region_bbox = (
            [str(region.x), str(region.y), str(region.width), str(region.height)] if region else []
        )
        preserved_human_revision = source.get("preserved_human_revision") is True
        has_current_human_revision = (
            block is not None
            and db.scalar(
                select(RecognitionRevision.id).where(
                    RecognitionRevision.recognition_block_id == block.id,
                    RecognitionRevision.source == "human",
                    RecognitionRevision.stale_at.is_(None),
                )
            )
            is not None
        )
        valid_preserved_human_revision = preserved_human_revision and has_current_human_revision
        if (
            region is None
            or block is None
            or block.student_answer_region_id != region.id
            or block.submission_page_id != region.submission_page_id
            or page_uuid != region.submission_page_id
            or lineage_job_uuid != block.submission_recognition_job_id
            or lineage_version_value != block.recognition_version
            or region_version_value != region.region_version
            or source.get("region_bbox") != region_bbox
            or block.stale_at is not None
            or (
                block.status not in _CURRENT_RECOGNITION_STATUSES
                and not (valid_preserved_human_revision and block.status == "human_edited")
            )
        ):
            return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
        lineage_matches_evidence = (
            block.submission_recognition_job_id == evidence.recognition_job_id
            and block.recognition_version == evidence.recognition_version
        )
        if not lineage_matches_evidence:
            if not valid_preserved_human_revision:
                return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
        if region.id not in seen_region_ids:
            current_regions.append(region)
            seen_region_ids.add(region.id)
    if seen_region_ids != {region.id for region in confirmed_regions}:
        return "RECOGNITION_EVIDENCE_STALE", confirmed_regions, evidence
    if not answer.is_blank and not _effective_answer_content(answer):
        return "EFFECTIVE_ANSWER_MISSING", current_regions, evidence
    return None, current_regions, evidence


def _require_answer_evidence(
    db: Session, answer: StudentAnswer
) -> tuple[list[StudentAnswerRegion], QuestionRecognitionEvidence]:
    reason, regions, evidence = _answer_evidence_context(db, answer)
    if reason is not None or evidence is None:
        raise ApiProblem(
            409,
            "ANSWER_EVIDENCE_REQUIRED",
            "当前答案证据链不完整，不能评分",
            {"reason": reason or "CURRENT_RECOGNITION_EVIDENCE_MISSING"},
        )
    return regions, evidence


def _has_current_grading_evidence(
    db: Session,
    result: GradingResult,
    answer: StudentAnswer,
    regions: list[StudentAnswerRegion],
) -> bool:
    current_coordinates = {
        (
            region.submission_page_id,
            Decimal(region.x),
            Decimal(region.y),
            Decimal(region.width),
            Decimal(region.height),
        )
        for region in regions
    }
    evidence_rows = db.scalars(
        select(GradingEvidence).where(
            GradingEvidence.grading_result_id == result.id,
            GradingEvidence.student_answer_id == answer.id,
            GradingEvidence.evidence_type == "answer_region",
        )
    ).all()
    evidence_coordinates = {
        (
            row.submission_page_id,
            Decimal(row.x) if row.x is not None else None,
            Decimal(row.y) if row.y is not None else None,
            Decimal(row.width) if row.width is not None else None,
            Decimal(row.height) if row.height is not None else None,
        )
        for row in evidence_rows
    }
    return bool(current_coordinates) and current_coordinates.issubset(evidence_coordinates)


def _batch_counts(db: Session, batch: GradingBatch) -> dict[str, int]:
    submissions = db.scalars(
        select(Submission).where(
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == batch.owner_id,
            Submission.status != "voided",
        )
    ).all()
    assignment = db.get(Assignment, batch.assignment_id)
    question_ids = (
        set(
            db.scalars(
                select(Question.id).where(
                    Question.paper_version_id == assignment.active_paper_version_id,
                    Question.status == QuestionStatus.active,
                )
            ).all()
        )
        if assignment and assignment.active_paper_version_id
        else set()
    )
    active_rubric_version_id = assignment.active_rubric_version_id if assignment else None
    recognized = graded = reviewed = failed = 0
    for submission in submissions:
        pages = db.scalars(
            select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)
        ).all()
        answers = db.scalars(
            select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
        ).all()
        by_question = {answer.question_id: answer for answer in answers}
        if pages and all(page.status in {"recognized", "blank"} for page in pages):
            recognized += 1
        if any(page.status == "failed" for page in pages):
            failed += 1
        question_answers = [by_question.get(question_id) for question_id in question_ids]
        if question_ids and all(question_answers):
            is_graded = True
            is_reviewed = True
            for answer in question_answers:
                assert answer is not None
                review = db.scalar(
                    select(TeacherReview).where(
                        TeacherReview.student_answer_id == answer.id,
                        TeacherReview.final_score.is_not(None),
                    )
                )
                current_result = db.scalar(
                    select(GradingResult)
                    .where(
                        GradingResult.student_answer_id == answer.id,
                        GradingResult.status.in_(["suggested", "accepted", "modified"]),
                        GradingResult.score.is_not(None),
                        GradingResult.rubric_version_id == active_rubric_version_id,
                    )
                    .order_by(GradingResult.created_at.desc())
                )
                is_graded = is_graded and (review is not None or current_result is not None)
                is_reviewed = is_reviewed and (
                    review is not None and review.confirmed_at is not None
                )
            graded += int(is_graded)
            reviewed += int(is_reviewed)
    return {
        "submission_count": len(submissions),
        "recognized_count": recognized,
        "graded_count": graded,
        "reviewed_count": reviewed,
        "failed_count": failed,
    }


def _submission_workflow(db: Session, submission: Submission) -> dict[str, Any]:
    """Return the current teacher-facing stage and next action."""
    if submission.status == "voided":
        return {
            "stage": "voided",
            "stage_label": "已撤销",
            "reason_code": "SUBMISSION_VOIDED",
            "reason": "这份上传已撤销，不会进入批改。",
            "action": "无需处理",
        }
    if submission.status == "finalized" or submission.finalized_at is not None:
        complete_snapshot_id = db.scalar(
            select(SubmissionScoreSnapshot.id).where(
                SubmissionScoreSnapshot.submission_id == submission.id,
                SubmissionScoreSnapshot.status == "complete",
            )
        )
        if complete_snapshot_id is not None:
            return {
                "stage": "completed",
                "stage_label": "批改完成",
                "reason_code": None,
                "reason": "可以检查结果。",
                "action": "检查结果",
            }
        return {
            "stage": "failed",
            "stage_label": "结果状态异常",
            "reason_code": "FINALIZED_SNAPSHOT_MISSING",
            "reason": "作业已锁定，但缺少完整成绩快照。",
            "action": "联系管理员恢复结果",
        }
    if submission.student_id is None:
        return {
            "stage": "matching",
            "stage_label": "等待学生匹配",
            "reason_code": "STUDENT_MATCH_REQUIRED",
            "reason": "文件尚未与班级学生确认匹配。",
            "action": "确认学生匹配",
        }
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission.id)
        .order_by(SubmissionPage.page_number)
    ).all()
    if not pages:
        return {
            "stage": "pages",
            "stage_label": "等待页面处理",
            "reason_code": "PAGES_MISSING",
            "reason": "没有可用于识别的作业页面。",
            "action": "检查原文件并重新上传",
        }
    failed_pages = [page for page in pages if page.status == "failed"]
    if failed_pages:
        return {
            "stage": "failed",
            "stage_label": "页面处理失败",
            "reason_code": "PAGE_PROCESSING_FAILED",
            "reason": f"{len(failed_pages)} 页处理失败。",
            "action": "重新识别失败页面",
        }
    pending_pages = [page for page in pages if page.status not in {"recognized", "blank"}]
    if pending_pages:
        return {
            "stage": "recognition",
            "stage_label": "等待答案识别",
            "reason_code": "RECOGNITION_PENDING",
            "reason": f"{len(pending_pages)} 页尚未完成识别。",
            "action": "启动或重试答案识别",
        }
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    assignment = db.get(Assignment, submission.assignment_id)
    active_rubric_version_id = assignment.active_rubric_version_id if assignment else None
    if not answers:
        return {
            "stage": "segmentation",
            "stage_label": "等待题目切分",
            "reason_code": "ANSWERS_MISSING",
            "reason": "页面已处理，但尚未形成题目答案区域。",
            "action": "确认题目区域后继续",
        }
    for answer in answers:
        evidence_reason, _, _ = _answer_evidence_context(db, answer)
        if evidence_reason is not None:
            return {
                "stage": "answer_review",
                "stage_label": "等待答案校对",
                "reason_code": "ANSWER_EVIDENCE_REQUIRED",
                "reason": f"至少有一道题的当前证据链不完整（{evidence_reason}）。",
                "action": "校对答案区域和识别结果",
            }
        result = db.scalar(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status.in_(["suggested", "accepted", "modified"]),
                GradingResult.rubric_version_id == active_rubric_version_id,
            )
            .order_by(GradingResult.created_at.desc())
        )
        if result is None:
            return {
                "stage": "grading",
                "stage_label": "等待评分建议",
                "reason_code": "GRADING_RESULT_REQUIRED",
                "reason": "至少有一道题还没有有效评分建议。",
                "action": "运行评分建议",
            }
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        if review is None or review.final_score is None or answer.requires_review:
            return {
                "stage": "teacher_review",
                "stage_label": "等待教师复核",
                "reason_code": "TEACHER_REVIEW_REQUIRED",
                "reason": "评分建议尚未由教师确认，或该题被标记为必须复核。",
                "action": "进入教师复核",
            }
    return {
        "stage": "completed",
        "stage_label": "已完成",
        "reason_code": None,
        "reason": "所有题目均已有教师确认分数。",
        "action": "可进行成绩就绪检查",
    }


def _batch_students(db: Session, batch: GradingBatch) -> list[tuple[Student, str, str]]:
    assignment = db.get(Assignment, batch.assignment_id)
    if assignment is not None and assignment.delivery_mode == "joint_exam":
        return [
            (student, student_number, student_name)
            for student, student_number, student_name in db.execute(
                select(
                    Student,
                    AssignmentParticipantSnapshot.student_number,
                    AssignmentParticipantSnapshot.student_name,
                )
                .join(
                    AssignmentParticipantSnapshot,
                    AssignmentParticipantSnapshot.student_id == Student.id,
                )
                .where(
                    AssignmentParticipantSnapshot.assignment_id == batch.assignment_id,
                    AssignmentParticipantSnapshot.class_id == batch.class_id,
                )
                .order_by(
                    AssignmentParticipantSnapshot.student_number,
                    AssignmentParticipantSnapshot.student_id,
                )
            )
        ]
    return [
        (student, student.student_number, student.name)
        for student in db.scalars(
            select(Student)
            .join(ClassStudent, ClassStudent.student_id == Student.id)
            .where(
                ClassStudent.class_id == batch.class_id,
                ClassStudent.status == MembershipStatus.active,
                Student.owner_id == batch.owner_id,
            )
            .order_by(Student.student_number, Student.id)
        )
    ]


def batch_json(db: Session, x: GradingBatch) -> dict[str, Any]:
    matches = db.scalars(
        select(SubmissionFileMatch).where(SubmissionFileMatch.grading_batch_id == x.id)
    ).all()
    members = _batch_students(db, x)
    counts = _batch_counts(db, x)
    submissions = db.scalars(
        select(Submission).where(
            Submission.grading_batch_id == x.id,
            Submission.owner_id == x.owner_id,
            Submission.status != "voided",
        )
    ).all()
    workflow_items = [_submission_workflow(db, submission) for submission in submissions]
    stage_counts: dict[str, int] = {}
    for workflow in workflow_items:
        stage = str(workflow["stage"])
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    blocked = []
    for stage, count in stage_counts.items():
        if stage == "completed":
            continue
        example = next(item for item in workflow_items if item["stage"] == stage)
        blocked.append(
            {
                "stage": stage,
                "stage_label": example["stage_label"],
                "reason_code": example["reason_code"],
                "reason": example["reason"],
                "action": example["action"],
                "count": count,
            }
        )
    return {
        "id": str(x.id),
        "assignment_id": str(x.assignment_id),
        "class_id": str(x.class_id),
        "name": x.name,
        "description": x.description,
        "status": x.status,
        **counts,
        "workflow": {
            "stage_counts": stage_counts,
            "blocked": blocked,
            "completed_count": stage_counts.get("completed", 0),
            "blocked_count": len(submissions) - stage_counts.get("completed", 0),
        },
        "matching": {
            "total": len(matches),
            "confirmed": sum(m.status == "confirmed" for m in matches),
            "ambiguous": sum(m.match_method == "ambiguous" for m in matches),
            "unmatched": sum(m.match_method == "unmatched" for m in matches),
            "items": [
                {
                    "id": str(match.id),
                    "filename": (
                        stored.original_name
                        if (stored := db.get(StoredFile, match.stored_file_id))
                        else "unknown"
                    ),
                    "status": match.status,
                    "method": match.match_method,
                    "reason": match.reason,
                    "suggested_student_id": (
                        str(match.suggested_student_id) if match.suggested_student_id else None
                    ),
                    "confirmed_student_id": (
                        str(match.confirmed_student_id) if match.confirmed_student_id else None
                    ),
                }
                for match in matches
            ],
            "student_options": [
                {
                    "id": str(student.id),
                    "student_number": student_number,
                    "name": student_name,
                }
                for student, student_number, student_name in members
            ],
        },
        "actions": ["upload", "review_matches", "grade", "archive"]
        if x.status != "archived"
        else [],
    }


@router.post("/assignments/{assignment_id}/grading-batches", status_code=201)
def create_batch(
    assignment_id: uuid.UUID, data: BatchInput, db: Db, actor: Actor
) -> dict[str, Any]:
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
        .with_for_update()
    )
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    school_class = db.scalar(
        select(SchoolClass).where(SchoolClass.id == data.class_id, SchoolClass.owner_id == actor.id)
    )
    linked = db.scalar(
        select(AssignmentClass.id).where(
            AssignmentClass.assignment_id == assignment.id,
            AssignmentClass.class_id == data.class_id,
        )
    )
    if school_class is None or linked is None:
        raise ApiProblem(409, "ASSIGNMENT_CLASS_MISMATCH", "班级未关联到该作业")
    _ensure_assignment_gradable(db, assignment)
    if assignment.delivery_mode == "joint_exam":
        existing = db.scalar(
            select(GradingBatch.id).where(
                GradingBatch.assignment_id == assignment.id,
                GradingBatch.class_id == data.class_id,
                GradingBatch.owner_id == actor.id,
                GradingBatch.status != "archived",
            )
        )
        if existing is not None:
            raise ApiProblem(409, "JOINT_EXAM_BATCH_EXISTS", "该联考班级已有活动批改批次")
    item = GradingBatch(
        owner_id=actor.id,
        assignment_id=assignment.id,
        class_id=data.class_id,
        name=data.name,
        description=data.description,
        status="collecting",
    )
    db.add(item)
    db.flush()
    audit(db, actor.id, "grading_batch.create", "grading_batch", item.id)
    db.commit()
    return batch_json(db, item)


def _ensure_assignment_gradable(db: Session, assignment: Assignment) -> None:
    if assignment.status not in {
        AssignmentStatus.published,
        AssignmentStatus.grading,
        AssignmentStatus.completed,
    }:
        raise ApiProblem(409, "ASSIGNMENT_NOT_GRADABLE", "作业尚未发布")
    if not assignment.active_paper_version_id or not assignment.active_rubric_version_id:
        raise ApiProblem(409, "GRADING_VERSIONS_REQUIRED", "缺少有效试卷或评分标准版本")
    questions = db.scalars(
        select(Question).where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    ).all()
    if not questions or any(q.max_score is None for q in questions):
        raise ApiProblem(409, "QUESTION_SCORE_REQUIRED", "题目分值不完整")
    rubric_ids = set(
        db.scalars(
            select(QuestionRubric.question_id).where(
                QuestionRubric.rubric_version_id == assignment.active_rubric_version_id
            )
        ).all()
    )
    if any(q.id not in rubric_ids for q in questions):
        raise ApiProblem(409, "RUBRIC_INCOMPLETE", "评分标准不完整")


def _joint_pool_json(
    db: Session, assignment: Assignment, batches: list[GradingBatch]
) -> dict[str, Any]:
    class_names = {
        class_id: class_name
        for class_id, class_name in db.execute(
            select(SchoolClass.id, SchoolClass.name)
            .join(AssignmentClass, AssignmentClass.class_id == SchoolClass.id)
            .where(AssignmentClass.assignment_id == assignment.id)
        )
    }
    items = [batch_json(db, batch) for batch in batches]
    count_keys = ("submission_count", "recognized_count", "graded_count", "reviewed_count")
    batch_ids = [batch.id for batch in batches]
    questions = (
        list(
            db.scalars(
                select(Question)
                .where(
                    Question.paper_version_id == assignment.active_paper_version_id,
                    Question.status == QuestionStatus.active,
                )
                .order_by(Question.display_order, Question.id)
            )
        )
        if assignment.active_paper_version_id is not None
        else []
    )
    question_items: list[dict[str, Any]] = []
    for question in questions:
        assignment_rows = list(
            db.scalars(
                select(GradingQuestionAssignment).where(
                    GradingQuestionAssignment.grading_batch_id.in_(batch_ids),
                    GradingQuestionAssignment.question_id == question.id,
                )
            )
        )
        assignee_ids = {row.assignee_id for row in assignment_rows}
        total = (
            db.scalar(
                select(func.count())
                .select_from(StudentAnswer)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id.in_(batch_ids),
                    Submission.status != "voided",
                    StudentAnswer.question_id == question.id,
                )
            )
            or 0
        )
        reviewed = (
            db.scalar(
                select(func.count())
                .select_from(TeacherReview)
                .join(StudentAnswer, StudentAnswer.id == TeacherReview.student_answer_id)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id.in_(batch_ids),
                    StudentAnswer.question_id == question.id,
                    TeacherReview.final_score.is_not(None),
                    StudentAnswer.requires_review.is_(False),
                )
            )
            or 0
        )
        assignment_complete = len(assignment_rows) == len(batch_ids) and len(assignee_ids) == 1
        question_items.append(
            {
                "id": str(question.id),
                "number": question.question_number,
                "total": total,
                "reviewed": reviewed,
                "assignee_id": (
                    str(next(iter(assignee_ids))) if assignment_complete and assignee_ids else None
                ),
                "assignment_mixed": bool(assignment_rows) and not assignment_complete,
            }
        )
    return {
        "assignment_id": str(assignment.id),
        "delivery_mode": assignment.delivery_mode,
        "class_count": len(class_names),
        "batch_count": len(items),
        **{key: sum(int(item[key]) for item in items) for key in count_keys},
        "items": [
            item | {"class_name": class_names.get(uuid.UUID(item["class_id"]), "未知班级")}
            for item in items
        ],
        "questions": question_items,
    }


@router.get("/joint-grading-work")
def list_joint_grading_work(db: Db, actor: Actor) -> list[dict[str, Any]]:
    if not _is_active_teacher(db, actor.id):
        raise ApiProblem(403, "TEACHER_ROLE_REQUIRED", "只有活动教师账号可以查看联考批改任务")
    rows = db.execute(
        select(Assignment, Question, GradingBatch)
        .join(GradingBatch, GradingBatch.assignment_id == Assignment.id)
        .join(
            GradingQuestionAssignment,
            GradingQuestionAssignment.grading_batch_id == GradingBatch.id,
        )
        .join(Question, Question.id == GradingQuestionAssignment.question_id)
        .join(
            GradingCollaborator,
            GradingCollaborator.assignment_id == Assignment.id,
        )
        .where(
            Assignment.delivery_mode == "joint_exam",
            Assignment.status != AssignmentStatus.archived,
            GradingBatch.status != "archived",
            GradingQuestionAssignment.assignee_id == actor.id,
            GradingCollaborator.user_id == actor.id,
            GradingCollaborator.status == "active",
        )
        .order_by(Assignment.updated_at.desc(), Question.question_number, GradingBatch.created_at)
    ).all()
    grouped: dict[tuple[uuid.UUID, uuid.UUID], tuple[Assignment, Question, list[GradingBatch]]] = {}
    for assignment, question, batch in rows:
        key = (assignment.id, question.id)
        if key not in grouped:
            grouped[key] = (assignment, question, [])
        grouped[key][2].append(batch)
    result: list[dict[str, Any]] = []
    for assignment, question, batches in grouped.values():
        batch_ids = [batch.id for batch in batches]
        total = (
            db.scalar(
                select(func.count())
                .select_from(StudentAnswer)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id.in_(batch_ids),
                    Submission.status != "voided",
                    StudentAnswer.question_id == question.id,
                )
            )
            or 0
        )
        reviewed = (
            db.scalar(
                select(func.count())
                .select_from(TeacherReview)
                .join(StudentAnswer, StudentAnswer.id == TeacherReview.student_answer_id)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id.in_(batch_ids),
                    StudentAnswer.question_id == question.id,
                    TeacherReview.final_score.is_not(None),
                    StudentAnswer.requires_review.is_(False),
                )
            )
            or 0
        )
        result.append(
            {
                "assignment_id": str(assignment.id),
                "assignment_title": assignment.title,
                "question_id": str(question.id),
                "question_number": question.question_number,
                "first_batch_id": str(batches[0].id),
                "class_count": len(batches),
                "total": total,
                "reviewed": reviewed,
            }
        )
    return result


@router.get("/assignments/{assignment_id}/joint-grading-pool")
def get_joint_grading_pool(assignment_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    assignment = owned_assignment(db, actor.id, assignment_id)
    if assignment.delivery_mode != "joint_exam":
        raise ApiProblem(409, "NOT_JOINT_EXAM", "该作业不是联考统批")
    batches = list(
        db.scalars(
            select(GradingBatch)
            .where(
                GradingBatch.assignment_id == assignment.id,
                GradingBatch.owner_id == actor.id,
                GradingBatch.status != "archived",
            )
            .order_by(GradingBatch.created_at, GradingBatch.id)
        )
    )
    return _joint_pool_json(db, assignment, batches)


@router.post("/assignments/{assignment_id}/joint-grading-pool", status_code=201)
def create_joint_grading_pool(
    assignment_id: uuid.UUID, data: JointPoolInput, db: Db, actor: Actor
) -> dict[str, Any]:
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
        .with_for_update()
    )
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    if assignment.delivery_mode != "joint_exam":
        raise ApiProblem(409, "NOT_JOINT_EXAM", "该作业不是联考统批")
    _ensure_assignment_gradable(db, assignment)
    class_rows = list(
        db.execute(
            select(SchoolClass.id, SchoolClass.name)
            .join(AssignmentClass, AssignmentClass.class_id == SchoolClass.id)
            .where(AssignmentClass.assignment_id == assignment.id)
            .order_by(SchoolClass.name, SchoolClass.id)
        )
    )
    existing = {
        batch.class_id: batch
        for batch in db.scalars(
            select(GradingBatch)
            .where(
                GradingBatch.assignment_id == assignment.id,
                GradingBatch.owner_id == actor.id,
                GradingBatch.status != "archived",
            )
            .order_by(GradingBatch.created_at.desc())
        )
    }
    created_ids: list[str] = []
    for class_id, class_name in class_rows:
        if class_id in existing:
            continue
        batch = GradingBatch(
            owner_id=actor.id,
            assignment_id=assignment.id,
            class_id=class_id,
            name=f"{data.name or assignment.title} · {class_name}",
            description=data.description,
            status="collecting",
        )
        db.add(batch)
        db.flush()
        existing[class_id] = batch
        created_ids.append(str(batch.id))
        audit(
            db, actor.id, "grading_batch.create", "grading_batch", batch.id, {"mode": "joint_exam"}
        )
    audit(
        db,
        actor.id,
        "joint_grading_pool.ensure",
        "assignment",
        assignment.id,
        {"created_batch_ids": created_ids, "class_count": len(class_rows)},
    )
    db.commit()
    return _joint_pool_json(db, assignment, list(existing.values()))


@router.get("/assignments/{assignment_id}/grading-batches")
def list_batches(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    status: str | None = None,
    class_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    owned_assignment(db, actor.id, assignment_id)
    filters: list[Any] = [
        GradingBatch.owner_id == actor.id,
        GradingBatch.assignment_id == assignment_id,
    ]
    if status:
        filters.append(GradingBatch.status == status)
    if class_id:
        filters.append(GradingBatch.class_id == class_id)
    total = db.scalar(select(func.count()).select_from(GradingBatch).where(*filters)) or 0
    items = db.scalars(
        select(GradingBatch)
        .where(*filters)
        .order_by(GradingBatch.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [batch_json(db, x) for x in items],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/grading-batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    return batch_json(db, owned_batch(db, actor.id, batch_id))


@router.post("/grading-batches/{batch_id}/archive")
def archive_batch(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    item = owned_batch(db, actor.id, batch_id)
    item.status = "archived"
    audit(db, actor.id, "grading_batch.archive", "grading_batch", item.id)
    db.commit()
    return batch_json(db, item)


def match_student(
    db: Session, batch: GradingBatch, filename: str
) -> tuple[Student | None, str, Decimal, str]:
    members = _batch_students(db, batch)
    # Treat punctuation, whitespace, underscores, hyphens and Chinese brackets as
    # separators. Only adjacent ASCII letters/digits can be part of another identifier.
    numbers = [
        member
        for member in members
        if re.search(
            rf"(?<![0-9A-Za-z]){re.escape(member[1])}(?![0-9A-Za-z])",
            filename,
            re.I,
        )
    ]
    names = [member for member in members if member[2] in filename]
    candidates = {member[0].id: member for member in numbers + names}
    if len(candidates) > 1:
        return None, "ambiguous", Decimal("0"), "文件名包含多个学生标识"
    if len(numbers) == 1:
        return numbers[0][0], "student_number", Decimal("1"), "学号精确匹配"
    if len(names) == 1 and sum(member[2] == names[0][2] for member in members) == 1:
        return names[0][0], "exact_name", Decimal("0.98"), "班级内唯一姓名精确匹配"
    if names:
        return None, "ambiguous", Decimal("0"), "姓名在班级内不唯一"
    return None, "unmatched", Decimal("0"), "未找到可靠学生标识"


@router.post("/grading-batches/{batch_id}/files", status_code=201)
async def upload_submissions(
    batch_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    files: Annotated[list[UploadFile], File()],
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    settings = get_settings()
    if batch.status == "archived":
        raise ApiProblem(409, "BATCH_ARCHIVED", "批次已归档")
    if len(files) > settings.submission_max_files:
        raise ApiProblem(413, "TOO_MANY_FILES", "文件数量超过限制")
    created: list[dict[str, Any]] = []
    total = 0
    inspected: list[tuple[UploadFile, bytes, str, str, int]] = []
    seen_checksums: set[str] = set()
    for upload in files:
        content = await upload.read(settings.assignment_max_file_bytes + 1)
        total += len(content)
        if (
            not content
            or len(content) > settings.assignment_max_file_bytes
            or total > settings.submission_batch_max_bytes
        ):
            raise ApiProblem(413, "SUBMISSION_UPLOAD_TOO_LARGE", "学生作业超过上传限制")
        try:
            name = safe_filename(upload.filename)
            inspection = inspect_upload(
                name,
                content,
                upload.content_type,
                max_pdf_pages=settings.recognition_max_pdf_pages,
                max_image_pixels=settings.recognition_max_image_pixels,
            )
        except UnsafeFile as exc:
            status = 415 if exc.code in {"FILE_TYPE_INVALID", "FILE_CONTENT_INVALID"} else 422
            raise ApiProblem(status, exc.code, exc.message) from exc
        suffix = f".{inspection.kind}"
        mime = upload.content_type or "application/octet-stream"
        checksum = hashlib.sha256(content).hexdigest()
        if checksum in seen_checksums:
            raise ApiProblem(409, "DUPLICATE_SUBMISSION_FILE", "批次中已存在相同文件")
        duplicate = db.scalar(
            select(StoredFile)
            .join(SubmissionFileMatch, SubmissionFileMatch.stored_file_id == StoredFile.id)
            .where(
                SubmissionFileMatch.grading_batch_id == batch.id, StoredFile.checksum == checksum
            )
        )
        if duplicate:
            raise ApiProblem(409, "DUPLICATE_SUBMISSION_FILE", "批次中已存在相同文件")
        seen_checksums.add(checksum)
        inspected.append((upload, content, suffix, mime, inspection.page_count))
    written_keys: list[str] = []
    try:
        for upload, content, suffix, mime, page_count in inspected:
            checksum = hashlib.sha256(content).hexdigest()
            key = f"submissions/{actor.id}/{batch.id}/{uuid.uuid4().hex}{suffix}"
            written_keys.append(key)
            storage.put(key, io.BytesIO(content), len(content), mime)
            stored = StoredFile(
                owner_id=actor.id,
                storage_key=key,
                original_name=safe_filename(upload.filename),
                content_type=mime,
                size=len(content),
                checksum=checksum,
                status=FileStatus.ready,
            )
            db.add(stored)
            db.flush()
            student, method, confidence, reason = match_student(db, batch, stored.original_name)
            match = SubmissionFileMatch(
                grading_batch_id=batch.id,
                stored_file_id=stored.id,
                suggested_student_id=student.id if student else None,
                confirmed_student_id=student.id
                if student and confidence >= Decimal(str(settings.submission_match_threshold))
                else None,
                match_method=method,
                confidence=confidence,
                status="confirmed"
                if student and confidence >= Decimal(str(settings.submission_match_threshold))
                else "pending",
                reason=reason,
                confirmed_by=actor.id
                if student and confidence >= Decimal(str(settings.submission_match_threshold))
                else None,
                confirmed_at=now_utc()
                if student and confidence >= Decimal(str(settings.submission_match_threshold))
                else None,
            )
            db.add(match)
            db.flush()
            submission = None
            if match.confirmed_student_id:
                submission = db.scalar(
                    select(Submission).where(
                        Submission.grading_batch_id == batch.id,
                        Submission.student_id == match.confirmed_student_id,
                        Submission.attempt_number == 1,
                    )
                )
                if submission is None:
                    submission = Submission(
                        owner_id=actor.id,
                        grading_batch_id=batch.id,
                        assignment_id=batch.assignment_id,
                        class_id=batch.class_id,
                        student_id=match.confirmed_student_id,
                        status="matched",
                    )
                    db.add(submission)
                    db.flush()
                    batch.submission_count += 1
                next_page = (
                    db.scalar(
                        select(func.max(SubmissionPage.page_number)).where(
                            SubmissionPage.submission_id == submission.id
                        )
                    )
                    or 0
                ) + 1
                for source_page in range(1, page_count + 1):
                    db.add(
                        SubmissionPage(
                            submission_id=submission.id,
                            stored_file_id=stored.id,
                            page_number=next_page + source_page - 1,
                            source_page_number=source_page,
                            status="ready",
                        )
                    )
            created.append(
                {
                    "match_id": str(match.id),
                    "file_id": str(stored.id),
                    "filename": stored.original_name,
                    "method": method,
                    "confidence": str(confidence),
                    "status": match.status,
                    "suggested_student_id": str(student.id) if student else None,
                    "submission_id": str(submission.id) if submission else None,
                }
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        for key in written_keys:
            try:
                storage.delete(key)
            except Exception:
                pass
        if isinstance(exc, ApiProblem):
            raise
        raise ApiProblem(503, "STORAGE_UNAVAILABLE", "文件批次保存失败，未保留半成品") from exc
    return {"items": created, "count": len(created)}


@router.post("/grading-batches/{batch_id}/matches/{match_id}/confirm")
def confirm_match(
    batch_id: uuid.UUID,
    match_id: uuid.UUID,
    data: MatchInput,
    db: Db,
    actor: Actor,
    storage: Storage,
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    match = db.scalar(
        select(SubmissionFileMatch).where(
            SubmissionFileMatch.id == match_id, SubmissionFileMatch.grading_batch_id == batch.id
        )
    )
    student = next(
        (
            candidate
            for candidate, _, _ in _batch_students(db, batch)
            if candidate.id == data.student_id
        ),
        None,
    )
    if match is None or student is None:
        raise ApiProblem(404, "MATCH_OR_STUDENT_NOT_FOUND", "匹配记录或班级学生不存在")
    if match.status == "confirmed":
        if match.confirmed_student_id != student.id:
            raise ApiProblem(409, "MATCH_ALREADY_CONFIRMED", "已确认匹配不能改到其他学生")
        existing_submission = db.scalar(
            select(Submission)
            .join(SubmissionPage, SubmissionPage.submission_id == Submission.id)
            .where(
                Submission.grading_batch_id == batch.id,
                Submission.student_id == student.id,
                SubmissionPage.stored_file_id == match.stored_file_id,
            )
        )
        if existing_submission is None:
            raise ApiProblem(409, "MATCH_CONFIRMATION_INCONSISTENT", "已确认匹配缺少提交页面")
        return {"submission_id": str(existing_submission.id), "status": "confirmed"}
    submission = db.scalar(
        select(Submission).where(
            Submission.grading_batch_id == batch.id,
            Submission.student_id == student.id,
            Submission.attempt_number == 1,
        )
    )
    if submission is None:
        submission = Submission(
            owner_id=actor.id,
            grading_batch_id=batch.id,
            assignment_id=batch.assignment_id,
            class_id=batch.class_id,
            student_id=student.id,
            status="matched",
        )
        db.add(submission)
        db.flush()
        batch.submission_count += 1
    if (
        db.scalar(
            select(SubmissionPage.id).where(
                SubmissionPage.submission_id == submission.id,
                SubmissionPage.stored_file_id == match.stored_file_id,
            )
        )
        is None
    ):
        stored = db.get(StoredFile, match.stored_file_id)
        if stored is None or stored.owner_id != actor.id:
            raise ApiProblem(404, "SUBMISSION_FILE_NOT_FOUND", "上传文件不存在")
        try:
            content = storage.get(stored.storage_key).read()
            inspection = inspect_upload(
                stored.original_name,
                content,
                stored.content_type,
                max_pdf_pages=get_settings().recognition_max_pdf_pages,
                max_image_pixels=get_settings().recognition_max_image_pixels,
            )
        except Exception as exc:
            raise ApiProblem(
                422, "SUBMISSION_FILE_RECHECK_FAILED", "无法重新校验上传文件分页"
            ) from exc
        next_page = (
            db.scalar(
                select(func.max(SubmissionPage.page_number)).where(
                    SubmissionPage.submission_id == submission.id
                )
            )
            or 0
        ) + 1
        for source_page in range(1, inspection.page_count + 1):
            db.add(
                SubmissionPage(
                    submission_id=submission.id,
                    stored_file_id=match.stored_file_id,
                    page_number=next_page + source_page - 1,
                    source_page_number=source_page,
                    status="ready",
                )
            )
    (
        match.confirmed_student_id,
        match.match_method,
        match.status,
        match.confirmed_by,
        match.confirmed_at,
    ) = student.id, "manual", "confirmed", actor.id, now_utc()
    audit(db, actor.id, "submission_match.confirm", "submission_file_match", match.id)
    db.commit()
    return {"submission_id": str(submission.id), "status": "confirmed"}


@router.delete("/grading-batches/{batch_id}/matches/{match_id}", status_code=204)
def undo_uploaded_file(
    batch_id: uuid.UUID, match_id: uuid.UUID, db: Db, actor: Actor, storage: Storage
) -> None:
    batch = owned_batch(db, actor.id, batch_id)
    match = db.scalar(
        select(SubmissionFileMatch).where(
            SubmissionFileMatch.id == match_id,
            SubmissionFileMatch.grading_batch_id == batch.id,
        )
    )
    if match is None:
        raise ApiProblem(404, "SUBMISSION_FILE_MATCH_NOT_FOUND", "上传记录不存在")
    stored = db.scalar(
        select(StoredFile).where(
            StoredFile.id == match.stored_file_id,
            StoredFile.owner_id == actor.id,
        )
    )
    if stored is None:
        raise ApiProblem(409, "SUBMISSION_FILE_INCONSISTENT", "上传记录缺少原文件")
    pages = db.scalars(
        select(SubmissionPage).where(SubmissionPage.stored_file_id == stored.id)
    ).all()
    submission_ids = {page.submission_id for page in pages}
    for submission_id in submission_ids:
        submission = owned_submission(db, actor.id, submission_id)
        if submission.status == "finalized" or submission.finalized_at is not None:
            raise ApiProblem(409, "FINALIZED_SUBMISSION_REQUIRES_VOID", "已完成提交必须显式作废")
        if db.scalar(select(StudentAnswer.id).where(StudentAnswer.submission_id == submission.id)):
            raise ApiProblem(409, "GRADED_UPLOAD_REQUIRES_VOID", "已进入批改的上传必须显式作废")
    derivative_keys = {
        key
        for page in pages
        for key in (
            page.rendered_storage_key,
            page.processed_storage_key,
            page.thumbnail_storage_key,
        )
        if key
    }
    for page in pages:
        db.execute(
            delete(SubmissionRecognitionBlock).where(
                SubmissionRecognitionBlock.submission_page_id == page.id
            )
        )
        db.delete(page)
    db.flush()
    for submission_id in submission_ids:
        remaining = db.scalar(
            select(SubmissionPage.id).where(SubmissionPage.submission_id == submission_id)
        )
        if remaining is None:
            db.delete(db.get(Submission, submission_id))
    audit(
        db,
        actor.id,
        "submission_upload.undo",
        "submission_file_match",
        match.id,
        {"stored_file_id": str(stored.id)},
    )
    db.delete(match)
    db.delete(stored)
    db.commit()
    failed_keys: list[str] = []
    for key in [stored.storage_key, *sorted(derivative_keys)]:
        try:
            storage.delete(key)
        except Exception:
            failed_keys.append(key)
    if failed_keys:
        raise ApiProblem(
            503,
            "OBJECT_CLEANUP_PENDING",
            "数据库撤销完成，但对象存储清理失败；请从上传记录重新扫描重试",
        )


@router.get("/grading-batches/{batch_id}/submissions")
def list_submissions(batch_id: uuid.UUID, db: Db, actor: Actor) -> list[dict[str, Any]]:
    batch = owned_batch(db, actor.id, batch_id)
    assignment = db.get(Assignment, batch.assignment_id)
    rows = db.scalars(
        select(Submission)
        .where(Submission.grading_batch_id == batch.id)
        .order_by(Submission.created_at)
    ).all()
    student_ids = {row.student_id for row in rows if row.student_id is not None}
    student_query = select(Student).where(Student.id.in_(student_ids))
    if assignment is None or assignment.delivery_mode != "joint_exam":
        student_query = student_query.where(Student.owner_id == actor.id)
    students = {student.id: student for student in db.scalars(student_query)} if student_ids else {}
    result: list[dict[str, Any]] = []
    for row in rows:
        student = students.get(row.student_id) if row.student_id is not None else None
        result.append(
            {
                "id": str(row.id),
                "student_id": str(row.student_id) if row.student_id else None,
                "student_name": student.name if student else None,
                "student_number": student.student_number if student else None,
                "status": row.status,
                "attempt_number": row.attempt_number,
                "page_count": db.scalar(
                    select(func.count())
                    .select_from(SubmissionPage)
                    .where(SubmissionPage.submission_id == row.id)
                )
                or 0,
                "workflow": _submission_workflow(db, row),
            }
        )
    return result


def _editable_submission(db: Session, owner: uuid.UUID, submission_id: uuid.UUID) -> Submission:
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if submission is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    if submission.status == "finalized" or submission.finalized_at is not None:
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交不能修改页面结构")
    if submission.status == "voided":
        raise ApiProblem(409, "SUBMISSION_VOIDED", "已作废提交不能修改")
    return submission


def _renumber_pages(db: Session, submission_id: uuid.UUID) -> None:
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission_id)
        .order_by(SubmissionPage.page_number, SubmissionPage.id)
    ).all()
    for index, page in enumerate(pages, 1):
        page.page_number = -index
    db.flush()
    for index, page in enumerate(pages, 1):
        page.page_number = index
        page.page_version += 1


def submission_job_json(db: Session, job: SubmissionRecognitionJob) -> dict[str, Any]:
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == job.submission_id)
        .order_by(SubmissionPage.page_number)
    ).all()
    return {
        "id": str(job.id),
        "submission_id": str(job.submission_id),
        "status": job.status,
        "provider": job.provider,
        "provider_version": job.provider_version,
        "provider_kind": job.provider_kind,
        "config_version": job.config_version,
        "progress": job.progress,
        "attempt": job.attempt,
        "generation": job.generation,
        "max_attempts": job.max_attempts,
        "input_hash": job.input_hash,
        "output_hash": job.output_hash,
        "warning_codes": job.warning_codes,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "pages": [
            {
                "id": str(page.id),
                "page_number": page.page_number,
                "status": page.status,
                "rendered_storage_key": page.rendered_storage_key,
                "processed_storage_key": page.processed_storage_key,
                "thumbnail_storage_key": page.thumbnail_storage_key,
            }
            for page in pages
        ],
    }


@router.post("/submissions/{submission_id}/recognition-jobs", status_code=201)
def start_submission_recognition(
    submission_id: uuid.UUID,
    data: RecognitionStartInput,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if submission is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "答卷不存在")
    if submission.status == "finalized" or submission.finalized_at is not None:
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交不能启动识别")
    if submission.status == "voided":
        raise ApiProblem(409, "SUBMISSION_VOIDED", "已作废提交不能启动识别")
    processing_job = db.scalar(
        select(SubmissionProcessingJob)
        .where(SubmissionProcessingJob.submission_id == submission.id)
        .order_by(SubmissionProcessingJob.created_at.desc())
    )
    if processing_job is None:
        raise ApiProblem(
            409,
            "SUBMISSION_PROCESSING_REQUIRED",
            "必须先完成页面处理和题目切分",
        )
    if processing_job.status != "completed":
        raise ApiProblem(
            409,
            "SUBMISSION_PROCESSING_INCOMPLETE",
            "最新页面处理任务尚未完整完成",
            {
                "processing_job_id": str(processing_job.id),
                "status": processing_job.status,
            },
        )
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    if not answers:
        raise ApiProblem(
            409,
            "SEGMENTATION_CONFIRMATION_REQUIRED",
            "必须先形成并确认有效题目的答题区域",
            {"reason": "ANSWERS_MISSING", "answer_ids": [], "question_ids": []},
        )
    incomplete = [
        answer
        for answer in answers
        if db.scalar(
            select(StudentAnswerRegion.id).where(
                StudentAnswerRegion.student_answer_id == answer.id,
                StudentAnswerRegion.status == "confirmed",
            )
        )
        is None
    ]
    if incomplete:
        raise ApiProblem(
            409,
            "SEGMENTATION_CONFIRMATION_REQUIRED",
            "全部有效题目完成区域确认后才能运行 OCR",
            {
                "answer_ids": [str(answer.id) for answer in incomplete],
                "question_ids": [str(answer.question_id) for answer in incomplete],
            },
        )
    regions = list(
        db.scalars(
            select(StudentAnswerRegion)
            .join(StudentAnswer, StudentAnswer.id == StudentAnswerRegion.student_answer_id)
            .join(SubmissionPage, SubmissionPage.id == StudentAnswerRegion.submission_page_id)
            .where(
                StudentAnswer.submission_id == submission.id,
                StudentAnswerRegion.status == "confirmed",
            )
            .order_by(
                SubmissionPage.page_number,
                StudentAnswerRegion.y,
                StudentAnswerRegion.x,
                StudentAnswerRegion.id,
            )
        ).all()
    )
    settings = get_settings()
    provider = recognition_provider_from_settings(settings)
    input_hash = hashlib.sha256(
        json.dumps(
            [(region.id, region.region_version, region.segmentation_version) for region in regions],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    existing = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.owner_id == actor.id,
            SubmissionRecognitionJob.idempotency_key == data.idempotency_key,
        )
    )
    if existing:
        expected = {
            "submission_id": submission.id,
            "provider": provider.name,
            "provider_version": provider.version,
            "provider_kind": data.provider_kind,
            "config_version": settings.answer_recognition_config_version,
            "input_hash": input_hash,
        }
        mismatches = sorted(
            field for field, value in expected.items() if getattr(existing, field) != value
        )
        if mismatches:
            raise ApiProblem(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                "幂等键已用于不同的识别请求",
                {
                    "resource_type": "submission_recognition_job",
                    "existing_job_id": str(existing.id),
                    "mismatched_fields": mismatches,
                },
            )
        return submission_job_json(db, existing)
    job = SubmissionRecognitionJob(
        owner_id=actor.id,
        submission_id=submission.id,
        provider=provider.name,
        provider_version=provider.version,
        idempotency_key=data.idempotency_key,
        status="queued",
        provider_kind=data.provider_kind,
        config_version=settings.answer_recognition_config_version,
        input_hash=input_hash,
        max_attempts=settings.answer_recognition_max_attempts,
        generation=(
            db.scalar(
                select(func.max(SubmissionRecognitionJob.generation)).where(
                    SubmissionRecognitionJob.submission_id == submission.id
                )
            )
            or 0
        )
        + 1,
    )
    db.add(job)
    try:
        db.flush()
        audit(db, actor.id, "submission_recognition.create", "submission_recognition_job", job.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        winner = db.scalar(
            select(SubmissionRecognitionJob).where(
                SubmissionRecognitionJob.owner_id == actor.id,
                SubmissionRecognitionJob.idempotency_key == data.idempotency_key,
            )
        )
        if winner is None:
            raise ApiProblem(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                "识别请求并发冲突，请使用相同请求重试",
                {"resource_type": "submission_recognition_job"},
            ) from exc
        expected = {
            "submission_id": submission.id,
            "provider": provider.name,
            "provider_version": provider.version,
            "provider_kind": data.provider_kind,
            "config_version": settings.answer_recognition_config_version,
            "input_hash": input_hash,
        }
        mismatches = sorted(
            field for field, value in expected.items() if getattr(winner, field) != value
        )
        if mismatches:
            raise ApiProblem(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                "幂等键已用于不同的识别请求",
                {
                    "resource_type": "submission_recognition_job",
                    "existing_job_id": str(winner.id),
                    "mismatched_fields": mismatches,
                },
            ) from exc
        return submission_job_json(db, winner)
    if run_now:
        assert storage is not None
        from app.recognition.answer_evidence import run_answer_evidence_phase

        run_answer_evidence_phase(db, storage, get_settings(), job.id)
    else:
        try:
            from workers.celery_app import celery_app

            celery_app.send_task(
                "ahamark.answer_recognition.run",
                args=[str(job.id)],
                headers=celery_request_headers(),
            )
        except Exception as exc:
            job.status, job.error_code, job.error_message = (
                "failed",
                "WORKER_UNAVAILABLE",
                type(exc).__name__,
            )
            db.commit()
    return submission_job_json(db, job)


@router.get("/submissions/{submission_id}/recognition-jobs/{job_id}")
def get_submission_recognition(
    submission_id: uuid.UUID, job_id: uuid.UUID, db: Db, actor: Actor
) -> dict[str, Any]:
    owned_submission(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.id == job_id,
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
        )
    )
    if job is None:
        raise ApiProblem(404, "SUBMISSION_RECOGNITION_NOT_FOUND", "识别任务不存在")
    return submission_job_json(db, job)


@router.post("/submissions/{submission_id}/recognition-jobs/{job_id}/pages/{page_id}/retry")
def retry_submission_page(
    submission_id: uuid.UUID,
    job_id: uuid.UUID,
    page_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    run_now: bool = False,
) -> dict[str, Any]:
    _editable_submission(db, actor.id, submission_id)
    job = db.scalar(
        select(SubmissionRecognitionJob).where(
            SubmissionRecognitionJob.id == job_id,
            SubmissionRecognitionJob.submission_id == submission_id,
            SubmissionRecognitionJob.owner_id == actor.id,
        )
    )
    page = db.scalar(
        select(SubmissionPage).where(
            SubmissionPage.id == page_id, SubmissionPage.submission_id == submission_id
        )
    )
    if job is None or page is None:
        raise ApiProblem(404, "SUBMISSION_PAGE_NOT_FOUND", "识别页面不存在")
    raise ApiProblem(
        409,
        "PAGE_RETRY_RESEGMENTATION_REQUIRED",
        "页面级旧 OCR 重试已关闭；请重新完成题目切分与区域确认后启动答案识别",
        {"job_id": str(job.id), "page_id": str(page.id)},
    )


@router.put("/submissions/{submission_id}/pages/order")
def reorder_submission_pages(
    submission_id: uuid.UUID, data: PageOrderInput, db: Db, actor: Actor
) -> dict[str, Any]:
    submission = _editable_submission(db, actor.id, submission_id)
    pages = db.scalars(
        select(SubmissionPage)
        .where(SubmissionPage.submission_id == submission.id)
        .order_by(SubmissionPage.page_number, SubmissionPage.id)
    ).all()
    if len(data.page_ids) != len(pages) or set(data.page_ids) != {page.id for page in pages}:
        raise ApiProblem(422, "PAGE_ORDER_INCOMPLETE", "排序必须包含且仅包含全部页面")
    current_page_ids = [page.id for page in pages]
    if data.page_ids == current_page_ids:
        return {
            "submission_id": str(submission.id),
            "page_ids": [str(page_id) for page_id in current_page_ids],
        }
    by_id = {page.id: page for page in pages}
    for index, page in enumerate(pages, 1):
        page.page_number = -index
    db.flush()
    for index, page_id in enumerate(data.page_ids, 1):
        by_id[page_id].page_number = index
        by_id[page_id].page_version += 1
    mark_submission_stale(db, submission.id)
    audit(
        db,
        actor.id,
        "submission.pages.reorder",
        "submission",
        submission.id,
        {"page_ids": [str(x) for x in data.page_ids]},
    )
    db.commit()
    return {"submission_id": str(submission.id), "page_ids": [str(x) for x in data.page_ids]}


@router.post("/submissions/{submission_id}/pages/move")
def move_submission_pages(
    submission_id: uuid.UUID, data: MovePagesInput, db: Db, actor: Actor
) -> dict[str, Any]:
    source = _editable_submission(db, actor.id, submission_id)
    target = _editable_submission(db, actor.id, data.target_submission_id)
    if source.grading_batch_id != target.grading_batch_id or source.id == target.id:
        raise ApiProblem(409, "SUBMISSION_MOVE_INVALID", "页面只能在同一批次的不同提交间移动")
    pages = db.scalars(
        select(SubmissionPage).where(
            SubmissionPage.submission_id == source.id, SubmissionPage.id.in_(data.page_ids)
        )
    ).all()
    source_count = (
        db.scalar(
            select(func.count())
            .select_from(SubmissionPage)
            .where(SubmissionPage.submission_id == source.id)
        )
        or 0
    )
    if len(pages) != len(set(data.page_ids)) or len(pages) >= source_count:
        raise ApiProblem(422, "PAGE_MOVE_INVALID", "移动页不存在或不能移走全部页面")
    next_page = (
        db.scalar(
            select(func.max(SubmissionPage.page_number)).where(
                SubmissionPage.submission_id == target.id
            )
        )
        or 0
    ) + 1
    for offset, page in enumerate(pages):
        page.submission_id, page.page_number = target.id, next_page + offset
    db.flush()
    _renumber_pages(db, source.id)
    mark_submission_stale(db, source.id)
    mark_submission_stale(db, target.id)
    audit(
        db,
        actor.id,
        "submission.pages.move",
        "submission",
        source.id,
        {"target_submission_id": str(target.id), "page_ids": [str(x.id) for x in pages]},
    )
    db.commit()
    return {"source_submission_id": str(source.id), "target_submission_id": str(target.id)}


@router.post("/submissions/{submission_id}/split", status_code=201)
def split_submission(
    submission_id: uuid.UUID, data: SplitSubmissionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    source = _editable_submission(db, actor.id, submission_id)
    pages = db.scalars(
        select(SubmissionPage).where(
            SubmissionPage.submission_id == source.id, SubmissionPage.id.in_(data.page_ids)
        )
    ).all()
    source_count = (
        db.scalar(
            select(func.count())
            .select_from(SubmissionPage)
            .where(SubmissionPage.submission_id == source.id)
        )
        or 0
    )
    if len(pages) != len(set(data.page_ids)) or len(pages) >= source_count:
        raise ApiProblem(422, "SUBMISSION_SPLIT_INVALID", "拆分页不存在或不能拆出全部页面")
    attempt = (
        db.scalar(
            select(func.max(Submission.attempt_number)).where(
                Submission.grading_batch_id == source.grading_batch_id,
                Submission.student_id == source.student_id,
            )
        )
        or 0
    ) + 1
    target = Submission(
        owner_id=source.owner_id,
        grading_batch_id=source.grading_batch_id,
        assignment_id=source.assignment_id,
        class_id=source.class_id,
        student_id=source.student_id,
        attempt_number=attempt,
        status="matched",
        source="split",
    )
    db.add(target)
    db.flush()
    for index, page in enumerate(pages, 1):
        page.submission_id, page.page_number = target.id, index
    db.flush()
    _renumber_pages(db, source.id)
    mark_submission_stale(db, source.id)
    mark_submission_stale(db, target.id)
    audit(
        db,
        actor.id,
        "submission.split",
        "submission",
        source.id,
        {"new_submission_id": str(target.id)},
    )
    db.commit()
    return {"source_submission_id": str(source.id), "new_submission_id": str(target.id)}


@router.post("/submissions/{submission_id}/merge")
def merge_submission(
    submission_id: uuid.UUID, data: MergeSubmissionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    target = _editable_submission(db, actor.id, submission_id)
    source = _editable_submission(db, actor.id, data.source_submission_id)
    pages = db.scalars(
        select(SubmissionPage).where(SubmissionPage.submission_id == source.id)
    ).all()
    if (
        source.grading_batch_id != target.grading_batch_id
        or source.assignment_id != target.assignment_id
        or source.class_id != target.class_id
        or source.id == target.id
    ):
        raise ApiProblem(409, "SUBMISSION_MERGE_INVALID", "只能合并同一批次的不同提交")
    next_page = (
        db.scalar(
            select(func.max(SubmissionPage.page_number)).where(
                SubmissionPage.submission_id == target.id
            )
        )
        or 0
    ) + 1
    for offset, page in enumerate(pages):
        page.submission_id, page.page_number = target.id, next_page + offset
    source.status = "merged"
    mark_submission_stale(db, target.id)
    mark_submission_stale(db, source.id)
    audit(
        db,
        actor.id,
        "submission.merge",
        "submission",
        target.id,
        {"source_submission_id": str(source.id)},
    )
    db.commit()
    return {
        "target_submission_id": str(target.id),
        "source_submission_id": str(source.id),
        "page_count": len(pages),
    }


@router.post("/submissions/{submission_id}/answers", status_code=201)
def create_answer(
    submission_id: uuid.UUID, data: AnswerInput, db: Db, actor: Actor
) -> dict[str, Any]:
    submission = _editable_submission(db, actor.id, submission_id)
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    question = db.scalar(
        select(Question).where(
            Question.id == data.question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    )
    if question is None:
        raise ApiProblem(409, "QUESTION_VERSION_MISMATCH", "题目不属于当前试卷版本")
    if db.scalar(
        select(StudentAnswer.id).where(
            StudentAnswer.submission_id == submission.id, StudentAnswer.question_id == question.id
        )
    ):
        raise ApiProblem(409, "ANSWER_EXISTS", "该题答案已存在")
    confidence = data.recognition_confidence
    formula = bool(data.recognized_latex)
    status = (
        "blank"
        if data.is_blank
        else "formula_unavailable"
        if formula
        else "low_confidence"
        if confidence is not None
        and confidence < Decimal(str(get_settings().recognition_low_confidence))
        else "ready_for_grading"
    )
    answer = StudentAnswer(
        submission_id=submission.id,
        question_id=question.id,
        question_version_reference=str(assignment.active_paper_version_id),
        status=status,
        recognized_text=data.recognized_text,
        recognized_latex=data.recognized_latex,
        recognition_confidence=confidence,
        recognition_provider=data.recognition_provider,
        recognition_provider_version=data.recognition_provider_version,
        is_blank=data.is_blank,
        requires_review=status != "ready_for_grading",
    )
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer_json(answer)


def answer_json(x: StudentAnswer) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "submission_id": str(x.submission_id),
        "question_id": str(x.question_id),
        "status": x.status,
        "recognized_text": x.recognized_text,
        "corrected_text": x.corrected_text,
        "effective_text": x.corrected_text if x.corrected_text is not None else x.recognized_text,
        "confidence": str(x.recognition_confidence)
        if x.recognition_confidence is not None
        else None,
        "requires_review": x.requires_review,
    }


@router.patch("/student-answers/{answer_id}")
def patch_answer(answer_id: uuid.UUID, data: AnswerPatch, db: Db, actor: Actor) -> dict[str, Any]:
    _, answer, _, _ = _locked_reviewable_answer(db, actor.id, answer_id)
    answer.corrected_text, answer.corrected_latex, answer.status, answer.requires_review = (
        data.corrected_text,
        data.corrected_latex,
        "manually_entered",
        True,
    )
    for result in db.scalars(
        select(GradingResult).where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
    ).all():
        result.status = "superseded"
    db.commit()
    return answer_json(answer)


def _owned_answer(db: Session, owner_id: uuid.UUID, answer_id: uuid.UUID) -> StudentAnswer:
    answer = db.scalar(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(StudentAnswer.id == answer_id, Submission.owner_id == owner_id)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    return answer


def _locked_mutable_answer(
    db: Session, owner_id: uuid.UUID, answer_id: uuid.UUID
) -> tuple[Submission, StudentAnswer]:
    hint = db.get(StudentAnswer, answer_id)
    if hint is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == hint.submission_id, Submission.owner_id == owner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if submission is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    answer = db.scalar(
        select(StudentAnswer)
        .where(StudentAnswer.id == answer_id, StudentAnswer.submission_id == submission.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    if submission.status == "finalized" or submission.finalized_at is not None:
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交只读")
    if submission.status == "voided":
        raise ApiProblem(409, "SUBMISSION_VOIDED", "已作废提交只读")
    return submission, answer


def _locked_reviewable_answer(
    db: Session, actor_id: uuid.UUID, answer_id: uuid.UUID
) -> tuple[Submission, StudentAnswer, GradingBatch, bool]:
    hint = db.get(StudentAnswer, answer_id)
    if hint is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == hint.submission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if submission is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    answer = db.scalar(
        select(StudentAnswer)
        .where(StudentAnswer.id == answer_id, StudentAnswer.submission_id == submission.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if answer is None:
        raise ApiProblem(404, "ANSWER_NOT_FOUND", "答案不存在")
    batch = db.get(GradingBatch, submission.grading_batch_id)
    if batch is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    is_owner = _require_question_scope(db, actor_id, batch, answer.question_id)
    if submission.status == "finalized" or submission.finalized_at is not None:
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交只读")
    if submission.status == "voided":
        raise ApiProblem(409, "SUBMISSION_VOIDED", "已作废提交只读")
    return submission, answer, batch, is_owner


def _stale_answer_derivatives(db: Session, answer: StudentAnswer) -> None:
    from app.recognition.answer_evidence import mark_answer_recognition_stale

    answer.status, answer.requires_review = "stale", True
    mark_answer_recognition_stale(db, answer.id)
    for result in db.scalars(
        select(GradingResult).where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.not_in(["superseded", "rejected"]),
        )
    ).all():
        result.status = "stale"


@router.post("/student-answers/{answer_id}/regions", status_code=201)
def create_answer_region(
    answer_id: uuid.UUID, data: AnswerRegionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    _, answer, _, _ = _locked_reviewable_answer(db, actor.id, answer_id)
    page = db.scalar(
        select(SubmissionPage).where(
            SubmissionPage.id == data.submission_page_id,
            SubmissionPage.submission_id == answer.submission_id,
        )
    )
    if page is None:
        raise ApiProblem(404, "SUBMISSION_PAGE_NOT_FOUND", "答题区域页面不存在")
    if data.x + data.width > 1 or data.y + data.height > 1:
        raise ApiProblem(422, "ANSWER_REGION_INVALID", "答题区域必须位于页面 0–1 坐标内")
    region = StudentAnswerRegion(
        student_answer_id=answer.id,
        submission_page_id=page.id,
        x=data.x,
        y=data.y,
        width=data.width,
        height=data.height,
        source=data.source,
        confidence=data.confidence,
        status="confirmed" if data.confirmed else "pending",
        confirmed_by=actor.id if data.confirmed else None,
        confirmed_at=now_utc() if data.confirmed else None,
    )
    db.add(region)
    db.flush()
    _stale_answer_derivatives(db, answer)
    audit(db, actor.id, "answer_region.create", "student_answer_region", region.id)
    db.commit()
    db.refresh(region)
    return {"id": str(region.id), "status": region.status}


@router.delete("/student-answers/{answer_id}/regions/{region_id}", status_code=204)
def delete_answer_region(answer_id: uuid.UUID, region_id: uuid.UUID, db: Db, actor: Actor) -> None:
    _, answer, _, _ = _locked_reviewable_answer(db, actor.id, answer_id)
    region = db.scalar(
        select(StudentAnswerRegion).where(
            StudentAnswerRegion.id == region_id,
            StudentAnswerRegion.student_answer_id == answer.id,
        )
    )
    if region is None:
        raise ApiProblem(404, "ANSWER_REGION_NOT_FOUND", "答题区域不存在")
    db.delete(region)
    _stale_answer_derivatives(db, answer)
    audit(db, actor.id, "answer_region.delete", "student_answer_region", region.id)
    db.commit()


@router.post("/student-answers/{answer_id}/grade")
def grade_answer(answer_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    submission, answer, _, _ = _locked_reviewable_answer(db, actor.id, answer_id)
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    question = db.get(Question, answer.question_id)
    rubric = db.scalar(
        select(QuestionRubric).where(
            QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
            QuestionRubric.question_id == answer.question_id,
        )
    )
    if question is None or question.max_score is None or rubric is None:
        raise ApiProblem(409, "RUBRIC_INCOMPLETE", "题目或评分标准不完整")
    regions, recognition_evidence = _require_answer_evidence(db, answer)
    grading_config_version = get_settings().grading_config_version
    grading_key = (
        "grade:"
        + hashlib.sha256(
            json.dumps(
                {
                    "answer_id": str(answer.id),
                    "evidence_id": str(recognition_evidence.id),
                    "evidence_input_hash": recognition_evidence.input_hash,
                    "recognition_version": recognition_evidence.recognition_version,
                    "rubric_version_id": str(assignment.active_rubric_version_id),
                    "prompt_version": get_settings().grading_prompt_version,
                    "config_version": grading_config_version,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    previous_job = db.scalar(
        select(GradingJob).where(
            GradingJob.owner_id == actor.id,
            GradingJob.idempotency_key == grading_key,
        )
    )
    if previous_job is not None:
        previous_result = db.scalar(
            select(GradingResult).where(GradingResult.grading_job_id == previous_job.id)
        )
        if previous_result is not None:
            return {
                "id": str(previous_result.id),
                "method": previous_result.grading_method,
                "provider": previous_result.provider,
                "provider_version": previous_result.provider_version,
                "prompt_version": previous_result.prompt_version,
                "score": str(previous_result.score) if previous_result.score is not None else None,
                "max_score": str(previous_result.max_score),
                "confidence": (
                    str(previous_result.confidence)
                    if previous_result.confidence is not None
                    else None
                ),
                "requires_review": previous_result.requires_review,
                "quality_flags": ([previous_job.error_code] if previous_job.error_code else []),
                "status": previous_result.status,
                "reasoning_summary": previous_result.reasoning_summary,
                "criterion_count": db.scalar(
                    select(func.count())
                    .select_from(GradingCriterionResult)
                    .where(GradingCriterionResult.grading_result_id == previous_result.id)
                )
                or 0,
                "evidence_count": db.scalar(
                    select(func.count())
                    .select_from(GradingEvidence)
                    .where(GradingEvidence.grading_result_id == previous_result.id)
                )
                or 0,
            }
    job = GradingJob(
        owner_id=actor.id,
        grading_batch_id=submission.grading_batch_id,
        submission_id=submission.id,
        question_id=question.id,
        rubric_version_id=assignment.active_rubric_version_id,
        status="running",
        provider="pending",
        provider_version="pending",
        prompt_version=get_settings().grading_prompt_version,
        config_version=grading_config_version,
        idempotency_key=grading_key,
        started_at=now_utc(),
    )
    db.add(job)
    db.flush()
    text = _effective_answer_content(answer)
    rubric_items = db.scalars(
        select(RubricItem)
        .where(RubricItem.question_rubric_id == rubric.id)
        .order_by(RubricItem.display_order)
    ).all()
    item_maximum = sum((Decimal(item.points) for item in rubric_items), Decimal("0"))
    if rubric_items and item_maximum != Decimal(question.max_score):
        raise ApiProblem(409, "RUBRIC_CRITERIA_TOTAL_MISMATCH", "评分分项总分与题目满分不一致")
    if question.question_type in {"single_choice", "multiple_choice", "true_false", "fill_blank"}:
        suggestion = grade_objective(
            text,
            [rubric.standard_answer or "", *rubric.alternative_answers],
            Decimal(question.max_score),
            question.question_type,
        )
        method, provider, version = "objective_rule", "objective-rule", "v1"
        boundary_recheck_failed = False
    else:
        chosen = provider_from_settings(get_settings())
        grading_context = {
            "question": {
                "id": str(question.id),
                "content_text": question.content_text,
                "content_latex": question.content_latex,
            },
            "standard_answer": rubric.standard_answer,
            "alternative_answers": rubric.alternative_answers,
            "rubric_items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "description": item.description,
                    "max_points": str(item.points),
                }
                for item in rubric_items
            ],
            "answer_latex": answer.corrected_latex or answer.recognized_latex,
            "evidence_regions": [
                {
                    "id": str(region.id),
                    "submission_page_id": str(region.submission_page_id),
                    "x": str(region.x),
                    "y": str(region.y),
                    "width": str(region.width),
                    "height": str(region.height),
                }
                for region in regions
            ],
            "versions": {
                "paper": str(assignment.active_paper_version_id),
                "rubric": str(assignment.active_rubric_version_id),
                "prompt": get_settings().grading_prompt_version,
                "config": get_settings().grading_config_version,
            },
        }
        suggestion = chosen.grade(
            text,
            Decimal(question.max_score),
            grading_context,
        )
        boundary_recheck_failed = False
        if _needs_boundary_recheck(
            suggestion.score,
            suggestion.confidence,
            Decimal(question.max_score),
        ):
            second = chosen.grade(
                text,
                Decimal(question.max_score),
                {
                    **grading_context,
                    "review_mode": "boundary_recheck",
                    "previous_suggestion": {
                        "score": str(suggestion.score),
                        "criterion_scores": {
                            key: str(value) for key, value in suggestion.criterion_scores.items()
                        },
                    },
                },
            )
            boundary_recheck_failed = (
                second.score is None
                or second.confidence is None
                or second.score != suggestion.score
                or second.criterion_scores != suggestion.criterion_scores
            )
            if boundary_recheck_failed:
                suggestion = replace(
                    suggestion,
                    summary=(suggestion.summary.rstrip("。") + "；二次复核不一致，请教师检查。"),
                )
            elif second.confidence is not None and suggestion.confidence is not None:
                suggestion = replace(
                    suggestion,
                    confidence=min(suggestion.confidence, second.confidence),
                )
        method, provider, version = (
            ("ai_provider" if suggestion.score is not None else "unavailable"),
            chosen.name,
            chosen.version,
        )
    job.status, job.provider, job.provider_version, job.completed_at = (
        "completed",
        provider,
        version,
        now_utc(),
    )
    if boundary_recheck_failed:
        job.error_code = "BOUNDARY_RECHECK_DISAGREEMENT"
    requires = (
        answer.requires_review
        or suggestion.score is None
        or suggestion.confidence is None
        or suggestion.confidence < Decimal(str(get_settings().grading_auto_accept_confidence))
        or boundary_recheck_failed
    )
    for previous in db.scalars(
        select(GradingResult).where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status.in_(["suggested", "accepted", "modified", "stale"]),
        )
    ).all():
        previous.status = "superseded"
    result = GradingResult(
        grading_job_id=job.id,
        student_answer_id=answer.id,
        question_id=question.id,
        rubric_version_id=assignment.active_rubric_version_id,
        grading_method=method,
        provider=provider,
        provider_version=version,
        prompt_version=job.prompt_version,
        score=suggestion.score,
        max_score=question.max_score,
        confidence=suggestion.confidence,
        recognized_answer_snapshot=text,
        reasoning_summary=suggestion.summary,
        error_type=suggestion.error_type,
        student_feedback=suggestion.feedback,
        requires_review=requires,
        status="suggested",
    )
    db.add(result)
    # A successful regrade creates a fresh result under the active Rubric.
    # Clear the answer-level stale marker so the teacher can review/accept it.
    answer.status = "graded"
    db.flush()
    for item in rubric_items:
        awarded = (
            suggestion.criterion_scores.get(str(item.id))
            if suggestion.criterion_scores
            else Decimal(item.points)
            if suggestion.score == Decimal(question.max_score)
            else Decimal("0")
            if suggestion.score is not None
            else None
        )
        db.add(
            GradingCriterionResult(
                grading_result_id=result.id,
                rubric_item_id=item.id,
                status="evaluated" if awarded is not None else "unavailable",
                awarded_points=awarded,
                max_points=item.points,
                reason=suggestion.criterion_reasons.get(str(item.id), suggestion.summary),
                confidence=suggestion.confidence,
            )
        )
    for region in regions:
        values = [
            Decimal(region.x),
            Decimal(region.y),
            Decimal(region.width),
            Decimal(region.height),
        ]
        if (
            any(value < 0 or value > 1 for value in values)
            or values[0] + values[2] > 1
            or values[1] + values[3] > 1
        ):
            raise ApiProblem(422, "EVIDENCE_REGION_INVALID", "证据区域必须位于页面 0–1 坐标内")
        mapped_rubric_items = sorted(
            item_id
            for item_id, evidence_refs in suggestion.criterion_evidence_refs.items()
            if str(region.id) in evidence_refs
        )
        db.add(
            GradingEvidence(
                grading_result_id=result.id,
                student_answer_id=answer.id,
                submission_page_id=region.submission_page_id,
                evidence_type="answer_region",
                quote=text[:500] or None,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                description=(
                    "rubric_items:" + ",".join(mapped_rubric_items)
                    if mapped_rubric_items
                    else "OCR/教师标注答案区域"
                ),
            )
        )
    db.commit()
    db.refresh(result)
    return {
        "id": str(result.id),
        "method": method,
        "provider": provider,
        "provider_version": version,
        "prompt_version": result.prompt_version,
        "score": str(result.score) if result.score is not None else None,
        "max_score": str(result.max_score),
        "confidence": str(result.confidence) if result.confidence is not None else None,
        "requires_review": result.requires_review,
        "quality_flags": ([job.error_code] if job.error_code else []),
        "status": result.status,
        "reasoning_summary": result.reasoning_summary,
        "criterion_count": len(rubric_items),
        "evidence_count": len(regions),
    }


@router.put("/student-answers/{answer_id}/codex-suggestion")
def save_codex_suggestion(
    answer_id: uuid.UUID, data: CodexSuggestionInput, db: Db, actor: Actor
) -> dict[str, Any]:
    """Retired: codex_local suggestions may only enter through the internal worker API."""
    raise ApiProblem(
        410,
        "CODEX_LOCAL_INTERNAL_ONLY",
        "Codex-assisted suggestions must use the internal suggestion-only workflow",
    )


@router.put("/student-answers/{answer_id}/review")
def review_answer(answer_id: uuid.UUID, data: ReviewInput, db: Db, actor: Actor) -> dict[str, Any]:
    submission, answer, batch, _ = _locked_reviewable_answer(db, actor.id, answer_id)
    assignment = db.get(Assignment, submission.assignment_id)
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    question = db.get(Question, answer.question_id)
    result = db.scalar(
        select(GradingResult)
        .where(GradingResult.student_answer_id == answer.id, GradingResult.status != "superseded")
        .order_by(GradingResult.created_at.desc())
    )
    if data.decision == "modified" and result is None:
        raise ApiProblem(
            409,
            "GRADING_RESULT_REQUIRED",
            "修改评分建议前必须存在评分结果；无建议时请使用 manual_scored",
        )
    if data.decision == "accepted":
        effective = _effective_answer_content(answer)
        if (
            result is None
            or result.status != "suggested"
            or result.rubric_version_id != assignment.active_rubric_version_id
            or result.recognized_answer_snapshot != effective
        ):
            raise ApiProblem(
                409,
                "GRADING_RESULT_STALE",
                "答案或 Rubric 已变化，旧建议不能接受；请重新批改或人工评分",
            )
    if data.decision in {"accepted", "modified"} and result is not None:
        effective = _effective_answer_content(answer)
        if (
            result.rubric_version_id != assignment.active_rubric_version_id
            or result.recognized_answer_snapshot != effective
        ):
            raise ApiProblem(
                409,
                "GRADING_RESULT_STALE",
                "答案或 Rubric 已变化，旧建议不能复核；请重新批改或人工评分",
            )
        current_regions, _ = _require_answer_evidence(db, answer)
        if not _has_current_grading_evidence(db, result, answer, current_regions):
            raise ApiProblem(
                409,
                "GRADING_EVIDENCE_REQUIRED",
                "当前评分建议缺少可追溯的答案区域证据",
                {"grading_result_id": str(result.id)},
            )
    score = (
        data.final_score
        if data.final_score is not None
        else (result.score if data.decision == "accepted" and result else None)
    )
    if data.decision in {"accepted", "modified", "manual_scored"} and score is None:
        raise ApiProblem(422, "FINAL_SCORE_REQUIRED", "确认结果必须包含最终分数")
    if score is not None and (
        question is None or question.max_score is None or score > Decimal(question.max_score)
    ):
        raise ApiProblem(422, "SCORE_OUT_OF_RANGE", "最终分数超出合法范围")
    if data.criterion_scores:
        if result is None:
            raise ApiProblem(409, "GRADING_RESULT_REQUIRED", "按评分项评分前需先创建人工评分结果")
        criterion_rows = db.scalars(
            select(GradingCriterionResult).where(
                GradingCriterionResult.grading_result_id == result.id
            )
        ).all()
        by_item = {row.rubric_item_id: row for row in criterion_rows}
        if set(data.criterion_scores) != set(by_item):
            raise ApiProblem(422, "CRITERION_SCORES_INCOMPLETE", "必须填写全部评分项")
        for item_id, awarded in data.criterion_scores.items():
            row = by_item[item_id]
            if awarded < 0 or awarded > Decimal(row.max_points):
                raise ApiProblem(422, "CRITERION_SCORE_OUT_OF_RANGE", "评分项得分超出范围")
        criterion_total = sum(data.criterion_scores.values(), Decimal("0"))
        if score is None or criterion_total != score:
            raise ApiProblem(422, "CRITERION_TOTAL_MISMATCH", "评分项得分之和必须等于最终分")
        for item_id, awarded in data.criterion_scores.items():
            row = by_item[item_id]
            row.awarded_points, row.status = awarded, "teacher_confirmed"
    review = db.scalar(
        select(TeacherReview)
        .where(TeacherReview.student_answer_id == answer.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if review is None:
        review = TeacherReview(
            student_answer_id=answer.id,
            grading_result_id=result.id if result else None,
            reviewer_id=actor.id,
            decision=data.decision,
            final_score=score,
            review_version=1,
        )
        db.add(review)
        db.flush()
    else:
        if data.expected_review_version != review.review_version:
            raise ApiProblem(
                409,
                "REVIEW_CONFLICT",
                "该题已被其他教师更新，请刷新后重试",
                {
                    "current_review_version": review.review_version,
                    "reviewer_id": str(review.reviewer_id),
                },
            )
        if review.final_score != score or review.final_feedback != data.final_feedback:
            db.add(
                ScoreRevision(
                    teacher_review_id=review.id,
                    student_answer_id=answer.id,
                    actor_id=actor.id,
                    previous_score=review.final_score,
                    new_score=score,
                    previous_feedback=review.final_feedback,
                    new_feedback=data.final_feedback,
                    reason=data.reason or "教师复核修改",
                )
            )
        review.decision, review.final_score = data.decision, score
        review.reviewer_id = actor.id
        review.review_version += 1
    review.grading_result_id = (
        result.id
        if result
        and result.status == "suggested"
        and result.rubric_version_id == assignment.active_rubric_version_id
        else None
    )
    review.final_feedback, review.final_error_type, review.review_notes, review.confirmed_at = (
        data.final_feedback,
        data.final_error_type,
        data.review_notes,
        now_utc(),
    )
    if result and result.status == "suggested":
        result.status = (
            "accepted"
            if data.decision == "accepted"
            else "modified"
            if data.decision in {"modified", "manual_scored"}
            else "rejected"
        )
    answer.requires_review = data.decision == "needs_more_information"
    audit(
        db,
        actor.id,
        "grading.review",
        "student_answer",
        answer.id,
        {
            "decision": data.decision,
            "final_score": str(score) if score is not None else None,
            "review_version": review.review_version,
            "grading_batch_id": str(batch.id),
            "question_id": str(answer.question_id),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiProblem(
            409,
            "REVIEW_CONFLICT",
            "该题已被其他教师更新，请刷新后重试",
        ) from exc
    return {
        "id": str(review.id),
        "decision": review.decision,
        "final_score": str(review.final_score) if review.final_score is not None else None,
        "review_version": review.review_version,
    }


def acceptance_eligibility(
    db: Session, answer: StudentAnswer, assignment: Assignment
) -> tuple[list[str], GradingResult | None]:
    reasons: list[str] = []
    result = db.scalar(
        select(GradingResult)
        .where(
            GradingResult.student_answer_id == answer.id,
            GradingResult.status != "superseded",
        )
        .order_by(GradingResult.created_at.desc())
    )
    if result is None:
        return ["RESULT_MISSING"], None
    try:
        current_regions, _ = _require_answer_evidence(db, answer)
    except ApiProblem as exc:
        reasons.append(str(exc.details.get("reason") or exc.code))
        current_regions = []
    if current_regions and not _has_current_grading_evidence(db, result, answer, current_regions):
        reasons.append("GRADING_EVIDENCE_REQUIRED")
    if result.status != "suggested":
        reasons.append("RESULT_NOT_SUGGESTED")
    if result.score is None or result.score < 0 or result.score > result.max_score:
        reasons.append("SCORE_INVALID")
    if answer.status == "stale" or result.status == "stale":
        reasons.append("STALE")
    if result.rubric_version_id != assignment.active_rubric_version_id:
        reasons.append("RUBRIC_VERSION_MISMATCH")
    if answer.recognized_latex or answer.corrected_latex:
        reasons.append("FORMULA_UNAVAILABLE")
    threshold = Decimal(str(get_settings().grading_auto_accept_confidence))
    if result.confidence is None or result.confidence < threshold:
        reasons.append("CONFIDENCE_LOW")
    if answer.requires_review or result.requires_review:
        reasons.append("REQUIRES_REVIEW")
    if _consistency_differs(db, answer, result):
        reasons.append("CONSISTENCY_REVIEW_REQUIRED")
    if result.provider in {"fake", "unavailable"}:
        reasons.append("PROVIDER_NOT_ELIGIBLE")
    effective = (
        answer.corrected_text
        if answer.corrected_text is not None
        else (answer.recognized_text or "")
    )
    if result.recognized_answer_snapshot != effective:
        reasons.append("ANSWER_CHANGED")
    rubric = db.scalar(
        select(QuestionRubric).where(
            QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
            QuestionRubric.question_id == answer.question_id,
        )
    )
    expected = (
        db.scalar(
            select(func.count())
            .select_from(RubricItem)
            .where(RubricItem.question_rubric_id == rubric.id)
        )
        if rubric
        else 0
    ) or 0
    actual = (
        db.scalar(
            select(func.count())
            .select_from(GradingCriterionResult)
            .where(
                GradingCriterionResult.grading_result_id == result.id,
                GradingCriterionResult.status == "evaluated",
            )
        )
        or 0
    )
    if actual != expected:
        reasons.append("CRITERION_INCOMPLETE")
    return list(dict.fromkeys(reasons)), result


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _snapshot_matches_confirm_plan(
    snapshot: SubmissionScoreSnapshot,
    assignment: Assignment,
    plan: dict[str, Any],
) -> bool:
    submission: Submission = plan["submission"]
    if (
        snapshot.status != "complete"
        or snapshot.submission_id != submission.id
        or snapshot.assignment_id != assignment.id
        or snapshot.student_id != submission.student_id
        or snapshot.paper_version_id != assignment.active_paper_version_id
        or snapshot.rubric_version_id != assignment.active_rubric_version_id
    ):
        return False
    details = {
        str(detail.get("question_id")): detail
        for detail in snapshot.details
        if isinstance(detail, dict) and detail.get("question_id")
    }
    if len(details) != len(plan["answers"]):
        return False
    total = Decimal("0")
    maximum = Decimal("0")
    for question, answer, review, result, auto_accept in plan["answers"]:
        if auto_accept or review is None or review.final_score is None:
            return False
        detail = details.get(str(question.id))
        if detail is None:
            return False
        expected = {
            "question_id": str(question.id),
            "question_number": question.question_number,
            "question_type": str(question.question_type),
            "student_answer_id": str(answer.id),
            "teacher_review_id": str(review.id),
            "score": str(Decimal(review.final_score)),
            "max_score": str(Decimal(question.max_score)),
            "error_type": review.final_error_type,
            "feedback": review.final_feedback,
            "final_error_type": review.final_error_type,
            "final_feedback": review.final_feedback,
            "knowledge_point_ids": sorted(plan["knowledge_point_ids"].get(question.id, [])),
            "grading_method": result.grading_method if result is not None else "manual",
        }
        try:
            actual = {
                "question_id": str(detail["question_id"]),
                "question_number": detail["question_number"],
                "question_type": str(detail["question_type"]),
                "student_answer_id": str(detail["student_answer_id"]),
                "teacher_review_id": str(detail["teacher_review_id"]),
                "score": str(Decimal(str(detail["score"]))),
                "max_score": str(Decimal(str(detail["max_score"]))),
                "error_type": detail["error_type"],
                "feedback": detail["feedback"],
                "final_error_type": detail["final_error_type"],
                "final_feedback": detail["final_feedback"],
                "knowledge_point_ids": sorted(
                    str(value) for value in detail["knowledge_point_ids"]
                ),
                "grading_method": detail["grading_method"],
            }
        except (KeyError, TypeError, ValueError):
            return False
        if set(detail) != {*expected, "finalized_at"} or _canonical_digest(
            actual
        ) != _canonical_digest(expected):
            return False
        total += Decimal(review.final_score)
        maximum += Decimal(question.max_score)
    return (
        snapshot.total_score is not None
        and Decimal(snapshot.total_score) == total
        and Decimal(snapshot.max_score) == maximum
    )


def _changed_questions_for_confirm_plan(plan: dict[str, Any]) -> list[dict[str, str]]:
    previous: SubmissionScoreSnapshot | None = plan.get("previous_snapshot")
    previous_details = {
        str(detail.get("question_id")): detail
        for detail in (previous.details if previous is not None else [])
        if isinstance(detail, dict) and detail.get("question_id")
    }
    changed: list[dict[str, str]] = []
    for question, answer, review, result, _auto_accept in plan["answers"]:
        detail = previous_details.get(str(question.id))
        current_score = (
            review.final_score if review is not None else result.score if result else None
        )
        current = {
            "question_number": question.question_number,
            "question_type": str(question.question_type),
            "student_answer_id": str(answer.id),
            "teacher_review_id": str(review.id) if review is not None else None,
            "score": str(Decimal(current_score)) if current_score is not None else None,
            "max_score": str(Decimal(question.max_score)),
            "final_error_type": review.final_error_type if review is not None else None,
            "final_feedback": review.final_feedback if review is not None else None,
            "knowledge_point_ids": sorted(plan["knowledge_point_ids"].get(question.id, [])),
            "grading_method": result.grading_method if result is not None else "manual",
        }
        prior = None
        if detail is not None:
            try:
                prior = {
                    "question_number": detail["question_number"],
                    "question_type": str(detail["question_type"]),
                    "student_answer_id": str(detail["student_answer_id"]),
                    "teacher_review_id": str(detail["teacher_review_id"]),
                    "score": str(Decimal(str(detail["score"]))),
                    "max_score": str(Decimal(str(detail["max_score"]))),
                    "final_error_type": detail["final_error_type"],
                    "final_feedback": detail["final_feedback"],
                    "knowledge_point_ids": sorted(
                        str(value) for value in detail["knowledge_point_ids"]
                    ),
                    "grading_method": detail["grading_method"],
                }
            except (KeyError, TypeError, ValueError):
                prior = None
        answer_changed_after_snapshot = (
            previous is not None
            and answer.updated_at is not None
            and answer.updated_at.replace(tzinfo=None) > previous.generated_at.replace(tzinfo=None)
        )
        review_changed_after_snapshot = (
            previous is not None
            and review is not None
            and review.updated_at is not None
            and review.updated_at.replace(tzinfo=None) > previous.generated_at.replace(tzinfo=None)
        )
        if prior != current or answer_changed_after_snapshot or review_changed_after_snapshot:
            changed.append(
                {
                    "question_id": str(question.id),
                    "question_number": str(question.question_number),
                }
            )
    return changed


def _confirm_results_plan_view(db: Session, plan: dict[str, Any]) -> dict[str, Any]:
    submission: Submission = plan["submission"]
    student = db.get(Student, submission.student_id) if submission.student_id is not None else None
    reused_snapshot: SubmissionScoreSnapshot | None = plan.get("reuse_snapshot")
    return {
        "submission_id": str(submission.id),
        "student_id": str(submission.student_id) if submission.student_id is not None else None,
        "student_name": student.name if student is not None else None,
        "student_number": student.student_number if student is not None else None,
        "action": "reuse_snapshot" if reused_snapshot is not None else "create_snapshot",
        "snapshot_id": str(reused_snapshot.id) if reused_snapshot is not None else None,
        "snapshot_version": reused_snapshot.version if reused_snapshot is not None else None,
        "changed_questions": (
            [] if reused_snapshot is not None else _changed_questions_for_confirm_plan(plan)
        ),
    }


def _effective_batch_submissions(submissions: list[Submission]) -> list[Submission]:
    """Return the one formal attempt per student, while preserving unmatched blockers."""
    unmatched: list[Submission] = []
    latest_by_student: dict[uuid.UUID, Submission] = {}
    for submission in submissions:
        if submission.status == "voided":
            continue
        if submission.student_id is None:
            unmatched.append(submission)
            continue
        current = latest_by_student.get(submission.student_id)
        if current is None or submission.attempt_number > current.attempt_number:
            latest_by_student[submission.student_id] = submission
    return sorted([*unmatched, *latest_by_student.values()], key=lambda item: item.id)


def _confirm_results_state(
    db: Session,
    batch: GradingBatch,
    assignment: Assignment,
    *,
    lock: bool = False,
) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    submission_query = (
        select(Submission)
        .where(
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == batch.owner_id,
        )
        .order_by(Submission.id)
    )
    if lock:
        submission_query = submission_query.with_for_update()
    submissions = _effective_batch_submissions(list(db.scalars(submission_query).all()))
    question_query = (
        select(Question)
        .where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
        .order_by(Question.id)
    )
    if lock:
        question_query = question_query.with_for_update()
    questions = db.scalars(question_query).all()
    previous = _existing_confirm_results_release(db, batch.owner_id, batch)
    previous_release: GradeRelease | None = None
    previous_notes: dict[str, Any] | None = None
    reusable_snapshots: dict[uuid.UUID, SubmissionScoreSnapshot] = {}
    if previous is not None:
        previous_release, previous_notes = previous
        for item in db.scalars(
            select(GradeReleaseItem).where(GradeReleaseItem.grade_release_id == previous_release.id)
        ):
            snapshot = db.get(SubmissionScoreSnapshot, item.score_snapshot_id)
            if snapshot is not None:
                reusable_snapshots[item.submission_id] = snapshot
    active_rubric_query = (
        select(RubricVersion).where(RubricVersion.id == assignment.active_rubric_version_id)
        if assignment.active_rubric_version_id
        else None
    )
    if active_rubric_query is not None and lock:
        active_rubric_query = active_rubric_query.with_for_update()
    active_rubric = db.scalar(active_rubric_query) if active_rubric_query is not None else None
    blockers: list[dict[str, str]] = []
    plans: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "batch_id": str(batch.id),
        "assignment_id": str(assignment.id),
        "paper_version_id": str(assignment.active_paper_version_id),
        "rubric_version_id": str(assignment.active_rubric_version_id),
        "rubric_status": str(active_rubric.status) if active_rubric else None,
        "rubric_created_at": active_rubric.created_at.isoformat() if active_rubric else None,
        "previous_confirmation": {
            "grade_release_id": str(previous_release.id),
            "version": previous_release.version,
            "review_hash": previous_notes.get("review_hash") if previous_notes else None,
        }
        if previous_release is not None
        else None,
        "submissions": [],
    }
    if not submissions:
        blockers.append({"code": "SUBMISSION_MISSING", "submission_id": "", "question_id": ""})
    if not questions:
        blockers.append({"code": "QUESTION_MISSING", "submission_id": "", "question_id": ""})
    if active_rubric is None or active_rubric.status != "confirmed":
        blockers.append(
            {"code": "RUBRIC_VERSION_NOT_CONFIRMED", "submission_id": "", "question_id": ""}
        )
    for submission in submissions:
        submission_state: dict[str, Any] = {
            "id": str(submission.id),
            "student_id": str(submission.student_id),
            "status": submission.status,
            "finalized_at": submission.finalized_at.isoformat()
            if submission.finalized_at
            else None,
            "answers": [],
        }
        state["submissions"].append(submission_state)
        plan: dict[str, Any] = {
            "submission": submission,
            "answers": [],
            "knowledge_point_ids": {},
        }
        plans.append(plan)
        if (
            submission.student_id is None
            or assignment.active_paper_version_id is None
            or assignment.active_rubric_version_id is None
        ):
            blockers.append(
                {
                    "code": "SUBMISSION_VERSION_INCOMPLETE",
                    "submission_id": str(submission.id),
                    "question_id": "",
                }
            )
        if submission.assignment_id != assignment.id or submission.class_id != batch.class_id:
            blockers.append(
                {
                    "code": "SUBMISSION_SCOPE_MISMATCH",
                    "submission_id": str(submission.id),
                    "question_id": "",
                }
            )
        finalized = submission.status == "finalized" and submission.finalized_at is not None
        if (submission.status == "finalized") != (submission.finalized_at is not None):
            blockers.append(
                {
                    "code": "SUBMISSION_FINALIZATION_INCONSISTENT",
                    "submission_id": str(submission.id),
                    "question_id": "",
                }
            )
        plan["previous_snapshot"] = reusable_snapshots.get(submission.id)
        plan["reuse_snapshot"] = plan["previous_snapshot"] if finalized else None
        answer_query = (
            select(StudentAnswer)
            .where(StudentAnswer.submission_id == submission.id)
            .order_by(StudentAnswer.id)
        )
        if lock:
            answer_query = answer_query.with_for_update()
        answers = db.scalars(answer_query).all()
        by_question = {answer.question_id: answer for answer in answers}
        for question in questions:
            knowledge_point_query = (
                select(QuestionKnowledgePoint)
                .where(QuestionKnowledgePoint.question_id == question.id)
                .order_by(QuestionKnowledgePoint.knowledge_point_id)
            )
            if lock:
                knowledge_point_query = knowledge_point_query.with_for_update()
            knowledge_point_ids = [
                str(item.knowledge_point_id) for item in db.scalars(knowledge_point_query)
            ]
            plan["knowledge_point_ids"][question.id] = knowledge_point_ids
            question_rubric_query = select(QuestionRubric).where(
                QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
                QuestionRubric.question_id == question.id,
            )
            if lock:
                question_rubric_query = question_rubric_query.with_for_update()
            question_rubric = db.scalar(question_rubric_query)
            if question_rubric is not None:
                rubric_item_query = (
                    select(RubricItem)
                    .where(RubricItem.question_rubric_id == question_rubric.id)
                    .order_by(RubricItem.id)
                )
                if lock:
                    rubric_item_query = rubric_item_query.with_for_update()
                rubric_items = list(db.scalars(rubric_item_query))
            else:
                rubric_items = []
            question_state = {
                "id": str(question.id),
                "number": question.question_number,
                "type": question.question_type,
                "max_score": str(question.max_score),
                "knowledge_point_ids": knowledge_point_ids,
                "rubric": {
                    "id": str(question_rubric.id) if question_rubric else None,
                    "standard_answer": question_rubric.standard_answer if question_rubric else None,
                    "alternative_answers": question_rubric.alternative_answers
                    if question_rubric
                    else [],
                    "scoring_notes": question_rubric.scoring_notes if question_rubric else None,
                    "items": [
                        {
                            "id": str(item.id),
                            "title": item.title,
                            "description": item.description,
                            "points": str(item.points),
                            "item_type": item.item_type,
                            "required": item.required,
                            "deduction_rule": item.deduction_rule,
                        }
                        for item in rubric_items
                    ],
                },
            }
            answer = by_question.get(question.id)
            if question.max_score is None or Decimal(question.max_score) <= 0:
                blockers.append(
                    {
                        "code": "QUESTION_SCORE_REQUIRED",
                        "submission_id": str(submission.id),
                        "question_id": str(question.id),
                    }
                )
                continue
            if question_rubric is None:
                blockers.append(
                    {
                        "code": "RUBRIC_INCOMPLETE",
                        "submission_id": str(submission.id),
                        "question_id": str(question.id),
                    }
                )
            if answer is None:
                blockers.append(
                    {
                        "code": "ANSWER_MISSING",
                        "submission_id": str(submission.id),
                        "question_id": str(question.id),
                    }
                )
                continue
            review_query = select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
            if lock:
                review_query = review_query.with_for_update()
            review = db.scalar(review_query)
            result_query = (
                select(GradingResult)
                .where(
                    GradingResult.student_answer_id == answer.id,
                    GradingResult.status != "superseded",
                )
                .order_by(GradingResult.created_at.desc(), GradingResult.id.desc())
            )
            if lock:
                result_query = result_query.with_for_update()
            result = db.scalar(result_query)
            criterion_rows: list[GradingCriterionResult] = []
            grading_evidence: list[GradingEvidence] = []
            if result is not None:
                criterion_query = (
                    select(GradingCriterionResult)
                    .where(GradingCriterionResult.grading_result_id == result.id)
                    .order_by(GradingCriterionResult.id)
                )
                evidence_query = (
                    select(GradingEvidence)
                    .where(GradingEvidence.grading_result_id == result.id)
                    .order_by(GradingEvidence.id)
                )
                if lock:
                    criterion_query = criterion_query.with_for_update()
                    evidence_query = evidence_query.with_for_update()
                criterion_rows = list(db.scalars(criterion_query))
                grading_evidence = list(db.scalars(evidence_query))
            region_query = (
                select(StudentAnswerRegion)
                .where(StudentAnswerRegion.student_answer_id == answer.id)
                .order_by(StudentAnswerRegion.id)
            )
            recognition_evidence_query = (
                select(QuestionRecognitionEvidence)
                .where(QuestionRecognitionEvidence.student_answer_id == answer.id)
                .order_by(QuestionRecognitionEvidence.id)
            )
            if lock:
                region_query = region_query.with_for_update()
                recognition_evidence_query = recognition_evidence_query.with_for_update()
            regions = list(db.scalars(region_query))
            recognition_evidence = list(db.scalars(recognition_evidence_query))
            answer_state = {
                "id": str(answer.id),
                "question": question_state,
                "question_version_reference": answer.question_version_reference,
                "status": answer.status,
                "requires_review": answer.requires_review,
                "recognized_text": answer.recognized_text,
                "recognized_latex": answer.recognized_latex,
                "corrected_text": answer.corrected_text,
                "corrected_latex": answer.corrected_latex,
                "result": {
                    "id": str(result.id),
                    "status": result.status,
                    "score": str(result.score),
                    "max_score": str(result.max_score),
                    "confidence": str(result.confidence),
                    "rubric_version_id": str(result.rubric_version_id),
                    "recognized_answer_snapshot": result.recognized_answer_snapshot,
                    "requires_review": result.requires_review,
                    "provider": result.provider,
                    "provider_version": result.provider_version,
                    "prompt_version": result.prompt_version,
                    "grading_method": result.grading_method,
                    "reasoning_summary": result.reasoning_summary,
                    "error_type": result.error_type,
                    "student_feedback": result.student_feedback,
                    "criteria": [
                        {
                            "id": str(item.id),
                            "rubric_item_id": str(item.rubric_item_id),
                            "status": item.status,
                            "awarded_points": str(item.awarded_points),
                            "max_points": str(item.max_points),
                            "reason": item.reason,
                            "confidence": str(item.confidence),
                        }
                        for item in criterion_rows
                    ],
                    "evidence": [
                        {
                            "id": str(item.id),
                            "submission_page_id": str(item.submission_page_id),
                            "type": item.evidence_type,
                            "quote": item.quote,
                            "x": str(item.x),
                            "y": str(item.y),
                            "width": str(item.width),
                            "height": str(item.height),
                            "description": item.description,
                        }
                        for item in grading_evidence
                    ],
                }
                if result
                else None,
                "review": {
                    "id": str(review.id),
                    "grading_result_id": str(review.grading_result_id),
                    "decision": review.decision,
                    "final_score": str(review.final_score),
                    "final_feedback": review.final_feedback,
                    "final_error_type": review.final_error_type,
                    "review_notes": review.review_notes,
                    "confirmed_at": review.confirmed_at.isoformat()
                    if review.confirmed_at
                    else None,
                }
                if review
                else None,
                "regions": [
                    {
                        "id": str(item.id),
                        "submission_page_id": str(item.submission_page_id),
                        "status": item.status,
                        "x": str(item.x),
                        "y": str(item.y),
                        "width": str(item.width),
                        "height": str(item.height),
                    }
                    for item in regions
                ],
                "recognition_evidence": [
                    {
                        "id": str(item.id),
                        "status": item.status,
                        "input_hash": item.input_hash,
                        "output_hash": item.output_hash,
                        "recognition_version": item.recognition_version,
                        "requires_review": item.requires_review,
                        "stale_at": item.stale_at.isoformat() if item.stale_at else None,
                        "block_sources": item.block_sources,
                    }
                    for item in recognition_evidence
                ],
            }
            submission_state["answers"].append(answer_state)
            binding_reasons: list[str] = []
            if answer.question_version_reference != str(assignment.active_paper_version_id):
                binding_reasons.append("PAPER_VERSION_MISMATCH")
            if result is not None and result.question_id != question.id:
                binding_reasons.append("RESULT_QUESTION_MISMATCH")
            if result is not None and Decimal(result.max_score) != Decimal(question.max_score):
                binding_reasons.append("RESULT_MAX_SCORE_MISMATCH")
            if review is not None and review.final_score is not None and not answer.requires_review:
                manually_reconfirmed = (
                    review.decision in {"modified", "manual_scored"}
                    and review.confirmed_at is not None
                    and active_rubric is not None
                    and review.confirmed_at >= active_rubric.created_at
                    and (result is None or review.confirmed_at >= result.created_at)
                )
                if review.decision not in {"accepted", "modified", "manual_scored"}:
                    reasons = ["REVIEW_DECISION_INVALID"]
                elif review.confirmed_at is None:
                    reasons = ["REVIEW_NOT_CONFIRMED"]
                elif review.final_score < 0 or review.final_score > Decimal(question.max_score):
                    reasons = ["SCORE_OUT_OF_RANGE"]
                elif (
                    result is not None
                    and review.grading_result_id != result.id
                    and not manually_reconfirmed
                ):
                    reasons = ["REVIEW_RESULT_MISMATCH"]
                elif (
                    result is not None
                    and (
                        result.status == "stale"
                        or result.rubric_version_id != assignment.active_rubric_version_id
                    )
                    and not manually_reconfirmed
                ):
                    reasons = ["STALE_RUBRIC"]
                else:
                    reasons = []
                plan["answers"].append((question, answer, review, result, False))
            else:
                reasons, result = acceptance_eligibility(db, answer, assignment)
                plan["answers"].append((question, answer, review, result, True))
            reasons = list(dict.fromkeys([*binding_reasons, *reasons]))
            for reason in reasons:
                blockers.append(
                    {
                        "code": reason,
                        "submission_id": str(submission.id),
                        "question_id": str(question.id),
                    }
                )
        reusable = plan["reuse_snapshot"]
        if finalized:
            if reusable is None:
                blockers.append(
                    {
                        "code": "FINALIZED_SNAPSHOT_NOT_REUSABLE",
                        "submission_id": str(submission.id),
                        "question_id": "",
                    }
                )
            elif not _snapshot_matches_confirm_plan(reusable, assignment, plan):
                plan["reuse_snapshot"] = None
                blockers.append(
                    {
                        "code": "SNAPSHOT_REUSE_MISMATCH",
                        "submission_id": str(submission.id),
                        "question_id": "",
                    }
                )
            else:
                submission_state["reused_snapshot"] = {
                    "id": str(reusable.id),
                    "version": reusable.version,
                    "paper_version_id": str(reusable.paper_version_id),
                    "rubric_version_id": str(reusable.rubric_version_id),
                }
        else:
            plan["reuse_snapshot"] = None
    if (
        previous_release is not None
        and plans
        and all(plan.get("reuse_snapshot") is not None for plan in plans)
    ):
        blockers.append(
            {
                "code": "CONFIRM_RESULTS_ALREADY_CURRENT",
                "submission_id": "",
                "question_id": "",
            }
        )
    state["blockers"] = sorted(
        blockers,
        key=lambda item: (
            item.get("code", ""),
            item.get("submission_id", ""),
            item.get("question_id", ""),
        ),
    )
    return _canonical_digest(state), blockers, plans


def _confirm_results_notes(release: GradeRelease) -> dict[str, Any] | None:
    try:
        value = json.loads(release.notes or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("kind") != "confirm_results":
        return None
    return value


def _confirm_results_storage_key(actor_id: uuid.UUID, idempotency_key: str) -> str:
    return _canonical_digest(
        {
            "operation": "confirm_results",
            "owner_id": str(actor_id),
            "idempotency_key": idempotency_key,
        }
    )


def _confirm_results_response(release: GradeRelease, notes: dict[str, Any]) -> dict[str, Any]:
    return {
        "grade_release_id": str(release.id),
        "status": release.status,
        "review_hash": notes["review_hash"],
        "submission_count": notes["submission_count"],
        "auto_accepted_count": notes["auto_accepted_count"],
        "teacher_review_ids": notes["teacher_review_ids"],
        "snapshot_ids": notes["snapshot_ids"],
        "grade_release_version": release.version,
        "new_snapshot_count": notes.get("new_snapshot_count", notes["submission_count"]),
        "reused_snapshot_count": notes.get("reused_snapshot_count", 0),
        "previous_grade_release_id": notes.get("previous_grade_release_id"),
        "new_snapshot_ids": notes.get("new_snapshot_ids", notes["snapshot_ids"]),
        "reused_snapshot_ids": notes.get("reused_snapshot_ids", []),
    }


def _validated_confirm_results_release(
    db: Session,
    actor_id: uuid.UUID,
    batch_id: uuid.UUID,
    release: GradeRelease,
    notes: dict[str, Any],
) -> dict[str, Any]:
    batch = db.scalar(
        select(GradingBatch).where(
            GradingBatch.id == batch_id,
            GradingBatch.owner_id == actor_id,
        )
    )
    items = list(
        db.scalars(
            select(GradeReleaseItem)
            .where(GradeReleaseItem.grade_release_id == release.id)
            .order_by(GradeReleaseItem.submission_id)
        )
    )
    snapshots = [db.get(SubmissionScoreSnapshot, item.score_snapshot_id) for item in items]
    submissions = [db.get(Submission, item.submission_id) for item in items]
    snapshot_ids = [str(item.score_snapshot_id) for item in items]
    detail_rows = [
        detail
        for snapshot in snapshots
        if snapshot is not None
        for detail in snapshot.details
        if isinstance(detail, dict)
    ]
    teacher_review_ids = sorted(
        {
            str(detail["teacher_review_id"])
            for detail in detail_rows
            if detail.get("teacher_review_id")
        }
    )
    reviews = {
        str(review_id): db.get(TeacherReview, review_id)
        for review_id in (uuid.UUID(value) for value in teacher_review_ids)
    }
    noted_new_snapshot_ids = notes.get("new_snapshot_ids", notes.get("snapshot_ids", []))
    noted_reused_snapshot_ids = notes.get("reused_snapshot_ids", [])
    if (
        batch is None
        or release.owner_id != actor_id
        or release.status != "released"
        or release.assignment_id != batch.assignment_id
        or release.class_id != batch.class_id
        or notes.get("batch_id") != str(batch.id)
        or len(items) != notes.get("submission_count")
        or any(
            snapshot is None
            or snapshot.status != "complete"
            or snapshot.submission_id != item.submission_id
            or snapshot.assignment_id != release.assignment_id
            or snapshot.student_id != item.student_id
            for item, snapshot in zip(items, snapshots, strict=True)
        )
        or any(
            submission is None
            or submission.grading_batch_id != batch.id
            or submission.owner_id != actor_id
            or submission.assignment_id != release.assignment_id
            or submission.class_id != release.class_id
            or submission.student_id != item.student_id
            for item, submission in zip(items, submissions, strict=True)
        )
        or sorted(snapshot_ids) != sorted(notes.get("snapshot_ids", []))
        or set(noted_new_snapshot_ids) & set(noted_reused_snapshot_ids)
        or sorted([*noted_new_snapshot_ids, *noted_reused_snapshot_ids]) != sorted(snapshot_ids)
        or len(noted_new_snapshot_ids)
        != notes.get("new_snapshot_count", notes.get("submission_count"))
        or len(noted_reused_snapshot_ids) != notes.get("reused_snapshot_count", 0)
        or teacher_review_ids != sorted(notes.get("teacher_review_ids", []))
        or any(
            review is None or str(review.student_answer_id) != str(detail.get("student_answer_id"))
            for detail in detail_rows
            if detail.get("teacher_review_id")
            for review in [reviews.get(str(detail.get("teacher_review_id")))]
        )
    ):
        raise ApiProblem(409, "CONFIRM_RESULTS_REPLAY_INVALID", "确认结果记录不完整")
    return _confirm_results_response(release, notes)


def _existing_confirm_results_release(
    db: Session,
    actor_id: uuid.UUID,
    batch: GradingBatch,
) -> tuple[GradeRelease, dict[str, Any]] | None:
    releases = db.scalars(
        select(GradeRelease)
        .where(
            GradeRelease.owner_id == actor_id,
            GradeRelease.assignment_id == batch.assignment_id,
            GradeRelease.class_id == batch.class_id,
            GradeRelease.status == "released",
        )
        .order_by(GradeRelease.version.desc(), GradeRelease.created_at.desc())
    )
    for release in releases:
        notes = _confirm_results_notes(release)
        if notes is not None and notes.get("batch_id") == str(batch.id):
            _validated_confirm_results_release(db, actor_id, batch.id, release, notes)
            return release, notes
    return None


def _existing_confirm_results(
    db: Session,
    actor_id: uuid.UUID,
    batch: GradingBatch,
) -> dict[str, Any] | None:
    existing = _existing_confirm_results_release(db, actor_id, batch)
    if existing is None:
        return None
    release, notes = existing
    return _confirm_results_response(release, notes)


def _confirm_results_replay(
    db: Session,
    actor_id: uuid.UUID,
    batch_id: uuid.UUID,
    data: ConfirmResultsInput,
) -> dict[str, Any] | None:
    storage_key = _confirm_results_storage_key(actor_id, data.idempotency_key)
    release = db.scalar(select(GradeRelease).where(GradeRelease.idempotency_key == storage_key))
    if release is None:
        return None
    notes = _confirm_results_notes(release)
    request_hash = _canonical_digest(
        {
            "batch_id": str(batch_id),
            "expected_review_hash": data.expected_review_hash,
        }
    )
    if (
        release.owner_id != actor_id
        or notes is None
        or notes.get("batch_id") != str(batch_id)
        or notes.get("request_hash") != request_hash
    ):
        raise ApiProblem(409, "IDEMPOTENCY_KEY_CONFLICT", "幂等键已用于不同的确认结果请求")
    return _validated_confirm_results_release(db, actor_id, batch_id, release, notes)


@router.get("/grading-batches/{batch_id}/confirm-results/readiness")
def confirm_results_readiness(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    review_hash, blockers, plans = _confirm_results_state(db, batch, assignment)
    reused_snapshot_count = sum(plan.get("reuse_snapshot") is not None for plan in plans)
    previous = _existing_confirm_results_release(db, actor.id, batch)
    previous_grade_release_id = str(previous[0].id) if previous is not None else None
    already_current = any(
        blocker["code"] == "CONFIRM_RESULTS_ALREADY_CURRENT" for blocker in blockers
    )
    return {
        "review_hash": review_hash,
        "ready": not blockers,
        "blockers": blockers,
        "submission_count": len(plans),
        "new_snapshot_count": len(plans) - reused_snapshot_count,
        "reused_snapshot_count": reused_snapshot_count,
        "previous_grade_release_id": previous_grade_release_id,
        "plan": [_confirm_results_plan_view(db, plan) for plan in plans],
        "confirmed_result": (
            _existing_confirm_results(db, actor.id, batch) if already_current else None
        ),
    }


@router.post("/grading-batches/{batch_id}/confirm-results", status_code=201)
def confirm_results(
    batch_id: uuid.UUID, data: ConfirmResultsInput, db: Db, actor: Actor
) -> dict[str, Any]:
    replay = _confirm_results_replay(db, actor.id, batch_id, data)
    if replay is not None:
        return replay
    batch_reference = db.scalar(
        select(GradingBatch).where(GradingBatch.id == batch_id, GradingBatch.owner_id == actor.id)
    )
    if batch_reference is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    if not serialize_grade_release_mutation(db, actor.id, batch_reference.assignment_id):
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    batch = db.scalar(
        select(GradingBatch)
        .where(GradingBatch.id == batch_id, GradingBatch.owner_id == actor.id)
        .with_for_update()
    )
    if batch is None:
        raise ApiProblem(404, "GRADING_BATCH_NOT_FOUND", "批改批次不存在")
    replay = _confirm_results_replay(db, actor.id, batch_id, data)
    if replay is not None:
        return replay
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == batch.assignment_id, Assignment.owner_id == actor.id)
        .with_for_update()
    )
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    review_hash, blockers, plans = _confirm_results_state(db, batch, assignment, lock=True)
    if review_hash != data.expected_review_hash:
        raise ApiProblem(
            409,
            "CONFIRM_RESULTS_STALE",
            "批改内容已变化，请刷新后重新确认",
            {"current_review_hash": review_hash},
        )
    if blockers:
        raise ApiProblem(
            409,
            "CONFIRM_RESULTS_BLOCKED",
            "仍有答案需要教师处理",
            {"blockers": blockers},
        )
    previous = _existing_confirm_results_release(db, actor.id, batch)
    previous_grade_release_id = str(previous[0].id) if previous is not None else None

    teacher_review_ids: list[str] = []
    snapshot_ids: list[str] = []
    auto_accepted_count = 0
    new_snapshot_count = 0
    reused_snapshot_count = 0
    new_snapshot_ids: list[str] = []
    reused_snapshot_ids: list[str] = []
    snapshot_rows: list[tuple[Submission, SubmissionScoreSnapshot]] = []
    confirmed_at = now_utc()
    for plan in plans:
        submission: Submission = plan["submission"]
        reused_snapshot: SubmissionScoreSnapshot | None = plan.get("reuse_snapshot")
        if reused_snapshot is not None:
            teacher_review_ids.extend(
                str(review.id)
                for _question, _answer, review, _result, _auto_accept in plan["answers"]
                if review is not None
            )
            snapshot_ids.append(str(reused_snapshot.id))
            reused_snapshot_ids.append(str(reused_snapshot.id))
            snapshot_rows.append((submission, reused_snapshot))
            reused_snapshot_count += 1
            continue
        total = Decimal("0")
        maximum = Decimal("0")
        details: list[dict[str, Any]] = []
        for question, answer, review, result, auto_accept in plan["answers"]:
            if auto_accept:
                assert result is not None
                review = TeacherReview(
                    id=uuid.uuid4(),
                    student_answer_id=answer.id,
                    grading_result_id=result.id,
                    reviewer_id=actor.id,
                    decision="accepted",
                    final_score=result.score,
                    confirmed_at=confirmed_at,
                )
                db.add(review)
                result.status = "accepted"
                auto_accepted_count += 1
            assert review is not None and review.final_score is not None
            teacher_review_ids.append(str(review.id))
            total += Decimal(review.final_score)
            maximum += Decimal(question.max_score)
            details.append(
                {
                    "question_id": str(question.id),
                    "question_number": question.question_number,
                    "question_type": question.question_type,
                    "student_answer_id": str(answer.id),
                    "teacher_review_id": str(review.id),
                    "score": str(review.final_score),
                    "max_score": str(question.max_score),
                    "error_type": review.final_error_type,
                    "feedback": review.final_feedback,
                    "final_error_type": review.final_error_type,
                    "final_feedback": review.final_feedback,
                    "knowledge_point_ids": [
                        str(value)
                        for value in db.scalars(
                            select(QuestionKnowledgePoint.knowledge_point_id).where(
                                QuestionKnowledgePoint.question_id == question.id
                            )
                        )
                    ],
                    "grading_method": result.grading_method if result else "manual",
                    "finalized_at": confirmed_at.isoformat(),
                }
            )
        version = (
            db.scalar(
                select(func.max(SubmissionScoreSnapshot.version)).where(
                    SubmissionScoreSnapshot.submission_id == submission.id
                )
            )
            or 0
        ) + 1
        snapshot = SubmissionScoreSnapshot(
            id=uuid.uuid4(),
            submission_id=submission.id,
            assignment_id=assignment.id,
            student_id=submission.student_id,
            paper_version_id=assignment.active_paper_version_id,
            rubric_version_id=assignment.active_rubric_version_id,
            total_score=total,
            max_score=maximum,
            status="complete",
            generated_by=actor.id,
            version=version,
            details=details,
        )
        db.add(snapshot)
        snapshot_ids.append(str(snapshot.id))
        new_snapshot_ids.append(str(snapshot.id))
        snapshot_rows.append((submission, snapshot))
        new_snapshot_count += 1
        submission.status = "finalized"
        submission.finalized_at = confirmed_at

    release_version = (
        db.scalar(
            select(func.max(GradeRelease.version)).where(
                GradeRelease.assignment_id == assignment.id,
                GradeRelease.class_id == batch.class_id,
            )
        )
        or 0
    ) + 1
    request_hash = _canonical_digest(
        {
            "batch_id": str(batch.id),
            "expected_review_hash": data.expected_review_hash,
        }
    )
    notes = {
        "kind": "confirm_results",
        "batch_id": str(batch.id),
        "request_hash": request_hash,
        "review_hash": review_hash,
        "submission_count": len(snapshot_rows),
        "auto_accepted_count": auto_accepted_count,
        "new_snapshot_count": new_snapshot_count,
        "reused_snapshot_count": reused_snapshot_count,
        "new_snapshot_ids": new_snapshot_ids,
        "reused_snapshot_ids": reused_snapshot_ids,
        "teacher_review_ids": teacher_review_ids,
        "snapshot_ids": snapshot_ids,
        "previous_grade_release_id": previous_grade_release_id,
    }
    release = GradeRelease(
        id=uuid.uuid4(),
        owner_id=actor.id,
        assignment_id=assignment.id,
        class_id=batch.class_id,
        version=release_version,
        status="released",
        release_mode="score_and_feedback",
        released_at=confirmed_at,
        created_by=actor.id,
        notes=json.dumps(notes, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        idempotency_key=_confirm_results_storage_key(actor.id, data.idempotency_key),
    )
    db.add(release)
    for submission, snapshot in snapshot_rows:
        db.add(
            GradeReleaseItem(
                grade_release_id=release.id,
                student_id=submission.student_id,
                submission_id=submission.id,
                score_snapshot_id=snapshot.id,
            )
        )
    audit(
        db,
        actor.id,
        "grading.confirm_results",
        "grading_batch",
        batch.id,
        {
            "grade_release_id": str(release.id),
            "submission_count": len(snapshot_rows),
            "auto_accepted_count": auto_accepted_count,
            "new_snapshot_count": new_snapshot_count,
            "reused_snapshot_count": reused_snapshot_count,
            "new_snapshot_ids": new_snapshot_ids,
            "reused_snapshot_ids": reused_snapshot_ids,
            "previous_grade_release_id": previous_grade_release_id,
            "review_hash": review_hash,
            "request_hash": request_hash,
            "teacher_authorized": True,
            "suggestion_only_before_confirmation": True,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        replay = _confirm_results_replay(db, actor.id, batch_id, data)
        if replay is not None:
            return replay
        raise ApiProblem(
            409,
            "CONFIRM_RESULTS_CONFLICT",
            "确认结果期间数据已被其他请求更新，请刷新后重试",
        ) from exc
    return _confirm_results_response(release, notes)


@router.get("/grading-batches/{batch_id}/bulk-accept-eligibility")
def bulk_accept_eligibility(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    answers = db.scalars(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(Submission.grading_batch_id == batch.id, Submission.owner_id == actor.id)
    ).all()
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    items = []
    reason_counts: dict[str, int] = {}
    for answer in answers:
        reasons, _ = acceptance_eligibility(db, answer, assignment)
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        items.append({"answer_id": str(answer.id), "eligible": not reasons, "reasons": reasons})
    return {
        "eligible_count": sum(item["eligible"] for item in items),
        "excluded_count": sum(not item["eligible"] for item in items),
        "reason_counts": reason_counts,
        "items": items,
    }


def _collaboration_json(db: Session, batch: GradingBatch, actor_id: uuid.UUID) -> dict[str, Any]:
    assignment = db.get(Assignment, batch.assignment_id)
    owner = db.get(User, batch.owner_id)
    collaborators = db.execute(
        select(GradingCollaborator, User)
        .join(User, User.id == GradingCollaborator.user_id)
        .where(
            GradingCollaborator.assignment_id == batch.assignment_id,
            GradingCollaborator.status == "active",
        )
        .order_by(User.display_name, User.email)
    ).all()
    active_collaborators = [
        (row, user)
        for row, user in collaborators
        if user.status == "active" and "teacher" in {role.name for role in user.roles}
    ]
    assignments = {
        row.question_id: row
        for row in db.scalars(
            select(GradingQuestionAssignment).where(
                GradingQuestionAssignment.grading_batch_id == batch.id
            )
        ).all()
    }
    question_rows = (
        db.scalars(
            select(Question)
            .where(
                Question.paper_version_id == assignment.active_paper_version_id,
                Question.status == QuestionStatus.active,
            )
            .order_by(Question.question_number, Question.id)
        ).all()
        if assignment is not None and assignment.active_paper_version_id is not None
        else []
    )
    questions: list[dict[str, Any]] = []
    for question in question_rows:
        assigned = assignments.get(question.id)
        total = (
            db.scalar(
                select(func.count())
                .select_from(StudentAnswer)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id == batch.id,
                    Submission.status != "voided",
                    StudentAnswer.question_id == question.id,
                )
            )
            or 0
        )
        reviewed = (
            db.scalar(
                select(func.count())
                .select_from(TeacherReview)
                .join(StudentAnswer, StudentAnswer.id == TeacherReview.student_answer_id)
                .join(Submission, Submission.id == StudentAnswer.submission_id)
                .where(
                    Submission.grading_batch_id == batch.id,
                    StudentAnswer.question_id == question.id,
                    TeacherReview.final_score.is_not(None),
                    StudentAnswer.requires_review.is_(False),
                )
            )
            or 0
        )
        questions.append(
            {
                "id": str(question.id),
                "number": question.question_number,
                "assignee_id": str(assigned.assignee_id) if assigned else None,
                "total": total,
                "reviewed": reviewed,
            }
        )
    return {
        "is_owner": batch.owner_id == actor_id,
        "can_confirm_results": batch.owner_id == actor_id,
        "owner": {
            "id": str(batch.owner_id),
            "display_name": owner.display_name if owner else "主责老师",
            "email": owner.email if owner else None,
        },
        "collaborators": [
            {
                "id": str(row.user_id),
                "display_name": user.display_name,
                "email": user.email,
                "role": row.role,
            }
            for row, user in active_collaborators
        ],
        "questions": questions,
    }


@router.get("/grading-batches/{batch_id}/collaboration")
def get_grading_collaboration(batch_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    batch, _, _ = _reviewable_batch(db, actor.id, batch_id)
    return _collaboration_json(db, batch, actor.id)


@router.post("/grading-batches/{batch_id}/collaborators", status_code=201)
def add_grading_collaborator(
    batch_id: uuid.UUID, data: CollaboratorInput, db: Db, actor: Actor
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    user = db.scalar(select(User).where(func.lower(User.email) == data.email.lower()))
    if user is None or user.status != "active":
        raise ApiProblem(404, "COLLABORATOR_NOT_FOUND", "未找到可用的教师账号")
    if "teacher" not in {role.name for role in user.roles}:
        raise ApiProblem(422, "COLLABORATOR_TEACHER_REQUIRED", "仅教师账号可参与批改")
    if user.id == actor.id:
        raise ApiProblem(422, "OWNER_ALREADY_LEADS", "主责老师无需添加为协作者")
    row = db.scalar(
        select(GradingCollaborator).where(
            GradingCollaborator.assignment_id == batch.assignment_id,
            GradingCollaborator.user_id == user.id,
        )
    )
    if row is None:
        row = GradingCollaborator(
            assignment_id=batch.assignment_id,
            user_id=user.id,
            added_by=actor.id,
            role="grader",
            status="active",
        )
        db.add(row)
    else:
        row.status, row.added_by = "active", actor.id
    audit(
        db,
        actor.id,
        "grading.collaborator.add",
        "grading_batch",
        batch.id,
        {"collaborator_id": str(user.id)},
    )
    db.commit()
    return _collaboration_json(db, batch, actor.id)


@router.delete("/grading-batches/{batch_id}/collaborators/{user_id}", status_code=204)
def remove_grading_collaborator(
    batch_id: uuid.UUID, user_id: uuid.UUID, db: Db, actor: Actor
) -> None:
    batch = owned_batch(db, actor.id, batch_id)
    row = db.scalar(
        select(GradingCollaborator).where(
            GradingCollaborator.assignment_id == batch.assignment_id,
            GradingCollaborator.user_id == user_id,
            GradingCollaborator.status == "active",
        )
    )
    if row is None:
        raise ApiProblem(404, "COLLABORATOR_NOT_FOUND", "协作老师不存在")
    batch_ids = select(GradingBatch.id).where(GradingBatch.assignment_id == batch.assignment_id)
    db.execute(
        delete(GradingQuestionAssignment).where(
            GradingQuestionAssignment.grading_batch_id.in_(batch_ids),
            GradingQuestionAssignment.assignee_id == user_id,
        )
    )
    row.status = "inactive"
    audit(
        db,
        actor.id,
        "grading.collaborator.remove",
        "grading_batch",
        batch.id,
        {"collaborator_id": str(user_id)},
    )
    db.commit()


@router.put("/grading-batches/{batch_id}/question-assignments/{question_id}")
def assign_grading_question(
    batch_id: uuid.UUID,
    question_id: uuid.UUID,
    data: QuestionAssignmentInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    question = db.scalar(
        select(Question).where(
            Question.id == question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    )
    if question is None:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    row = db.scalar(
        select(GradingQuestionAssignment).where(
            GradingQuestionAssignment.grading_batch_id == batch.id,
            GradingQuestionAssignment.question_id == question.id,
        )
    )
    previous_assignee = row.assignee_id if row else None
    if data.assignee_id is None:
        if row is not None:
            db.delete(row)
    else:
        collaborator = _active_collaborator(db, batch.assignment_id, data.assignee_id)
        if collaborator is None:
            raise ApiProblem(422, "COLLABORATOR_REQUIRED", "请先添加该教师为协作者")
        if row is None:
            db.add(
                GradingQuestionAssignment(
                    grading_batch_id=batch.id,
                    question_id=question.id,
                    assignee_id=data.assignee_id,
                    assigned_by=actor.id,
                )
            )
        else:
            row.assignee_id, row.assigned_by = data.assignee_id, actor.id
    audit(
        db,
        actor.id,
        "grading.question.assign",
        "question",
        question.id,
        {
            "grading_batch_id": str(batch.id),
            "previous_assignee_id": str(previous_assignee) if previous_assignee else None,
            "assignee_id": str(data.assignee_id) if data.assignee_id else None,
        },
    )
    db.commit()
    return _collaboration_json(db, batch, actor.id)


@router.put("/assignments/{assignment_id}/joint-question-assignments/{question_id}")
def assign_joint_grading_question(
    assignment_id: uuid.UUID,
    question_id: uuid.UUID,
    data: QuestionAssignmentInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    assignment = db.scalar(
        select(Assignment)
        .where(Assignment.id == assignment_id, Assignment.owner_id == actor.id)
        .with_for_update()
    )
    if assignment is None:
        raise ApiProblem(404, "ASSIGNMENT_NOT_FOUND", "作业不存在")
    if assignment.delivery_mode != "joint_exam":
        raise ApiProblem(409, "NOT_JOINT_EXAM", "该作业不是联考统批")
    question = db.scalar(
        select(Question).where(
            Question.id == question_id,
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    )
    if question is None:
        raise ApiProblem(404, "QUESTION_NOT_FOUND", "题目不存在")
    if (
        data.assignee_id is not None
        and _active_collaborator(db, assignment.id, data.assignee_id) is None
    ):
        raise ApiProblem(422, "COLLABORATOR_REQUIRED", "请先添加该教师为协作者")
    batches = list(
        db.scalars(
            select(GradingBatch)
            .where(
                GradingBatch.assignment_id == assignment.id,
                GradingBatch.owner_id == actor.id,
                GradingBatch.status != "archived",
            )
            .order_by(GradingBatch.created_at, GradingBatch.id)
        )
    )
    if not batches:
        raise ApiProblem(409, "JOINT_GRADING_POOL_REQUIRED", "请先创建联考统批池")
    previous_assignees: dict[str, str | None] = {}
    for batch in batches:
        row = db.scalar(
            select(GradingQuestionAssignment).where(
                GradingQuestionAssignment.grading_batch_id == batch.id,
                GradingQuestionAssignment.question_id == question.id,
            )
        )
        previous_assignees[str(batch.id)] = str(row.assignee_id) if row else None
        if data.assignee_id is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(
                GradingQuestionAssignment(
                    grading_batch_id=batch.id,
                    question_id=question.id,
                    assignee_id=data.assignee_id,
                    assigned_by=actor.id,
                )
            )
        else:
            row.assignee_id, row.assigned_by = data.assignee_id, actor.id
    audit(
        db,
        actor.id,
        "grading.joint_question.assign",
        "question",
        question.id,
        {
            "assignment_id": str(assignment.id),
            "batch_ids": [str(batch.id) for batch in batches],
            "previous_assignees": previous_assignees,
            "assignee_id": str(data.assignee_id) if data.assignee_id else None,
        },
    )
    db.commit()
    return _joint_pool_json(db, assignment, batches)


@router.get("/grading-batches/{batch_id}/review-workspace")
def review_workspace(
    batch_id: uuid.UUID,
    db: Db,
    actor: Actor,
    storage: Storage,
    question_id: uuid.UUID | None = None,
    submission_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    batch, is_owner, assigned_question_ids = _reviewable_batch(db, actor.id, batch_id)
    assignment = db.get(Assignment, batch.assignment_id)
    submission_filters: list[Any] = [
        Submission.grading_batch_id == batch.id,
        Submission.owner_id == batch.owner_id,
    ]
    if submission_id:
        submission_filters.append(Submission.id == submission_id)
    submissions = db.scalars(select(Submission).where(*submission_filters)).all()
    items: list[dict[str, Any]] = []
    for submission in submissions:
        pages = db.scalars(
            select(SubmissionPage)
            .where(SubmissionPage.submission_id == submission.id)
            .order_by(SubmissionPage.page_number)
        ).all()
        answer_filters: list[Any] = [StudentAnswer.submission_id == submission.id]
        if not is_owner:
            answer_filters.append(StudentAnswer.question_id.in_(assigned_question_ids))
        if question_id:
            answer_filters.append(StudentAnswer.question_id == question_id)
        answers = db.scalars(select(StudentAnswer).where(*answer_filters)).all()
        answer_items: list[dict[str, Any]] = []
        allowed_page_ids: set[uuid.UUID] = set()
        for answer in answers:
            question = db.get(Question, answer.question_id)
            result = db.scalar(
                select(GradingResult)
                .where(
                    GradingResult.student_answer_id == answer.id,
                    GradingResult.status != "superseded",
                )
                .order_by(GradingResult.created_at.desc())
            )
            review = db.scalar(
                select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
            )
            criteria = (
                db.scalars(
                    select(GradingCriterionResult).where(
                        GradingCriterionResult.grading_result_id == result.id
                    )
                ).all()
                if result
                else []
            )
            evidence = (
                db.scalars(
                    select(GradingEvidence).where(GradingEvidence.grading_result_id == result.id)
                ).all()
                if result
                else []
            )
            grading_job = db.get(GradingJob, result.grading_job_id) if result else None
            criterion_rubric_items = {
                item.rubric_item_id: db.get(RubricItem, item.rubric_item_id) for item in criteria
            }
            regions = db.scalars(
                select(StudentAnswerRegion).where(
                    StudentAnswerRegion.student_answer_id == answer.id
                )
            ).all()
            allowed_page_ids.update(item.submission_page_id for item in regions)
            allowed_page_ids.update(item.submission_page_id for item in evidence)
            answer_items.append(
                {
                    **answer_json(answer),
                    "question": {
                        "id": str(question.id) if question else str(answer.question_id),
                        "number": question.question_number if question else "?",
                        "type": question.question_type if question else "unknown",
                        "content": question.content_text if question else None,
                        "max_score": str(question.max_score)
                        if question and question.max_score
                        else None,
                    },
                    "result": {
                        "id": str(result.id),
                        "status": result.status,
                        "rubric_version_id": str(result.rubric_version_id),
                        "score": str(result.score) if result.score is not None else None,
                        "provider": result.provider,
                        "provider_version": result.provider_version,
                        "confidence": str(result.confidence)
                        if result.confidence is not None
                        else None,
                        "requires_review": result.requires_review,
                        "reasoning": result.reasoning_summary,
                        "quality_flags": (
                            [grading_job.error_code]
                            if grading_job is not None and grading_job.error_code
                            else []
                        ),
                    }
                    if result
                    else None,
                    "review": {
                        "decision": review.decision,
                        "final_score": str(review.final_score)
                        if review.final_score is not None
                        else None,
                        "feedback": review.final_feedback,
                        "error_type": review.final_error_type,
                        "reviewer_id": str(review.reviewer_id),
                        "review_version": review.review_version,
                    }
                    if review
                    else None,
                    "criteria": [
                        {
                            "rubric_item_id": str(item.rubric_item_id),
                            "title": (
                                rubric_item.title
                                if (rubric_item := criterion_rubric_items.get(item.rubric_item_id))
                                is not None
                                else None
                            ),
                            "description": (
                                rubric_item_for_description.description
                                if (
                                    rubric_item_for_description := criterion_rubric_items.get(
                                        item.rubric_item_id
                                    )
                                )
                                is not None
                                else None
                            ),
                            "status": item.status,
                            "awarded_points": str(item.awarded_points)
                            if item.awarded_points is not None
                            else None,
                            "max_points": str(item.max_points),
                            "reason": item.reason,
                            "evidence_quotes": list(
                                dict.fromkeys(
                                    evidence_item.quote
                                    for evidence_item in evidence
                                    if evidence_item.quote
                                    and (
                                        not (evidence_item.description or "").startswith(
                                            "rubric_items:"
                                        )
                                        or str(item.rubric_item_id)
                                        in (evidence_item.description or "")
                                        .removeprefix("rubric_items:")
                                        .split(",")
                                    )
                                )
                            ),
                        }
                        for item in criteria
                    ],
                    "evidence": [
                        {
                            "id": str(item.id),
                            "submission_page_id": str(item.submission_page_id),
                            "quote": item.quote,
                            "x": str(item.x) if item.x is not None else None,
                            "y": str(item.y) if item.y is not None else None,
                            "width": str(item.width) if item.width is not None else None,
                            "height": str(item.height) if item.height is not None else None,
                        }
                        for item in evidence
                    ],
                    "regions": [
                        {
                            "id": str(item.id),
                            "submission_page_id": str(item.submission_page_id),
                            "source": item.source,
                            "status": item.status,
                            "confidence": str(item.confidence)
                            if item.confidence is not None
                            else None,
                            "x": str(item.x),
                            "y": str(item.y),
                            "width": str(item.width),
                            "height": str(item.height),
                        }
                        for item in regions
                    ],
                }
            )
        # A scoped grader must not be able to enumerate submissions or pages for
        # which no assigned answer is present. Missing answer-to-page evidence is
        # deliberately fail-closed: usability must not expose the whole script.
        if not is_owner and not answer_items:
            continue
        page_items: list[dict[str, Any]] = []
        for page in pages:
            if not is_owner and page.id not in allowed_page_ids:
                continue
            original = db.get(StoredFile, page.stored_file_id)
            page_items.append(
                {
                    "id": str(page.id),
                    "page_number": page.page_number,
                    "status": page.status,
                    # A SubmissionPage may reference the multi-page source PDF.
                    # Only the owner receives that URL; scoped graders receive
                    # page-specific processed/thumbnail evidence at most.
                    "original_url": storage.presigned_get(original.storage_key, 900)
                    if is_owner and original
                    else None,
                    "processed_url": storage.presigned_get(page.processed_storage_key, 900)
                    if page.processed_storage_key
                    else None,
                    "thumbnail_url": storage.presigned_get(page.thumbnail_storage_key, 900)
                    if page.thumbnail_storage_key
                    else None,
                }
            )
        items.append(
            {
                "submission_id": str(submission.id),
                "student_id": str(submission.student_id) if submission.student_id else None,
                "status": submission.status,
                "pages": page_items,
                "answers": answer_items,
            }
        )
    _apply_consistency_quality_flags(items)
    total_answers = sum(len(item["answers"]) for item in items)
    reviewed_answers = sum(
        sum(
            answer["review"] is not None
            and answer["review"]["final_score"] is not None
            and not answer["requires_review"]
            for answer in item["answers"]
        )
        for item in items
    )
    batch_payload = batch_json(db, batch)
    if not is_owner:
        matching = batch_payload["matching"]
        batch_payload["matching"] = {
            "total": matching["total"],
            "confirmed": matching["confirmed"],
            "ambiguous": matching["ambiguous"],
            "unmatched": matching["unmatched"],
            "items": [],
            "student_options": [],
        }
        batch_payload["actions"] = ["grade"]
    return {
        "batch": batch_payload,
        "items": items,
        "progress": {"total": total_answers, "reviewed": reviewed_answers},
        "provider_notice": (
            "主观题由 Codex 根据已确认的答题内容、参考答案和评分标准生成建议；教师负责复核与定稿"
        ),
        "collaboration": _collaboration_json(db, batch, actor.id),
        "joint_navigation": (
            {
                "assignment_id": str(batch.assignment_id),
                "batches": [
                    {
                        "id": str(joint_batch.id),
                        "class_id": str(joint_batch.class_id),
                        "class_name": class_name,
                    }
                    for joint_batch, class_name in db.execute(
                        select(GradingBatch, SchoolClass.name)
                        .join(SchoolClass, SchoolClass.id == GradingBatch.class_id)
                        .where(
                            GradingBatch.assignment_id == batch.assignment_id,
                            GradingBatch.status != "archived",
                        )
                        .order_by(SchoolClass.name, GradingBatch.id)
                    )
                ],
            }
            if assignment is not None and assignment.delivery_mode == "joint_exam"
            else None
        ),
    }


@router.post("/grading-batches/{batch_id}/bulk-accept")
def bulk_accept(batch_id: uuid.UUID, data: BulkAcceptInput, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    assignment = owned_assignment(db, actor.id, batch.assignment_id)
    answers = db.scalars(
        select(StudentAnswer)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .where(
            StudentAnswer.id.in_(data.answer_ids),
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == actor.id,
        )
    ).all()
    if len(answers) != len(set(data.answer_ids)):
        raise ApiProblem(422, "BULK_ACCEPT_SCOPE_INVALID", "答案列表包含不存在或越权项目")
    excluded: list[dict[str, Any]] = []
    accepted: list[str] = []
    for answer in answers:
        reasons, result = acceptance_eligibility(db, answer, assignment)
        if reasons or result is None:
            excluded.append({"answer_id": str(answer.id), "reasons": reasons})
            continue
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        if review is None:
            review = TeacherReview(
                student_answer_id=answer.id,
                grading_result_id=result.id,
                reviewer_id=actor.id,
                decision="accepted",
                final_score=result.score,
                confirmed_at=now_utc(),
            )
            db.add(review)
        result.status = "accepted"
        accepted.append(str(answer.id))
    audit(
        db,
        actor.id,
        "grading.bulk_accept",
        "grading_batch",
        batch.id,
        {"accepted_count": len(accepted), "excluded_count": len(excluded)},
    )
    db.commit()
    return {"accepted_answer_ids": accepted, "excluded": excluded}


@router.get("/grading-batches/{batch_id}/questions/{question_id}/consistency")
def question_consistency(
    batch_id: uuid.UUID,
    question_id: uuid.UUID,
    db: Db,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    rows = db.execute(
        select(StudentAnswer, GradingResult)
        .join(Submission, Submission.id == StudentAnswer.submission_id)
        .join(GradingResult, GradingResult.student_answer_id == StudentAnswer.id)
        .where(
            Submission.grading_batch_id == batch.id,
            Submission.owner_id == actor.id,
            StudentAnswer.question_id == question_id,
            GradingResult.status.in_(["suggested", "accepted", "modified"]),
        )
        .order_by(StudentAnswer.id)
    ).all()
    grouped: dict[str, list[tuple[StudentAnswer, GradingResult]]] = {}
    for answer, result in rows:
        normalized = _normalized_consistency_answer(_effective_answer_content(answer))
        grouped.setdefault(normalized, []).append((answer, result))
    items: list[dict[str, Any]] = []
    for normalized, group in grouped.items():
        scores = {str(result.score) for _, result in group}
        errors = {result.error_type for _, result in group}
        rubric_versions = {str(result.rubric_version_id) for _, result in group}
        criterion_signatures = {
            tuple(
                (str(item.rubric_item_id), str(item.awarded_points))
                for item in db.scalars(
                    select(GradingCriterionResult)
                    .where(GradingCriterionResult.grading_result_id == result.id)
                    .order_by(GradingCriterionResult.rubric_item_id)
                )
            )
            for _, result in group
        }
        items.append(
            {
                "normalized_answer": normalized,
                "answer_ids": [str(answer.id) for answer, _ in group],
                "rubric_version_ids": sorted(rubric_versions),
                "scores": sorted(scores),
                "score_difference": len(scores) > 1 and len(rubric_versions) == 1,
                "error_type_difference": len(errors) > 1 and len(rubric_versions) == 1,
                "criterion_difference": len(criterion_signatures) > 1 and len(rubric_versions) == 1,
                "requires_review": len(rubric_versions) == 1
                and (len(scores) > 1 or len(errors) > 1 or len(criterion_signatures) > 1),
            }
        )
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


@router.post("/grading-batches/{batch_id}/regrade")
def regrade_batch(batch_id: uuid.UUID, data: RegradeInput, db: Db, actor: Actor) -> dict[str, Any]:
    batch = owned_batch(db, actor.id, batch_id)
    filters: list[Any] = [Submission.grading_batch_id == batch.id, Submission.owner_id == actor.id]
    if data.submission_ids:
        filters.append(Submission.id.in_(data.submission_ids))
    if data.question_id:
        filters.append(StudentAnswer.question_id == data.question_id)
    if data.only_stale:
        filters.append(StudentAnswer.status == "stale")
    answers = db.scalars(select(StudentAnswer).join(Submission).where(*filters)).all()
    if data.only_unreviewed:
        reviewed_ids = set(db.scalars(select(TeacherReview.student_answer_id)).all())
        answers = [answer for answer in answers if answer.id not in reviewed_ids]
    result_ids: list[str] = []
    for answer in answers:
        outcome = grade_answer(answer.id, db, actor)
        result_ids.append(outcome["id"])
    audit(db, actor.id, "grading.regrade", "grading_batch", batch.id, {"count": len(result_ids)})
    db.commit()
    return {"count": len(result_ids), "grading_result_ids": result_ids}


@router.post("/submissions/{submission_id}/finalize")
def finalize_submission(submission_id: uuid.UUID, db: Db, actor: Actor) -> dict[str, Any]:
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == actor.id)
        .with_for_update()
    )
    if submission is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    if submission.status == "finalized" or submission.finalized_at is not None:
        raise ApiProblem(409, "SUBMISSION_FINALIZED", "已完成提交只读")
    assignment = owned_assignment(db, actor.id, submission.assignment_id)
    questions = db.scalars(
        select(Question).where(
            Question.paper_version_id == assignment.active_paper_version_id,
            Question.status == QuestionStatus.active,
        )
    ).all()
    answers = db.scalars(
        select(StudentAnswer).where(StudentAnswer.submission_id == submission.id)
    ).all()
    by_q = {a.question_id: a for a in answers}
    problems: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    total = Decimal("0")
    maximum = Decimal("0")
    active_rubric = (
        db.get(RubricVersion, assignment.active_rubric_version_id)
        if assignment.active_rubric_version_id
        else None
    )
    if active_rubric is None or active_rubric.status != "confirmed":
        problems.append({"code": "RUBRIC_VERSION_NOT_CONFIRMED", "question_id": ""})
    for q in questions:
        if q.max_score is None or Decimal(q.max_score) <= 0:
            problems.append({"code": "QUESTION_SCORE_REQUIRED", "question_id": str(q.id)})
            continue
        maximum += Decimal(q.max_score)
        answer = by_q.get(q.id)
        if answer is None:
            problems.append({"code": "ANSWER_MISSING", "question_id": str(q.id)})
            continue
        review = db.scalar(
            select(TeacherReview).where(TeacherReview.student_answer_id == answer.id)
        )
        if review is None or review.final_score is None or answer.requires_review:
            problems.append({"code": "REVIEW_REQUIRED", "question_id": str(q.id)})
            continue
        if review.final_score < 0 or review.final_score > Decimal(q.max_score):
            problems.append({"code": "SCORE_OUT_OF_RANGE", "question_id": str(q.id)})
            continue
        current_question_rubric = db.scalar(
            select(QuestionRubric).where(
                QuestionRubric.rubric_version_id == assignment.active_rubric_version_id,
                QuestionRubric.question_id == q.id,
            )
        )
        if current_question_rubric is None:
            problems.append({"code": "RUBRIC_INCOMPLETE", "question_id": str(q.id)})
            continue
        latest_result = db.scalar(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status != "superseded",
            )
            .order_by(GradingResult.created_at.desc())
        )
        manually_reconfirmed = (
            review.decision in {"modified", "manual_scored"}
            and review.confirmed_at is not None
            and active_rubric is not None
            and review.confirmed_at >= active_rubric.created_at
        )
        if (
            latest_result
            and (
                latest_result.status == "stale"
                or latest_result.rubric_version_id != assignment.active_rubric_version_id
            )
            and not manually_reconfirmed
        ):
            problems.append({"code": "STALE_RUBRIC", "question_id": str(q.id)})
            continue
        active_result = db.scalar(
            select(GradingResult)
            .where(
                GradingResult.student_answer_id == answer.id,
                GradingResult.status.in_(["accepted", "modified"]),
            )
            .order_by(GradingResult.created_at.desc())
        )
        if active_result and active_result.rubric_version_id != assignment.active_rubric_version_id:
            problems.append({"code": "STALE_RUBRIC", "question_id": str(q.id)})
            continue
        total += Decimal(review.final_score)
        details.append(
            {
                "question_id": str(q.id),
                "question_number": q.question_number,
                "question_type": q.question_type,
                "student_answer_id": str(answer.id),
                "teacher_review_id": str(review.id),
                "score": str(review.final_score),
                "max_score": str(q.max_score),
                "error_type": review.final_error_type,
                "feedback": review.final_feedback,
                "final_error_type": review.final_error_type,
                "final_feedback": review.final_feedback,
                "knowledge_point_ids": [
                    str(value)
                    for value in db.scalars(
                        select(QuestionKnowledgePoint.knowledge_point_id).where(
                            QuestionKnowledgePoint.question_id == q.id
                        )
                    )
                ],
                "grading_method": active_result.grading_method if active_result else "manual",
                "finalized_at": now_utc().isoformat(),
            }
        )
    version = (
        db.scalar(
            select(func.max(SubmissionScoreSnapshot.version)).where(
                SubmissionScoreSnapshot.submission_id == submission.id
            )
        )
        or 0
    ) + 1
    if (
        not submission.student_id
        or not assignment.active_paper_version_id
        or not assignment.active_rubric_version_id
    ):
        problems.append({"code": "SUBMISSION_VERSION_INCOMPLETE", "question_id": ""})
    status = "incomplete" if problems else "complete"
    snapshot = SubmissionScoreSnapshot(
        submission_id=submission.id,
        assignment_id=assignment.id,
        student_id=submission.student_id,
        paper_version_id=assignment.active_paper_version_id,
        rubric_version_id=assignment.active_rubric_version_id,
        total_score=total if not problems else None,
        max_score=maximum,
        status=status,
        generated_by=actor.id,
        version=version,
        details=details,
    )
    db.add(snapshot)
    if not problems:
        submission.status, submission.finalized_at = "finalized", now_utc()
    audit(
        db,
        actor.id,
        "submission.finalize",
        "submission",
        submission.id,
        {"snapshot_status": status, "problems": problems},
    )
    db.commit()
    db.refresh(snapshot)
    return {
        "id": str(snapshot.id),
        "submission_id": str(submission.id),
        "status": status,
        "version": version,
        "total_score": str(snapshot.total_score) if snapshot.total_score is not None else None,
        "max_score": str(maximum),
        "details": details,
        "problems": problems,
    }


@router.post("/submissions/{submission_id}/reopen")
def reopen_submission(
    submission_id: uuid.UUID,
    data: ReopenSubmissionInput,
    db: Db,
    actor: Actor,
) -> dict[str, Any]:
    submission = db.scalar(
        select(Submission)
        .where(Submission.id == submission_id, Submission.owner_id == actor.id)
        .with_for_update()
    )
    if submission is None:
        raise ApiProblem(404, "SUBMISSION_NOT_FOUND", "提交不存在")
    if submission.status != "finalized" or submission.finalized_at is None:
        raise ApiProblem(409, "SUBMISSION_NOT_FINALIZED", "只有已完成提交可以重新打开")
    latest_snapshot = db.scalar(
        select(SubmissionScoreSnapshot)
        .where(
            SubmissionScoreSnapshot.submission_id == submission.id,
            SubmissionScoreSnapshot.status == "complete",
        )
        .order_by(
            SubmissionScoreSnapshot.version.desc(),
            SubmissionScoreSnapshot.generated_at.desc(),
        )
        .with_for_update()
    )
    if latest_snapshot is None:
        raise ApiProblem(409, "COMPLETE_SNAPSHOT_MISSING", "已完成提交缺少完整成绩快照")
    submission.status = "reviewed"
    submission.finalized_at = None
    audit(
        db,
        actor.id,
        "submission.reopen",
        "submission",
        submission.id,
        {
            "reason": data.reason.strip(),
            "previous_snapshot_id": str(latest_snapshot.id),
            "previous_snapshot_version": latest_snapshot.version,
        },
    )
    db.commit()
    return {
        "submission_id": str(submission.id),
        "status": submission.status,
        "previous_snapshot_id": str(latest_snapshot.id),
        "previous_snapshot_version": latest_snapshot.version,
    }


@router.get("/assignments/{assignment_id}/score-snapshots")
def score_snapshots(
    assignment_id: uuid.UUID,
    db: Db,
    actor: Actor,
    status: Literal["complete", "incomplete"] = "complete",
) -> list[dict[str, Any]]:
    owned_assignment(db, actor.id, assignment_id)
    rows = db.scalars(
        select(SubmissionScoreSnapshot)
        .join(Submission, Submission.id == SubmissionScoreSnapshot.submission_id)
        .where(
            SubmissionScoreSnapshot.assignment_id == assignment_id,
            Submission.owner_id == actor.id,
            SubmissionScoreSnapshot.status == status,
        )
        .order_by(SubmissionScoreSnapshot.generated_at.desc())
    ).all()
    return [
        {
            "id": str(x.id),
            "submission_id": str(x.submission_id),
            "student_id": str(x.student_id),
            "paper_version_id": str(x.paper_version_id),
            "rubric_version_id": str(x.rubric_version_id),
            "total_score": str(x.total_score) if x.total_score is not None else None,
            "max_score": str(x.max_score),
            "status": x.status,
            "version": x.version,
            "details": x.details,
        }
        for x in rows
    ]

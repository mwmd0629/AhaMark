from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assignment_generation.snapshot import canonical_hash
from app.models import (
    AssignmentAnswerDraftCandidate,
    AssignmentDraftRevision,
    AssignmentGenerationJob,
    AssignmentQuestionExtractionCandidate,
    AssignmentRubricCriterionDraft,
    AssignmentRubricDraftCandidate,
    AssignmentRubricValidationResult,
    Question,
    ReferenceAnswerVersion,
    RubricCriterion,
    StructuredRubricVersion,
    now_utc,
)
from app.question_versions import question_version_token
from app.semantic_content import (
    reference_answer_semantic_payload,
    semantic_hash,
    semantic_normalize,
    structured_rubric_semantic_payload,
)

ANSWER_SOURCES = {
    "teacher_official",
    "publisher_official",
    "teacher_provided",
    "third_party",
    "ai_generated",
    "unknown",
}
SCORING_MODES = {"deterministic", "ai_suggestion", "hybrid", "manual_only"}
CRITERION_TYPES = {
    "result",
    "method",
    "step",
    "reasoning",
    "proof",
    "format",
    "unit",
    "precision",
    "other",
}
VALIDATION_RULES = {
    "exact_scalar",
    "approximate_scalar",
    "vector",
    "matrix",
    "polynomial",
    "linear_system",
    "parameter_solution_set",
    "subspace",
    "subspace_basis",
    "characteristic_polynomial",
    "minimal_polynomial",
    "eigenvalue_multiset",
    "eigenvector",
    "eigenspace",
    "ap_pd",
    "pdp_inverse",
    "manual_only",
}
MANUAL_PATTERNS = re.compile(r"(?:证明|proof|Jordan|约旦|Smith|史密斯|作图|开放题)", re.I)
INJECTION_PATTERNS = re.compile(
    r"(?:忽略.{0,20}(?:系统|rubric|评分)|自动发布|给满分|ignore.{0,20}(?:system|rubric)|final_score)",
    re.I,
)
VALIDATOR_VERSION = "assignment-rubric-validator-v1"


class MaterializationConflict(ValueError):
    code = "MATERIALIZATION_CONTEXT_CONFLICT"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["question", "page", "region", "block", "file", "derived"]
    reference_id: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=240)


class AlternativeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=12000)
    relation: Literal["mathematically_equivalent", "format_equivalent", "candidate"]
    equivalence_status: Literal["verified", "indeterminate"] = "indeterminate"


class CriterionDraftSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(None, max_length=4000)
    points: Decimal | None = Field(None, ge=0, le=1000000)
    criterion_type: Literal[
        "result", "method", "step", "reasoning", "proof", "format", "unit", "precision", "other"
    ]
    required: bool = True
    dependency_keys: list[str] = Field(default_factory=list, max_length=30)
    alternative_group: str | None = Field(None, max_length=80)
    partial_credit_rule: dict[str, Any] = Field(default_factory=dict)
    deduction_rule: dict[str, Any] = Field(default_factory=dict)
    validation_rule: dict[str, Any] = Field(default_factory=dict)
    common_error_codes: list[str] = Field(default_factory=list, max_length=30)
    feedback_template: str | None = Field(None, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=30)
    degradation_reason: str | None = Field(None, max_length=500)
    manual_required: bool = False

    @field_validator("dependency_keys")
    @classmethod
    def dependency_shape(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", item) for item in value
        ):
            raise ValueError("invalid dependency keys")
        return value


class ProviderCriterionDraftSchema(CriterionDraftSchema):
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=30)
    degradation_reason: str | None = Field(max_length=500)


class AnswerRubricProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_content: str | None = Field(None, max_length=20000)
    normalized_content: str | None = Field(None, max_length=20000)
    structured_content: dict[str, Any] = Field(default_factory=dict)
    alternative_answers: list[AlternativeAnswer] = Field(default_factory=list, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    requested_scoring_mode: Literal["deterministic", "ai_suggestion", "hybrid", "manual_only"]
    total_points: Decimal | None = Field(gt=0, le=1000000)
    allow_partial_credit: bool = True
    domain_requirements: dict[str, Any] = Field(default_factory=dict)
    validation_config: dict[str, Any] = Field(default_factory=dict)
    common_error_types: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    feedback_templates: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=30)
    degradation_reason: str | None = Field(max_length=500)
    warning_codes: list[str] = Field(default_factory=list, max_length=30)
    criteria: list[ProviderCriterionDraftSchema] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def reject_privileged_fields(self) -> AnswerRubricProviderOutput:
        degradation_reason = (self.degradation_reason or "").strip()
        if self.requested_scoring_mode != "deterministic" and not degradation_reason:
            raise ValueError("non-deterministic output requires degradation_reason")
        if self.requested_scoring_mode == "deterministic" and degradation_reason:
            raise ValueError("deterministic output cannot declare degradation_reason")
        if self.requested_scoring_mode == "deterministic":
            answer_type = self.validation_config.get("answer_type")
            if answer_type not in VALIDATION_RULES - {"manual_only"}:
                raise ValueError("deterministic output requires a supported answer_type")
            if self.total_points is None:
                raise ValueError("deterministic output requires total_points")
            for criterion in self.criteria:
                if criterion.manual_required or (criterion.degradation_reason or "").strip():
                    raise ValueError("deterministic criterion cannot be degraded")
                criterion_answer_type = criterion.validation_rule.get("answer_type")
                if criterion_answer_type not in VALIDATION_RULES - {"manual_only"}:
                    raise ValueError(
                        "deterministic criterion requires a supported validation_rule answer_type"
                    )
        # extra=forbid is the actual privileged-field boundary.
        return self


@dataclass(frozen=True)
class StructuralValidation:
    valid: bool
    blocking: list[str]
    warnings: list[str]
    effective_points: Decimal | None


def question_version(question: Question) -> str:
    return f"{question.paper_version_id}:{question.id}:{question.updated_at.isoformat()}"


def route_scoring_mode(
    question: Question, output: AnswerRubricProviderOutput
) -> tuple[str, bool, list[str]]:
    if output.requested_scoring_mode != "deterministic":
        if not (output.degradation_reason or "").strip():
            return "manual_only", True, ["PROVIDER_DEGRADATION_REASON_REQUIRED"]
        warnings = ["PROVIDER_NON_DETERMINISTIC_MODE"]
        if output.requested_scoring_mode in {"manual_only", "hybrid"}:
            warnings.append("MANUAL_RUBRIC_REQUIRED")
        return output.requested_scoring_mode, True, warnings

    if (output.degradation_reason or "").strip() or any(
        item.manual_required or (item.degradation_reason or "").strip() for item in output.criteria
    ):
        return "manual_only", True, ["PROVIDER_DETERMINISTIC_CONTRACT_INVALID"]

    text = " ".join(
        filter(None, [question.question_type, question.content_text, question.content_latex])
    )
    answer_type = str(output.validation_config.get("answer_type", ""))
    if MANUAL_PATTERNS.search(text) or answer_type in {"jordan", "smith", "proof", "manual_only"}:
        return "manual_only", True, ["MANUAL_RUBRIC_REQUIRED"]
    if answer_type not in VALIDATION_RULES - {"manual_only"}:
        return "ai_suggestion", True, ["RUBRIC_VALIDATION_CONFIG_INVALID"]
    if any(
        item.manual_required or item.criterion_type in {"proof", "reasoning"}
        for item in output.criteria
    ):
        return "hybrid", True, ["MANUAL_RUBRIC_REQUIRED"]
    return "deterministic", False, []


def validate_candidate_structure(
    total_points: Decimal | None,
    scoring_mode: str,
    criteria: Sequence[CriterionDraftSchema | AssignmentRubricCriterionDraft],
) -> StructuralValidation:
    blocking: list[str] = []
    warnings: list[str] = []
    keys = [item.criterion_key for item in criteria]
    orders = [getattr(item, "display_order", index) for index, item in enumerate(criteria)]
    if (
        len(keys) != len(set(keys))
        or any(not key for key in keys)
        or len(orders) != len(set(orders))
    ):
        blocking.append("RUBRIC_SCHEMA_INVALID")
    graph: dict[str, list[str]] = {}
    plain = set(keys)
    non_group = Decimal("0")
    groups: dict[str, list[Decimal]] = {}
    for item in criteria:
        key = item.criterion_key
        deps = list(item.dependency_keys)
        graph[key] = deps
        if any(dep not in plain for dep in deps):
            blocking.append("RUBRIC_DEPENDENCY_MISSING")
        value = item.points
        if value is None:
            continue
        points = Decimal(value)
        group = item.alternative_group
        if group:
            groups.setdefault(group, []).append(points)
        else:
            non_group += points
        partial = dict(item.partial_credit_rule or {})
        deduction = dict(item.deduction_rule or {})
        try:
            if "max_points" in partial and Decimal(str(partial["max_points"])) > points:
                blocking.append("RUBRIC_PARTIAL_CREDIT_INVALID")
            if "max_deduction" in deduction and Decimal(str(deduction["max_deduction"])) > points:
                blocking.append("RUBRIC_DEDUCTION_INVALID")
        except Exception:
            blocking.append("RUBRIC_SCHEMA_INVALID")
        rule = dict(item.validation_rule or {})
        answer_type = rule.get("answer_type")
        if answer_type not in VALIDATION_RULES:
            blocking.append("RUBRIC_VALIDATION_CONFIG_INVALID")
        if scoring_mode == "manual_only" and answer_type not in {None, "manual_only"}:
            blocking.append("RUBRIC_VALIDATION_CONFIG_INVALID")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        cycle = any(visit(dep) for dep in graph.get(node, []) if dep in graph)
        visiting.remove(node)
        visited.add(node)
        return cycle

    if any(visit(key) for key in keys):
        blocking.append("RUBRIC_DEPENDENCY_CYCLE")
    effective = non_group + sum((max(values) for values in groups.values()), Decimal("0"))
    if total_points is None:
        blocking.append("RUBRIC_SCORE_REQUIRED")
        effective_value: Decimal | None = None
    else:
        effective_value = effective
        if effective != Decimal(total_points):
            blocking.append("RUBRIC_POINTS_MISMATCH")
        if any(value > Decimal(total_points) for values in groups.values() for value in values):
            blocking.append("RUBRIC_ALTERNATIVE_PATH_CONFLICT")
    return StructuralValidation(
        not blocking, sorted(set(blocking)), sorted(set(warnings)), effective_value
    )


def deterministic_fake_output(question: Question) -> AnswerRubricProviderOutput:
    points = Decimal(question.max_score) if question.max_score is not None else None
    text = (question.content_text or "").strip()
    injection = bool(INJECTION_PATTERNS.search(text))
    manual = bool(MANUAL_PATTERNS.search(" ".join([question.question_type, text])))
    answer_type = "manual_only" if manual else "exact_scalar"
    raw = None if manual else f"测试候选：{text[:300]}"
    warnings = ["PROMPT_INJECTION_CONTENT_DETECTED"] if injection else []
    if manual:
        warnings.append("MANUAL_ANSWER_REQUIRED")
    return AnswerRubricProviderOutput(
        raw_content=raw,
        normalized_content=raw,
        structured_content={"answer_type": answer_type, "value": None, "test_fixture": True},
        alternative_answers=[],
        title=f"{question.question_number} 评分标准草稿",
        requested_scoring_mode="manual_only" if manual else "deterministic",
        total_points=points,
        domain_requirements={},
        validation_config={"answer_type": answer_type, "domain": "real", "limits": {}},
        common_error_types=[],
        feedback_templates={"default": "请核对关键步骤与最终结果。"},
        confidence=0.45 if manual else 0.8,
        evidence=[
            EvidenceRef(kind="question", reference_id=str(question.id), summary="当前已物化题目")
        ],
        degradation_reason="题型需要人工评分" if manual else None,
        warning_codes=warnings,
        criteria=[
            ProviderCriterionDraftSchema(
                criterion_key="result",
                title="结果与过程",
                points=points,
                criterion_type="proof" if manual else "result",
                validation_rule={"answer_type": answer_type, "domain": "real", "limits": {}},
                confidence=0.45 if manual else 0.8,
                evidence=[
                    EvidenceRef(
                        kind="question", reference_id=str(question.id), summary="题目满分与类型"
                    )
                ],
                degradation_reason="题型需要人工评分" if manual else None,
                manual_required=manual,
            )
        ],
    )


def _current_questions(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> list[Question]:
    return list(
        db.scalars(
            select(Question)
            .join(
                AssignmentQuestionExtractionCandidate,
                AssignmentQuestionExtractionCandidate.materialized_question_id == Question.id,
            )
            .where(
                AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                AssignmentQuestionExtractionCandidate.generation_job_id == job.id,
                AssignmentQuestionExtractionCandidate.status.in_(
                    {"suggested", "accepted", "modified"}
                ),
                Question.status == "active",
            )
            .order_by(Question.display_order, Question.id)
        )
    )


def generate_candidates(
    db: Session,
    job: AssignmentGenerationJob,
    revision: AssignmentDraftRevision,
    provider_available: bool,
) -> dict[str, Any]:
    questions = _current_questions(db, job, revision)
    created = 0
    manual = 0
    prompt_injection = False
    for question in questions:
        old_answers = list(
            db.scalars(
                select(AssignmentAnswerDraftCandidate)
                .where(
                    AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
                    AssignmentAnswerDraftCandidate.question_id == question.id,
                    AssignmentAnswerDraftCandidate.status == "suggested",
                )
                .with_for_update()
            )
        )
        for old in old_answers:
            old.status = "superseded"
        version = (
            db.scalar(
                select(
                    func.coalesce(func.max(AssignmentAnswerDraftCandidate.candidate_version), 0)
                ).where(
                    AssignmentAnswerDraftCandidate.draft_revision_id == revision.id,
                    AssignmentAnswerDraftCandidate.question_id == question.id,
                )
            )
            or 0
        ) + 1
        output = deterministic_fake_output(question) if provider_available else None
        if output is None:
            answer = AssignmentAnswerDraftCandidate(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                question_id=question.id,
                question_version=question_version(question),
                candidate_version=version,
                source_type="ai_generated",
                raw_content=None,
                normalized_content=None,
                structured_content={},
                alternative_answers=[],
                provenance={
                    "source_type": "ai_generated",
                    "generation_job_id": str(job.id),
                    "provider": "unavailable",
                    "created_at": now_utc().isoformat(),
                },
                confidence=0,
                evidence=[],
                warning_codes=["ANSWER_GENERATION_UNAVAILABLE", "PROVIDER_UNAVAILABLE"],
                status="manual_required",
                manual_required=True,
                source_snapshot_hash=job.source_snapshot_hash,
            )
            db.add(answer)
            db.flush()
            manual += 1
            continue
        mode, manual_required, route_warnings = route_scoring_mode(question, output)
        warnings = sorted(set(output.warning_codes + route_warnings))
        prompt_injection = prompt_injection or "PROMPT_INJECTION_CONTENT_DETECTED" in warnings
        answer = AssignmentAnswerDraftCandidate(
            owner_id=job.owner_id,
            assignment_id=job.assignment_id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            question_id=question.id,
            question_version=question_version(question),
            candidate_version=version,
            source_type="ai_generated",
            raw_content=output.raw_content,
            normalized_content=output.normalized_content,
            structured_content=output.structured_content,
            alternative_answers=[
                item.model_dump(mode="json") for item in output.alternative_answers
            ],
            provenance={
                "source_type": "ai_generated",
                "generation_job_id": str(job.id),
                "provider": "fake",
                "model": "deterministic-test-only",
                "config_version": job.provider_config_version,
                "created_at": now_utc().isoformat(),
            },
            confidence=output.confidence,
            evidence=[item.model_dump() for item in output.evidence],
            warning_codes=warnings,
            status="suggested" if output.raw_content else "manual_required",
            manual_required=manual_required or output.raw_content is None,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(answer)
        db.flush()
        rubric = AssignmentRubricDraftCandidate(
            owner_id=job.owner_id,
            assignment_id=job.assignment_id,
            generation_job_id=job.id,
            draft_revision_id=revision.id,
            question_id=question.id,
            question_version=question_version(question),
            answer_candidate_id=answer.id,
            candidate_version=version,
            title=output.title,
            scoring_mode=mode,
            total_points=output.total_points,
            allow_partial_credit=output.allow_partial_credit,
            domain_requirements=output.domain_requirements,
            validation_config=output.validation_config,
            common_error_types=output.common_error_types,
            feedback_templates=output.feedback_templates,
            confidence=output.confidence,
            evidence=[item.model_dump() for item in output.evidence],
            warning_codes=warnings,
            status="suggested",
            manual_required=manual_required,
            source_snapshot_hash=job.source_snapshot_hash,
        )
        db.add(rubric)
        db.flush()
        for order, criterion in enumerate(output.criteria):
            db.add(
                AssignmentRubricCriterionDraft(
                    rubric_candidate_id=rubric.id,
                    display_order=order,
                    **criterion.model_dump(exclude={"evidence", "degradation_reason"}),
                    evidence=[item.model_dump() for item in criterion.evidence],
                )
            )
        created += 1
        manual += int(manual_required)
    return {
        "kind": "answer_rubric_candidates",
        "question_count": len(questions),
        "created": created,
        "manual_required": manual,
        "prompt_injection_detected": prompt_injection,
        "draft_only": True,
    }


def validate_revision_candidates(
    db: Session, job: AssignmentGenerationJob, revision: AssignmentDraftRevision
) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(AssignmentRubricDraftCandidate)
            .where(
                AssignmentRubricDraftCandidate.draft_revision_id == revision.id,
                AssignmentRubricDraftCandidate.status.in_({"suggested", "manual_required"}),
            )
            .with_for_update()
        )
    )
    counts = {
        "verified": 0,
        "partially_verified": 0,
        "indeterminate": 0,
        "unsupported": 0,
        "failed": 0,
    }
    for rubric in rows:
        criteria = list(
            db.scalars(
                select(AssignmentRubricCriterionDraft)
                .where(AssignmentRubricCriterionDraft.rubric_candidate_id == rubric.id)
                .order_by(AssignmentRubricCriterionDraft.display_order)
            )
        )
        structural = validate_candidate_structure(
            Decimal(rubric.total_points) if rubric.total_points is not None else None,
            rubric.scoring_mode,
            criteria,
        )
        if rubric.scoring_mode == "manual_only":
            status = "unsupported"
            issues = structural.blocking + ["VALIDATION_UNSUPPORTED", "MANUAL_RUBRIC_REQUIRED"]
        elif structural.blocking:
            status = "failed"
            issues = structural.blocking + ["VALIDATION_FAILED"]
        elif rubric.scoring_mode == "hybrid":
            status = "partially_verified"
            issues = ["VALIDATION_PARTIALLY_VERIFIED"]
        else:
            # Structural/configuration safety is verified. Mathematical correctness/equivalence
            # is intentionally indeterminate until a teacher-approved source is available.
            status = "indeterminate"
            issues = ["VALIDATION_INDETERMINATE", "ALTERNATIVE_ANSWER_EQUIVALENCE_INDETERMINATE"]
        answer = db.get(AssignmentAnswerDraftCandidate, rubric.answer_candidate_id)
        assert answer is not None
        input_value = {
            "rubric": str(rubric.id),
            "answer_hash": canonical_hash(answer.structured_content),
            "criteria": [item.criterion_key for item in criteria],
        }
        output_value = {
            "status": status,
            "issues": sorted(set(issues)),
            "structural_valid": structural.valid,
        }
        db.add(
            AssignmentRubricValidationResult(
                rubric_candidate_id=rubric.id,
                answer_candidate_id=answer.id,
                question_id=rubric.question_id,
                validation_mode=rubric.scoring_mode,
                status=status,
                deterministic_result={
                    "status": "not_claimed"
                    if status in {"indeterminate", "unsupported"}
                    else status,
                    "does_not_score_students": True,
                },
                structural_result={
                    "valid": structural.valid,
                    "blocking": structural.blocking,
                    "effective_points": str(structural.effective_points)
                    if structural.effective_points is not None
                    else None,
                },
                issue_codes=sorted(set(issues)),
                input_hash=canonical_hash(input_value),
                output_hash=canonical_hash(output_value),
                validator_version=VALIDATOR_VERSION,
                started_at=now_utc(),
                completed_at=now_utc(),
            )
        )
        rubric.warning_codes = sorted(set(rubric.warning_codes + issues))
        if status in {"unsupported", "failed"}:
            rubric.manual_required = True
        counts[status] += 1
    return {
        "kind": "answer_rubric_validation",
        "counts": counts,
        "review_required": True,
        "publishes_assignment": False,
        "writes_final_score": False,
    }


def _is_materialization_conflict(
    exc: IntegrityError, constraint_names: set[str], column_names: set[str]
) -> bool:
    diagnostic = getattr(exc.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in constraint_names:
        return True
    message = str(exc.orig).lower()
    return "unique" in message and any(name.lower() in message for name in column_names)


def _answer_semantic_payload(
    candidate: AssignmentAnswerDraftCandidate,
    raw: str,
    normalized: str,
    structured: Any,
) -> dict[str, Any]:
    return reference_answer_semantic_payload(
        source_type=candidate.source_type,
        source_region=candidate.source_region,
        raw_content=raw,
        normalized_content=normalized,
        structured_content=structured,
        alternative_answers=candidate.alternative_answers,
        provenance=candidate.provenance,
    )


def _rubric_semantic_payload(
    candidate: AssignmentRubricDraftCandidate,
    criteria: Sequence[AssignmentRubricCriterionDraft],
    reference_answer_content_hash: str,
) -> dict[str, Any]:
    return structured_rubric_semantic_payload(
        reference_answer_content_hash=reference_answer_content_hash,
        title=candidate.title,
        scoring_mode=candidate.scoring_mode,
        total_points=candidate.total_points,
        allow_partial_credit=candidate.allow_partial_credit,
        domain_requirements=candidate.domain_requirements,
        validation_config=candidate.validation_config,
        common_error_types=candidate.common_error_types,
        feedback_templates=candidate.feedback_templates,
        manual_required=candidate.manual_required,
        criteria=[
            {
                "criterion_key": criterion.criterion_key,
                "display_order": criterion.display_order,
                "title": criterion.title,
                "description": criterion.description,
                "points": str(criterion.points) if criterion.points is not None else None,
                "criterion_type": criterion.criterion_type,
                "required": criterion.required,
                "dependency_keys": semantic_normalize(criterion.dependency_keys),
                "alternative_group": criterion.alternative_group,
                "partial_credit_rule": semantic_normalize(criterion.partial_credit_rule),
                "deduction_rule": semantic_normalize(criterion.deduction_rule),
                "validation_rule": semantic_normalize(criterion.validation_rule),
                "common_error_codes": semantic_normalize(criterion.common_error_codes),
                "feedback_template": criterion.feedback_template,
                "manual_required": criterion.manual_required,
                "evidence": semantic_normalize(criterion.evidence),
            }
            for criterion in criteria
        ],
    )


def _existing_reference_materialization(
    db: Session,
    candidate: AssignmentAnswerDraftCandidate,
    materialization_key: str,
    content_hash: str,
) -> ReferenceAnswerVersion | None:
    if candidate.materialized_reference_answer_id:
        existing = db.get(ReferenceAnswerVersion, candidate.materialized_reference_answer_id)
        if existing is None:
            raise RuntimeError("MATERIALIZED_REFERENCE_ANSWER_NOT_FOUND")
        if existing.origin_answer_candidate_id not in {None, candidate.id}:
            raise MaterializationConflict("REFERENCE_ANSWER_ORIGIN_MISMATCH")
        if (
            existing.materialization_key != materialization_key
            or existing.content_hash != content_hash
        ):
            raise MaterializationConflict("REFERENCE_ANSWER_MATERIALIZATION_DRIFT")
        return existing
    existing = db.scalar(
        select(ReferenceAnswerVersion).where(
            or_(
                ReferenceAnswerVersion.origin_answer_candidate_id == candidate.id,
                ReferenceAnswerVersion.materialization_key == materialization_key,
            )
        )
    )
    if existing is not None and existing.origin_answer_candidate_id not in {None, candidate.id}:
        raise MaterializationConflict("REFERENCE_ANSWER_MATERIALIZATION_KEY_COLLISION")
    if existing is not None and (
        existing.materialization_key != materialization_key or existing.content_hash != content_hash
    ):
        raise MaterializationConflict("REFERENCE_ANSWER_MATERIALIZATION_DRIFT")
    return existing


def _existing_rubric_materialization(
    db: Session,
    candidate: AssignmentRubricDraftCandidate,
    materialization_key: str,
    content_hash: str,
) -> StructuredRubricVersion | None:
    if candidate.materialized_structured_rubric_id:
        existing = db.get(StructuredRubricVersion, candidate.materialized_structured_rubric_id)
        if existing is None:
            raise RuntimeError("MATERIALIZED_STRUCTURED_RUBRIC_NOT_FOUND")
        if existing.origin_rubric_candidate_id not in {None, candidate.id}:
            raise MaterializationConflict("STRUCTURED_RUBRIC_ORIGIN_MISMATCH")
        if (
            existing.materialization_key != materialization_key
            or existing.content_hash != content_hash
        ):
            raise MaterializationConflict("STRUCTURED_RUBRIC_MATERIALIZATION_DRIFT")
        return existing
    existing = db.scalar(
        select(StructuredRubricVersion).where(
            or_(
                StructuredRubricVersion.origin_rubric_candidate_id == candidate.id,
                StructuredRubricVersion.materialization_key == materialization_key,
            )
        )
    )
    if existing is not None and existing.origin_rubric_candidate_id not in {None, candidate.id}:
        raise MaterializationConflict("STRUCTURED_RUBRIC_MATERIALIZATION_KEY_COLLISION")
    if existing is not None and (
        existing.materialization_key != materialization_key or existing.content_hash != content_hash
    ):
        raise MaterializationConflict("STRUCTURED_RUBRIC_MATERIALIZATION_DRIFT")
    return existing


def materialize_reference(
    db: Session, candidate: AssignmentAnswerDraftCandidate, actor_id: uuid.UUID
) -> ReferenceAnswerVersion:
    value = candidate.teacher_value or {}
    raw = str(value.get("raw_content", candidate.raw_content or ""))
    normalized = str(value.get("normalized_content", candidate.normalized_content or raw))
    structured = value.get("structured_content", candidate.structured_content)
    semantic_payload = _answer_semantic_payload(candidate, raw, normalized, structured)
    content_hash = semantic_hash(semantic_payload)
    materialization_payload = {
        "schema": "reference-answer-materialization-v1",
        "candidate_id": str(candidate.id),
        "assignment_id": str(candidate.assignment_id),
        "draft_revision_id": str(candidate.draft_revision_id),
        "question_id": str(candidate.question_id),
        "question_version": candidate.question_version,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "teacher_edit_version": candidate.teacher_edit_version,
        "content_hash": content_hash,
    }
    materialization_key = canonical_hash(materialization_payload)
    existing = _existing_reference_materialization(db, candidate, materialization_key, content_hash)
    if existing is not None:
        candidate.materialized_reference_answer_id = existing.id
        return existing
    version = (
        db.scalar(
            select(func.coalesce(func.max(ReferenceAnswerVersion.version), 0)).where(
                ReferenceAnswerVersion.question_id == candidate.question_id
            )
        )
        or 0
    ) + 1
    item = ReferenceAnswerVersion(
        question_id=candidate.question_id,
        source_type=candidate.source_type,
        source_file=str(candidate.source_file_analysis_id)
        if candidate.source_file_analysis_id
        else None,
        source_page=None,
        source_region=candidate.source_region,
        raw_content=raw,
        normalized_content=normalized,
        structured_content=structured,
        content_hash=content_hash,
        version=version,
        provenance={
            **candidate.provenance,
            (
                "teacher_reviewed_by" if candidate.reviewed_by is not None else "system_prepared_by"
            ): str(actor_id),
            "candidate_id": str(candidate.id),
        },
        created_by=actor_id,
        status="draft",
        origin_answer_candidate_id=candidate.id,
        materialization_key=materialization_key,
    )
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError as exc:
        if not _is_materialization_conflict(
            exc,
            {
                "uq_reference_answer_origin_candidate",
                "uq_reference_answer_materialization_key",
            },
            {"origin_answer_candidate_id", "materialization_key"},
        ):
            raise
        existing = _existing_reference_materialization(
            db, candidate, materialization_key, content_hash
        )
        if existing is None:
            raise
        item = existing
    candidate.materialized_reference_answer_id = item.id
    return item


def materialize_rubric(
    db: Session, candidate: AssignmentRubricDraftCandidate, actor_id: uuid.UUID
) -> StructuredRubricVersion:
    answer = db.get(AssignmentAnswerDraftCandidate, candidate.answer_candidate_id)
    if (
        answer is None
        or answer.status not in {"accepted", "modified", "system_prepared"}
        or answer.materialized_reference_answer_id is None
    ):
        raise ValueError("ANSWER_CANDIDATE_NOT_ACCEPTED")
    question = db.get(Question, candidate.question_id)
    if question is None:
        raise ValueError("QUESTION_NOT_FOUND")
    criteria = list(
        db.scalars(
            select(AssignmentRubricCriterionDraft)
            .where(AssignmentRubricCriterionDraft.rubric_candidate_id == candidate.id)
            .order_by(AssignmentRubricCriterionDraft.display_order)
        )
    )
    structural = validate_candidate_structure(
        Decimal(candidate.total_points) if candidate.total_points is not None else None,
        candidate.scoring_mode,
        criteria,
    )
    if not structural.valid or candidate.total_points is None:
        raise ValueError(structural.blocking[0] if structural.blocking else "RUBRIC_SCORE_REQUIRED")
    reference_answer = db.get(ReferenceAnswerVersion, answer.materialized_reference_answer_id)
    if reference_answer is None:
        raise ValueError("MATERIALIZED_REFERENCE_ANSWER_NOT_FOUND")
    semantic_payload = _rubric_semantic_payload(candidate, criteria, reference_answer.content_hash)
    content_hash = semantic_hash(semantic_payload)
    materialization_payload = {
        "schema": "structured-rubric-materialization-v1",
        "candidate_id": str(candidate.id),
        "assignment_id": str(candidate.assignment_id),
        "draft_revision_id": str(candidate.draft_revision_id),
        "question_id": str(candidate.question_id),
        "question_version": candidate.question_version,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "answer_candidate_id": str(candidate.answer_candidate_id),
        "reference_answer_version_id": str(answer.materialized_reference_answer_id),
        "teacher_edit_version": candidate.teacher_edit_version,
        "content_hash": content_hash,
    }
    materialization_key = canonical_hash(materialization_payload)
    existing = _existing_rubric_materialization(db, candidate, materialization_key, content_hash)
    if existing is not None:
        candidate.materialized_structured_rubric_id = existing.id
        return existing
    version = (
        db.scalar(
            select(func.coalesce(func.max(StructuredRubricVersion.rubric_version), 0)).where(
                StructuredRubricVersion.question_id == candidate.question_id
            )
        )
        or 0
    ) + 1
    item = StructuredRubricVersion(
        question_id=candidate.question_id,
        # Candidate versions also include the question UUID and can exceed the
        # 100-character storage contract for formal rubric versions.
        question_version=question_version_token(question),
        reference_answer_version_id=answer.materialized_reference_answer_id,
        rubric_version=version,
        title=candidate.title,
        total_points=candidate.total_points,
        status="draft",
        content_hash=content_hash,
        created_by=actor_id,
        origin_rubric_candidate_id=candidate.id,
        materialization_key=materialization_key,
    )
    formal_types = {
        "result": "final_answer",
        "method": "method",
        "step": "intermediate_result",
        "reasoning": "justification",
        "proof": "proof_step",
        "format": "presentation",
        "unit": "presentation",
        "precision": "presentation",
        "other": "method",
    }
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
            for criterion in criteria:
                db.add(
                    RubricCriterion(
                        rubric_version_id=item.id,
                        stable_key=criterion.criterion_key,
                        title=criterion.title,
                        description=criterion.description,
                        max_points=criterion.points or Decimal("0"),
                        display_order=criterion.display_order,
                        criterion_type=formal_types[criterion.criterion_type],
                        required=criterion.required,
                        dependencies=criterion.dependency_keys,
                        expected_evidence={"candidate_evidence": criterion.evidence},
                        validation_mode="manual_only"
                        if candidate.scoring_mode == "manual_only" or criterion.manual_required
                        else "deterministic",
                        manual_review_policy={
                            "required": candidate.manual_required or criterion.manual_required
                        },
                        partial_credit_policy=criterion.partial_credit_rule,
                        error_category=criterion.common_error_codes[0]
                        if criterion.common_error_codes
                        else None,
                        validation_rule=criterion.validation_rule,
                        metadata_={
                            "alternative_group": criterion.alternative_group,
                            "deduction_rule": criterion.deduction_rule,
                            "common_error_codes": criterion.common_error_codes,
                            "feedback_template": criterion.feedback_template,
                            "scoring_mode": candidate.scoring_mode,
                        },
                    )
                )
            db.flush()
    except IntegrityError as exc:
        if not _is_materialization_conflict(
            exc,
            {
                "uq_structured_rubric_origin_candidate",
                "uq_structured_rubric_materialization_key",
            },
            {"origin_rubric_candidate_id", "materialization_key"},
        ):
            raise
        existing = _existing_rubric_materialization(
            db, candidate, materialization_key, content_hash
        )
        if existing is None:
            raise
        item = existing
    candidate.materialized_structured_rubric_id = item.id
    return item

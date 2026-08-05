import uuid
from typing import Any

from app.assignment_generation.answer_rubric import (
    AnswerRubricProviderOutput,
    generate_candidates,
    validate_revision_candidates,
)
from app.assignment_generation.dispatcher import DispatchedProviderResult, dispatch_stage
from app.assignment_generation.extraction_stage import (
    build_fake_candidates,
    build_page_suggestions,
    materialize_draft_questions,
)
from app.assignment_generation.file_analysis import collect_file_analysis
from app.assignment_generation.materializers import (
    ProviderSemanticError,
    materialize_answer,
    materialize_file_analysis,
    materialize_metadata,
    materialize_questions,
    materialize_rubric,
)
from app.assignment_generation.metadata_analysis import deterministic_metadata
from app.assignment_generation.providers import AssignmentProviderResponse, select_provider
from app.assignment_generation.question_extraction import ExtractionOutput
from app.assignment_generation.schemas import FileAnalysisOutput, MetadataProviderOutput
from app.assignment_generation.service import (
    ACTIVE_STATUSES,
    STAGES,
    complete_stage_retry,
    ensure_current,
    has_retryable_stage,
    issue,
    next_stage_generation,
    pages_for_job,
    transition,
    update_risk_summary,
)
from app.assignment_generation.snapshot import canonical_hash
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import (
    Assignment,
    AssignmentDraftRevision,
    AssignmentFieldSuggestion,
    AssignmentGenerationJob,
    AssignmentGenerationProviderInvocation,
    AssignmentPageAnalysis,
    AssignmentQuestionExtractionCandidate,
    AssignmentSourceFileAnalysis,
    GenerationStageResult,
    Question,
    RecognitionBlock,
    RecognitionJob,
    RecognitionStatus,
    StoredFile,
    now_utc,
)
from pydantic import ValidationError
from sqlalchemy import func, select, update

from workers.celery_app import celery_app
from workers.task_context import run_traced_task

PROVIDER_ERROR_CODES = {
    "provider_unavailable": "PROVIDER_UNAVAILABLE",
    "PROVIDER_CONFIGURATION_INCOMPLETE": "PROVIDER_CREDENTIALS_MISSING",
    "provider_configuration_incomplete": "PROVIDER_CREDENTIALS_MISSING",
    "provider_network_error": "PROVIDER_NETWORK_ERROR",
    "provider_timeout": "PROVIDER_TIMEOUT",
    "provider_schema_invalid": "PROVIDER_SCHEMA_INVALID",
    "provider_empty_response": "PROVIDER_SCHEMA_INVALID",
    "provider_refusal": "PROVIDER_SCHEMA_INVALID",
    "input_token_limit_exceeded": "PROVIDER_OUTPUT_TOO_LARGE",
    "estimated_cost_limit_exceeded": "PROVIDER_COST_LIMIT",
    "actual_cost_limit_exceeded": "PROVIDER_COST_LIMIT",
    "image_count_limit_exceeded": "PROVIDER_IMAGE_LIMIT",
    "image_byte_limit_exceeded": "PROVIDER_IMAGE_LIMIT",
    "total_image_byte_limit_exceeded": "PROVIDER_IMAGE_LIMIT",
}


def _stable_provider_error(response: AssignmentProviderResponse) -> str | None:
    if response.error is None:
        return None
    if response.error == "http_429":
        return "PROVIDER_RATE_LIMITED"
    if response.error.startswith("http_5"):
        return "PROVIDER_SERVER_ERROR"
    return PROVIDER_ERROR_CODES.get(response.error, response.error.upper())


def _record_invocation(
    db: Any,
    job: AssignmentGenerationJob,
    row: GenerationStageResult,
    dispatched: DispatchedProviderResult,
) -> AssignmentGenerationProviderInvocation:
    settings = get_settings()
    response = dispatched.response
    error_code = _stable_provider_error(response)
    invocation = AssignmentGenerationProviderInvocation(
        job_id=job.id,
        stage_result_id=row.id,
        provider=dispatched.selection.name,
        model=settings.assignment_generation_model,
        model_snapshot=response.model_snapshot,
        endpoint_mode=dispatched.selection.endpoint_mode,
        provider_config_version=job.provider_config_version,
        prompt_version=job.prompt_version,
        schema_version=job.schema_version,
        stage_generation=row.stage_generation,
        request_hash=response.request_hash or row.input_hash,
        response_hash=response.response_hash,
        provider_request_id=response.request_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        estimated_cost=response.estimated_cost,
        retry_count=max(0, response.attempts - 1),
        image_count=response.image_count,
        image_bytes=response.image_bytes,
        status="completed" if response.output is not None else "failed",
        error_code=error_code,
        error_message=None if response.output is not None else "Provider 调用未产生可物化草稿",
        started_at=row.started_at,
        completed_at=now_utc(),
    )
    db.add(invocation)
    db.flush()
    if row.provider_invocation_id is None:
        row.provider_invocation_id = invocation.id
    return invocation


def _load(
    db: Any, job_id: uuid.UUID
) -> tuple[AssignmentGenerationJob | None, AssignmentDraftRevision | None]:
    job = db.scalar(
        select(AssignmentGenerationJob)
        .where(AssignmentGenerationJob.id == job_id)
        .with_for_update()
    )
    if job is None:
        return None, None
    revision = db.scalar(
        select(AssignmentDraftRevision)
        .where(AssignmentDraftRevision.generation_job_id == job.id)
        .with_for_update()
    )
    return job, revision


def _claim_job(
    db: Any,
    job_id: uuid.UUID,
    retry_stage: str | None,
) -> tuple[AssignmentGenerationJob | None, AssignmentDraftRevision | None, str | None]:
    job, revision = _load(db, job_id)
    if job is None or revision is None:
        return job, revision, "missing"
    if retry_stage and retry_stage not in STAGES:
        return job, revision, "invalid_stage"
    if job.status in {"cancelled", "stale", "ready", "review_required"}:
        return job, revision, "discarded_late"
    reason = ensure_current(db, job, revision)
    if reason:
        if reason == "CANCEL_REQUESTED":
            transition(job, "cancelled")
        db.commit()
        return job, revision, reason.lower()
    if job.status != "queued":
        return job, revision, "duplicate_delivery"
    if retry_stage and job.current_stage != retry_stage:
        return job, revision, "retry_stage_mismatch"
    if not retry_stage:
        job.attempt += 1
        if job.attempt > job.max_attempts:
            transition(job, "failed")
            job.retryable = False
            job.error_code = "GENERATION_MAX_ATTEMPTS_REACHED"
            job.error_message = "生成任务已达到最大尝试次数"
            db.commit()
            return job, revision, "max_attempts_reached"
    if retry_stage:
        reserved = db.scalar(
            select(GenerationStageResult)
            .where(
                GenerationStageResult.job_id == job.id,
                GenerationStageResult.stage == retry_stage,
                GenerationStageResult.status == "queued",
            )
            .order_by(GenerationStageResult.stage_generation.desc())
            .with_for_update()
        )
        if reserved is None:
            return job, revision, "retry_reservation_missing"
        job.status = retry_stage
        job.current_stage = retry_stage
        job.progress = max(
            job.progress,
            {
                "analyzing": 10,
                "processing_pages": 30,
                "extracting_questions": 55,
                "generating_rubrics": 75,
                "validating": 90,
            }[retry_stage],
        )
    else:
        transition(job, "analyzing")
    if job.started_at is None:
        job.started_at = now_utc()
    db.commit()
    return job, revision, None


def _execute_stage(db: Any, job_id: uuid.UUID, stage: str, *, retry: bool = False) -> str:
    job, revision = _load(db, job_id)
    if job is None or revision is None:
        return "missing"
    if job.status not in STAGES:
        return "discarded_late"
    reason = ensure_current(db, job, revision)
    if reason:
        if reason == "CANCEL_REQUESTED":
            transition(job, "cancelled")
        db.commit()
        return reason.lower()
    if retry:
        row = db.scalar(
            select(GenerationStageResult)
            .where(
                GenerationStageResult.job_id == job.id,
                GenerationStageResult.stage == stage,
                GenerationStageResult.status == "queued",
            )
            .order_by(GenerationStageResult.stage_generation.desc())
            .with_for_update()
        )
        if row is None:
            return "retry_reservation_missing"
        edit_version = row.expected_teacher_edit_version
        row.status = "running"
        row.started_at = now_utc()
    else:
        stage_generation = next_stage_generation(db, job.id, stage)
        edit_version = revision.teacher_edit_version
        row = GenerationStageResult(
            job_id=job.id,
            stage=stage,
            stage_generation=stage_generation,
            status="running",
            expected_teacher_edit_version=edit_version,
            input_hash=canonical_hash(
                {
                    "snapshot": job.source_snapshot_hash,
                    "generation": job.generation,
                    "stage": stage,
                    "stage_generation": stage_generation,
                    "teacher_edit_version": edit_version,
                }
            ),
            started_at=now_utc(),
        )
        db.add(row)
    if job.status != stage:
        transition(job, stage)
    db.commit()

    result: dict[str, Any] = {"kind": "orchestration_placeholder", "stage": stage}
    metadata_output = None
    file_output = None
    extraction_output = None
    answer_rubric_outputs: list[
        tuple[Question, AnswerRubricProviderOutput, AnswerRubricProviderOutput, dict[str, Any]]
    ] = []
    status = "completed"
    error_code = None
    invocation = None
    extraction_provider_available = False
    if stage == "analyzing":
        provider = select_provider(get_settings(), job.provider_mode)
        assignment = db.get(Assignment, job.assignment_id)
        pages = list(pages_for_job(db, job))
        file_ids = {page.stored_file_id for page in pages}
        files = (
            list(db.scalars(select(StoredFile).where(StoredFile.id.in_(file_ids))).all())
            if file_ids
            else []
        )
        ocr_text = ""
        if pages:
            recognition_job = db.scalar(
                select(RecognitionJob)
                .where(RecognitionJob.paper_version_id == pages[0].paper_version_id)
                .order_by(RecognitionJob.created_at.desc())
                .limit(1)
            )
            if recognition_job:
                ocr_text = " ".join(
                    (block.text or "")
                    for block in db.scalars(
                        select(RecognitionBlock)
                        .where(RecognitionBlock.recognition_job_id == recognition_job.id)
                        .order_by(RecognitionBlock.paper_page_id, RecognitionBlock.display_order)
                        .limit(200)
                    ).all()
                )[:12000]
        request_hash = canonical_hash(
            {
                "snapshot": job.source_snapshot_hash,
                "stage": stage,
                "files": [str(x.id) for x in files],
            }
        )
        if provider.name == "openai_compatible" and provider.available and assignment is not None:
            dispatched = dispatch_stage(
                get_settings(),
                job.provider_mode,
                "metadata_analysis",
                {
                    "assignment": {
                        "id": str(assignment.id),
                        "title": assignment.title,
                        "subject": assignment.subject,
                        "grade": assignment.grade,
                        "description": assignment.description,
                        "instructions": assignment.instructions,
                        "total_score": str(assignment.total_score)
                        if assignment.total_score is not None
                        else None,
                    },
                    "files": [
                        {
                            "id": str(item.id),
                            "name": item.original_name,
                            "checksum": item.checksum,
                            "content_type": item.content_type,
                        }
                        for item in sorted(files, key=lambda value: value.original_name)
                    ],
                    "ocr_text": ocr_text,
                },
            )
            invocation = _record_invocation(db, job, row, dispatched)
            if isinstance(dispatched.response.output, MetadataProviderOutput):
                metadata_output = dispatched.response.output
                result = {
                    "kind": "metadata_suggestions",
                    "stage": stage,
                    "suggestion_count": len(metadata_output.suggestions),
                    "fields": [item.field_name for item in metadata_output.suggestions],
                    "draft_only": True,
                }
            else:
                status = "unavailable"
                error_code = invocation.error_code or "PROVIDER_UNAVAILABLE"
                result = {
                    "kind": "metadata_suggestions",
                    "stage": stage,
                    "capability": "unavailable",
                    "suggestion_count": 0,
                    "draft_only": True,
                }
        elif provider.name == "fake" and provider.available and assignment is not None:
            invocation = AssignmentGenerationProviderInvocation(
                job_id=job.id,
                stage_result_id=row.id,
                provider=provider.name,
                endpoint_mode=provider.endpoint_mode,
                prompt_version=job.prompt_version,
                schema_version=job.schema_version,
                request_hash=request_hash,
                status="completed",
                started_at=now_utc(),
                completed_at=now_utc(),
            )
            db.add(invocation)
            db.flush()
            row.provider_invocation_id = invocation.id
            try:
                metadata_output = MetadataProviderOutput.model_validate(
                    deterministic_metadata(
                        assignment, sorted(files, key=lambda x: x.original_name), ocr_text
                    )
                )
                result = {
                    "kind": "metadata_suggestions",
                    "stage": stage,
                    "suggestion_count": len(metadata_output.suggestions),
                    "fields": [x.field_name for x in metadata_output.suggestions],
                    "draft_only": True,
                }
            except ValidationError:
                metadata_output = None
                status = "unavailable"
                error_code = "PROVIDER_SCHEMA_INVALID"
                invocation.status = "invalid_schema"
                invocation.error_code = error_code
                invocation.error_message = "Provider 输出未通过结构校验，未写入业务建议"
                result = {
                    "kind": "metadata_suggestions",
                    "stage": stage,
                    "capability": "invalid_schema",
                    "suggestion_count": 0,
                    "draft_only": True,
                }
        else:
            invocation = AssignmentGenerationProviderInvocation(
                job_id=job.id,
                stage_result_id=row.id,
                provider=provider.name,
                endpoint_mode=provider.endpoint_mode,
                prompt_version=job.prompt_version,
                schema_version=job.schema_version,
                request_hash=request_hash,
                status="unavailable",
                error_code=provider.error_code,
                error_message="未配置基本信息分析 Provider；未写入字段建议",
                started_at=now_utc(),
                completed_at=now_utc(),
            )
            db.add(invocation)
            db.flush()
            row.provider_invocation_id = invocation.id
            status = "unavailable"
            error_code = provider.error_code or "PROVIDER_UNAVAILABLE"
            result = {
                "kind": "metadata_suggestions",
                "stage": stage,
                "capability": "unavailable",
                "suggestion_count": 0,
                "draft_only": True,
            }
    elif stage == "processing_pages":
        pages = list(pages_for_job(db, job))
        provider = select_provider(get_settings(), job.provider_mode)
        if provider.available and provider.name == "openai_compatible":
            file_ids = {page.stored_file_id for page in pages}
            files = list(db.scalars(select(StoredFile).where(StoredFile.id.in_(file_ids))).all())
            dispatched = dispatch_stage(
                get_settings(),
                job.provider_mode,
                "file_analysis",
                {
                    "files": [
                        {
                            "stored_file_id": str(item.id),
                            "original_name": item.original_name,
                            "content_type": item.content_type,
                            "checksum": item.checksum,
                            "size": item.size,
                        }
                        for item in files
                    ],
                    "pages": [
                        {
                            "paper_page_id": str(page.id),
                            "stored_file_id": str(page.stored_file_id),
                            "page_number": page.page_number,
                            "rotation": page.rotation,
                            "status": page.status,
                        }
                        for page in pages
                    ],
                },
            )
            invocation = _record_invocation(db, job, row, dispatched)
            if isinstance(dispatched.response.output, FileAnalysisOutput):
                file_output = dispatched.response.output
            else:
                status = "unavailable"
                error_code = invocation.error_code or "PROVIDER_UNAVAILABLE"
        else:
            file_output = collect_file_analysis(db, pages)
        result["pages"] = [
            {"id": str(page.id), "page_number": page.page_number, "status": page.status}
            for page in pages
        ]
        result["ready"] = sum(page.status == "ready" for page in pages)
        result["kind"] = "file_page_analysis"
        result["files_analyzed"] = len(file_output.files) if file_output else 0
        result["page_analyses"] = len(file_output.pages) if file_output else 0
        result["prompt_injection_detected"] = bool(
            file_output and file_output.prompt_injection_detected
        )
    elif stage in {"extracting_questions", "generating_rubrics"}:
        provider = select_provider(get_settings(), job.provider_mode)
        request_hash = canonical_hash(
            {"job_id": job.id, "stage": stage, "snapshot": job.source_snapshot_hash}
        )
        if provider.available and (
            provider.name == "openai_compatible"
            or (provider.name == "fake" and stage == "generating_rubrics")
        ):
            if stage == "extracting_questions":
                pages = list(pages_for_job(db, job))
                blocks = list(
                    db.scalars(
                        select(RecognitionBlock).where(
                            RecognitionBlock.paper_page_id.in_({page.id for page in pages})
                        )
                    )
                )
                dispatched = dispatch_stage(
                    get_settings(),
                    job.provider_mode,
                    "question_extraction",
                    {
                        "pages": [
                            {
                                "id": str(page.id),
                                "page_number": page.page_number,
                                "status": page.status,
                            }
                            for page in pages
                        ],
                        "blocks": [
                            {
                                "id": str(block.id),
                                "page_id": str(block.paper_page_id),
                                "type": block.block_type,
                                "text": block.text,
                                "bounds": [
                                    str(block.x),
                                    str(block.y),
                                    str(block.width),
                                    str(block.height),
                                ],
                            }
                            for block in blocks
                        ],
                    },
                )
                invocation = _record_invocation(db, job, row, dispatched)
                if isinstance(dispatched.response.output, ExtractionOutput):
                    extraction_output = dispatched.response.output
                    result["capability"] = "openai_compatible"
                else:
                    status = "unavailable"
                    error_code = invocation.error_code or "PROVIDER_UNAVAILABLE"
            else:
                questions = list(
                    db.scalars(
                        select(Question)
                        .join(
                            AssignmentQuestionExtractionCandidate,
                            AssignmentQuestionExtractionCandidate.materialized_question_id
                            == Question.id,
                        )
                        .where(
                            AssignmentQuestionExtractionCandidate.draft_revision_id == revision.id,
                            AssignmentQuestionExtractionCandidate.status.in_(
                                {"accepted", "modified"}
                            ),
                            Question.status == "active",
                        )
                        .order_by(Question.display_order, Question.id)
                    )
                )
                for question in questions:
                    payload = {
                        "question": {
                            "id": str(question.id),
                            "number": question.question_number,
                            "type": question.question_type,
                            "text": question.content_text,
                            "latex": question.content_latex,
                            "max_score": str(question.max_score)
                            if question.max_score is not None
                            else None,
                        }
                    }
                    answer_dispatch = dispatch_stage(
                        get_settings(), job.provider_mode, "answer_generation", payload
                    )
                    answer_invocation = _record_invocation(db, job, row, answer_dispatch)
                    rubric_dispatch = dispatch_stage(
                        get_settings(), job.provider_mode, "rubric_generation", payload
                    )
                    rubric_invocation = _record_invocation(db, job, row, rubric_dispatch)
                    if not isinstance(
                        answer_dispatch.response.output, AnswerRubricProviderOutput
                    ) or not isinstance(
                        rubric_dispatch.response.output, AnswerRubricProviderOutput
                    ):
                        status = "unavailable"
                        error_code = (
                            answer_invocation.error_code
                            or rubric_invocation.error_code
                            or "PROVIDER_UNAVAILABLE"
                        )
                        continue
                    answer_rubric_outputs.append(
                        (
                            question,
                            answer_dispatch.response.output,
                            rubric_dispatch.response.output,
                            {
                                "answer_request_hash": answer_dispatch.response.request_hash,
                                "answer_response_hash": answer_dispatch.response.response_hash,
                                "rubric_request_hash": rubric_dispatch.response.request_hash,
                                "rubric_response_hash": rubric_dispatch.response.response_hash,
                                "model_snapshot": answer_dispatch.response.model_snapshot,
                                "provider": provider.name,
                            },
                        )
                    )
                result["capability"] = (
                    "fake_test" if provider.name == "fake" else "openai_compatible"
                )
                result["question_count"] = len(questions)
        else:
            invocation = AssignmentGenerationProviderInvocation(
                job_id=job.id,
                stage_result_id=row.id,
                provider=provider.name,
                endpoint_mode=provider.endpoint_mode,
                prompt_version=job.prompt_version,
                schema_version=job.schema_version,
                request_hash=request_hash,
                status="test_placeholder"
                if provider.name == "fake" and provider.available
                else "unavailable",
                error_code=provider.error_code,
                error_message=(
                    "生成 Provider 不可用或当前阶段未进入测试 fake 路径"
                    if provider.name != "fake" or not provider.available
                    else None
                ),
                started_at=now_utc(),
                completed_at=now_utc(),
            )
            db.add(invocation)
            db.flush()
            row.provider_invocation_id = invocation.id
        if provider.name not in {"fake", "openai_compatible"} or not provider.available:
            status = "unavailable"
            error_code = provider.error_code or "PROVIDER_UNAVAILABLE"
            result["capability"] = "unavailable"
            issue(
                db,
                job,
                revision,
                stage,
                "blocking" if stage == "extracting_questions" else "warning",
                "PROVIDER_UNAVAILABLE",
                "未配置真实生成 Provider；未生成或确认任何题目、答案或 Rubric",
                {"provider": provider.name, "endpoint_mode": provider.endpoint_mode},
            )
        elif provider.name == "fake":
            extraction_provider_available = stage == "extracting_questions"
            result["capability"] = "fake_test"
    elif stage == "validating":
        result["checks"] = {
            "draft_only": True,
            "publishes_assignment": False,
            "provider_quality_verified": False,
        }

    db.expire_all()
    job, revision = _load(db, job_id)
    assert job is not None and revision is not None
    row = db.scalar(
        select(GenerationStageResult).where(GenerationStageResult.id == row.id).with_for_update()
    )
    assert row is not None
    reason = ensure_current(db, job, revision, edit_version)
    if reason:
        row.status = "discarded"
        row.error_code = reason
        row.error_message = "阶段结果因状态、输入或教师修改变化而丢弃"
        row.completed_at = now_utc()
        if reason == "CANCEL_REQUESTED":
            transition(job, "cancelled")
        db.commit()
        return "discarded"
    if stage == "analyzing":
        if metadata_output is None:
            unavailable_code = error_code or "PROVIDER_UNAVAILABLE"
            issue(
                db,
                job,
                revision,
                stage,
                "warning",
                unavailable_code,
                "基本信息 Provider 不可用或输出无效；字段保持原值并需要教师人工填写",
                {"provider": job.provider_mode},
            )
            issue(
                db,
                job,
                revision,
                stage,
                "warning",
                "MANUAL_REVIEW_REQUIRED",
                "基本信息未自动确认，班级和截止时间始终由教师设置",
            )
        elif invocation is not None and invocation.provider == "openai_compatible":
            try:
                created = materialize_metadata(db, job, revision, metadata_output)
                result["suggestion_count"] = created
            except ProviderSemanticError as exc:
                status = "unavailable"
                error_code = exc.code
                invocation.status = "invalid_semantics"
                invocation.error_code = exc.code
                invocation.error_message = "Provider evidence or ownership validation failed"
                metadata_output = None
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "warning",
                    exc.code,
                    "Provider 输出引用了无效实体，未写入字段建议",
                    {"draft_only": True},
                )
        else:
            for metadata_candidate in metadata_output.suggestions:
                previous_version = (
                    db.scalar(
                        select(func.max(AssignmentFieldSuggestion.suggestion_version)).where(
                            AssignmentFieldSuggestion.draft_revision_id == revision.id,
                            AssignmentFieldSuggestion.field_name == metadata_candidate.field_name,
                        )
                    )
                    or 0
                )
                for old in db.scalars(
                    select(AssignmentFieldSuggestion)
                    .where(
                        AssignmentFieldSuggestion.draft_revision_id == revision.id,
                        AssignmentFieldSuggestion.field_name == metadata_candidate.field_name,
                        AssignmentFieldSuggestion.status == "suggested",
                    )
                    .with_for_update()
                ).all():
                    old.status = "superseded"
                db.add(
                    AssignmentFieldSuggestion(
                        owner_id=job.owner_id,
                        assignment_id=job.assignment_id,
                        generation_job_id=job.id,
                        draft_revision_id=revision.id,
                        field_name=metadata_candidate.field_name,
                        suggested_value=metadata_candidate.suggested_value,
                        normalized_value=metadata_candidate.normalized_value,
                        confidence=metadata_candidate.confidence,
                        evidence=[x.model_dump() for x in metadata_candidate.evidence],
                        source_type=metadata_candidate.source_type,
                        source_stage=stage,
                        suggestion_version=previous_version + 1,
                    )
                )
                if metadata_candidate.confidence < 0.6:
                    created_issue = issue(
                        db,
                        job,
                        revision,
                        stage,
                        "warning",
                        "BASIC_INFO_LOW_CONFIDENCE",
                        f"{metadata_candidate.field_name} 建议置信度较低，需教师确认",
                        {
                            "field_name": metadata_candidate.field_name,
                            "confidence": metadata_candidate.confidence,
                        },
                    )
                    created_issue.entity_type = "assignment_field"
                    created_issue.entity_id = metadata_candidate.field_name
                if metadata_candidate.field_name == "total_score":
                    conflict = metadata_candidate.normalized_value is None and isinstance(
                        metadata_candidate.suggested_value, dict
                    )
                    created_issue = issue(
                        db,
                        job,
                        revision,
                        stage,
                        "blocking" if conflict else "warning",
                        "TOTAL_SCORE_CONFLICT" if conflict else "TOTAL_SCORE_UNCONFIRMED",
                        "总分存在冲突，必须由教师选择并明确确认"
                        if conflict
                        else "总分建议尚未由教师明确确认",
                        {"field_name": "total_score"},
                    )
                    created_issue.entity_type = "assignment_field"
                    created_issue.entity_id = "total_score"
    elif (
        stage == "processing_pages"
        and file_output is not None
        and invocation is not None
        and invocation.provider == "openai_compatible"
    ):
        try:
            counts = materialize_file_analysis(db, job, revision, file_output)
            result.update(counts)
            if file_output.prompt_injection_detected:
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "warning",
                    "PROMPT_INJECTION_CONTENT_DETECTED",
                    "上传材料含类似越权指令的文字；已作为不可信数据隔离",
                    {"untrusted_document_content": True},
                )
        except ProviderSemanticError as exc:
            status = "unavailable"
            error_code = exc.code
            invocation.status = "invalid_semantics"
            invocation.error_code = exc.code
            invocation.error_message = "Provider evidence or ownership validation failed"
            issue(
                db,
                job,
                revision,
                stage,
                "blocking",
                exc.code,
                "文件或页面 Provider 输出引用无效，未写入分析草稿",
                {"draft_only": True},
            )
    elif stage == "processing_pages" and file_output is not None:
        analyses: dict[str, AssignmentSourceFileAnalysis] = {}
        for file_candidate in file_output.files:
            for old in db.scalars(
                select(AssignmentSourceFileAnalysis)
                .where(
                    AssignmentSourceFileAnalysis.draft_revision_id == revision.id,
                    AssignmentSourceFileAnalysis.stored_file_id
                    == uuid.UUID(file_candidate.stored_file_id),
                    AssignmentSourceFileAnalysis.analysis_status == "suggested",
                )
                .with_for_update()
            ).all():
                old.analysis_status = "superseded"
            analysis = AssignmentSourceFileAnalysis(
                owner_id=job.owner_id,
                assignment_id=job.assignment_id,
                generation_job_id=job.id,
                draft_revision_id=revision.id,
                stored_file_id=uuid.UUID(file_candidate.stored_file_id),
                source_snapshot_hash=job.source_snapshot_hash,
                detected_mime_type=file_candidate.detected_mime_type,
                checksum=file_candidate.checksum,
                page_count=file_candidate.page_count,
                suggested_role=file_candidate.suggested_role,
                role_confidence=file_candidate.role_confidence,
                suggested_answer_source=file_candidate.suggested_answer_source,
                answer_source_confidence=file_candidate.answer_source_confidence,
                duplicate_of_file_id=uuid.UUID(file_candidate.duplicate_of_file_id)
                if file_candidate.duplicate_of_file_id
                else None,
                evidence=[x.model_dump() for x in file_candidate.evidence],
                warning_codes=file_candidate.warning_codes,
            )
            db.add(analysis)
            db.flush()
            analyses[file_candidate.stored_file_id] = analysis
            for code in file_candidate.warning_codes:
                severity = "warning"
                created_issue = issue(
                    db,
                    job,
                    revision,
                    stage,
                    severity,
                    code,
                    "文件用途无法可靠判断，需要教师选择"
                    if code in {"FILE_ROLE_REVIEW_REQUIRED", "FILE_ROLE_CONFLICT_REVIEW_REQUIRED"}
                    else "检测到重复文件；系统不会自动删除",
                    {"stored_file_id": file_candidate.stored_file_id},
                )
                created_issue.entity_type = "stored_file"
                created_issue.entity_id = file_candidate.stored_file_id
        for page_candidate in file_output.pages:
            analysis = analyses[page_candidate.stored_file_id]
            db.add(
                AssignmentPageAnalysis(
                    owner_id=job.owner_id,
                    assignment_id=job.assignment_id,
                    generation_job_id=job.id,
                    draft_revision_id=revision.id,
                    paper_page_id=uuid.UUID(page_candidate.paper_page_id),
                    source_file_analysis_id=analysis.id,
                    source_snapshot_hash=job.source_snapshot_hash,
                    status=page_candidate.status,
                    quality_score=page_candidate.quality_score,
                    blank_probability=page_candidate.blank_probability,
                    duplicate_probability=page_candidate.duplicate_probability,
                    duplicate_of_page_id=uuid.UUID(page_candidate.duplicate_of_page_id)
                    if page_candidate.duplicate_of_page_id
                    else None,
                    missing_page_suspected=page_candidate.missing_page_suspected,
                    low_quality=page_candidate.low_quality,
                    corrupted=page_candidate.corrupted,
                    mixed_document_suspected=page_candidate.mixed_document_suspected,
                    variant_label=page_candidate.variant_label,
                    metrics=page_candidate.metrics,
                    evidence=[x.model_dump() for x in page_candidate.evidence],
                    warning_codes=page_candidate.warning_codes,
                )
            )
            for code in page_candidate.warning_codes:
                severity = (
                    "blocking"
                    if code
                    in {
                        "POSSIBLE_MISSING_PAGE",
                        "MULTIPLE_VARIANTS_SUSPECTED",
                        "CORRUPT_FILE",
                        "UNSUPPORTED_FILE",
                    }
                    else "warning"
                )
                created_issue = issue(
                    db,
                    job,
                    revision,
                    stage,
                    severity,
                    code,
                    "页面异常需要教师人工复核",
                    {"paper_page_id": page_candidate.paper_page_id},
                )
                created_issue.entity_type = "paper_page"
                created_issue.entity_id = page_candidate.paper_page_id
        if file_output.prompt_injection_detected:
            issue(
                db,
                job,
                revision,
                stage,
                "warning",
                "PROMPT_INJECTION_CONTENT_DETECTED",
                "上传材料含类似越权指令的文字；已作为不可信数据隔离，未执行任何操作",
                {"evidence": [x.model_dump() for x in file_output.prompt_injection_evidence]},
            )
    elif stage == "extracting_questions":
        result["page_organization_suggestions"] = build_page_suggestions(db, job, revision)
        if extraction_output is not None and invocation is not None:
            try:
                extraction = materialize_questions(db, job, revision, extraction_output)
                result.update(extraction)
                result["materialized_draft_questions"] = materialize_draft_questions(
                    db, job, revision
                )
                if extraction["manual_required"]:
                    issue(
                        db,
                        job,
                        revision,
                        stage,
                        "warning",
                        "MANUAL_REVIEW_REQUIRED",
                        "部分题目依据服务端能力边界强制人工复核",
                        {"count": extraction["manual_required"]},
                    )
            except ProviderSemanticError as exc:
                status = "unavailable"
                error_code = exc.code
                invocation.status = "invalid_semantics"
                invocation.error_code = exc.code
                invocation.error_message = "Provider evidence or ownership validation failed"
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "blocking",
                    exc.code,
                    "题目 Provider 输出引用无效，未创建题目候选",
                    {"draft_only": True},
                )
        elif extraction_provider_available:
            extraction = build_fake_candidates(db, job, revision)
            result.update(extraction)
            blocked = extraction.get("blocked")
            if blocked:
                status = "unavailable"
                error_code = str(blocked)
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "blocking",
                    str(blocked),
                    "题目抽取前置条件未满足；未创建业务候选",
                    {"draft_only": True},
                )
            else:
                result["materialized_draft_questions"] = materialize_draft_questions(
                    db, job, revision
                )
            if extraction.get("prompt_injection_detected"):
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "warning",
                    "PROMPT_INJECTION_CONTENT_DETECTED",
                    "题干含类似越权指令的文字；仅作为不可信题目内容保存",
                    {"untrusted_document_content": True},
                )
            if extraction.get("question_number_conflict"):
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "blocking",
                    "QUESTION_NUMBER_CONFLICT",
                    "检测到重复题号；必须由教师解决后再确认",
                    {"structural_conflict": True},
                )
    elif stage == "generating_rubrics":
        if answer_rubric_outputs:
            created = 0
            manual_required = 0
            try:
                with db.begin_nested():
                    for question, answer_output, rubric_output, audit in answer_rubric_outputs:
                        answer = materialize_answer(
                            db, job, revision, question, answer_output, audit
                        )
                        rubric = materialize_rubric(
                            db, job, revision, question, answer, rubric_output
                        )
                        created += 1
                        manual_required += int(answer.manual_required or rubric.manual_required)
                generated = {
                    "kind": "answer_rubric_candidates",
                    "question_count": len(answer_rubric_outputs),
                    "created": created,
                    "manual_required": manual_required,
                    "prompt_injection_detected": False,
                    "draft_only": True,
                }
            except ProviderSemanticError as exc:
                status = "unavailable"
                error_code = exc.code
                generated = {
                    "kind": "answer_rubric_candidates",
                    "question_count": len(answer_rubric_outputs),
                    "created": 0,
                    "manual_required": len(answer_rubric_outputs),
                    "prompt_injection_detected": False,
                    "draft_only": True,
                }
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "blocking",
                    exc.code,
                    "答案或 Rubric Provider 输出未通过服务端语义校验",
                    {"draft_only": True},
                )
        else:
            generated = generate_candidates(db, job, revision, False)
        result.update(generated)
        if generated["question_count"] == 0:
            status = "unavailable"
            error_code = "QUESTION_CONFIRMATION_REQUIRED"
            issue(
                db,
                job,
                revision,
                stage,
                "blocking",
                "QUESTION_CONFIRMATION_REQUIRED",
                "只有教师已接受并物化的当前题目才能生成答案和 Rubric 草稿",
                {"draft_only": True},
            )
        if generated["manual_required"]:
            issue(
                db,
                job,
                revision,
                stage,
                "warning",
                "MANUAL_RUBRIC_REQUIRED",
                "部分题目超出确定性能力边界，已保留为空草稿或人工模式",
                {"count": generated["manual_required"]},
            )
        if generated["prompt_injection_detected"]:
            issue(
                db,
                job,
                revision,
                stage,
                "warning",
                "PROMPT_INJECTION_CONTENT_DETECTED",
                "题目含类似越权指令的文字；仅作为不可信内容保存，未改变来源、模式或状态机",
                {"untrusted_document_content": True},
            )
    elif stage == "validating":
        result.update(validate_revision_candidates(db, job, revision))
        counts = result["counts"]
        for validation_status, count in counts.items():
            if count:
                code = f"VALIDATION_{validation_status.upper()}"
                issue(
                    db,
                    job,
                    revision,
                    stage,
                    "warning" if validation_status != "failed" else "blocking",
                    code,
                    "验证结果仅用于草稿风险审查，不判定教师答案错误或写入学生分数",
                    {"count": count, "does_not_score_students": True},
                )
    row.status = status
    row.error_code = error_code
    row.result_payload = result
    row.output_hash = canonical_hash(result)
    row.completed_at = now_utc()
    payload = dict(revision.draft_payload)
    payload["stages"] = {**payload.get("stages", {}), stage: result}
    write = db.execute(
        update(AssignmentDraftRevision)
        .where(
            AssignmentDraftRevision.id == revision.id,
            AssignmentDraftRevision.teacher_edit_version == edit_version,
        )
        .values(draft_payload=payload)
    )
    if write.rowcount != 1:
        row.status = "discarded"
        row.error_code = "DRAFT_MODIFIED_BY_TEACHER"
        row.error_message = "阶段结果因教师修改变化而丢弃"
        db.commit()
        return "discarded"
    revision.draft_payload = payload
    update_risk_summary(db, revision)
    db.commit()
    return status


def _run(job_id: str, retry_stage: str | None) -> dict[str, Any]:
    parsed = uuid.UUID(job_id)
    with SessionLocal() as db:
        job, revision = _load(db, parsed)
        db.rollback()
        job, revision, claim_status = _claim_job(db, parsed, retry_stage)
        if claim_status:
            return {"status": claim_status}
        assert job is not None and revision is not None
        stages = (retry_stage,) if retry_stage else STAGES
        outcomes: dict[str, str] = {}
        for stage in stages:
            outcome = _execute_stage(db, parsed, stage, retry=retry_stage is not None)
            outcomes[stage] = outcome
            if outcome in {
                "cancel_requested",
                "source_changed",
                "generation_superseded",
                "discarded",
                "discarded_late",
            }:
                return {"status": outcome, "stages": outcomes}
        job, revision = _load(db, parsed)
        assert job is not None and revision is not None
        reason = ensure_current(db, job, revision)
        if reason:
            db.commit()
            return {"status": reason.lower(), "stages": outcomes}
        if retry_stage:
            complete_stage_retry(job)
            job.retryable = has_retryable_stage(db, job)
        else:
            target = (
                "partial"
                if any(value == "unavailable" for value in outcomes.values())
                else "review_required"
            )
            transition(job, target)
            if target == "partial":
                issue(
                    db,
                    job,
                    revision,
                    "validating",
                    "blocking",
                    "GENERATION_PARTIAL",
                    "至少一个 Provider 阶段不可用；已保留空草稿或部分结果，未伪造成功",
                )
            issue(
                db,
                job,
                revision,
                "validating",
                "warning",
                "MANUAL_REVIEW_REQUIRED",
                "教师必须检查并确认所有草稿内容；Worker 不能发布作业",
            )
        revision.status = "review_required" if job.status == "review_required" else "partial"
        update_risk_summary(db, revision)
        db.commit()
        return {"status": job.status, "stages": outcomes}


def _guarded_run(job_id: str, retry_stage: str | None) -> dict[str, Any]:
    try:
        return _run(job_id, retry_stage)
    except Exception:
        # Persist a stable, redacted failure. Provider/database exceptions must
        # never expose credentials or signed URLs through the user-facing job.
        parsed = uuid.UUID(job_id)
        with SessionLocal() as db:
            job, revision = _load(db, parsed)
            if job is not None and revision is not None and job.status in ACTIVE_STATUSES:
                transition(job, "failed")
                job.retryable = True
                job.error_code = "STAGE_FAILED"
                job.error_message = "生成阶段执行失败，可由教师选择阶段重试"
                issue(
                    db,
                    job,
                    revision,
                    retry_stage or job.current_stage,
                    "blocking",
                    "STAGE_FAILED",
                    "生成阶段执行失败，详细异常仅保留在受控 Worker 日志",
                )
                update_risk_summary(db, revision)
                db.commit()
        return {"status": "failed"}


@celery_app.task(
    name="ahamark.assignment_generation.run",
    bind=True,
    soft_time_limit=300,
    time_limit=330,
)
def run_assignment_generation(
    self: Any, job_id: str, retry_stage: str | None = None
) -> dict[str, Any]:
    return run_traced_task(self, job_id, lambda: _guarded_run(job_id, retry_stage))


@celery_app.task(
    name="ahamark.assignment_generation.run_after_recognition",
    bind=True,
    max_retries=180,
)
def run_assignment_generation_after_recognition(
    self: Any, job_id: str, recognition_job_id: str
) -> dict[str, Any]:
    with SessionLocal() as db:
        recognition = db.get(RecognitionJob, uuid.UUID(recognition_job_id))
        if recognition is not None and recognition.status in {
            RecognitionStatus.queued,
            RecognitionStatus.running,
        }:
            raise self.retry(countdown=2)
    return run_traced_task(self, job_id, lambda: _guarded_run(job_id, None))

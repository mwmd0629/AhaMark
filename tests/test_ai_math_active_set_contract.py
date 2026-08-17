import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.api.ai_grading import CreateJob
from app.api.math_validation import ValidationInput
from app.structured_rubric_authority import (
    ActiveStructuredRubricAuthority,
    StructuredRubricAuthorityError,
    require_job_authority,
)
from pydantic import ValidationError


def test_client_cannot_select_arbitrary_rubric_for_ai_or_math_jobs() -> None:
    payload = {
        "student_answer_id": uuid.uuid4(),
        "rubric_version_id": uuid.uuid4(),
        "idempotency_key": "fixed-to-active-set",
    }
    with pytest.raises(ValidationError, match="rubric_version_id"):
        CreateJob.model_validate(payload)
    with pytest.raises(ValidationError, match="rubric_version_id"):
        ValidationInput.model_validate(payload)


def test_job_authority_requires_exact_active_set_item_versions() -> None:
    set_id, rubric_id, reference_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    authority = ActiveStructuredRubricAuthority(
        rubric_set=cast(Any, SimpleNamespace(id=set_id)),
        item=cast(Any, SimpleNamespace()),
        reference=cast(Any, SimpleNamespace(id=reference_id)),
        rubric=cast(Any, SimpleNamespace(id=rubric_id)),
        criteria=(),
    )
    require_job_authority(
        authority,
        structured_rubric_set_id=set_id,
        rubric_version_id=rubric_id,
        reference_answer_version_id=reference_id,
    )
    with pytest.raises(StructuredRubricAuthorityError, match="no longer matches"):
        require_job_authority(
            authority,
            structured_rubric_set_id=uuid.uuid4(),
            rubric_version_id=rubric_id,
            reference_answer_version_id=reference_id,
        )

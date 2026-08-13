from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TutorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=160)
    kind: Literal["student_answer", "published_feedback", "rubric", "recognition"]
    text: str = Field(min_length=1, max_length=4000)


class TutorConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["student", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class WrongQuestionTutorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wrong-question-input-v1"] = "wrong-question-input-v1"
    question_id: str = Field(min_length=1, max_length=80)
    score_snapshot_id: str = Field(min_length=1, max_length=80)
    question_text: str = Field(min_length=1, max_length=12000)
    student_answer_text: str = Field(default="", max_length=12000)
    published_feedback: str = Field(default="", max_length=4000)
    awarded_points: Decimal | None = Field(default=None, ge=0)
    max_points: Decimal | None = Field(default=None, gt=0)
    evidence: list[TutorEvidence] = Field(default_factory=list, max_length=30)
    conversation: list[TutorConversationTurn] = Field(default_factory=list, max_length=20)
    student_question: str = Field(min_length=1, max_length=4000)
    response_language: str = Field(default="zh-CN", min_length=2, max_length=20)

    @model_validator(mode="after")
    def score_is_coherent(self) -> Self:
        if (
            self.awarded_points is not None
            and self.max_points is not None
            and self.awarded_points > self.max_points
        ):
            raise ValueError("awarded points exceed maximum")
        refs = [item.evidence_id for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate evidence id")
        return self


class WrongQuestionReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wrong-question-reply-v1"]
    verdict: Literal["likely_student_error", "likely_ai_misjudgment", "uncertain"]
    confidence: Decimal = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    knowledge_gaps: list[str] = Field(default_factory=list, max_length=12)
    next_steps: list[str] = Field(default_factory=list, max_length=12)
    requires_teacher_review: bool
    safety_note: str = Field(
        default="AI suggestions do not change the published score.", max_length=500
    )

    @model_validator(mode="after")
    def possible_misjudgment_requires_review(self) -> Self:
        if self.verdict == "likely_ai_misjudgment" and not self.requires_teacher_review:
            raise ValueError("possible AI misjudgment requires teacher review")
        return self


def validate_reply(
    output: WrongQuestionReply,
    request: WrongQuestionTutorInput,
) -> WrongQuestionReply:
    allowed = {item.evidence_id for item in request.evidence}
    if len(output.evidence_refs) != len(set(output.evidence_refs)):
        raise ValueError("duplicate evidence reference")
    if not set(output.evidence_refs) <= allowed:
        raise ValueError("unknown evidence reference")
    if output.confidence > Decimal("0.8") and not output.evidence_refs:
        raise ValueError("high confidence requires evidence")
    return output

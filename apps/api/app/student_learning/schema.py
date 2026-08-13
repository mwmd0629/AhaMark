from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReleasedResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grade_release_id: str = Field(min_length=1, max_length=80)
    assignment_title: str = Field(min_length=1, max_length=300)
    published_at: str = Field(min_length=1, max_length=80)
    awarded_points: Decimal | None = Field(default=None, ge=0)
    max_points: Decimal | None = Field(default=None, gt=0)


class LearningEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=160)
    knowledge_point: str = Field(default="", max_length=200)
    summary: str = Field(min_length=1, max_length=2000)


class LearningResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    resource_type: str = Field(min_length=1, max_length=50)


class StudentLearningAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["student-learning-input-v1"] = "student-learning-input-v1"
    source_hash: str = Field(min_length=16, max_length=128)
    released_results: list[ReleasedResultSummary] = Field(min_length=1, max_length=50)
    evidence: list[LearningEvidence] = Field(default_factory=list, max_length=300)
    available_resources: list[LearningResourceRef] = Field(default_factory=list, max_length=100)
    response_language: str = Field(default="zh-CN", min_length=2, max_length=20)


class LearningFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=30)


class StudyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Literal["high", "medium", "low"]
    action: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)


class ResourceRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)


class StudentLearningAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["student-learning-analysis-v1"]
    summary: str = Field(min_length=1, max_length=4000)
    strengths: list[LearningFinding] = Field(default_factory=list, max_length=12)
    weaknesses: list[LearningFinding] = Field(default_factory=list, max_length=12)
    knowledge_gaps: list[LearningFinding] = Field(default_factory=list, max_length=12)
    study_plan: list[StudyAction] = Field(default_factory=list, max_length=20)
    resource_recommendations: list[ResourceRecommendation] = Field(
        default_factory=list, max_length=20
    )
    disclaimer: str = Field(min_length=1, max_length=500)


def validate_analysis(
    output: StudentLearningAnalysisOutput,
    request: StudentLearningAnalysisInput,
) -> StudentLearningAnalysisOutput:
    evidence_ids = {item.evidence_id for item in request.evidence}
    resource_ids = {item.resource_id for item in request.available_resources}
    all_findings = [*output.strengths, *output.weaknesses, *output.knowledge_gaps]
    for finding in all_findings:
        if len(finding.evidence_refs) != len(set(finding.evidence_refs)):
            raise ValueError("duplicate evidence reference")
        if not set(finding.evidence_refs) <= evidence_ids:
            raise ValueError("unknown evidence reference")
    for action in output.study_plan:
        if not set(action.evidence_refs) <= evidence_ids:
            raise ValueError("unknown evidence reference")
    if any(item.resource_id not in resource_ids for item in output.resource_recommendations):
        raise ValueError("unknown resource reference")
    return output

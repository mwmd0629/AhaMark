from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FieldName = Literal[
    "title",
    "subject",
    "grade",
    "academic_year",
    "assessment_type",
    "description",
    "instructions",
    "total_score",
]
FileRole = Literal[
    "question_paper", "reference_answer", "rubric", "instructions", "attachment", "unknown"
]
AnswerSource = Literal[
    "teacher_official",
    "publisher_official",
    "teacher_provided",
    "third_party",
    "ai_generated",
    "unknown",
    "not_applicable",
]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["assignment_field", "file_name", "file", "page", "ocr_region", "derived"]
    reference_id: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=240)


class MetadataSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: FieldName
    suggested_value: Any | None
    normalized_value: Any | None
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    source_type: Literal["provider", "deterministic", "current_draft", "unknown"]

    @field_validator("suggested_value", "normalized_value")
    @classmethod
    def restrict_value_shape(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError("suggested text exceeds limit")
        if isinstance(value, list) and len(value) > 20:
            raise ValueError("candidate list exceeds limit")
        if isinstance(value, dict) and len(value) > 20:
            raise ValueError("candidate object exceeds limit")
        return value


class MetadataProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[MetadataSuggestion] = Field(default_factory=list, max_length=8)


class FileAnalysisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stored_file_id: str
    detected_mime_type: str = Field(min_length=1, max_length=127)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int | None = Field(None, ge=0)
    suggested_role: FileRole
    role_confidence: float = Field(ge=0, le=1)
    suggested_answer_source: AnswerSource
    answer_source_confidence: float = Field(ge=0, le=1)
    duplicate_of_file_id: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=30)
    warning_codes: list[str] = Field(default_factory=list, max_length=30)


class PageAnalysisCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_page_id: str
    stored_file_id: str
    status: Literal[
        "ready",
        "blank",
        "low_quality",
        "corrupted",
        "unsupported",
        "pending_conversion",
        "processing_failed",
    ]
    quality_score: float | None = Field(None, ge=0, le=1)
    blank_probability: float | None = Field(None, ge=0, le=1)
    duplicate_probability: float | None = Field(None, ge=0, le=1)
    duplicate_of_page_id: str | None = None
    missing_page_suspected: bool = False
    low_quality: bool = False
    corrupted: bool = False
    mixed_document_suspected: bool = False
    variant_label: (
        Literal["possible_variant_a", "possible_variant_b", "mixed_variants", "unknown"] | None
    ) = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=30)
    warning_codes: list[str] = Field(default_factory=list, max_length=30)


class FileAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[FileAnalysisCandidate] = Field(default_factory=list)
    pages: list[PageAnalysisCandidate] = Field(default_factory=list)
    prompt_injection_detected: bool = False
    prompt_injection_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=10)

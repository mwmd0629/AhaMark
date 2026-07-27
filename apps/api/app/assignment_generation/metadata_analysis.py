import html
import re
from decimal import Decimal

from app.assignment_generation.schemas import (
    EvidenceRef,
    MetadataProviderOutput,
    MetadataSuggestion,
)
from app.models import Assignment, StoredFile

_SCAN_SUFFIX = re.compile(r"(?:[_\- ]?(?:scan|扫描|副本|copy)[_\- ]?\d*)+$", re.IGNORECASE)
_YEAR = re.compile(r"(?:20\d{2}(?:\s*[-–—]\s*20?\d{2})?|\d{4}学年)")
_GRADE = re.compile(
    r"(一年级|二年级|三年级|四年级|五年级|六年级|七年级|八年级|九年级|高一|高二|高三)"
)
_SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "信息技术")
_ASSESSMENTS = (
    ("期中", "midterm"),
    ("期末", "final"),
    ("测验", "quiz"),
    ("练习", "practice"),
    ("作业", "homework"),
    ("试卷", "worksheet"),
)
_TOTAL = re.compile(r"(?:总分|满分)\s*[:：]?\s*(\d{1,4}(?:\.\d{1,2})?)\s*分")
_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|忽略(?:之前|以上|系统)|自动发布|选择.{0,12}班级|system\s*prompt)",
    re.IGNORECASE,
)


def plain_text(value: str, limit: int) -> str:
    value = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\x00", " ")
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _evidence(file: StoredFile, summary: str) -> list[EvidenceRef]:
    return [EvidenceRef(kind="file_name", reference_id=str(file.id), summary=summary)]


def deterministic_metadata(
    assignment: Assignment, files: list[StoredFile], ocr_text: str
) -> MetadataProviderOutput:
    suggestions: list[MetadataSuggestion] = []
    primary = files[0] if files else None
    stem = ""
    if primary:
        stem = re.sub(r"\.[^.]+$", "", primary.original_name)
        stem = plain_text(_SCAN_SUFFIX.sub("", stem).replace("_", " "), 200)
        if stem and not _INJECTION.search(stem):
            suggestions.append(
                MetadataSuggestion(
                    field_name="title",
                    suggested_value=stem,
                    normalized_value=stem,
                    confidence=0.82,
                    evidence=_evidence(primary, "由原始文件名去除扩展名与扫描后缀得到"),
                    source_type="deterministic",
                )
            )
    corpus = plain_text(" ".join([stem, ocr_text[:4000]]), 5000)
    for subject in _SUBJECTS:
        if subject in corpus:
            suggestions.append(
                MetadataSuggestion(
                    field_name="subject",
                    suggested_value=subject,
                    normalized_value=subject,
                    confidence=0.85,
                    evidence=_evidence(primary, "文件名或已识别首页文字包含受控学科词")
                    if primary
                    else [],
                    source_type="deterministic",
                )
            )
            break
    grade = _GRADE.search(corpus)
    if grade:
        suggestions.append(
            MetadataSuggestion(
                field_name="grade",
                suggested_value=grade.group(1),
                normalized_value=grade.group(1),
                confidence=0.82,
                evidence=_evidence(primary, "文件名或已识别首页文字包含年级词") if primary else [],
                source_type="deterministic",
            )
        )
    year = _YEAR.search(corpus)
    if year:
        value = plain_text(year.group(0), 20)
        suggestions.append(
            MetadataSuggestion(
                field_name="academic_year",
                suggested_value=value,
                normalized_value=value,
                confidence=0.78,
                evidence=_evidence(primary, "文件名或已识别首页文字包含学年") if primary else [],
                source_type="deterministic",
            )
        )
    for marker, value in _ASSESSMENTS:
        if marker in corpus:
            suggestions.append(
                MetadataSuggestion(
                    field_name="assessment_type",
                    suggested_value=value,
                    normalized_value=value,
                    confidence=0.78,
                    evidence=_evidence(primary, f"检测到受控考试类型词：{marker}")
                    if primary
                    else [],
                    source_type="deterministic",
                )
            )
            break
    totals = sorted({Decimal(x) for x in _TOTAL.findall(corpus) if Decimal(x) > 0})
    if len(totals) == 1:
        value = str(totals[0])
        suggestions.append(
            MetadataSuggestion(
                field_name="total_score",
                suggested_value=value,
                normalized_value=value,
                confidence=0.9,
                evidence=_evidence(primary, "已识别文字中存在唯一明确总分") if primary else [],
                source_type="deterministic",
            )
        )
    elif len(totals) > 1:
        suggestions.append(
            MetadataSuggestion(
                field_name="total_score",
                suggested_value={"candidates": [str(x) for x in totals]},
                normalized_value=None,
                confidence=0,
                evidence=_evidence(primary, "已识别文字中存在互相冲突的总分候选")
                if primary
                else [],
                source_type="deterministic",
            )
        )
    existing = {row.field_name for row in suggestions}
    for field_name in (
        "title",
        "subject",
        "grade",
        "academic_year",
        "assessment_type",
        "total_score",
    ):
        if field_name not in existing:
            suggestions.append(
                MetadataSuggestion(
                    field_name=field_name,
                    suggested_value=None,
                    normalized_value=None,
                    confidence=0,
                    evidence=[],
                    source_type="unknown",
                )
            )
    # class_ids and due_at are deliberately absent from the closed schema.
    return MetadataProviderOutput(suggestions=suggestions)

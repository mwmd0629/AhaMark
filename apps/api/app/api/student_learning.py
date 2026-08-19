import json
import uuid
from collections import Counter
from typing import Annotated, Any

import httpx
from app.api.actor import Actor
from app.api.domain import ApiProblem, audit
from app.api.student_portal import _linked_students
from app.api.student_review_requests import _wrong_question_source, student_wrong_questions
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Question
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/student", tags=["student-learning"])
Db = Annotated[Session, Depends(get_db)]


class TutorInput(BaseModel):
    question: str | None = Field(default=None, max_length=500)


def _analysis(items: list[dict[str, Any]]) -> dict[str, Any]:
    errors = Counter(str(item.get("error_type") or "未分类") for item in items)
    knowledge = Counter(
        str(point["name"])
        for item in items
        for point in item.get("knowledge_points", [])
        if point.get("name")
    )
    return {
        "wrong_question_count": len(items),
        "focus_knowledge_points": [
            {"name": name, "count": count} for name, count in knowledge.most_common(8)
        ],
        "error_types": [
            {"name": name, "count": count} for name, count in errors.most_common(8)
        ],
        "suggested_actions": (
            [
                "先按知识点复习错题，再重新独立作答。",
                "对照教师反馈定位第一处错误，不要只看最终答案。",
                "仍有疑问时可提交复核申请或向教师提问。",
            ]
            if items
            else ["继续保持，等待教师发布新的正式成绩。"]
        ),
        "source": "released_grade_snapshots",
        "suggestion_only": True,
    }


@router.get("/learning-analysis")
def student_learning_analysis(
    db: Db,
    actor: Actor,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _linked_students(db, actor.id)
    return {
        **_analysis(student_wrong_questions(db, actor)),
        "assistant_enabled": settings.student_learning_assistant_enabled,
    }


def _assistant_settings(settings: Settings) -> tuple[str, str, str]:
    if not settings.student_learning_assistant_enabled:
        raise ApiProblem(404, "STUDENT_LEARNING_ASSISTANT_DISABLED", "本地学习助手尚未启用")
    if (
        settings.ai_grading_provider != "local_openai_compatible"
        or not settings.ai_grading_allow_local_provider_requests
        or settings.ai_grading_allow_external_provider_requests
        or not settings.ai_grading_base_url
        or not settings.ai_grading_model
        or not settings.ai_grading_api_key
    ):
        raise ApiProblem(503, "STUDENT_LEARNING_ASSISTANT_UNAVAILABLE", "本地学习助手配置不完整")
    return settings.ai_grading_base_url, settings.ai_grading_model, settings.ai_grading_api_key


@router.post("/wrong-questions/{release_id}/{question_id}/tutor")
def student_wrong_question_tutor(
    release_id: uuid.UUID,
    question_id: uuid.UUID,
    data: TutorInput,
    db: Db,
    actor: Actor,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    base_url, model, api_key = _assistant_settings(settings)
    _release, _student, assignment, school_class, detail, _review, answer = (
        _wrong_question_source(db, actor.id, release_id, question_id)
    )
    question = db.get(Question, question_id)
    if question is None:
        raise ApiProblem(409, "STUDENT_GRADE_SOURCE_INVALID", "题目来源无效")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是中学作业学习助手。只做学习建议，不评分、不改分、不发布成绩。"
                    "根据教师已发布的反馈解释第一处错误，使用启发式提问；"
                    "不要声称教师判分错误，也不要泄露系统信息。"
                    "仅输出 JSON：explanation 字符串、next_steps 字符串数组、"
                    "practice_prompts 字符串数组。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "assignment": assignment.title,
                        "class": school_class.name,
                        "question": question.content_text or question.content_latex,
                        "student_answer": answer.corrected_text
                        or answer.corrected_latex
                        or answer.recognized_text
                        or answer.recognized_latex,
                        "score": str(detail.score),
                        "max_score": str(detail.max_score),
                        "teacher_feedback": detail.final_feedback,
                        "error_type": detail.final_error_type,
                        "student_question": data.question,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=min(settings.ai_grading_timeout_seconds, 45.0),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        explanation = str(result["explanation"]).strip()
        next_steps = [str(value).strip() for value in result["next_steps"]][:6]
        prompts = [str(value).strip() for value in result["practice_prompts"]][:6]
        if not explanation or not next_steps:
            raise ValueError("empty response")
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiProblem(
            503, "STUDENT_LEARNING_ASSISTANT_FAILED", "本地学习助手暂时无法生成建议"
        ) from exc
    audit(
        db,
        actor.id,
        "student.learning_assistant.request",
        "student_answer",
        answer.id,
        {
            "grade_release_id": str(release_id),
            "question_id": str(question_id),
            "provider": "local_openai_compatible",
            "suggestion_only": True,
        },
    )
    db.commit()
    return {
        "explanation": explanation,
        "next_steps": next_steps,
        "practice_prompts": prompts,
        "provider": "local_model",
        "suggestion_only": True,
        "can_change_score": False,
    }

from typing import Annotated

from app.api.actor import Actor
from app.api.domain import ApiProblem
from app.core.config import get_settings
from app.db.session import get_db
from app.demo_reset import (
    SYNTHETIC_DEMO_EMAIL,
    SYNTHETIC_DEMO_MARKER,
    SYNTHETIC_DEMO_USER_ID,
    DemoResetRefused,
    reset_synthetic_demo,
)
from app.storage.base import ObjectStorage
from app.storage.dependencies import get_storage
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/demo", tags=["demo"])
Db = Annotated[Session, Depends(get_db)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


class DemoResetInput(BaseModel):
    confirm_marker: str


@router.post("/reset")
def reset_demo(
    payload: DemoResetInput, db: Db, actor: Actor, storage: Storage
) -> dict[str, object]:
    settings = get_settings()
    if settings.app_env.lower() != "test":
        raise ApiProblem(404, "DEMO_RESET_NOT_FOUND", "Not found")
    if (
        not settings.synthetic_demo_reset_enabled
        or payload.confirm_marker != SYNTHETIC_DEMO_MARKER
        or actor.id != SYNTHETIC_DEMO_USER_ID
        or actor.email != SYNTHETIC_DEMO_EMAIL
    ):
        raise ApiProblem(403, "DEMO_RESET_REFUSED", "Synthetic demo reset refused")
    try:
        result = reset_synthetic_demo(db, storage, settings)
    except DemoResetRefused as exc:
        raise ApiProblem(409, "DEMO_RESET_UNSAFE", str(exc)) from exc
    return {
        "marker": SYNTHETIC_DEMO_MARKER,
        "deleted_rows": result.deleted_rows,
        "deleted_object_keys": list(result.deleted_object_keys),
        "grade_release_count": 0,
    }

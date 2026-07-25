import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from app.core.config import get_settings
from app.db.session import get_db
from app.models import Status, User, UserSession
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CurrentActor:
    id: uuid.UUID
    email: str


def digest(value: str) -> str:
    return hmac.new(
        get_settings().session_hmac_secret.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def authenticated_session(request: Request, db: Session) -> tuple[UserSession, User] | None:
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == digest(token)))
    now = datetime.now(UTC)
    expires_at = (
        session.expires_at.replace(tzinfo=UTC)
        if session is not None and session.expires_at.tzinfo is None
        else (session.expires_at if session is not None else now)
    )
    if session is None or session.revoked_at is not None or expires_at <= now:
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status != Status.active:
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.csrf_trusted_origins:
            raise HTTPException(403, "CSRF origin validation failed")
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(digest(csrf), session.csrf_hash):
            raise HTTPException(403, "CSRF 校验失败")
    session.last_seen_at = now
    return session, user


def get_current_actor(request: Request, db: Annotated[Session, Depends(get_db)]) -> CurrentActor:
    authenticated = authenticated_session(request, db)
    if authenticated:
        return CurrentActor(authenticated[1].id, authenticated[1].email)
    settings = get_settings()
    if settings.app_env.lower() == "production" or not settings.demo_actor_enabled:
        raise HTTPException(401, "请先登录")
    user = db.scalar(select(User).where(User.email == settings.demo_actor_email))
    if user is None:
        user = User(
            email=settings.demo_actor_email,
            password_hash="!demo-no-login!",
            display_name="演示教师",
            status=Status.active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return CurrentActor(user.id, user.email)


Actor = Annotated[CurrentActor, Depends(get_current_actor)]

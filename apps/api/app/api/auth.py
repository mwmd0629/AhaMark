import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Annotated

from app.api.actor import authenticated_session, digest
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Status, User, UserSession, now_utc
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[Session, Depends(get_db)]
_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = threading.Lock()


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${value.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def check_rate_limit(key: str) -> None:
    settings = get_settings()
    cutoff = time.monotonic() - settings.auth_login_window_seconds
    with _attempt_lock:
        attempts = _attempts[key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= settings.auth_login_max_attempts:
            raise HTTPException(429, "登录尝试过多，请稍后再试")
        attempts.append(time.monotonic())


def user_view(user: User, csrf_token: str | None = None) -> dict[str, str | None]:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "csrf_token": csrf_token,
    }


@router.post("/login")
def login(
    payload: LoginInput, request: Request, response: Response, db: Db
) -> dict[str, str | None]:
    email = payload.email.lower().strip()
    check_rate_limit(f"{request.client.host if request.client else 'unknown'}:{email}")
    user = db.scalar(select(User).where(User.email == email))
    if (
        user is None
        or user.status != Status.active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(401, "邮箱或密码错误")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    settings = get_settings()
    session = UserSession(
        user_id=user.id,
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        expires_at=now_utc() + timedelta(hours=settings.auth_session_hours),
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure or settings.app_env.lower() == "production",
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "ahamark_csrf",
        csrf,
        max_age=settings.auth_session_hours * 3600,
        httponly=False,
        secure=settings.auth_cookie_secure or settings.app_env.lower() == "production",
        samesite="lax",
        path="/",
    )
    return user_view(user, csrf)


@router.get("/me")
def me(request: Request, db: Db) -> dict[str, str | None]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    return user_view(authenticated[1])


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Db) -> None:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    authenticated[0].revoked_at = now_utc()
    db.commit()
    response.delete_cookie(get_settings().auth_cookie_name, path="/")
    response.delete_cookie("ahamark_csrf", path="/")

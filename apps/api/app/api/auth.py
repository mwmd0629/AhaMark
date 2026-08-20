import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

import structlog
from app.api.actor import Actor, authenticated_session, digest
from app.api.domain import ApiProblem
from app.core.config import get_settings
from app.db.session import get_db
from app.integrations.email_sender import EmailDeliveryError, send_auth_code
from app.models import (
    ArchiveStatus,
    AuditLog,
    AuthEmailChallenge,
    Role,
    SchoolClass,
    Status,
    Student,
    StudentAccountLink,
    User,
    UserRole,
    UserSession,
    now_utc,
)
from app.security.identity import (
    normalize_email,
    normalize_login_name,
    normalize_recovery_email,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[Session, Depends(get_db)]
_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_password_reset_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = threading.Lock()
log = structlog.get_logger()


class LoginInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    identifier: str = Field(
        min_length=1,
        max_length=320,
        validation_alias=AliasChoices("identifier", "email"),
    )
    password: str = Field(min_length=8, max_length=256)

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("登录账号不能为空")
        if "@" in normalized:
            return normalize_email(normalized)
        return normalize_login_name(normalized)

    @property
    def email(self) -> str:
        """Compatibility accessor for callers that still name the field email."""

        return self.identifier


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


def rate_limit_key(key: str) -> str:
    value = hmac.new(
        get_settings().session_hmac_secret.encode(), key.encode(), hashlib.sha256
    ).hexdigest()
    return f"ahamark:auth:login:{value}"


def check_rate_limit(key: str) -> None:
    settings = get_settings()
    if settings.app_env.lower() == "production":
        if redis is None:
            raise HTTPException(503, "登录服务暂时不可用")
        try:
            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            shared_key = rate_limit_key(key)
            count = int(cast(int, client.incr(shared_key)))
            if count == 1:
                client.expire(shared_key, settings.auth_login_window_seconds)
            if count > settings.auth_login_max_attempts:
                log.warning("auth_rate_limit_rejected", service="api")
                raise HTTPException(429, "登录尝试过多，请稍后再试")
            return
        except HTTPException:
            raise
        except Exception:
            log.error("auth_rate_limit_backend_unavailable", service="api")
            if settings.auth_rate_limit_fail_closed:
                raise HTTPException(503, "登录服务暂时不可用") from None
    cutoff = time.monotonic() - settings.auth_login_window_seconds
    with _attempt_lock:
        attempts = _attempts[key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= settings.auth_login_max_attempts:
            raise HTTPException(429, "登录尝试过多，请稍后再试")
        attempts.append(time.monotonic())


def _password_reset_rate_limit_key(key: str) -> str:
    value = hmac.new(
        get_settings().session_hmac_secret.encode(), key.encode(), hashlib.sha256
    ).hexdigest()
    return f"ahamark:auth:password-reset:{value}"


def check_password_reset_rate_limit(key: str) -> None:
    settings = get_settings()
    maximum = settings.auth_password_reset_max_requests
    window = settings.auth_password_reset_window_seconds
    if settings.app_env.lower() == "production":
        if redis is None:
            raise HTTPException(503, "密码找回服务暂时不可用")
        try:
            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            shared_key = _password_reset_rate_limit_key(key)
            count = int(cast(int, client.incr(shared_key)))
            if count == 1:
                client.expire(shared_key, window)
            if count > maximum:
                raise HTTPException(429, "找回密码请求过多，请稍后再试")
            return
        except HTTPException:
            raise
        except Exception:
            log.error("password_reset_rate_limit_backend_unavailable", service="api")
            if settings.auth_rate_limit_fail_closed:
                raise HTTPException(503, "密码找回服务暂时不可用") from None
    cutoff = time.monotonic() - window
    with _attempt_lock:
        attempts = _password_reset_attempts[key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= maximum:
            raise HTTPException(429, "找回密码请求过多，请稍后再试")
        attempts.append(time.monotonic())


def user_view(db: Session, user: User, csrf_token: str | None = None) -> dict[str, Any]:
    roles = sorted(
        db.scalars(
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        ).all()
    )
    has_any_student_link = bool(
        db.scalar(
            select(StudentAccountLink.id).where(StudentAccountLink.user_id == user.id).limit(1)
        )
    )
    active_student_link = bool(
        db.scalar(
            select(StudentAccountLink.id)
            .join(Student, Student.id == StudentAccountLink.student_id)
            .where(
                StudentAccountLink.user_id == user.id,
                StudentAccountLink.status == "active",
                Student.status == "active",
            )
            .limit(1)
        )
    )
    student_account = "student" in roles or has_any_student_link
    if user.must_change_password:
        landing_surface = "change_password"
    elif active_student_link:
        landing_surface = "student"
    elif student_account:
        landing_surface = "account_unavailable"
    else:
        landing_surface = "teacher"
    recovery_email = user.email if student_account else None
    return {
        "id": str(user.id),
        "email": recovery_email if student_account else user.email,
        "recovery_email": recovery_email,
        "login_name": user.login_name,
        "display_name": user.display_name,
        "csrf_token": csrf_token,
        "must_change_password": user.must_change_password,
        "recovery_email_verified": bool(
            recovery_email is not None and user.email_verified_at is not None
        ),
        "roles": roles,
        "active_student_link": active_student_link,
        "landing_surface": landing_surface,
    }


@router.post("/login")
def login(payload: LoginInput, request: Request, response: Response, db: Db) -> dict[str, Any]:
    identifier = payload.identifier.strip()
    check_rate_limit(f"{request.client.host if request.client else 'unknown'}:{identifier}")
    user: User | None = None
    if "@" in identifier:
        try:
            email = normalize_email(identifier)
        except ValueError:
            email = ""
        if email:
            candidate = db.scalar(select(User).where(User.email == email))
            # Once a student has a dedicated login name, the recovery email is no
            # longer accepted as a primary credential. Legacy linked accounts that
            # could not be migrated remain usable until a teacher resolves the
            # student-number collision.
            if candidate is not None:
                has_student_link = bool(
                    db.scalar(
                        select(StudentAccountLink.id)
                        .where(StudentAccountLink.user_id == candidate.id)
                        .limit(1)
                    )
                )
                if not has_student_link or candidate.login_name is None:
                    user = candidate
    else:
        try:
            login_name = normalize_login_name(identifier)
        except ValueError:
            login_name = ""
        if login_name:
            user = db.scalar(select(User).where(User.login_name == login_name))
    if (
        user is None
        or user.status != Status.active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(401, "账号或密码错误")
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
    return user_view(db, user, csrf)


@router.get("/me")
def me(request: Request, db: Db) -> dict[str, Any]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    return user_view(db, authenticated[1])


class ChangePasswordInput(BaseModel):
    current_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordInput,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    user = authenticated[1]
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "当前密码错误")
    if hmac.compare_digest(payload.current_password, payload.new_password):
        raise HTTPException(422, "新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    return user_view(db, user)


class EmailChallengeConfirmInput(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")


class PasswordResetRequestInput(BaseModel):
    identifier: str = Field(min_length=1, max_length=64)
    recovery_email: str = Field(min_length=3, max_length=320)

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return normalize_login_name(value)

    @field_validator("recovery_email")
    @classmethod
    def validate_recovery_email(cls, value: str) -> str:
        return normalize_recovery_email(value)


class PasswordResetConfirmInput(EmailChallengeConfirmInput):
    new_password: str = Field(min_length=12, max_length=256)


def _recovery_hmac_secret() -> str:
    settings = get_settings()
    if settings.auth_recovery_hmac_secret is not None:
        configured = settings.auth_recovery_hmac_secret.get_secret_value().strip()
        if configured:
            return configured
    return settings.session_hmac_secret


def _auth_code_digest(challenge_id: uuid.UUID, code: str) -> str:
    return hmac.new(
        _recovery_hmac_secret().encode(),
        f"{challenge_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _active_student_user(db: Session, login_name: str) -> User | None:
    return db.scalar(
        select(User)
        .join(StudentAccountLink, StudentAccountLink.user_id == User.id)
        .join(Student, Student.id == StudentAccountLink.student_id)
        .where(
            User.login_name == login_name,
            User.status == Status.active,
            StudentAccountLink.status == "active",
            Student.status == ArchiveStatus.active,
        )
    )


def _expire_open_challenges(db: Session, user_id: uuid.UUID, purpose: str) -> None:
    timestamp = now_utc()
    for challenge in db.scalars(
        select(AuthEmailChallenge).where(
            AuthEmailChallenge.user_id == user_id,
            AuthEmailChallenge.purpose == purpose,
            AuthEmailChallenge.consumed_at.is_(None),
        )
    ).all():
        challenge.consumed_at = timestamp


def _create_email_challenge(
    db: Session, user: User, purpose: Literal["verify_email", "reset_password"]
) -> tuple[AuthEmailChallenge, str | None]:
    settings = get_settings()
    recipient = user.email
    if recipient is None:
        raise EmailDeliveryError("Recovery email is not configured.")
    _expire_open_challenges(db, user.id, purpose)
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = AuthEmailChallenge(
        user_id=user.id,
        purpose=purpose,
        email_snapshot=recipient,
        code_hash="",
        expires_at=now_utc() + timedelta(seconds=settings.auth_email_code_ttl_seconds),
        attempts=0,
    )
    db.add(challenge)
    db.flush()
    challenge.code_hash = _auth_code_digest(challenge.id, code)
    development_preview = send_auth_code(
        recipient,
        code,
        purpose,
        settings.auth_email_code_ttl_seconds,
    )
    return challenge, code if development_preview else None


def _locked_valid_challenge(
    db: Session,
    challenge_id: uuid.UUID,
    code: str,
    purpose: Literal["verify_email", "reset_password"],
    *,
    user_id: uuid.UUID | None = None,
) -> AuthEmailChallenge | None:
    challenge = db.scalar(
        select(AuthEmailChallenge).where(AuthEmailChallenge.id == challenge_id).with_for_update()
    )
    now = datetime.now(UTC)
    expires_at = (
        challenge.expires_at.replace(tzinfo=UTC)
        if challenge is not None and challenge.expires_at.tzinfo is None
        else (challenge.expires_at if challenge is not None else now)
    )
    settings = get_settings()
    if (
        challenge is None
        or challenge.purpose != purpose
        or (user_id is not None and challenge.user_id != user_id)
        or challenge.consumed_at is not None
        or expires_at <= now
        or challenge.attempts >= settings.auth_email_code_max_attempts
    ):
        return None
    if not hmac.compare_digest(challenge.code_hash, _auth_code_digest(challenge.id, code)):
        challenge.attempts += 1
        if challenge.attempts >= settings.auth_email_code_max_attempts:
            challenge.consumed_at = now_utc()
        db.commit()
        return None
    return challenge


def _challenge_response(
    challenge_id: uuid.UUID,
    message: str,
    development_code: str | None,
) -> dict[str, Any]:
    return {
        "challenge_id": str(challenge_id),
        "message": message,
        "expires_in_seconds": get_settings().auth_email_code_ttl_seconds,
        "development_code": development_code,
    }


class RecoveryEmailUpdateInput(BaseModel):
    recovery_email: str | None = Field(None, max_length=320)
    current_password: str = Field(min_length=8, max_length=256)

    @field_validator("recovery_email", mode="before")
    @classmethod
    def validate_recovery_email(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str):
            raise ValueError("安全邮箱格式无效")
        return normalize_recovery_email(value)


@router.put("/recovery-email")
def update_recovery_email(
    payload: RecoveryEmailUpdateInput, request: Request, db: Db
) -> dict[str, Any]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    session_user = authenticated[1]
    if not verify_password(payload.current_password, session_user.password_hash):
        raise HTTPException(401, "当前密码错误")
    user = db.scalar(select(User).where(User.id == session_user.id).with_for_update())
    if user is None or user.login_name is None or _active_student_user(db, user.login_name) is None:
        raise HTTPException(403, "只有已绑定的学生账号可以设置安全邮箱")

    previous_email = user.email
    if previous_email == payload.recovery_email:
        return user_view(db, user)
    if payload.recovery_email is not None:
        conflict = db.scalar(
            select(User.id).where(
                User.email == payload.recovery_email,
                User.id != user.id,
            )
        )
        if conflict is not None:
            raise HTTPException(409, "该安全邮箱已被其他账号使用")

    user.email = payload.recovery_email
    user.email_verified_at = None
    _expire_open_challenges(db, user.id, "verify_email")
    _expire_open_challenges(db, user.id, "reset_password")
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.recovery_email.update",
            resource_type="user",
            resource_id=str(user.id),
            metadata_={
                "previously_configured": previous_email is not None,
                "configured": payload.recovery_email is not None,
                "verification_cleared": True,
            },
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该安全邮箱已被其他账号使用") from None
    return user_view(db, user)


@router.post("/email-verification/request")
def request_email_verification(request: Request, db: Db) -> dict[str, Any]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    user = authenticated[1]
    if _active_student_user(db, user.login_name or "") is None:
        raise HTTPException(403, "只有已绑定的学生账号可以验证安全邮箱")
    if user.email is None:
        raise HTTPException(409, "请先设置安全邮箱")
    client_host = request.client.host if request.client else "unknown"
    check_password_reset_rate_limit(f"verify-email:{client_host}:{user.id}")
    try:
        challenge, development_code = _create_email_challenge(db, user, "verify_email")
        db.add(
            AuditLog(
                actor_id=user.id,
                action="auth.recovery_email_verification.request",
                resource_type="user",
                resource_id=str(user.id),
                metadata_={"delivery": "development_preview" if development_code else "smtp"},
            )
        )
        db.commit()
    except EmailDeliveryError:
        db.rollback()
        raise HTTPException(503, "验证码暂时无法发送，请稍后重试") from None
    return _challenge_response(
        challenge.id,
        "验证码已发送到安全邮箱",
        development_code,
    )


@router.post("/email-verification/confirm")
def confirm_email_verification(
    payload: EmailChallengeConfirmInput, request: Request, db: Db
) -> dict[str, Any]:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    user = authenticated[1]
    challenge = _locked_valid_challenge(
        db, payload.challenge_id, payload.code, "verify_email", user_id=user.id
    )
    if challenge is None or challenge.email_snapshot != user.email:
        raise HTTPException(422, "验证码无效或已过期")
    challenge.consumed_at = now_utc()
    user.email_verified_at = now_utc()
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.recovery_email_verification.confirm",
            resource_type="user",
            resource_id=str(user.id),
            metadata_={},
        )
    )
    db.commit()
    return user_view(db, user)


PASSWORD_RESET_GENERIC_MESSAGE = "若账号与已验证安全邮箱匹配，验证码将发送到该邮箱"


@router.post("/password-reset/request", status_code=202)
def request_password_reset(
    payload: PasswordResetRequestInput, request: Request, db: Db
) -> dict[str, Any]:
    client_host = request.client.host if request.client else "unknown"
    check_password_reset_rate_limit(f"{client_host}:{payload.identifier}:{payload.recovery_email}")
    response_challenge_id = uuid.uuid4()
    development_code: str | None = None
    user = _active_student_user(db, payload.identifier)
    if (
        user is not None
        and user.email_verified_at is not None
        and user.email is not None
        and hmac.compare_digest(user.email, payload.recovery_email)
    ):
        try:
            challenge, development_code = _create_email_challenge(db, user, "reset_password")
            response_challenge_id = challenge.id
            db.add(
                AuditLog(
                    actor_id=user.id,
                    action="auth.password_reset.request",
                    resource_type="user",
                    resource_id=str(user.id),
                    metadata_={"delivery": "development_preview" if development_code else "smtp"},
                )
            )
            db.commit()
        except EmailDeliveryError:
            db.rollback()
            log.error("password_reset_email_delivery_failed", service="api")
    return _challenge_response(
        response_challenge_id,
        PASSWORD_RESET_GENERIC_MESSAGE,
        development_code,
    )


@router.post("/password-reset/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirmInput, request: Request, db: Db
) -> dict[str, str]:
    client_host = request.client.host if request.client else "unknown"
    check_password_reset_rate_limit(f"confirm-reset:{client_host}:{payload.challenge_id}")
    challenge = _locked_valid_challenge(db, payload.challenge_id, payload.code, "reset_password")
    if challenge is None:
        raise HTTPException(422, "验证码无效或已过期")
    candidate = db.get(User, challenge.user_id)
    user = _active_student_user(db, candidate.login_name or "") if candidate is not None else None
    if (
        user is None
        or user.id != challenge.user_id
        or user.email_verified_at is None
        or user.email is None
        or challenge.email_snapshot != user.email
    ):
        challenge.consumed_at = now_utc()
        db.commit()
        raise HTTPException(422, "验证码无效或已过期")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    challenge.consumed_at = now_utc()
    for session in db.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
    ).all():
        session.revoked_at = now_utc()
    db.add(
        AuditLog(
            actor_id=user.id,
            action="auth.password_reset.confirm",
            resource_type="user",
            resource_id=str(user.id),
            metadata_={"sessions_revoked": True},
        )
    )
    db.commit()
    return {"message": "密码已重置，请使用学号和新密码登录"}


PREFERENCE_ACTION = "user_preferences.update"
PREFERENCE_RESOURCE = "user_preferences"


class TeacherPreferenceValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_class_id: uuid.UUID | None = None
    rubric_status_filter: Literal["all", "draft", "confirmed", "retired"] = "all"
    rubric_page_size: Literal[10, 20, 50] = 20
    compact_rubric_cards: bool = False


class TeacherPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=120)
    preferences: TeacherPreferenceValues

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name 不能为空")
        return value


def _latest_preferences(db: Session, user_id: uuid.UUID) -> AuditLog | None:
    return db.scalar(
        select(AuditLog)
        .where(
            AuditLog.actor_id == user_id,
            AuditLog.action == PREFERENCE_ACTION,
            AuditLog.resource_type == PREFERENCE_RESOURCE,
            AuditLog.resource_id == str(user_id),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )


def _preference_view(db: Session, user: User) -> dict[str, Any]:
    latest = _latest_preferences(db, user.id)
    metadata = latest.metadata_ if latest is not None else {}
    raw_preferences = metadata.get("preferences", {})
    try:
        preferences = TeacherPreferenceValues.model_validate(raw_preferences)
    except ValueError:
        preferences = TeacherPreferenceValues()
    return {
        "profile": {"display_name": user.display_name, "email": user.email},
        "preferences": preferences.model_dump(mode="json"),
        "revision": int(metadata.get("revision", 0)),
        "updated_at": latest.created_at if latest is not None else None,
        "server_managed": {
            "external_ai_enabled": get_settings().ai_external_requests_enabled,
            "ai_configuration_editable": False,
        },
    }


@router.get("/preferences")
def get_preferences(db: Db, actor: Actor) -> dict[str, Any]:
    user = db.get(User, actor.id)
    if user is None:
        raise ApiProblem(404, "USER_NOT_FOUND", "用户不存在")
    return _preference_view(db, user)


@router.put("/preferences")
def update_preferences(payload: TeacherPreferenceUpdate, db: Db, actor: Actor) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.id == actor.id).with_for_update())
    if user is None:
        raise ApiProblem(404, "USER_NOT_FOUND", "用户不存在")
    latest = _latest_preferences(db, user.id)
    current_revision = int(latest.metadata_.get("revision", 0)) if latest is not None else 0
    if payload.expected_revision != current_revision:
        raise ApiProblem(
            409,
            "PREFERENCES_VERSION_CONFLICT",
            "设置已在其他页面更新，请刷新后重试",
            {"current_revision": current_revision},
        )
    default_class_id = payload.preferences.default_class_id
    if default_class_id is not None:
        owned_class = db.scalar(
            select(SchoolClass.id).where(
                SchoolClass.id == default_class_id,
                SchoolClass.owner_id == actor.id,
                SchoolClass.status == ArchiveStatus.active,
            )
        )
        if owned_class is None:
            raise ApiProblem(422, "DEFAULT_CLASS_NOT_AVAILABLE", "默认班级不存在或已归档")
    user.display_name = payload.display_name
    next_revision = current_revision + 1
    db.add(
        AuditLog(
            actor_id=actor.id,
            action=PREFERENCE_ACTION,
            resource_type=PREFERENCE_RESOURCE,
            resource_id=str(actor.id),
            metadata_={
                "schema_version": 1,
                "revision": next_revision,
                "preferences": payload.preferences.model_dump(mode="json"),
            },
        )
    )
    db.commit()
    return _preference_view(db, user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Db) -> None:
    authenticated = authenticated_session(request, db)
    if not authenticated:
        raise HTTPException(401, "请先登录")
    authenticated[0].revoked_at = now_utc()
    db.commit()
    response.delete_cookie(get_settings().auth_cookie_name, path="/")
    response.delete_cookie("ahamark_csrf", path="/")

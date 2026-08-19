import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from app.account_management import AccountType, account_type_for, create_managed_account
from app.api.actor import authenticated_session
from app.api.auth import hash_password
from app.db.session import get_db
from app.models import AuditLog, Status, User, UserSession, now_utc
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

router = APIRouter(prefix="/admin/accounts", tags=["admin-accounts"])
Db = Annotated[Session, Depends(get_db)]


class AccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)
    account_type: AccountType


class AccountPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "inactive"] | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class BulkAccountRow(BaseModel):
    username: str = Field(max_length=64)
    display_name: str = Field(max_length=120)
    password: str = Field(max_length=256)
    account_type: Literal["teacher", "student"]


class BulkAccountCreate(BaseModel):
    rows: list[BulkAccountRow] = Field(min_length=1, max_length=200)


class BulkAccountAction(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: Literal["activate", "deactivate", "revoke_sessions"]
    confirmed: Literal[True]


def require_admin(request: Request, db: Db) -> User:
    authenticated = authenticated_session(request, db)
    if authenticated is None:
        raise HTTPException(401, "请先登录")
    user = authenticated[1]
    if "admin" not in {role.name for role in user.roles}:
        raise HTTPException(403, "需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def account_view(
    user: User,
    session_stats: dict[uuid.UUID, tuple[int, datetime | None]],
) -> dict[str, object]:
    active_sessions, last_seen_at = session_stats.get(user.id, (0, None))
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "account_type": account_type_for(user),
        "status": user.status.value,
        "active_session_count": active_sessions,
        "last_seen_at": last_seen_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def active_session_stats(
    db: Session, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, datetime | None]]:
    if not user_ids:
        return {}
    active = (UserSession.revoked_at.is_(None)) & (UserSession.expires_at > now_utc())
    rows = db.execute(
        select(
            UserSession.user_id,
            func.sum(case((active, 1), else_=0)),
            func.max(UserSession.last_seen_at),
        )
        .where(UserSession.user_id.in_(user_ids))
        .group_by(UserSession.user_id)
    ).all()
    return {
        user_id: (int(active_count or 0), last_seen_at)
        for user_id, active_count, last_seen_at in rows
    }


def audit(
    db: Session,
    actor_id: uuid.UUID,
    action: str,
    target: User,
    details: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type="user_account",
            resource_id=str(target.id),
            metadata_=details,
        )
    )


def audit_event(
    db: Session,
    actor_id: uuid.UUID,
    action: str,
    details: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type="user_account",
            resource_id=None,
            metadata_=details,
        )
    )


def get_account(db: Session, account_id: uuid.UUID) -> User:
    user = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == account_id)
    )
    if user is None:
        raise HTTPException(404, "账号不存在")
    return user


def active_admin_count_locked(db: Session) -> int:
    users = list(
        db.scalars(
            select(User).options(selectinload(User.roles)).with_for_update()
        ).all()
    )
    return sum(
        account_type_for(user) == "admin" and user.status == Status.active for user in users
    )


def revoke_sessions(db: Session, user_id: uuid.UUID) -> int:
    sessions = list(
        db.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
        ).all()
    )
    revoked_at = now_utc()
    for session in sessions:
        session.revoked_at = revoked_at
    return len(sessions)


def spreadsheet_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("")
def list_accounts(
    db: Db,
    admin: AdminUser,
    query: str | None = Query(default=None, max_length=120),
    account_type: AccountType | None = None,
    account_status: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    del admin
    users = list(db.scalars(select(User).options(selectinload(User.roles))).all())
    normalized_query = (query or "").strip().lower()
    filtered = [
        user
        for user in users
        if (account_type is None or account_type_for(user) == account_type)
        and (account_status is None or user.status.value == account_status)
        and (
            not normalized_query
            or normalized_query in user.username.lower()
            or normalized_query in user.display_name.lower()
        )
    ]
    filtered.sort(key=lambda item: (item.status != Status.active, item.username))
    page = filtered[offset : offset + limit]
    stats = active_session_stats(db, [user.id for user in page])
    summary = {
        role: {
            "total": sum(account_type_for(user) == role for user in users),
            "active": sum(
                account_type_for(user) == role and user.status == Status.active for user in users
            ),
        }
        for role in ("teacher", "student", "admin")
    }
    return {
        "items": [account_view(user, stats) for user in page],
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
        "summary": summary,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: Db, admin: AdminUser) -> dict[str, object]:
    try:
        user = create_managed_account(
            db,
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            account_type=payload.account_type,
        )
        audit(
            db,
            admin.id,
            "admin.account.create",
            user,
            {"account_type": payload.account_type, "username": user.username},
        )
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, ValueError) else "用户名已存在"
        raise HTTPException(409, message) from exc
    db.refresh(user)
    return account_view(user, {})


@router.post("/bulk", status_code=status.HTTP_201_CREATED)
def bulk_create_accounts(
    payload: BulkAccountCreate,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    created: list[User] = []
    errors: list[dict[str, object]] = []
    seen_usernames: set[str] = set()
    for index, row in enumerate(payload.rows, start=2):
        normalized_hint = row.username.strip().lower()
        if normalized_hint in seen_usernames:
            errors.append(
                {
                    "row_number": index,
                    "username": normalized_hint,
                    "message": "CSV 中用户名重复",
                }
            )
            continue
        seen_usernames.add(normalized_hint)
        try:
            user = create_managed_account(
                db,
                username=row.username,
                display_name=row.display_name,
                password=row.password,
                account_type=row.account_type,
            )
        except ValueError as exc:
            errors.append(
                {
                    "row_number": index,
                    "username": normalized_hint,
                    "message": str(exc),
                }
            )
            continue
        audit(
            db,
            admin.id,
            "admin.account.create",
            user,
            {
                "account_type": row.account_type,
                "username": user.username,
                "source": "csv_bulk",
            },
        )
        created.append(user)
    db.flush()
    audit_event(
        db,
        admin.id,
        "admin.account.bulk_create",
        {
            "requested_count": len(payload.rows),
            "created_count": len(created),
            "error_count": len(errors),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "导入期间账号发生冲突，请刷新后重试") from exc
    for user in created:
        db.refresh(user)
    return {
        "created": [account_view(user, {}) for user in created],
        "errors": errors,
        "requested_count": len(payload.rows),
    }


@router.post("/bulk-actions")
def bulk_account_action(
    payload: BulkAccountAction,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    account_ids = list(dict.fromkeys(payload.account_ids))
    users = {
        user.id: user
        for user in db.scalars(
            select(User).options(selectinload(User.roles)).where(User.id.in_(account_ids))
        ).all()
    }
    active_admins = active_admin_count_locked(db)
    processed: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    action_names = {
        "activate": "admin.account.bulk_activate",
        "deactivate": "admin.account.bulk_deactivate",
        "revoke_sessions": "admin.account.bulk_revoke_sessions",
    }
    for account_id in account_ids:
        user = users.get(account_id)
        if user is None:
            errors.append({"account_id": str(account_id), "message": "账号不存在"})
            continue
        if user.id == admin.id and payload.action in {"deactivate", "revoke_sessions"}:
            errors.append(
                {
                    "account_id": str(account_id),
                    "username": user.username,
                    "message": "不能批量停用当前管理员或撤销其当前会话",
                }
            )
            continue
        sessions_revoked = 0
        changed = False
        if payload.action == "activate":
            if user.status != Status.active:
                user.status = Status.active
                changed = True
        elif payload.action == "deactivate":
            if user.status == Status.active:
                if account_type_for(user) == "admin" and active_admins <= 1:
                    errors.append(
                        {
                            "account_id": str(account_id),
                            "username": user.username,
                            "message": "必须至少保留一个启用的管理员账号",
                        }
                    )
                    continue
                if account_type_for(user) == "admin":
                    active_admins -= 1
                user.status = Status.inactive
                sessions_revoked = revoke_sessions(db, user.id)
                changed = True
        else:
            sessions_revoked = revoke_sessions(db, user.id)
            changed = sessions_revoked > 0
        audit(
            db,
            admin.id,
            action_names[payload.action],
            user,
            {
                "username": user.username,
                "changed": changed,
                "sessions_revoked": sessions_revoked,
            },
        )
        processed.append(
            {
                "account_id": str(user.id),
                "username": user.username,
                "status": user.status.value,
                "changed": changed,
                "sessions_revoked": sessions_revoked,
            }
        )
    db.flush()
    audit_event(
        db,
        admin.id,
        "admin.account.bulk_action",
        {
            "action": payload.action,
            "requested_count": len(account_ids),
            "processed_count": len(processed),
            "error_count": len(errors),
        },
    )
    db.commit()
    return {
        "action": payload.action,
        "processed": processed,
        "errors": errors,
        "requested_count": len(account_ids),
    }


@router.get("/export.csv")
def export_accounts(
    db: Db,
    admin: AdminUser,
    query: str | None = Query(default=None, max_length=120),
    account_type: AccountType | None = None,
    account_status: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
) -> Response:
    del admin
    users = list(db.scalars(select(User).options(selectinload(User.roles))).all())
    normalized_query = (query or "").strip().lower()
    users = [
        user
        for user in users
        if (account_type is None or account_type_for(user) == account_type)
        and (account_status is None or user.status.value == account_status)
        and (
            not normalized_query
            or normalized_query in user.username.lower()
            or normalized_query in user.display_name.lower()
        )
    ]
    users.sort(key=lambda user: user.username)
    stats = active_session_stats(db, [user.id for user in users])
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["username", "display_name", "account_type", "status", "active_sessions", "last_seen_at"]
    )
    for user in users:
        active_sessions, last_seen_at = stats.get(user.id, (0, None))
        writer.writerow(
            [
                spreadsheet_safe(user.username),
                spreadsheet_safe(user.display_name),
                account_type_for(user),
                user.status.value,
                active_sessions,
                last_seen_at.isoformat() if last_seen_at else "",
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ahamark-accounts.csv"'},
    )


@router.get("/security")
def account_security_overview(
    request: Request,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    del admin
    current = authenticated_session(request, db)
    current_session_id = current[0].id if current is not None else None
    current_time = now_utc()
    users = list(db.scalars(select(User).options(selectinload(User.roles))).all())
    all_stats = active_session_stats(db, [user.id for user in users])
    active_condition = (UserSession.revoked_at.is_(None)) & (
        UserSession.expires_at > current_time
    )
    active_session_count = (
        db.scalar(select(func.count(UserSession.id)).where(active_condition)) or 0
    )
    active_sessions = list(
        db.scalars(
            select(UserSession)
            .where(active_condition)
            .order_by(UserSession.last_seen_at.desc())
            .limit(100)
        ).all()
    )
    usernames = {user.id: user.username for user in users}
    failed_condition = (
        (AuditLog.action == "auth.login.failed")
        & (AuditLog.created_at >= current_time - timedelta(hours=24))
    )
    failed_logins = db.scalar(select(func.count(AuditLog.id)).where(failed_condition)) or 0
    stale_cutoff = current_time - timedelta(days=90)
    never_logged_in = sum(user.id not in all_stats for user in users)
    stale_accounts = sum(
        last_seen_at is not None and as_utc(last_seen_at) < stale_cutoff
        for _, last_seen_at in all_stats.values()
    )
    multiple_sessions = sum(active_count > 1 for active_count, _ in all_stats.values())
    return {
        "failed_logins_24h": failed_logins,
        "active_sessions": active_session_count,
        "accounts_with_multiple_sessions": multiple_sessions,
        "never_logged_in_accounts": never_logged_in,
        "stale_accounts_90d": stale_accounts,
        "sessions": [
            {
                "id": str(session.id),
                "user_id": str(session.user_id),
                "username": usernames.get(session.user_id, "已删除账号"),
                "created_at": session.created_at,
                "last_seen_at": session.last_seen_at,
                "expires_at": session.expires_at,
                "is_current": session.id == current_session_id,
            }
            for session in active_sessions
        ],
    }


@router.post("/sessions/{session_id}/revoke")
def revoke_account_session(
    session_id: uuid.UUID,
    request: Request,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    current = authenticated_session(request, db)
    if current is None:
        raise HTTPException(401, "请先登录")
    session = db.scalar(select(UserSession).where(UserSession.id == session_id))
    if (
        session is None
        or session.revoked_at is not None
        or as_utc(session.expires_at) <= now_utc()
    ):
        raise HTTPException(404, "活动会话不存在")
    if session.id == current[0].id:
        raise HTTPException(409, "不能在安全面板撤销当前登录会话")
    user = get_account(db, session.user_id)
    session.revoked_at = now_utc()
    audit(
        db,
        admin.id,
        "admin.account.session_revoke",
        user,
        {"username": user.username, "session_id": str(session.id)},
    )
    db.commit()
    return {"ok": True, "session_id": str(session.id), "username": user.username}


@router.get("/audit")
def list_account_audit(
    db: Db,
    admin: AdminUser,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    del admin
    condition = AuditLog.action.like("admin.account.%")
    total = db.scalar(select(func.count(AuditLog.id)).where(condition)) or 0
    entries = list(
        db.scalars(
            select(AuditLog)
            .where(condition)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    user_ids = {
        user_id
        for entry in entries
        for user_id in (
            entry.actor_id,
            uuid.UUID(entry.resource_id) if entry.resource_id else None,
        )
        if user_id is not None
    }
    usernames = {
        user.id: user.username
        for user in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    return {
        "items": [
            {
                "id": str(entry.id),
                "action": entry.action,
                "actor_username": (
                    usernames.get(entry.actor_id, "已删除账号")
                    if entry.actor_id is not None
                    else "系统"
                ),
                "target_username": (
                    usernames.get(uuid.UUID(entry.resource_id), "已删除账号")
                    if entry.resource_id
                    else None
                ),
                "details": entry.metadata_,
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/{account_id}")
def update_account(
    account_id: uuid.UUID,
    payload: AccountPatch,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    user = get_account(db, account_id)
    changes: dict[str, object] = {}
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(422, "姓名不能为空")
        if display_name != user.display_name:
            changes["display_name"] = {"from": user.display_name, "to": display_name}
            user.display_name = display_name
    if payload.status is not None:
        next_status = Status(payload.status)
        if next_status != user.status:
            if user.id == admin.id and next_status == Status.inactive:
                raise HTTPException(409, "不能停用当前登录的管理员账号")
            if account_type_for(user) == "admin" and next_status == Status.inactive:
                active_admins = active_admin_count_locked(db)
                if active_admins <= 1:
                    raise HTTPException(409, "必须至少保留一个启用的管理员账号")
            changes["status"] = {"from": user.status.value, "to": next_status.value}
            user.status = next_status
            if next_status == Status.inactive:
                revoked = revoke_sessions(db, user.id)
                changes["sessions_revoked"] = revoked
    if changes:
        audit(db, admin.id, "admin.account.update", user, changes)
        db.commit()
        db.refresh(user)
    stats = active_session_stats(db, [user.id])
    return account_view(user, stats)


@router.post("/{account_id}/reset-password")
def reset_password(
    account_id: uuid.UUID,
    payload: PasswordReset,
    db: Db,
    admin: AdminUser,
) -> dict[str, object]:
    user = get_account(db, account_id)
    if user.id == admin.id:
        raise HTTPException(409, "不能在此重置当前管理员密码，请由另一管理员操作")
    user.password_hash = hash_password(payload.password)
    user.must_change_password = False
    revoked = revoke_sessions(db, user.id)
    audit(
        db,
        admin.id,
        "admin.account.password_reset",
        user,
        {"username": user.username, "sessions_revoked": revoked},
    )
    db.commit()
    return {"ok": True, "sessions_revoked": revoked}

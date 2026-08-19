from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import hash_password, normalize_email, normalize_username
from app.models import Role, Status, User

AccountType = Literal["teacher", "student", "admin"]
ACCOUNT_TYPES: tuple[AccountType, ...] = ("teacher", "student", "admin")
ROLE_DESCRIPTIONS: dict[AccountType, str] = {
    "teacher": "教师端账号",
    "student": "学生端账号",
    "admin": "系统管理员账号",
}


def ensure_role(db: Session, account_type: AccountType) -> Role:
    role = db.scalar(select(Role).where(Role.name == account_type))
    if role is None:
        role = Role(name=account_type, description=ROLE_DESCRIPTIONS[account_type])
        db.add(role)
        db.flush()
    return role


def account_type_for(user: User) -> AccountType:
    names = {role.name for role in user.roles}
    for candidate in ACCOUNT_TYPES[::-1]:
        if candidate in names:
            return candidate
    # 兼容 0050 之前由 create_teacher 创建、尚未带显式角色的教师账号。
    return "teacher"


def create_managed_account(
    db: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    account_type: AccountType,
) -> User:
    normalized_username = normalize_username(username)
    normalized_display_name = display_name.strip()
    if not normalized_display_name:
        raise ValueError("姓名不能为空")
    if len(normalized_display_name) > 120:
        raise ValueError("姓名不能超过 120 个字符")
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    if len(password) > 256:
        raise ValueError("密码不能超过 256 个字符")
    email = normalize_email(f"{normalized_username}@ahamark.local")
    if db.scalar(select(User).where(User.username == normalized_username)):
        raise ValueError("该用户名已存在")
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("该账号的内部邮箱已存在")
    role = ensure_role(db, account_type)
    user = User(
        username=normalized_username,
        email=email,
        display_name=normalized_display_name,
        password_hash=hash_password(password),
        status=Status.active,
        roles=[role],
    )
    db.add(user)
    db.flush()
    return user

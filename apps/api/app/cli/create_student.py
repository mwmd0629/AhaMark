import argparse
import getpass

from sqlalchemy import select

from app.api.auth import hash_password, normalize_email
from app.db.session import SessionLocal
from app.models import Role, Status, User, UserRole


def create_student_account(email: str, display_name: str, password: str) -> User:
    normalized = normalize_email(email)
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == normalized)):
            raise ValueError("该邮箱已存在")
        role = db.scalar(select(Role).where(Role.name == "student"))
        if role is None:
            role = Role(name="student", description="学生端账号")
            db.add(role)
            db.flush()
        user = User(
            email=normalized,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            status=Status.active,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 AhaMark 学生账号")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("密码（不会回显）: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致")
    user = create_student_account(args.email, args.display_name, password)
    print(f"学生账号已创建：{user.email}")


if __name__ == "__main__":
    main()

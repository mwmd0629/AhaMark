import argparse
import getpass

from sqlalchemy import select

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User
from app.security.identity import normalize_email


def create_teacher(email: str, display_name: str, password: str) -> User:
    normalized = normalize_email(email)
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == normalized)):
            raise ValueError("该邮箱已存在")
        user = User(
            email=normalized,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            status=Status.active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 AhaMark 教师账号")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("密码（不会回显）: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致")
    user = create_teacher(args.email, args.display_name, password)
    print(f"教师账号已创建：{user.email}")


if __name__ == "__main__":
    main()

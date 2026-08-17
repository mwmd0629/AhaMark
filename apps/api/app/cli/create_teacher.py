import argparse
import getpass

from sqlalchemy import select

from app.api.auth import hash_password, normalize_email, normalize_username
from app.db.session import SessionLocal
from app.models import Status, User


def create_teacher(username: str, display_name: str, password: str) -> User:
    normalized_username = normalize_username(username)
    email = normalize_email(f"{normalized_username}@ahamark.local")
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == normalized_username)):
            raise ValueError("该用户名已存在")
        if db.scalar(select(User).where(User.email == email)):
            raise ValueError("该账号的内部邮箱已存在")
        user = User(
            username=normalized_username,
            email=email,
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
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("密码（不会回显）: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致")
    user = create_teacher(args.username, args.display_name, password)
    print(f"教师账号已创建：{user.username}")


if __name__ == "__main__":
    main()

import argparse
import getpass

from app.account_management import create_managed_account
from app.db.session import SessionLocal
from app.models import User


def create_admin_account(username: str, display_name: str, password: str) -> User:
    with SessionLocal() as db:
        user = create_managed_account(
            db,
            username=username,
            display_name=display_name,
            password=password,
            account_type="admin",
        )
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 AhaMark 管理员账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("密码（不会回显）: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次密码不一致")
    user = create_admin_account(args.username, args.display_name, password)
    print(f"管理员账号已创建：{user.username}")


if __name__ == "__main__":
    main()

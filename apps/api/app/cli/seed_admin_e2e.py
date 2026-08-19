import os

from sqlalchemy import select

from app.account_management import ensure_role
from app.api.auth import hash_password, normalize_email, normalize_username
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Status, User

DEFAULT_USERNAME = "admin-e2e-root"
DEFAULT_DISPLAY_NAME = "合成管理员 Admin E2E"


def main() -> None:
    if get_settings().app_env.lower() != "test":
        raise SystemExit("admin E2E seed is only allowed when APP_ENV=test")
    password = os.environ.get("ADMIN_E2E_ADMIN_PASSWORD")
    username = normalize_username(
        os.environ.get("ADMIN_E2E_ADMIN_USERNAME", DEFAULT_USERNAME)
    )
    display_name = os.environ.get("ADMIN_E2E_ADMIN_NAME", DEFAULT_DISPLAY_NAME).strip()
    if not username.startswith("admin-e2e-"):
        raise SystemExit("ADMIN_E2E_ADMIN_USERNAME must start with admin-e2e-")
    if not password or len(password) < 12:
        raise SystemExit("ADMIN_E2E_ADMIN_PASSWORD must contain at least 12 characters")
    if not display_name:
        raise SystemExit("ADMIN_E2E_ADMIN_NAME cannot be empty")
    email = normalize_email(f"{username}@ahamark.local")
    with SessionLocal() as db:
        role = ensure_role(db, "admin")
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status=Status.active,
                roles=[role],
            )
            db.add(user)
        else:
            user.email = email
            user.display_name = display_name
            user.password_hash = hash_password(password)
            user.status = Status.active
            user.roles = [role]
        db.commit()
        db.refresh(user)
        print(f"admin E2E account ready: {user.id} ({username})")


if __name__ == "__main__":
    main()

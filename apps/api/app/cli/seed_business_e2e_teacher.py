import os

from sqlalchemy import select

from app.account_management import ensure_role
from app.api.auth import hash_password, normalize_username
from app.db.session import SessionLocal
from app.models import Status, User

DEFAULT_EMAIL = "teacher@business-e2e.synthetic.invalid"
DEFAULT_USERNAME = "business-e2e-teacher"
DEFAULT_DISPLAY_NAME = "合成教师 Business E2E"


def main() -> None:
    password = os.environ.get("BUSINESS_E2E_TEACHER_PASSWORD")
    email = os.environ.get("BUSINESS_E2E_TEACHER_EMAIL", DEFAULT_EMAIL)
    username = normalize_username(os.environ.get("BUSINESS_E2E_TEACHER_USERNAME", DEFAULT_USERNAME))
    display_name = os.environ.get("BUSINESS_E2E_TEACHER_NAME", DEFAULT_DISPLAY_NAME)
    if not password or len(password) < 12:
        raise SystemExit("BUSINESS_E2E_TEACHER_PASSWORD must contain at least 12 characters")
    if not email.endswith(".synthetic.invalid"):
        raise SystemExit("BUSINESS_E2E_TEACHER_EMAIL must use .synthetic.invalid")
    with SessionLocal() as db:
        teacher_role = ensure_role(db, "teacher")
        user = db.scalar(select(User).where(User.email == email))
        username_owner = db.scalar(select(User).where(User.username == username))
        if username_owner is not None and username_owner is not user:
            raise SystemExit("BUSINESS_E2E_TEACHER_USERNAME is already in use")
        if user is None:
            user = User(
                username=username,
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status=Status.active,
                roles=[teacher_role],
            )
            db.add(user)
        else:
            user.username = username
            user.display_name = display_name
            user.password_hash = hash_password(password)
            user.status = Status.active
            if teacher_role not in user.roles:
                user.roles.append(teacher_role)
        db.commit()
        db.refresh(user)
        print(f"business-e2e teacher ready: {user.id} ({username})")


if __name__ == "__main__":
    main()

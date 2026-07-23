import os

from sqlalchemy import select

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User

DEFAULT_EMAIL = "teacher@business-e2e.synthetic.invalid"
DEFAULT_DISPLAY_NAME = "合成教师 Business E2E"


def main() -> None:
    password = os.environ.get("BUSINESS_E2E_TEACHER_PASSWORD")
    email = os.environ.get("BUSINESS_E2E_TEACHER_EMAIL", DEFAULT_EMAIL)
    display_name = os.environ.get("BUSINESS_E2E_TEACHER_NAME", DEFAULT_DISPLAY_NAME)
    if not password or len(password) < 12:
        raise SystemExit("BUSINESS_E2E_TEACHER_PASSWORD must contain at least 12 characters")
    if not email.endswith(".synthetic.invalid"):
        raise SystemExit("BUSINESS_E2E_TEACHER_EMAIL must use .synthetic.invalid")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                status=Status.active,
            )
            db.add(user)
        else:
            user.display_name = display_name
            user.password_hash = hash_password(password)
            user.status = Status.active
        db.commit()
        db.refresh(user)
        print(f"business-e2e teacher ready: {user.id} ({email})")


if __name__ == "__main__":
    main()

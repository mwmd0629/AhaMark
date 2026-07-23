import os

from sqlalchemy import select

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User

EMAIL = "teacher@business-e2e.synthetic.invalid"
DISPLAY_NAME = "合成教师 Business E2E"


def main() -> None:
    password = os.environ.get("BUSINESS_E2E_TEACHER_PASSWORD")
    if not password or len(password) < 12:
        raise SystemExit("BUSINESS_E2E_TEACHER_PASSWORD must contain at least 12 characters")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            user = User(
                email=EMAIL,
                display_name=DISPLAY_NAME,
                password_hash=hash_password(password),
                status=Status.active,
            )
            db.add(user)
        else:
            user.display_name = DISPLAY_NAME
            user.password_hash = hash_password(password)
            user.status = Status.active
        db.commit()
        db.refresh(user)
        print(f"business-e2e teacher ready: {user.id} ({EMAIL})")


if __name__ == "__main__":
    main()

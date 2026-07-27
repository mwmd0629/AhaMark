"""Create only the unique synthetic Stage 6 teacher in the isolated database."""

import json
import os

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User
from sqlalchemy import select


def main() -> None:
    email = os.environ["PREPROD_TEACHER_EMAIL"]
    password = os.environ["PREPROD_TEACHER_PASSWORD"]
    if not email.endswith("@evaluation.synthetic.invalid"):
        raise RuntimeError("synthetic Stage 6 identity required")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        created = user is None
        if user is None:
            user = User(
                email=email,
                display_name="Stage 6 Synthetic Teacher",
                password_hash=hash_password(password),
                status=Status.active,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        print(json.dumps({"created": created, "user_id": str(user.id), "synthetic": True}))


if __name__ == "__main__":
    main()

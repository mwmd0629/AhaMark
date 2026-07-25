"""Sanitized shared Redis login-rate-limit verification through Nginx."""

import http.client
import json
import os
import ssl

from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User
from redis import Redis
from sqlalchemy import select

CASE_ID = os.environ.get("RATE_LIMIT_CASE_ID", "case-1")
EMAIL = f"rate-limit-{CASE_ID}@preprod.synthetic.invalid"
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]


def ensure_teacher() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == EMAIL)) is None:
            db.add(
                User(
                    email=EMAIL,
                    display_name="Synthetic Rate Limit Teacher",
                    password_hash=hash_password(PASSWORD),
                    status=Status.active,
                )
            )
            db.commit()


def login(password: str, request_number: int) -> int:
    context = ssl._create_unverified_context()
    connection = http.client.HTTPSConnection("nginx", 8443, context=context, timeout=5)
    body = json.dumps({"email": EMAIL, "password": password})
    connection.request(
        "POST",
        "/auth/login",
        body=body,
        headers={
            "Host": "localhost",
            "Content-Type": "application/json",
            "X-Request-ID": f"rate-limit-{request_number}",
        },
    )
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


def main() -> None:
    ensure_teacher()
    failures = [login("deliberately-wrong-password", number) for number in range(1, 6)]
    correct_while_blocked = login(PASSWORD, 6)
    blocked_again = login("deliberately-wrong-password", 7)
    redis_client = Redis.from_url(os.environ["REDIS_URL"])
    keys = list(redis_client.scan_iter("ahamark:auth:login:*"))
    matching_ttls = [redis_client.ttl(key) for key in keys]
    key_text = b" ".join(keys).decode(errors="replace")
    print(
        json.dumps(
            {
                "first_five": failures,
                "correct_while_blocked": correct_while_blocked,
                "blocked_again": blocked_again,
                "redis_key_count": len(keys),
                "ttl_in_window": any(1 <= ttl <= 300 for ttl in matching_ttls),
                "key_has_plain_email": EMAIL in key_text,
                "key_has_password": PASSWORD in key_text,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

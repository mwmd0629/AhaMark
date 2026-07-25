"""Sanitized dual-instance authentication/CSRF verification for the isolated stack."""

import http.client
import json
import os
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any

from app.api.actor import digest
from app.api.auth import hash_password
from app.db.session import SessionLocal
from app.models import Status, User, UserSession, now_utc
from sqlalchemy import select

TEACHER_A = "teacher-v8-20260725-000100@preprod.synthetic.invalid"
TEACHER_B = "teacher-b-v8-20260725-000100@preprod.synthetic.invalid"
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]


def request(
    host: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    cookie: str = "",
    csrf: str = "",
    origin: str = "https://localhost:9443",
) -> tuple[int, dict[str, Any], list[str]]:
    connection = http.client.HTTPConnection(host, 8000, timeout=5)
    headers = {"Host": "localhost", "Origin": origin, "X-Request-ID": f"runtime-{host}-{path}"}
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = csrf
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    parsed = json.loads(raw) if raw else {}
    cookies = [value for name, value in response.getheaders() if name.lower() == "set-cookie"]
    connection.close()
    return response.status, parsed, cookies


def login(host: str, email: str = TEACHER_A) -> tuple[str, str, int]:
    status, payload, set_cookies = request(
        host, "POST", "/auth/login", body={"email": email, "password": PASSWORD}
    )
    jar = SimpleCookie()
    for value in set_cookies:
        jar.load(value)
    cookie = "; ".join(f"{name}={morsel.value}" for name, morsel in jar.items())
    return cookie, str(payload.get("csrf_token", "")), status


def ensure_teacher_b() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == TEACHER_B)) is None:
            db.add(
                User(
                    email=TEACHER_B,
                    display_name="Synthetic Teacher B",
                    password_hash=hash_password(PASSWORD),
                    status=Status.active,
                )
            )
            db.commit()


def set_teacher_status(email: str, status: Status) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        user.status = status
        db.commit()


def expire(cookie: str) -> None:
    jar = SimpleCookie()
    jar.load(cookie)
    token = jar["ahamark_session"].value
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == digest(token)))
        assert session is not None
        session.expires_at = now_utc() - timedelta(seconds=1)
        db.commit()


def main() -> None:
    ensure_teacher_b()
    results: dict[str, Any] = {}

    cookie_a, csrf_a, results["login_a"] = login("api-a")
    results["a_session_on_b"] = request("api-b", "GET", "/auth/me", cookie=cookie_a)[0]
    created_status, created, _ = request(
        "api-b",
        "POST",
        "/api/classes",
        body={"name": "Synthetic Preproduction Class"},
        cookie=cookie_a,
        csrf=csrf_a,
    )
    results["csrf_a_on_b"] = created_status
    class_id = created.get("id")
    results["missing_csrf"] = request(
        "api-a", "POST", "/api/classes", body={"name": "Rejected Missing"}, cookie=cookie_a
    )[0]
    results["wrong_csrf"] = request(
        "api-b",
        "POST",
        "/api/classes",
        body={"name": "Rejected Wrong"},
        cookie=cookie_a,
        csrf="wrong",
    )[0]
    results["wrong_origin"] = request(
        "api-a",
        "POST",
        "/api/classes",
        body={"name": "Rejected Origin"},
        cookie=cookie_a,
        csrf=csrf_a,
        origin="https://evil.example",
    )[0]

    cookie_b, _, results["login_b"] = login("api-b", TEACHER_B)
    results["teacher_b_cross_access"] = request(
        "api-a", "GET", f"/api/classes/{class_id}", cookie=cookie_b
    )[0]

    results["logout_on_b"] = request("api-b", "POST", "/auth/logout", cookie=cookie_a, csrf=csrf_a)[
        0
    ]
    results["revoked_on_a"] = request("api-a", "GET", "/auth/me", cookie=cookie_a)[0]
    results["revoked_on_b"] = request("api-b", "GET", "/auth/me", cookie=cookie_a)[0]

    new_cookie, new_csrf, results["new_login"] = login("api-a")
    results["old_csrf_new_session"] = request(
        "api-b",
        "POST",
        "/api/classes",
        body={"name": "Rejected Old CSRF"},
        cookie=new_cookie,
        csrf=csrf_a,
    )[0]
    expire(new_cookie)
    results["expired_on_a"] = request("api-a", "GET", "/auth/me", cookie=new_cookie)[0]
    results["expired_on_b"] = request("api-b", "GET", "/auth/me", cookie=new_cookie)[0]

    active_cookie, _, results["login_before_disable"] = login("api-b")
    set_teacher_status(TEACHER_A, Status.inactive)
    results["disabled_on_a"] = request("api-a", "GET", "/auth/me", cookie=active_cookie)[0]
    results["disabled_on_b"] = request("api-b", "GET", "/auth/me", cookie=active_cookie)[0]
    set_teacher_status(TEACHER_A, Status.active)

    reverse_cookie, _, results["login_b_reverse"] = login("api-b")
    results["b_session_on_a"] = request("api-a", "GET", "/auth/me", cookie=reverse_cookie)[0]
    results["contains_credentials"] = False
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()

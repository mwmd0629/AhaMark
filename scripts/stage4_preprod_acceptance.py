"""Exercise Stage 4 production safety and session failover through HTTPS."""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from typing import Any

BASE_URL = os.environ["PREPROD_BASE_URL"].rstrip("/")
EMAIL = os.environ["PREPROD_TEACHER_EMAIL"]
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]


class Api:
    def __init__(self) -> None:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=context),
        )
        self.csrf = ""
        self.request_ids: list[str] = []

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[Any, int]:
        headers = {
            "Origin": BASE_URL,
            "X-Request-ID": f"stage4-preprod-{uuid.uuid4().hex[:16]}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            f"{BASE_URL}{path}", data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read()
                request_id = response.headers.get("X-Request-ID")
                if request_id:
                    self.request_ids.append(request_id)
                content_type = response.headers.get("Content-Type", "")
                payload = (
                    json.loads(raw)
                    if raw and "json" in content_type
                    else raw.decode("utf-8", "replace")
                )
                return payload, response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            return payload, exc.code


def wait_for(api: Api, path: str, expected: int = 200) -> tuple[Any, int]:
    deadline = time.monotonic() + 60
    last: tuple[Any, int] = ({}, 0)
    while time.monotonic() < deadline:
        try:
            last = api.request("GET", path)
            if last[1] == expected:
                return last
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f"{path} did not return {expected}; last={last[1]}")


def main() -> None:
    api = Api()
    health, health_status = wait_for(api, "/health")
    ready, ready_status = wait_for(api, "/ready")
    login, login_status = api.request("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    if login_status != 200:
        raise RuntimeError(f"login failed: {login_status}")
    api.csrf = login["csrf_token"]
    me, me_status = api.request("GET", "/auth/me")
    grading_status = api.request("GET", "/grading")[1]
    assignments_status = api.request("GET", "/assignments")[1]
    capability = ready["capabilities"]["ai_grading"]
    if capability["status"] != "unavailable" or not capability["suggestion_only"]:
        raise RuntimeError(f"unexpected production AI capability: {capability}")

    print("STAGE4_READY_FOR_API_A_STOP", flush=True)
    sys.stdin.readline()
    failover_me_status = wait_for(api, "/auth/me")[1]
    failover_health_status = wait_for(api, "/health")[1]

    print("STAGE4_READY_FOR_API_A_RESTORE", flush=True)
    sys.stdin.readline()
    restored_me_status = wait_for(api, "/auth/me")[1]

    session_cookie = next(
        (cookie for cookie in api.cookies if cookie.name == "ahamark_session"), None
    )
    result = {
        "health": health,
        "health_status": health_status,
        "ready_status": ready_status,
        "ai_capability": capability,
        "login_status": login_status,
        "me_status": me_status,
        "teacher_id": me.get("id") if isinstance(me, dict) else None,
        "grading_status": grading_status,
        "assignments_status": assignments_status,
        "failover_same_session_me_status": failover_me_status,
        "failover_health_status": failover_health_status,
        "restored_same_session_me_status": restored_me_status,
        "secure_http_only_session": bool(
            session_cookie
            and session_cookie.secure
            and session_cookie.has_nonstandard_attr("HttpOnly")
        ),
        "request_ids": api.request_ids,
        "synthetic_account": "synthetic" in EMAIL,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

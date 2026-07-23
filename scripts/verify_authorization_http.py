import json
import os
import sys
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from PIL import Image

BASE_URL = os.environ.get("AHAMARK_SECURITY_BASE_URL", "http://localhost:8800")
PASSWORD = os.environ.get("AHAMARK_SECURITY_PASSWORD", "Synthetic-Security-Matrix-Only!")
EMAIL_A = os.environ.get("AHAMARK_SECURITY_EMAIL_A", "teacher-a@security-matrix.synthetic.invalid")
EMAIL_B = os.environ.get("AHAMARK_SECURITY_EMAIL_B", "teacher-b@security-matrix.synthetic.invalid")


def png_fixture() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, "PNG")
    return output.getvalue()


def error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") or (body.get("detail") if isinstance(body.get("detail"), str) else None)


def main() -> int:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/authorization-http-verification.json")
    started = datetime.now(UTC)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, Any]] = []

    def record(
        response: httpx.Response,
        *,
        method: str,
        path: str,
        actor: str,
        expected: int,
        resource: str,
        operation: str,
    ) -> None:
        results.append(
            {
                "method": method,
                "path_template": path,
                "actor": actor,
                "expected_status": expected,
                "actual_status": response.status_code,
                "error_code": error_code(response),
                "request_id": response.headers.get("x-request-id"),
                "resource_type": resource,
                "operation": operation,
                "passed": response.status_code == expected,
            }
        )

    with httpx.Client(base_url=BASE_URL, timeout=15) as anonymous:
        response = anonymous.get("/api/classes")
        record(
            response,
            method="GET",
            path="/api/classes",
            actor="unauthenticated",
            expected=401,
            resource="Class",
            operation="list",
        )

    clients: dict[str, tuple[httpx.Client, str]] = {}
    try:
        for actor, email in (("teacher_a", EMAIL_A), ("teacher_b", EMAIL_B)):
            client = httpx.Client(base_url=BASE_URL, timeout=15)
            login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
            record(
                login,
                method="POST",
                path="/auth/login",
                actor=actor,
                expected=200,
                resource="Session/User",
                operation="create",
            )
            if login.status_code != 200:
                raise RuntimeError(f"{actor} login failed")
            clients[actor] = (client, login.json()["csrf_token"])

        a, csrf_a = clients["teacher_a"]
        b, csrf_b = clients["teacher_b"]
        missing = a.post("/api/classes", json={"name": "missing csrf"})
        record(
            missing,
            method="POST",
            path="/api/classes",
            actor="teacher_a_missing_csrf",
            expected=403,
            resource="Class",
            operation="create",
        )
        wrong = a.post(
            "/api/classes", headers={"x-csrf-token": "wrong"}, json={"name": "wrong csrf"}
        )
        record(
            wrong,
            method="POST",
            path="/api/classes",
            actor="teacher_a_invalid_csrf",
            expected=403,
            resource="Class",
            operation="create",
        )
        created_a = a.post(
            "/api/classes",
            headers={"x-csrf-token": csrf_a},
            json={"name": f"security-a-{run_id}-{uuid4().hex[:8]}"},
        )
        record(
            created_a,
            method="POST",
            path="/api/classes",
            actor="teacher_a",
            expected=201,
            resource="Class",
            operation="create",
        )
        class_a = created_a.json()["id"]
        hidden = b.get(f"/api/classes/{class_a}")
        record(
            hidden,
            method="GET",
            path="/api/classes/{class_id}",
            actor="teacher_b",
            expected=404,
            resource="Class",
            operation="get",
        )
        listing = b.get("/api/classes")
        record(
            listing,
            method="GET",
            path="/api/classes",
            actor="teacher_b",
            expected=200,
            resource="Class",
            operation="list",
        )
        if any(item["id"] == class_a for item in listing.json()["items"]):
            results[-1]["passed"] = False
            results[-1]["error_code"] = "CROSS_TENANT_LIST_LEAK"

        uploaded = a.post(
            "/files",
            headers={"x-csrf-token": csrf_a},
            files={"file": ("synthetic.png", png_fixture(), "image/png")},
        )
        record(
            uploaded,
            method="POST",
            path="/files",
            actor="teacher_a",
            expected=201,
            resource="StoredFile",
            operation="upload",
        )
        key = uploaded.json()["key"]
        for method, suffix, operation in (
            ("GET", "/metadata", "metadata"),
            ("POST", "/signed-url", "signed_url"),
            ("DELETE", "", "delete"),
        ):
            response = b.request(
                method,
                f"/files/{key}{suffix}",
                headers={"x-csrf-token": csrf_b} if method != "GET" else None,
            )
            record(
                response,
                method=method,
                path=f"/files/{{key}}{suffix}",
                actor="teacher_b",
                expected=404,
                resource="StoredFile",
                operation=operation,
            )
        signed = a.post(f"/files/{key}/signed-url", headers={"x-csrf-token": csrf_a})
        record(
            signed,
            method="POST",
            path="/files/{key}/signed-url",
            actor="teacher_a",
            expected=200,
            resource="StoredFile",
            operation="signed_url",
        )
        signed_url = signed.json()["url"]
        before = httpx.get(signed_url, timeout=10)
        results.append(
            {
                "resource_type": "StoredFile",
                "operation": "signed_url_before_expiry",
                "actor": "teacher_a",
                "expected_status": 200,
                "actual_status": before.status_code,
                "passed": before.status_code == 200,
            }
        )
        time.sleep(3)
        expired = httpx.get(signed_url, timeout=10)
        results.append(
            {
                "resource_type": "StoredFile",
                "operation": "signed_url_after_expiry",
                "actor": "expired_url",
                "expected_status": 403,
                "actual_status": expired.status_code,
                "passed": expired.status_code == 403,
            }
        )
        renewed = a.post(f"/files/{key}/signed-url", headers={"x-csrf-token": csrf_a})
        renewed_ok = renewed.status_code == 200 and renewed.json()["url"] != signed_url
        results.append(
            {
                "resource_type": "StoredFile",
                "operation": "signed_url_reissue",
                "actor": "teacher_a",
                "expected_status": 200,
                "actual_status": renewed.status_code,
                "passed": renewed_ok,
            }
        )
    finally:
        for client, _ in clients.values():
            client.close()

    passed = sum(item["passed"] for item in results)
    evidence = {
        "result": "PASS" if passed == len(results) else "FAIL",
        "environment": "docker-compose.business-e2e",
        "synthetic_marker": "security-matrix.synthetic.invalid",
        "run_id": run_id,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "passed_case_count": passed,
        "failed_case_count": len(results) - passed,
        "results": results,
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{evidence['result']}: {passed}/{len(results)} -> {output}")
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

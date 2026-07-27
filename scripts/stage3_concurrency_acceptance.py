"""Verify concurrent reference-answer version allocation through HTTPS."""

import json
import os
import ssl
import threading
import urllib.request
from http.cookiejar import CookieJar

BASE = os.environ["PREPROD_BASE_URL"].rstrip("/")
EMAIL = os.environ["PREPROD_TEACHER_EMAIL"]
PASSWORD = os.environ["PREPROD_TEACHER_PASSWORD"]
QUESTION_ID = os.environ["STAGE3_QUESTION_ID"]
BARRIER = threading.Barrier(2)


def client() -> tuple[urllib.request.OpenerDirector, str]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(context=context),
    )
    request = urllib.request.Request(
        f"{BASE}/auth/login",
        data=json.dumps({"email": EMAIL, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json", "Origin": BASE},
        method="POST",
    )
    with opener.open(request) as response:
        return opener, json.loads(response.read())["csrf_token"]


def create(index: int, output: list[dict[str, object]]) -> None:
    opener, csrf = client()
    BARRIER.wait()
    body = {
        "source_type": "teacher_authored",
        "raw_content": f"stage3_e2e concurrent {index}",
        "normalized_content": f"stage3_e2e concurrent {index}",
        "structured_content": {},
        "provenance": {"synthetic": True, "marker": "stage3_e2e_concurrency"},
    }
    request = urllib.request.Request(
        f"{BASE}/api/questions/{QUESTION_ID}/reference-answers",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": BASE,
            "X-CSRF-Token": csrf,
            "X-Request-ID": f"stage3-concurrency-{index}",
        },
        method="POST",
    )
    with opener.open(request) as response:
        payload = json.loads(response.read())
        output.append(
            {
                "status": response.status,
                "request_id": response.headers.get("X-Request-ID"),
                "id": payload["id"],
                "version": payload["version"],
            }
        )


def main() -> None:
    records: list[dict[str, object]] = []
    threads = [threading.Thread(target=create, args=(index, records)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    passed = (
        len(records) == 2
        and all(record["status"] == 201 for record in records)
        and len({record["version"] for record in records}) == 2
    )
    result = {"passed": passed, "records": sorted(records, key=lambda item: item["version"])}
    print(json.dumps(result))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

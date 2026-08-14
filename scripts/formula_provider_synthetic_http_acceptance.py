from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

SYNTHETIC_EMAIL = "teacher@business-e2e.synthetic.invalid"
SYNTHETIC_MARKER = "formula-ocr.synthetic.invalid"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    api_url = _required("FORMULA_ACCEPTANCE_API_URL").rstrip("/")
    assignment_id = uuid.UUID(_required("FORMULA_ACCEPTANCE_ASSIGNMENT_ID"))
    password = _required("FORMULA_ACCEPTANCE_TEACHER_PASSWORD")
    image_path = Path(_required("FORMULA_ACCEPTANCE_IMAGE_PATH")).resolve()
    parsed = urlsplit(api_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("synthetic acceptance is restricted to localhost HTTP")
    if image_path.suffix.lower() != ".png" or not image_path.is_file():
        raise RuntimeError("FORMULA_ACCEPTANCE_IMAGE_PATH must be an existing PNG")

    with httpx.Client(base_url=api_url, timeout=180) as client:
        login = client.post(
            "/auth/login",
            json={"email": SYNTHETIC_EMAIL, "password": password},
        )
        login.raise_for_status()
        login_payload = login.json()
        if login_payload.get("email") != SYNTHETIC_EMAIL:
            raise RuntimeError("synthetic teacher identity mismatch")
        headers = {"x-csrf-token": login_payload["csrf_token"]}

        assignment_response = client.get(f"/api/assignments/{assignment_id}")
        assignment_response.raise_for_status()
        assignment = assignment_response.json()
        if SYNTHETIC_MARKER not in assignment.get("title", ""):
            raise RuntimeError("assignment is not the dedicated synthetic formula fixture")
        if assignment.get("status") != "draft":
            raise RuntimeError("synthetic formula fixture must remain draft")
        paper_version = assignment.get("paper_version")
        if paper_version is None:
            with image_path.open("rb") as stream:
                uploaded = client.post(
                    f"/api/assignments/{assignment_id}/files",
                    headers=headers,
                    files={"file": ("public-formula-synthetic.png", stream, "image/png")},
                )
            uploaded.raise_for_status()
            if uploaded.json().get("pages_created") != 1:
                raise RuntimeError("synthetic formula upload did not create exactly one page")
            assignment_response = client.get(f"/api/assignments/{assignment_id}")
            assignment_response.raise_for_status()
            paper_version = assignment_response.json().get("paper_version")
        if not isinstance(paper_version, dict):
            raise RuntimeError("synthetic formula upload did not create a paper version")
        pages = paper_version.get("pages", [])
        if (
            len(pages) != 1
            or pages[0].get("file_name") != "public-formula-synthetic.png"
            or pages[0].get("status") != "ready"
        ):
            raise RuntimeError(
                "synthetic formula fixture does not contain the expected single page"
            )
        paper_version_id = paper_version["id"]

        job_response = client.post(
            f"/api/assignments/{assignment_id}/recognition/jobs",
            params={"run_now": "true"},
            headers=headers,
            json={
                "paper_version_id": paper_version_id,
                "idempotency_key": f"formula-synthetic-{assignment_id}",
            },
        )
        job_response.raise_for_status()
        job = job_response.json()
        if job.get("status") != "completed":
            raise RuntimeError(f"synthetic recognition did not complete: {job.get('status')}")
        job_id = job["id"]

        pages_response = client.get(
            f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/pages"
        )
        pages_response.raise_for_status()
        pages = pages_response.json()
        if len(pages) != 1 or pages[0].get("status") != "completed":
            raise RuntimeError("synthetic formula page was not processed")

        regions_url = f"/api/assignments/{assignment_id}/recognition/jobs/{job_id}/formulas/regions"
        regions_response = client.get(regions_url)
        regions_response.raise_for_status()
        regions = regions_response.json()
        if len(regions) > 1:
            raise RuntimeError("synthetic formula fixture contains unexpected extra regions")
        if regions:
            recognized = regions[0]
            region_id = recognized["id"]
            region_box = recognized.get("region", {})
            if (
                recognized.get("paper_page_id") != pages[0]["paper_page_id"]
                or recognized.get("region_kind") != "display"
                or any(
                    float(region_box.get(key, -1)) != value
                    for key, value in {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    }.items()
                )
            ):
                raise RuntimeError("existing synthetic formula region does not match fixture")
        else:
            region_response = client.post(
                regions_url,
                headers=headers,
                json={
                    "paper_page_id": pages[0]["paper_page_id"],
                    "region_kind": "display",
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 1,
                },
            )
            region_response.raise_for_status()
            recognized = region_response.json()
            region_id = recognized["id"]

        if not recognized.get("candidates"):
            recognized_response = client.post(
                f"{regions_url}/{region_id}/recognize",
                headers=headers,
            )
            recognized_response.raise_for_status()
            recognized = recognized_response.json()
        candidates = recognized.get("candidates", [])
        if recognized.get("status") != "manual_required" or len(candidates) != 1:
            raise RuntimeError("formula result did not remain a single manual-review candidate")
        candidate = candidates[0]
        if candidate.get("status") != "manual_required" or not candidate.get("latex"):
            raise RuntimeError("formula candidate is not awaiting teacher review")
        if candidate.get("confidence") is not None:
            raise RuntimeError("uncalibrated Paddle confidence must remain null")
        warnings = set(candidate.get("warning_codes", []))
        if warnings != {"UNCALIBRATED_CONFIDENCE", "TEACHER_REVIEW_REQUIRED"}:
            raise RuntimeError("formula candidate review warnings are incomplete")

        print(f"assignment_id={assignment_id}")
        print(f"recognition_job_id={job_id}")
        print(f"formula_region_id={region_id}")
        print(f"formula_candidate_id={candidate['id']}")
        print("formula_status=manual_required")
        print("teacher_confirmation_required=true")


if __name__ == "__main__":
    main()

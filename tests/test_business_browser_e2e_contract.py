import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "business_browser_e2e.mjs").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.business-e2e.yml").read_text(encoding="utf-8")
WEB_API = (ROOT / "apps" / "web" / "lib" / "api.ts").read_text(encoding="utf-8")
REVIEW_PAGE = (
    ROOT / "apps" / "web" / "app" / "(teacher)" / "grading" / "[batchId]" / "review" / "page.tsx"
).read_text(encoding="utf-8")
BATCH_PAGE = (
    ROOT / "apps" / "web" / "app" / "(teacher)" / "grading" / "[batchId]" / "page.tsx"
).read_text(encoding="utf-8")


def extract_js_function(source: str, function_name: str) -> str:
    marker = f"function {function_name}"
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
        elif block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char == "/" and following == "/":
            line_comment = True
            index += 1
        elif char == "/" and following == "*":
            block_comment = True
            index += 1
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
        index += 1
    raise AssertionError(f"unterminated JavaScript function: {function_name}")


def extract_balanced_call(source: str, opening: int) -> str:
    depth = 0
    quote = ""
    escaped = False
    index = opening
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
        index += 1
    raise AssertionError("unterminated JavaScript call")


def direct_region_mutations(function_source: str) -> list[str]:
    violations: list[str] = []
    generic_call = re.compile(r"\b(?:apiJson|fetch|request|evaluate)\s*\(")
    mutating_method = re.compile(
        r"\bmethod\s*:\s*[\"'](?:POST|PUT|PATCH|DELETE)[\"']", re.IGNORECASE
    )
    for match in generic_call.finditer(function_source):
        call = extract_balanced_call(function_source, function_source.index("(", match.start()))
        if "region-candidates" in call and mutating_method.search(call):
            violations.append(call)
    direct_request_method = re.compile(
        r"\brequest\s*\.\s*(?:post|put|patch|delete)\s*\(", re.IGNORECASE
    )
    for match in direct_request_method.finditer(function_source):
        call = extract_balanced_call(function_source, function_source.index("(", match.start()))
        if "region-candidates" in call:
            violations.append(call)
    return violations


PAGE_MUTATION_HELPERS = (
    "reorderPages",
    "reversePages",
    "splitSubmission",
    "mergeSubmission",
    "rotatePage",
    "saveCurrentPageOrderThroughUi",
)
PAGE_MUTATION_UI_LABELS = (
    "保存当前页面顺序",
    "反转页面顺序",
    "拆出末页",
    "合并回首次 Submission",
    "顺时针旋转",
    "逆时针旋转",
    "旋转页面",
)


def page_mutation_violations(source: str) -> list[str]:
    violations: list[str] = []
    for helper in PAGE_MUTATION_HELPERS:
        if re.search(rf"\b{re.escape(helper)}\s*\(", source):
            violations.append(f"helper:{helper}")
    for label in PAGE_MUTATION_UI_LABELS:
        if re.search(
            rf"getByRole\s*\(\s*[\"']button[\"']\s*,[\s\S]*?"
            rf"name\s*:\s*[\"']{re.escape(label)}[\"']",
            source,
        ):
            violations.append(f"ui:{label}")
    if re.search(r"/pages/order|/split|/merge|/rotate", source):
        violations.append("page-mutation-api-path")
    return violations


def object_calls(function_source: str, owner: str, method: str) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(owner)}\s*\.\s*{re.escape(method)}\s*\(")
    return [
        extract_balanced_call(function_source, function_source.index("(", match.start()))
        for match in pattern.finditer(function_source)
    ]


def has_scoped_click(function_source: str, owner: str, test_id: str) -> bool:
    for match in re.finditer(rf"\b{re.escape(owner)}\s*\.\s*getByTestId\s*\(", function_source):
        opening = function_source.index("(", match.start())
        call = extract_balanced_call(function_source, opening)
        call_end = opening + len(call)
        if test_id in call and re.match(r"\s*\.\s*click\s*\(", function_source[call_end:]):
            return True
    return False


def segmentation_scope_violations(delete_helper: str, draw_helper: str) -> list[str]:
    violations: list[str] = []
    delete_locators = object_calls(delete_helper, "submissionCard", "locator")
    if not any(
        "submission-processing-page" in call and "data-page-id" in call for call in delete_locators
    ):
        violations.append("delete-page-not-submission-scoped")
    if not any(
        "submission-region-card" in call and "data-region-id" in call for call in delete_locators
    ):
        violations.append("delete-region-not-id-scoped")
    if not has_scoped_click(delete_helper, "regionCard", "submission-region-delete"):
        violations.append("delete-control-not-region-scoped")

    draw_locators = object_calls(draw_helper, "submissionCard", "locator")
    if not any(
        "submission-processing-page" in call and "data-page-id" in call for call in draw_locators
    ):
        violations.append("draw-page-not-submission-scoped")
    draw_controls = object_calls(draw_helper, "submissionCard", "getByTestId")
    if not any("submission-question-select" in call for call in draw_controls):
        violations.append("draw-question-not-submission-scoped")
    if not any("submission-region-canvas" in call for call in draw_controls):
        violations.append("draw-canvas-not-submission-scoped")
    return violations


def draw_readiness_violations(draw_helper: str) -> list[str]:
    violations: list[str] = []
    readiness_position = draw_helper.find('pollUntil("segmentation canvas ready')
    mouse_position = draw_helper.find("page.mouse.move(")
    if readiness_position == -1 or readiness_position > mouse_position:
        violations.append("readiness-poll-must-precede-mouse")
    for required, violation in (
        ("cardSubmissionId === submissionId", "submission-not-verified"),
        ("canvasPageId === pageId", "page-not-verified"),
        ("selectedQuestionId === questionId", "question-not-verified"),
        ("currentBox.width >= 200", "minimum-canvas-width-missing"),
        ("currentBox.height >= 100", "minimum-canvas-height-missing"),
        ("image.complete", "image-complete-missing"),
        ("image.naturalWidth", "image-natural-width-missing"),
        ("image.naturalHeight", "image-natural-height-missing"),
        ("stableBoxSamples >= 2", "stable-box-check-missing"),
        ("canvas_display", "computed-display-diagnostic-missing"),
        ("canvas_width", "computed-width-diagnostic-missing"),
        (
            "ancestor_grid_template_columns",
            "grid-template-diagnostic-missing",
        ),
        ("src_without_query", "sanitized-image-source-diagnostic-missing"),
    ):
        if required not in draw_helper:
            violations.append(violation)
    if "const box = await pollUntil" not in draw_helper:
        violations.append("draw-must-use-poll-returned-box")
    return violations


def draw_pointer_safety_violations(draw_helper: str) -> list[str]:
    violations: list[str] = []
    scroll_position = draw_helper.find("await canvas.scrollIntoViewIfNeeded()")
    viewport_position = draw_helper.find("page.viewportSize()")
    visible_rect_position = draw_helper.find("const visibleRect = {")
    element_from_point_position = draw_helper.find("document.elementFromPoint")
    mouse_position = draw_helper.find("page.mouse.move(")
    if scroll_position == -1 or scroll_position > mouse_position:
        violations.append("scroll-into-view-must-precede-mouse")
    if viewport_position == -1 or viewport_position > mouse_position:
        violations.append("viewport-must-be-read-before-mouse")
    if visible_rect_position == -1 or visible_rect_position > mouse_position:
        violations.append("visible-rect-must-be-computed-before-mouse")
    if element_from_point_position == -1 or element_from_point_position > mouse_position:
        violations.append("element-from-point-must-precede-mouse")
    for required, violation in (
        ("Math.max(visibleBox.x, 0)", "visible-left-clipping-missing"),
        ("Math.max(visibleBox.y, 0)", "visible-top-clipping-missing"),
        (
            "Math.min(visibleBox.x + visibleBox.width, viewport.width)",
            "visible-right-clipping-missing",
        ),
        (
            "Math.min(visibleBox.y + visibleBox.height, viewport.height)",
            "visible-bottom-clipping-missing",
        ),
        ("visibleRect.width >= 200", "visible-width-threshold-missing"),
        ("visibleRect.height >= 100", "visible-height-threshold-missing"),
        ("pointerElements.start.inside_canvas", "start-element-safety-missing"),
        ("pointerElements.end.inside_canvas", "end-element-safety-missing"),
        ("page.mouse.move(dragStart.x, dragStart.y)", "start-mouse-not-visible-rect-based"),
        ("page.mouse.move(dragEnd.x, dragEnd.y)", "end-mouse-not-visible-rect-based"),
    ):
        if required not in draw_helper:
            violations.append(violation)
    return violations


def test_stop_after_is_disabled_by_default_and_only_accepts_exact_f() -> None:
    assert "const requestedStopAfter = process.env.BUSINESS_E2E_STOP_AFTER;" in SCRIPT
    assert 'requestedStopAfter !== undefined && requestedStopAfter !== "F"' in SCRIPT
    assert "const singleContinueProof = true;" in SCRIPT
    assert 'const stopAfterF = requestedStopAfter === "F" || singleContinueProof;' in SCRIPT


def test_script_has_no_dormant_post_f_release_or_analytics_write_branch() -> None:
    assert "BUSINESS_E2E_GH_GUARD" not in SCRIPT
    assert "if (!stopAfterF)" not in SCRIPT
    assert 'currentStage = "G"' not in SCRIPT
    assert 'currentStage = "H"' not in SCRIPT
    assert "创建新的 GradeRelease 版本" not in SCRIPT
    assert "生成 XLSX" not in SCRIPT
    assert "生成首名学生中文 PDF" not in SCRIPT
    assert "await page.goto(`${base}/analytics`)" not in SCRIPT
    assert "grade_release_write_attempted = true" not in SCRIPT


def test_stopped_evidence_and_terminal_output_do_not_claim_full_pass() -> None:
    stopped_branch = SCRIPT.split("if (stopAfterF) {", 1)[1].split("} catch", 1)[0]
    assert 'evidence.result = "passed_through_F"' in stopped_branch
    assert "completed_stage_count = 6" in SCRIPT
    assert 'scope: stopAfterF ? "snapshot_only"' in SCRIPT
    assert "grade_release_write_attempted: false" in SCRIPT
    assert "BUSINESS_BROWSER_E2E_STOPPED" in SCRIPT
    assert "stages=6 completed_through=F" in SCRIPT
    assert "BUSINESS_BROWSER_E2E_PASSED" not in SCRIPT


def test_evidence_fingerprints_dirty_source_and_compose_images_without_diff_content() -> None:
    assert '["status", "--porcelain=v1", "--untracked-files=all"]' in SCRIPT
    assert '["diff", "--binary", "HEAD", "--"]' in SCRIPT
    assert 'createHash("sha256")' in SCRIPT
    assert "worktree_dirty: worktreeStatus.trim().length > 0" in SCRIPT
    assert "worktree_status_sha256:" in SCRIPT
    assert "tracked_diff_sha256:" in SCRIPT
    assert "untracked_file_count:" in SCRIPT
    assert '["api", "worker", "web"].map' in SCRIPT
    assert 'execFileSync("docker", ["inspect", "--format={{.Image}}", containerId]' in SCRIPT
    provenance = SCRIPT.split("source_provenance:", 1)[1].split("environment:", 1)[0]
    assert "trackedDiff.toString" not in provenance
    assert "worktreeStatus," not in provenance


def test_stop_branch_uses_authenticated_gets_for_snapshots_and_release_absence() -> None:
    assert "async function apiJson(" in SCRIPT
    assert '"X-CSRF-Token": csrf ?? ""' in SCRIPT
    assert 'credentials: "include"' in SCRIPT
    assert "/score-snapshots?status=complete" in SCRIPT
    assert "/api/grade-releases?assignment_id=${assignmentId}" in SCRIPT
    assert "safe grading E2E must not create a GradeRelease" in SCRIPT


def test_frontend_and_safe_e2e_never_post_the_legacy_grade_release_endpoint() -> None:
    frontend = "\n".join((WEB_API, REVIEW_PAGE, BATCH_PAGE))
    direct_release_post = re.compile(
        r'(?:request|apiJson)(?:<[^>]+>)?\(\s*[`"\']/api/grade-releases[`"\']'
        r'\s*,\s*\{[^}]*method:\s*"POST"',
        re.DOTALL,
    )
    assert direct_release_post.search(frontend) is None
    assert direct_release_post.search(SCRIPT) is None
    assert "createRelease" not in frontend
    assert SCRIPT.count("/api/grade-releases") == 1
    release_read = SCRIPT.split("const releasesResponse = await apiJson(", 1)[1].split(
        "assert.equal(releasesResponse.status", 1
    )[0]
    assert "?assignment_id=${assignmentId}" in release_read
    assert 'method: "POST"' not in release_read
    assert "grade_release_write_attempted: false" in SCRIPT
    assert "grade_release_write_attempted = true" not in SCRIPT


def test_api_requests_use_explicit_absolute_api_origin_and_auditable_response_metadata() -> None:
    assert 'process.env.BUSINESS_E2E_API_URL ?? "http://localhost:8800"' in SCRIPT
    assert ').replace(/\\/+$/, "");' in SCRIPT
    assert "function absoluteApiUrl(requestPath)" in SCRIPT
    assert 'requestPath.startsWith("/api/")' in SCRIPT
    assert "new URL(requestPath, `${apiBase}/`)" in SCRIPT
    assert "resolved.origin" in SCRIPT
    assert '"API URL must remain on configured origin"' in SCRIPT

    helper = SCRIPT.split("async function apiJson", 1)[1].split("function workspaceAnswers", 1)[0]
    assert "const absoluteUrl = absoluteApiUrl(apiPath);" in helper
    assert "requestUrl: absoluteUrl" in helper
    assert "requestUrl: apiPath" not in helper
    assert 'credentials: "include"' in helper
    assert '"HTTP_CONTENT_TYPE_ERROR"' in helper
    assert "request_url: response.url" in helper
    assert "request_origin: new URL(response.url).origin" in helper
    assert "content_type: contentType" in helper
    assert "evidence.api_requests[`${method} ${apiPath}`]" in helper
    assert "request_url: result.request_url" in helper
    assert "request_origin: result.request_origin" in helper


def test_api_url_guard_rejects_normalized_path_escape_and_non_http_protocols() -> None:
    assert "const parsedApiBase = new URL(apiBase);" in SCRIPT
    assert '["http:", "https:"].includes(parsedApiBase.protocol)' in SCRIPT
    assert '"BUSINESS_E2E_API_URL must use http: or https:"' in SCRIPT

    guard = SCRIPT.split("function absoluteApiUrl(requestPath)", 1)[1].split(
        "async function apiJson", 1
    )[0]
    assert "new URL(requestPath, `${apiBase}/`)" in guard
    assert 'resolved.pathname === "/api"' in guard
    assert 'resolved.pathname.startsWith("/api/")' in guard
    assert "normalized API path escaped /api" in guard
    assert "assert.equal(\n    resolved.origin,\n    apiOrigin" in guard

    # `/api/../auth/me` passes the raw prefix check, then normalizes to
    # `/auth/me`; the post-resolution pathname assertion is the rejecting guard.
    assert 'requestPath.startsWith("/api/")' in guard
    assert guard.index("new URL(requestPath") < guard.index("resolved.pathname")
    assert '"http://localhost:8800"' in SCRIPT


def test_codex_processing_write_read_and_teacher_review_order() -> None:
    if "single_continue_from_uploaded_unconfirmed_submission" in SCRIPT:
        codex_continue = SCRIPT.index("let continueResponse")
        technical_poll = SCRIPT.index("const waitingCodex", codex_continue)
        claim = SCRIPT.index("/api/internal/codex-local/work-items/claim", technical_poll)
        submit = SCRIPT.index("/submit`", claim)
        apply = SCRIPT.index("/apply`", submit)
        teacher_review = SCRIPT.index("const initialWorkspaceResponse", apply)
        finalize = SCRIPT.index("`/api/submissions/${submissionId}/finalize`", teacher_review)
        assert codex_continue < technical_poll < claim < submit < apply
        assert apply < teacher_review < finalize
        assert "manual_submission_processing_start_count: 0" in SCRIPT
        assert "manual_region_confirmation_count: 0" in SCRIPT
        assert "manual_recognition_confirmation_count: 0" in SCRIPT
        assert 'done: run.status === "awaiting_teacher_review"' in SCRIPT
        assert "assert.equal(completedRun.run.pending_codex_count, 0)" in SCRIPT
        return

    recognition_continue = SCRIPT.index("const recognitionContinueResponse")
    recognition_reconcile = SCRIPT.index(
        "business-e2e-recognition-reconcile-", recognition_continue
    )
    confirmation = SCRIPT.index("/recognition/confirm`", recognition_reconcile)
    confirmation_read = SCRIPT.index("/question-recognition-evidence`", confirmation)
    codex_continue = SCRIPT.index("const continueResponse", confirmation_read)
    claim = SCRIPT.index("/api/internal/codex-local/work-items/claim", codex_continue)
    submit = SCRIPT.index("/submit`", claim)
    apply = SCRIPT.index("/apply`", submit)
    reconcile = SCRIPT.index("/reconcile`", apply)
    run_read = SCRIPT.index("const runReadResponse", reconcile)
    suggestion_read = SCRIPT.index("const readAfterWrite", run_read)
    suggestion_assertion = SCRIPT.index(
        'assert.equal(verified.result.provider, "codex_local")',
        suggestion_read,
    )
    teacher_accept = SCRIPT.index(
        'panel.getByRole("button", { name: "接受", exact: true }).click()',
        suggestion_assertion,
    )
    teacher_modify = SCRIPT.index(
        'panel.getByRole("button", { name: "修改", exact: true }).click()',
        teacher_accept,
    )
    finalize = SCRIPT.index("`/api/submissions/${submissionId}/finalize`", teacher_modify)

    assert "/codex-suggestion" not in SCRIPT
    assert recognition_continue < recognition_reconcile < confirmation
    assert confirmation < confirmation_read < codex_continue
    assert codex_continue < claim < submit < apply < reconcile < run_read
    assert run_read < suggestion_read < suggestion_assertion
    assert suggestion_assertion < teacher_accept < teacher_modify < finalize
    assert 'assert.equal(processingRun.provider, "codex_local")' in SCRIPT
    assert 'assert.equal(processingRun.provider_label, "Codex-assisted")' in SCRIPT
    assert "assert.equal(processingRun.suggestion_only, true)" in SCRIPT
    assert "assert.equal(recognitionRun.pending_codex_count, 0)" in SCRIPT
    assert 'step.kind === "recognition"' in SCRIPT
    assert 'step.status === "blocked_review"' in SCRIPT
    assert 'step.error_code === "RECOGNITION_CONFIRMATION_REQUIRED"' in SCRIPT
    assert 'currentEvidence.status, "confirmed"' in SCRIPT
    assert "processingRun.generation > recognitionRun.generation" in SCRIPT
    assert "assert.ok(processingRun.pending_codex_count > 0)" in SCRIPT
    assert 'assert.equal(runReadResponse.body.status, "awaiting_teacher_review")' in SCRIPT
    assert "assert.equal(runReadResponse.body.pending_codex_count, 0)" in SCRIPT
    assert 'assert.equal(verified.result.provider_version, "local")' in SCRIPT
    assert 'assert.equal(verified.result.status, "suggested")' in SCRIPT
    assert "assert.ok(verified.evidence.length >= 1)" in SCRIPT
    assert 'assert.equal(answer.result.provider, "unavailable")' in SCRIPT
    assert 'assert.equal(answer.result.provider_version, "none")' in SCRIPT
    assert 'assert.equal(answer.result.status, "suggested")' in SCRIPT
    assert "assert.equal(answer.result.score, null)" in SCRIPT
    assert "assert.equal(answer.result.requires_review, true)" in SCRIPT
    assert "answer.requires_review,\n      false," in SCRIPT
    assert "verified.requires_review,\n      false," in SCRIPT
    assert (
        "StudentAnswer recognition must not inherit GradingResult suggestion review state" in SCRIPT
    )
    assert "assert.equal(answer.requires_review, true)" not in SCRIPT
    assert "assert.equal(verified.requires_review, true)" not in SCRIPT
    assert 'expectedDecision = "accepted"' in SCRIPT
    assert 'expectedDecision = "modified"' in SCRIPT
    assert 'criterion.status,\n            expectedDecision === "modified"' in SCRIPT
    assert "assert.equal(reviewedAnswer.requires_review, false)" in SCRIPT


def test_codex_internal_auth_is_test_only_and_redacted_from_evidence() -> None:
    assert "APP_ENV: test" in COMPOSE
    assert 'CODEX_LOCAL_ENABLED: "true"' in COMPOSE
    assert re.search(r"CODEX_LOCAL_INTERNAL_TOKEN: \S{32,}", COMPOSE)
    helper = SCRIPT.split("async function internalApiJson", 1)[1].split(
        "function syntheticCodexResponse", 1
    )[0]
    assert "Authorization: `Bearer ${codexLocalInternalToken}`" in helper
    assert 'auth: "internal_bearer_redacted"' in helper
    evidence_write = helper.split("evidence.api_requests", 1)[1]
    assert "codexLocalInternalToken" not in evidence_write


def test_f_decimal_score_assertions_normalize_only_valid_decimal_strings() -> None:
    normalizer = extract_js_function(SCRIPT, "normalizeDecimalString")
    comparator = extract_js_function(SCRIPT, "decimalStringsEqual")
    assertion = extract_js_function(SCRIPT, "assertDecimalStringsEqual")
    f_stage = SCRIPT.split('currentStage = "F";', 1)[1]

    assert "total_suggested_points: hasManual ? null : String(total)" in SCRIPT
    assert "assertDecimalStringsEqual(\n          reviewedAnswer.review.final_score," in f_stage
    assert "assertDecimalStringsEqual(\n            criterion.awarded_points," in f_stage
    assert "String(verified.result.score)" not in f_stage
    assert "String(reviewedAnswer.review.final_score)" not in f_stage
    assert "String(criterion.awarded_points)" not in f_stage

    node_source = f"""\
import assert from "node:assert/strict";
{normalizer}
{comparator}
{assertion}
assert.equal(decimalStringsEqual("4.00", "4"), true);
assert.equal(decimalStringsEqual("+0004.5000", "+4.5"), true);
assert.equal(normalizeDecimalString("-000.000"), "-0");
for (const invalid of ["", " 4", "4 ", "4e0", "Infinity", "NaN", "1_0", ".", "+", "--4"]) {{
  assert.throws(() => normalizeDecimalString(invalid), TypeError);
}}
assert.throws(() => normalizeDecimalString(4), TypeError);
assert.throws(() => assertDecimalStringsEqual("4x", "4"), TypeError);
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", node_source],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_f_uses_stable_controls_and_a_single_prepare_click_before_review() -> None:
    f_stage = SCRIPT.split('currentStage = "F";', 1)[1].split(
        "evidence.codex_suggestions = [];", 1
    )[0]
    if "single_continue_from_uploaded_unconfirmed_submission" in SCRIPT:
        single_branch = SCRIPT.split("if (singleContinueProof) {", 2)[2].split(
            "\n  } else {\n    const beforePreparationResponse", 1
        )[0]
        assert 'method: "POST"' in single_branch
        assert "/processing-runs" in single_branch
        assert "const waitingCodex" in single_branch
        assert 'run.status === "awaiting_teacher_review"' in single_branch
        assert "page.goto(`${base}/grading/${batchId}/review`)" in single_branch
        assert 'page.getByTestId("prepare-grading-inputs")' not in single_branch
        return
    prepare = f_stage.index('page.getByTestId("prepare-grading-inputs")')
    click = f_stage.index("await prepareGradingInputs.click()", prepare)
    write_after_get = f_stage.index('"prepared grading workspace"', click)
    open_review = f_stage.index('page.getByTestId("open-teacher-review")', write_after_get)

    assert '"运行确定性初批"' not in SCRIPT
    assert f_stage.count("await prepareGradingInputs.click()") == 1
    assert prepare < click < write_after_get < open_review
    assert '"prepare grading inputs enabled"' in f_stage
    assert "prepareGradingInputs.isEnabled()" in f_stage
    assert "prepareGradingInputs.isVisible()" in f_stage
    assert "click_count: 1" in f_stage
    assert "before_read_status" in f_stage
    assert "after_read_status" in f_stage
    assert "after_answers" in f_stage


def test_f_uses_safe_compatibility_finalize_without_confirming_results() -> None:
    f_stage = SCRIPT.split('currentStage = "F";', 1)[1]

    progress_wait = "page.getByText(/^\\s*已复核\\s+4\\/4\\s*$/).waitFor()"
    confirm_button = 'name: "确认结果"'
    finalize = "`/api/submissions/${submissionId}/finalize`"
    final_snapshot_get = "const completeSnapshotsResponse = await apiJson("
    grade_release_check = "`/api/grade-releases?assignment_id=${assignmentId}`"

    assert progress_wait in f_stage
    assert "进度 4/4" not in f_stage
    assert confirm_button in f_stage
    assert '"review UI must expose exactly one confirm-results authorization"' in f_stage
    assert '"confirm-results authorization must be ready' in f_stage
    assert "clicked: false" in f_stage
    confirm_block = f_stage.split("const confirmResultsButton", 1)[1].split(
        "const beforeFinalizeResponse", 1
    )[0]
    assert ".click()" not in confirm_block
    assert 'name: "全部定稿"' not in SCRIPT
    assert "beforeFinalizeResponse.body.items.filter" in f_stage
    assert 'item.status !== "finalized"' in f_stage
    assert '"compatibility finalize scope must not contain duplicate submissions"' in f_stage
    assert "for (const submissionId of evidence.objects.submission_ids)" in f_stage
    assert SCRIPT.count(finalize) == 1
    assert "compatibility finalize failed:" in f_stage
    assert 'assert.equal(finalizeResponse.body.status, "complete")' in f_stage
    assert "finalize write-after-GET must expose snapshot" in f_stage
    assert "write_request_id: finalizeResponse.request_id" in f_stage
    assert "read_request_id: snapshotReadResponse.request_id" in f_stage
    assert '"F must create exactly two complete snapshots' in f_stage
    assert "complete score snapshot IDs must be unique" in f_stage
    assert "finalizedBatchResponse.body.workflow.completed_count" in f_stage
    assert "finalizedBatchResponse.body.workflow.blocked" in f_stage
    assert 'item.status === "finalized" && item.workflow.stage === "completed"' in f_stage
    assert "evidence.finalized_workflow_verification" in f_stage
    assert (
        f_stage.index(progress_wait)
        < f_stage.index(confirm_button)
        < f_stage.index(finalize)
        < f_stage.index(final_snapshot_get)
        < f_stage.index(grade_release_check)
    )
    assert "safe grading E2E must not create a GradeRelease" in f_stage


def test_f_workspace_readiness_requires_all_provider_and_evidence_branches() -> None:
    helper = SCRIPT.split("function gradingWorkspaceReadiness", 1)[1].split(
        "function assertApiOk", 1
    )[0]
    f_stage = SCRIPT.split('currentStage = "F";', 1)[1].split(
        "evidence.codex_suggestions = [];", 1
    )[0]

    assert "answers.length === 4" in helper
    assert "objectiveAnswers.length === 2" in helper
    assert "subjectiveAnswers.length === 2" in helper
    assert 'answer.result?.provider === "objective-rule"' in helper
    assert 'answer.result?.provider === "unavailable"' in helper
    assert 'answer.result.provider_version === "none"' in helper
    assert 'answer.result.status === "suggested"' in helper
    assert "answer.result.score !== null" in helper
    assert "score >= 0" in helper
    assert "score <= maximum" in helper
    assert "answer.result.requires_review === true" in helper
    assert "answer.regions.length >= 1" in helper
    assert "answer.evidence.length >= 1" in helper
    if "single_continue_from_uploaded_unconfirmed_submission" in SCRIPT:
        single_branch = SCRIPT.split("if (singleContinueProof) {", 2)[2].split(
            "\n  } else {\n    const beforePreparationResponse", 1
        )[0]
        assert 'answer.result.provider, "codex_local"' in single_branch
        assert 'answer.result.provider_version, "local"' in single_branch
        assert 'answer.result.status, "suggested"' in single_branch
        assert "answer.result.requires_review, true" in single_branch
        assert "/review-workspace" in f_stage
        assert "assertApiOk(" in single_branch
        return
    assert '"stale", "manual_segmentation_required"' in f_stage
    assert 'transition.after_status,\n        "graded"' in f_stage
    assert "/review-workspace" in f_stage
    assert 'pollUntil(\n    "prepared grading workspace"' in f_stage
    assert "assertApiOk(" in f_stage


def test_evidence_declares_provider_boundary_and_write_after_read_details() -> None:
    assert 'ai_grading_provider: "unavailable"' in SCRIPT
    assert 'codex_assisted_mode: "local suggestion-only"' in SCRIPT
    assert "real_provider_called: false" in SCRIPT
    assert "initial_result: initialResult" in SCRIPT
    assert "response_error_code: readAfterWrite.error_code" in SCRIPT
    assert "response_request_id: readAfterWrite.request_id" in SCRIPT
    assert "review_id: reviewWriteBody.id" in SCRIPT
    assert "write_status: reviewWriteResponse.status()" in SCRIPT
    assert "read_status: reviewRead.status" in SCRIPT


def test_custom_evidence_path_creates_its_nested_parent_before_any_write() -> None:
    resolved = SCRIPT.index("const evidencePath = path.resolve(")
    parent = SCRIPT.index(
        "fs.mkdirSync(path.dirname(evidencePath), { recursive: true });",
        resolved,
    )
    write = SCRIPT.index("fs.writeFileSync(evidencePath", parent)
    assert resolved < parent < write


def test_evidence_path_never_falls_back_to_docs_after_initial_resolution() -> None:
    default_path = '"docs/business-e2e-verification.json"'
    assert SCRIPT.count(default_path) == 1
    assert SCRIPT.count("const evidencePath =") == 1
    assert "let evidencePath" not in SCRIPT
    assert "evidencePath =" not in SCRIPT.split("const evidencePath =", 1)[1]
    assert "fs.writeFileSync(evidencePath" in SCRIPT


def test_evidence_write_failure_is_secondary_to_the_primary_business_error() -> None:
    terminal_start = SCRIPT.rindex("} catch (error) {\n  hasPrimaryFailure = true;")
    terminal = SCRIPT[terminal_start:]
    primary_mark = terminal.index("hasPrimaryFailure = true;")
    primary_capture = terminal.index("primaryFailure = error;", primary_mark)
    primary_record = terminal.index("evidence.failure = {", primary_capture)
    primary_rethrow = terminal.index("throw error;", primary_record)
    evidence_write = terminal.index("fs.writeFileSync(evidencePath", primary_rethrow)
    secondary_record = terminal.index("evidence.secondary_errors.push", evidence_write)
    secondary_log = terminal.index("BUSINESS_E2E_SECONDARY_ERROR", secondary_record)
    browser_close = terminal.index("await browser.close()", secondary_log)
    secondary_rethrow_guard = terminal.index(
        "evidenceWriteFailure !== null && !hasPrimaryFailure",
        browser_close,
    )
    assert (
        primary_mark
        < primary_capture
        < primary_record
        < primary_rethrow
        < evidence_write
        < secondary_record
        < secondary_log
        < browser_close
        < secondary_rethrow_guard
    )
    assert "primary_failure_preserved: hasPrimaryFailure" in terminal
    assert "primary_failure_code:" in terminal
    assert "throw evidenceWriteFailure;" in terminal[secondary_rethrow_guard:]


def test_browser_close_failure_is_secondary_to_a_primary_business_error() -> None:
    terminal_start = SCRIPT.rindex("} catch (error) {\n  hasPrimaryFailure = true;")
    terminal = SCRIPT[terminal_start:]
    close_try = terminal.index("try {\n    await browser.close();")
    close_catch = terminal.index("} catch (error) {", close_try)
    close_secondary = terminal.index('phase: "browser_close"', close_catch)
    close_log = terminal.index("BUSINESS_E2E_SECONDARY_ERROR", close_secondary)
    evidence_guard = terminal.index(
        "evidenceWriteFailure !== null && !hasPrimaryFailure",
        close_log,
    )
    close_guard = terminal.index("browserCloseFailure !== null", evidence_guard)
    assert close_try < close_catch < close_secondary < close_log < evidence_guard < close_guard
    assert "primary_failure_preserved: hasPrimaryFailure" in terminal[close_catch:]
    assert "throw browserCloseFailure;" not in terminal[:close_guard]


def test_evidence_write_failure_keeps_priority_over_browser_close_failure() -> None:
    terminal_start = SCRIPT.rindex("} catch (error) {\n  hasPrimaryFailure = true;")
    terminal = SCRIPT[terminal_start:]
    evidence_guard = terminal.index("evidenceWriteFailure !== null && !hasPrimaryFailure")
    evidence_throw = terminal.index("throw evidenceWriteFailure;", evidence_guard)
    close_guard = terminal.index("browserCloseFailure !== null", evidence_throw)
    close_throw = terminal.index("throw browserCloseFailure;", close_guard)
    assert evidence_guard < evidence_throw < close_guard < close_throw
    assert "evidenceWriteFailure === null" in terminal[close_guard:close_throw]
    assert "evidence_write_failure_preserved: evidenceWriteFailure !== null" in terminal


def test_browser_close_failure_is_primary_only_when_no_other_failure_exists() -> None:
    terminal_start = SCRIPT.rindex("} catch (error) {\n  hasPrimaryFailure = true;")
    terminal = SCRIPT[terminal_start:]
    close_guard = terminal.index("browserCloseFailure !== null")
    close_throw = terminal.index("throw browserCloseFailure;", close_guard)
    guard = terminal[close_guard:close_throw]
    assert "evidenceWriteFailure === null" in guard
    assert "!hasPrimaryFailure" in guard
    assert 'phase: "browser_close"' in terminal[:close_guard]
    assert "BUSINESS_E2E_SECONDARY_ERROR" in terminal[:close_guard]


def test_compose_keeps_only_explicit_synthetic_providers_fake() -> None:
    assert "AI_GRADING_PROVIDER: unavailable" in COMPOSE
    assert "GRADING_PROVIDER: unavailable" in COMPOSE
    assert "RECOGNITION_PROVIDER: fake" in COMPOSE
    assert "ASSIGNMENT_GENERATION_PROVIDER: fake" in COMPOSE


def test_file_analysis_wait_requires_materialization_and_durable_confirmation() -> None:
    helper = SCRIPT.split("async function confirmFileAnalyses", 1)[1].split(
        "async function settleGeneratedSuggestions", 1
    )[0]
    assert 'pollUntil("all file analyses teacher-confirmed", 90_000' in helper
    assert "if (files.length === 0)" in helper
    assert 'done: false, state: "analyses not materialized"' in helper
    assert '!["suggested", "confirmed"].includes(item.analysis_status)' in helper
    assert 'item.analysis_status !== "suggested"' in helper
    assert "roleIsAutomatic" in helper
    assert "sourceIsAutomatic" in helper
    assert 'warnings.includes("FILE_ROLE_CONFLICT_REVIEW_REQUIRED")' in helper
    assert "return !(roleIsAutomatic && sourceIsAutomatic)" in helper
    assert "adoption:" in helper
    assert '"system_auto"' in helper
    assert "teacher_confirmed_role" in helper
    assert "teacher_confirmed_answer_source" in helper
    assert "file analysis write-after-GET" in helper
    assert "if (before === 0) return" not in helper
    pending = helper.index("const pending = files.filter")
    expand = helper.index('"file analysis details expanded"', pending)
    button = helper.index('name: "确认文件分析"', expand)
    assert pending < expand < button
    assert '"details#generation-file-analysis"' in helper
    assert "node instanceof HTMLDetailsElement" in helper
    assert 'fileAnalysisRegion.locator("summary").first()' in helper
    assert "await summary.click()" in helper
    assert "fileAnalysisRegion.getByRole" in helper
    assert 'page.getByRole("button", { name: "确认文件分析" })' not in helper
    assert 'method: "PATCH"' not in helper


def test_generated_suggestions_are_audited_without_mandatory_disposition_writes() -> None:
    helper = SCRIPT.split("async function settleGeneratedSuggestions", 1)[1].split(
        "async function getReviewSession", 1
    )[0]
    assert "/page-organization-suggestions" in helper
    assert "/question-extraction-candidates" in helper
    assert 'assertApiOk(pagesResponse, "page suggestions GET")' in helper
    assert 'assertApiOk(questionsResponse, "question candidates GET")' in helper
    assert "page_statuses: pages.map" in helper
    assert "question_statuses: questions.map" in helper
    assert "materialized_question_id" in helper
    assert "writes: []" in helper
    assert "teacher_action_required: false" in helper
    assert 'item.status === "suggested"' not in helper
    assert 'method: "PATCH"' not in helper
    assert 'method: "POST"' not in helper
    assert "generationReviewInputs" in SCRIPT
    assert "confirmed answer/rubric" in SCRIPT


def test_each_confirmation_waits_ready_or_confirmed_and_records_write_after_get() -> None:
    helper = SCRIPT.split("async function ensureReviewConfirmation", 1)[1].split(
        "function assertCurrentStructuredRubricSet", 1
    )[0]
    assert "confirmation ready-or-confirmed" in helper
    assert "current.session.confirmations.includes(kind)" in helper
    assert "button.isEnabled()" in helper
    assert "confirmation session write-after-GET" in helper
    assert "current.session.review_version > ready.session.review_version" in helper
    assert "review-confirmation-state-${kind}" in helper
    assert "idempotent confirmation UI projection" in helper
    assert "confirmation Bundle write-after-GET" in helper
    assert "requireCurrentBundleConfirmation" in helper
    assert 'item.type === kind && item.status === "confirmed"' in helper
    assert '"confirmation-fingerprint-v2"' in SCRIPT
    assert "sha256HexPattern" in SCRIPT
    assert '["origin", "inherited", "system_auto"]' in SCRIPT
    assert 'matches[0].inherited, matches[0].origin === "inherited"' in SCRIPT
    assert "new ${kind} confirmation must be recorded in the current session" in helper
    assert "confirmation.inherited, false" in helper
    assert "bundle_read_request_id" in helper
    assert "read_request_id" in helper
    assert "write_request_id" in helper
    assert "if (await button.isEnabled())" not in helper
    assert "button.isDisabled()" not in helper

    loop = SCRIPT.split("for (const kind of [", 1)[1].split(
        "evidence.central_review.structured_rubric_set", 1
    )[0]
    assert "ensureReviewConfirmation(reviewSessionId, kind)" in loop
    assert "else" not in loop


def test_structured_set_is_automatically_prepared_and_read_back() -> None:
    helper = SCRIPT.split("async function ensureStructuredRubricSet", 1)[1].split(
        "async function drainReviewCount", 1
    )[0]
    assert "/structured-rubric-set" in helper
    assert "Structured Rubric Set automatic preparation" in helper
    assert "setResponse.body.current === true" in helper
    assert "current.session.structured_rubric_set_id === setResponse.body.id" in helper
    assert "bundle.bundle.structured_rubric_set?.id === setResponse.body.id" in helper
    assert 'getByTestId("structured-rubric-set-summary")' in helper
    assert "assertCurrentStructuredRubricSet" in helper
    assert "writes: []" in helper
    assert 'method: "POST"' not in helper


def test_structured_set_contract_is_exact_and_fail_closed() -> None:
    helper = SCRIPT.split("function assertCurrentStructuredRubricSet", 1)[1].split(
        "async function ensureStructuredRubricSet", 1
    )[0]
    assert "session.structured_rubric_set_id, rubricSet.id" in helper
    assert "bundle.structured_rubric_set?.id, rubricSet.id" in helper
    assert "bundle.structured_rubric_set.current, true" in helper
    assert "bundle.structured_rubric_set.reason, null" in helper
    assert "bundle.structured_rubric_set.content_hash" in helper
    assert "bundle.structured_rubric_set.source_snapshot_hash" in helper
    assert "rubricSet.items.length > 0" in helper
    assert "item.answer_content_hash" in helper
    assert "item.rubric_content_hash" in helper
    assert "item.criteria_hash" in helper
    assert "item.reference_answer_version_id" in helper
    assert "item.structured_rubric_version_id" in helper


def test_grading_evidence_uses_structured_ids_only() -> None:
    assert "structured_rubric_set_id" in SCRIPT
    assert "structured_rubric_version_id" in SCRIPT
    assert "criterion.criterion_id" in SCRIPT
    assert "snapshot.structured_rubric_set_id" in SCRIPT
    for legacy_token in (
        "rubric_item_id",
        "rubricItemId",
        "legacy_binding",
        "/rubric-binding",
        "projection_profile",
        "target_legacy_hash",
    ):
        assert legacy_token not in SCRIPT


def test_review_bundle_reads_require_the_current_schema_and_assignment() -> None:
    helper = SCRIPT.split("async function getReviewBundle", 1)[1].split(
        "function requireCurrentBundleConfirmation", 1
    )[0]
    assert '"assignment-review-bundle-v1"' in helper
    assert "bundle.assignment_id, assignmentId" in helper
    assert "Array.isArray(bundle.confirmations)" in helper


def test_review_item_drain_reopens_and_scopes_the_pending_details_each_iteration() -> None:
    helper = SCRIPT.split("async function drainReviewCount", 1)[1].split(
        "function boundedCriterionAllocation", 1
    )[0]
    loop = helper.index("for (let attempt = 0;")
    zero_exit = helper.index("counts[key] === 0", loop)
    expand = helper.index("pending details expanded", zero_exit)
    action = helper.index('pendingDetails.getByRole("button"', expand)
    click = helper.index("scopedActions.first().click()", action)
    assert loop < zero_exit < expand < action < click
    assert 'locator("summary")' in helper
    assert 'hasText: "查看全部待处理明细"' in helper
    assert 'pendingSummary.locator("xpath=..")' in helper
    assert "node instanceof HTMLDetailsElement" in helper
    assert "await pendingSummary.click()" in helper
    assert "pending-summary-count=" in helper
    assert "pending-details-open=true" in helper
    assert "page.getByRole" not in helper
    assert "/api/assignment-review-items/" not in helper
    assert 'method: "PATCH"' not in helper
    assert "write-after-GET" in helper
    assert "review_version > before.session.review_version" in helper


def test_publication_has_api_and_ui_hard_preconditions() -> None:
    block = SCRIPT.split("const requiredConfirmations", 1)[1].split(
        "await preparePublication.click()", 1
    )[0]
    for kind in (
        "classes",
        "due_at",
        "total_score",
        "file_roles",
        "answer_sources",
        "paper_version",
        "reference_answers",
        "structured_rubrics",
    ):
        assert f'"{kind}"' in block
    assert "publicationStructuredSet.body.current === true" in block
    assert "publicationSession.session.structured_rubric_set_id" in block
    assert "publicationStructuredSet.body.id" in block
    assert "publicationSession.session.counts.blocking, 0" in block
    assert "publicationSession.session.counts.warning, 0" in block
    assert '"✓ 已满足发布条件"' in block
    assert "preparePublication.isEnabled()" in block


def test_stage_e_processes_and_teacher_segments_before_recognition() -> None:
    stage_e = SCRIPT.split('currentStage = "E";', 1)[1].split('currentStage = "F";', 1)[0]
    processing = stage_e.index("processAndSegmentSyntheticSubmission(")
    processing_stage = stage_e.index(
        'stage("E", "submission_processing_completed_before_recognition")',
        processing,
    )
    page_order_read = stage_e.index(
        'stage("E", "processing_page_order_read_before_recognition")',
        processing_stage,
    )
    recognition = stage_e.index('page.getByTestId("submission-ocr-start").click()', page_order_read)
    evidence_assertions = stage_e.index("latest.block_sources.length > 0", recognition)
    passed = stage_e.index('evidence.stages.E.status = "passed"', evidence_assertions)

    assert (
        processing < processing_stage < page_order_read < recognition < evidence_assertions < passed
    )
    assert "[subjectiveQuestionId, objectiveQuestionId]" in stage_e
    assert ':not([data-status="finalized"])' in stage_e
    assert ':not([data-status="merged"])' in stage_e
    assert ':not([data-status="voided"])' in stage_e
    assert "SUBMISSION_PROCESSING_FAILED" in SCRIPT
    assert "ANSWER_RECOGNITION_FAILED" in stage_e
    assert "segmentation-incomplete" in SCRIPT
    assert "question-recognition-evidence" in stage_e
    assert "recognition-blocks" in stage_e
    assert "assert.equal(latest.stale, false)" in stage_e
    assert '["recognized", "requires_review", "confirmed"]' in stage_e


def test_recognition_completion_forbids_later_page_mutation_helpers_or_clicks() -> None:
    stage_e = SCRIPT.split('currentStage = "E";', 1)[1].split('currentStage = "F";', 1)[0]
    recognition = stage_e.index('page.getByTestId("submission-ocr-start").click()')
    after_recognition = stage_e[recognition:]

    assert page_mutation_violations(after_recognition) == []
    assert 'getByRole("button", { name: "保存当前页面顺序" })' not in after_recognition


def test_page_mutation_contract_rejects_post_recognition_ui_click_and_helper() -> None:
    stage_e = SCRIPT.split('currentStage = "E";', 1)[1].split('currentStage = "F";', 1)[0]
    recognition = stage_e.index('page.getByTestId("submission-ocr-start").click()')
    after_recognition = stage_e[recognition:]

    ui_mutation = (
        after_recognition
        + '\nawait page.getByRole("button", { name: "保存当前页面顺序" }).click();'
    )
    assert "ui:保存当前页面顺序" in page_mutation_violations(ui_mutation)

    helper_mutation = after_recognition + "\nawait reversePages(submissionId, pageIds);"
    assert "helper:reversePages" in page_mutation_violations(helper_mutation)


def test_segmentation_writes_are_ui_driven_and_followed_by_gets() -> None:
    if "single_continue_from_uploaded_unconfirmed_submission" in SCRIPT:
        assert "manual_submission_processing_start_count: 0" in SCRIPT
        assert "manual_region_confirmation_count: 0" in SCRIPT
        assert "manual_recognition_confirmation_count: 0" in SCRIPT
        return

    delete_helper = extract_js_function(SCRIPT, "deleteSyntheticRegionThroughUi")
    draw_helper = extract_js_function(SCRIPT, "drawSyntheticRegionThroughUi")
    processing_helper = extract_js_function(SCRIPT, "processAndSegmentSyntheticSubmission")

    assert 'getByTestId("submission-region-delete").click()' in delete_helper
    assert 'response.request().method() === "DELETE"' in delete_helper
    assert "regions after UI delete GET" in delete_helper
    assert "getByTestId(" in draw_helper
    assert '"submission-question-select"' in draw_helper
    assert '"submission-region-canvas"' in draw_helper
    assert "canvas.boundingBox()" in draw_helper
    assert "page.mouse.down()" in draw_helper
    assert "page.mouse.up()" in draw_helper
    assert 'response.request().method() === "POST"' in draw_helper
    assert "regions after UI draw GET" in draw_helper
    assert 'writeBody.status, "confirmed"' in draw_helper
    assert 'writeBody.source, "manual"' in draw_helper
    assert draw_readiness_violations(draw_helper) == []
    assert draw_pointer_safety_violations(draw_helper) == []
    assert "box.x + box.width * 0.03" not in draw_helper
    assert "box.x + box.width * 0.97" not in draw_helper
    assert "dispatchEvent" not in draw_helper
    assert "const dragPromise" in draw_helper
    assert "page.waitForResponse(" in draw_helper
    assert "BUSINESS_E2E_REGION_DRAW_DIAGNOSTIC" in draw_helper
    assert "request_failed" in draw_helper
    assert "post_observed" in draw_helper
    assert "sanitizeRequestUrl" in draw_helper

    for helper in (delete_helper, draw_helper, processing_helper):
        assert direct_region_mutations(helper) == []
    assert segmentation_scope_violations(delete_helper, draw_helper) == []

    assert "deleteSyntheticRegionThroughUi(" in processing_helper
    assert "drawSyntheticRegionThroughUi(" in processing_helper


def test_region_mutation_contract_rejects_a_direct_api_write_mutation() -> None:
    helper = extract_js_function(SCRIPT, "drawSyntheticRegionThroughUi")
    mutation = """
      await apiJson(
        `/api/submissions/${submissionId}/region-candidates`,
        { method: "DELETE" },
      );
    """
    mutated = helper.replace("const readResponse =", mutation + "\nconst readResponse =", 1)
    violations = direct_region_mutations(mutated)
    assert len(violations) == 1
    assert "region-candidates" in violations[0]
    assert 'method: "DELETE"' in violations[0]


def test_region_draw_readiness_contract_rejects_missing_image_or_stability_checks() -> None:
    helper = extract_js_function(SCRIPT, "drawSyntheticRegionThroughUi")

    missing_image = helper.replace("image.complete", "false", 1)
    assert "image-complete-missing" in draw_readiness_violations(missing_image)

    unstable = helper.replace("stableBoxSamples >= 2", "stableBoxSamples >= 1", 1)
    assert "stable-box-check-missing" in draw_readiness_violations(unstable)


def test_region_draw_pointer_safety_contract_rejects_unscrolled_or_unclipped_drag() -> None:
    helper = extract_js_function(SCRIPT, "drawSyntheticRegionThroughUi")

    unscrolled = helper.replace("await canvas.scrollIntoViewIfNeeded()", "", 1)
    assert "scroll-into-view-must-precede-mouse" in draw_pointer_safety_violations(unscrolled)

    unbounded = helper.replace(
        "page.mouse.move(dragStart.x, dragStart.y)",
        "page.mouse.move(box.x + box.width * 0.03, box.y + box.height * 0.03)",
        1,
    )
    assert "start-mouse-not-visible-rect-based" in draw_pointer_safety_violations(unbounded)


def test_segmentation_scope_contract_rejects_unscoped_ui_mutations() -> None:
    delete_helper = extract_js_function(SCRIPT, "deleteSyntheticRegionThroughUi")
    draw_helper = extract_js_function(SCRIPT, "drawSyntheticRegionThroughUi")

    global_delete = delete_helper.replace("regionCard.getByTestId", "page.getByTestId", 1)
    assert "delete-control-not-region-scoped" in segmentation_scope_violations(
        global_delete, draw_helper
    )

    region_without_id = delete_helper.replace('[data-region-id="${region.id}"]', "", 1)
    assert "delete-region-not-id-scoped" in segmentation_scope_violations(
        region_without_id, draw_helper
    )

    draw_without_page_id = draw_helper.replace('[data-page-id="${pageId}"]', "", 1)
    assert "draw-page-not-submission-scoped" in segmentation_scope_violations(
        delete_helper, draw_without_page_id
    )


def test_stage_e_records_processing_regions_and_current_evidence_metadata() -> None:
    helper = SCRIPT.split("async function processAndSegmentSyntheticSubmission", 1)[1].split(
        "\ntry {", 1
    )[0]
    stage_e = SCRIPT.split('currentStage = "E";', 1)[1].split('currentStage = "F";', 1)[0]

    assert "/processing-jobs/${startBody.id}" in helper
    assert "/processing-pages" in helper
    assert "/region-candidates" in helper
    assert "/segmentation-incomplete" in helper
    assert "processing_start: responseMetadata" in helper
    assert "processing_read:" in helper
    assert "pages_read:" in helper
    assert "initial_regions:" in helper
    assert "final_regions:" in helper
    assert "request_id: completed.response.request_id" in helper
    assert "error_code: completed.response.error_code" in helper
    assert "request_id: pagesResponse.request_id" in helper
    assert "error_code: pagesResponse.error_code" in helper

    assert "evidence.answer_recognition = []" in stage_e
    assert "recognition_version: latest.recognition_version" in stage_e
    assert "block_sources: latest.block_sources" in stage_e
    assert "provider_versions: latest.provider_versions" in stage_e
    assert 'assert.equal(block.provider, "fake")' in stage_e
    assert "assert.equal(block.stale, false)" in stage_e
    if "single_continue_from_uploaded_unconfirmed_submission" in SCRIPT:
        single_branch = SCRIPT.split("if (singleContinueProof) {", 2)[2].split(
            "\n  } else {\n    const beforePreparationResponse", 1
        )[0]
        assert "automaticConfirmations" in single_branch
        assert "automaticConfirmationOrigins" in single_branch
        assert "manual_region_confirmation_count: 0" in single_branch
        assert "manual_recognition_confirmation_count: 0" in single_branch
        assert 'run.status === "awaiting_teacher_review"' in single_branch
        return
    assert 'current?.status,\n      "recognized"' in stage_e

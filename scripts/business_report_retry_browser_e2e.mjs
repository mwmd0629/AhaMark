import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const base = process.env.REPORT_RETRY_WEB_URL ?? "http://localhost:3300";
const password =
  process.env.BUSINESS_E2E_TEACHER_PASSWORD ?? "Synthetic-Business-E2E-Only!";
const startedAt = new Date();
const runId = `report-retry-${startedAt
  .toISOString()
  .replace(/\D/g, "")
  .slice(0, 17)}`;
const marker = `${runId}.report-retry-e2e.synthetic.invalid`;
const email = `teacher-${Date.now()}@report-retry-e2e.synthetic.invalid`;
const evidencePath = path.resolve(
  process.env.REPORT_RETRY_EVIDENCE_PATH ??
    "docs/business-report-retry-verification.json",
);
const bootstrapPath = path.resolve(
  "test-results",
  "business-report-retry",
  `${runId}-bootstrap.json`,
);
const screenshotPath = path.resolve(
  "test-results",
  "business-report-retry",
  `${runId}-failure.png`,
);
const evidence = {
  result: "failed",
  code_version: execFileSync("git", ["rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim(),
  environment: "isolated APP_ENV=test six-service stack and real Edge",
  synthetic_marker: marker,
  failed_job_id: null,
  expired_job_id: null,
  retried_failed_job_id: null,
  retried_expired_job_id: null,
  release_id: null,
  assignment_id: null,
  class_id: null,
  student_id: null,
  old_failed_status: null,
  old_expired_status: null,
  old_failed_error_code: null,
  old_created_at_unchanged: false,
  new_job_status: {},
  old_job_unchanged: false,
  release_id_consistent: false,
  assignment_id_consistent: false,
  class_id_consistent: false,
  student_scope_consistent: false,
  report_type_consistent: false,
  duplicate_guard_verified: false,
  refresh_reconciliation_verified: false,
  browser_verified: [],
  api_verified: [
    "retry response status 201 observed from browser action",
    "replacement identifiers and inherited fields read from browser response",
  ],
  ui_component_verified: [
    "terminal status rendering",
    "retry action and duplicate-click guard",
  ],
  download_expired_verified: "not_applicable_ui_exposes_retry_directly",
  started_at: startedAt.toISOString(),
  completed_at: null,
  failure: null,
};

function persist() {
  evidence.completed_at = new Date().toISOString();
  fs.mkdirSync(path.dirname(evidencePath), { recursive: true });
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
}

function dockerExec(args, capture = false) {
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      "ahamark-business-e2e",
      "-f",
      "docker-compose.business-e2e.yml",
      "exec",
      "-T",
      ...args,
    ],
    {
      cwd: process.cwd(),
      encoding: capture ? "utf8" : undefined,
      stdio: capture ? ["ignore", "pipe", "inherit"] : "inherit",
    },
  );
}

fs.mkdirSync(path.dirname(bootstrapPath), { recursive: true });
dockerExec([
  "-e",
  `BUSINESS_E2E_TEACHER_EMAIL=${email}`,
  "-e",
  "BUSINESS_E2E_TEACHER_NAME=合成教师 Report Retry E2E",
  "api",
  "python",
  "-m",
  "app.cli.seed_business_e2e_teacher",
]);
execFileSync(process.execPath, ["scripts/business_browser_e2e.mjs"], {
  cwd: process.cwd(),
  stdio: "inherit",
  env: {
    ...process.env,
    BUSINESS_E2E_TEACHER_EMAIL: email,
    BUSINESS_E2E_RUN_PREFIX: runId,
    BUSINESS_E2E_MARKER_SUFFIX: "report-retry-e2e.synthetic.invalid",
    BUSINESS_E2E_ARTIFACT_ROOT: "test-results/business-report-retry/artifacts",
    BUSINESS_E2E_EVIDENCE_PATH: bootstrapPath,
  },
});
const bootstrap = JSON.parse(fs.readFileSync(bootstrapPath, "utf8"));
if (bootstrap.result !== "passed")
  throw new Error("REPORT_RETRY_BOOTSTRAP_FAILED");
const fixture = {
  batchId: bootstrap.objects.grading_batch_id,
  releaseId: bootstrap.objects.grade_release_id,
  assignmentId: bootstrap.objects.assignment_id,
  classId: bootstrap.objects.class_id,
  studentId: bootstrap.objects.student_ids[0],
};
evidence.release_id = fixture.releaseId;
evidence.assignment_id = fixture.assignmentId;
evidence.class_id = fixture.classId;
evidence.student_id = fixture.studentId;
const fixtureOutput = dockerExec(
  [
    "-e",
    `REPORT_RETRY_TEACHER_EMAIL=${email}`,
    "-e",
    `REPORT_RETRY_RELEASE_ID=${fixture.releaseId}`,
    "-e",
    `REPORT_RETRY_STUDENT_ID=${fixture.studentId}`,
    "-e",
    `REPORT_RETRY_RUN_ID=${runId}`,
    "api",
    "python",
    "-m",
    "app.cli.seed_report_retry_fixture",
  ],
  true,
);
const ids = fixtureOutput.match(
  /failed=([0-9a-f-]+) expired=([0-9a-f-]+) release=([0-9a-f-]+) student=([0-9a-f-]+)/i,
);
if (!ids) throw new Error("REPORT_RETRY_FIXTURE_OUTPUT_INVALID");
[, evidence.failed_job_id, evidence.expired_job_id] = ids;

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.EDGE_EXECUTABLE ??
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const context = await browser.newContext();
const page = await context.newPage();

function job(jobId) {
  return page.locator(`[data-testid="report-job"][data-report-id="${jobId}"]`);
}

async function retry(oldId, expectedStatus) {
  const old = job(oldId);
  await old.waitFor();
  const originalCreatedAt = await old.getAttribute("data-report-created-at");
  const originalErrorCode = await old.getAttribute("data-report-error-code");
  if ((await old.getAttribute("data-report-status")) !== expectedStatus)
    throw new Error(`OLD_STATUS_MISMATCH:${oldId}`);
  if ((await old.getAttribute("data-report-release-id")) !== fixture.releaseId)
    throw new Error(`OLD_RELEASE_MISMATCH:${oldId}`);
  if ((await old.getAttribute("data-report-student-id")) !== fixture.studentId)
    throw new Error(`OLD_STUDENT_MISMATCH:${oldId}`);
  if (
    (await old.getAttribute("data-report-assignment-id")) !==
      fixture.assignmentId ||
    (await old.getAttribute("data-report-class-id")) !== fixture.classId
  )
    throw new Error(`OLD_CONTEXT_MISMATCH:${oldId}`);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes(`/api/report-jobs/${oldId}/retry`),
  );
  await old.getByRole("button", { name: "创建新任务重试" }).click();
  const response = await responsePromise;
  if (response.status() !== 201)
    throw new Error(`RETRY_HTTP_${response.status()}:${oldId}`);
  const replacement = await response.json();
  if (replacement.id === oldId) throw new Error(`RETRY_REUSED_ID:${oldId}`);
  const next = job(replacement.id);
  await next.waitFor();
  const observedStatus = await next.getAttribute("data-report-status");
  const domReleaseId = await next.getAttribute("data-report-release-id");
  const domStudentId = await next.getAttribute("data-report-student-id");
  const domReportType = await next.getAttribute("data-report-type");
  if (
    domReleaseId !== replacement.grade_release_id ||
    domStudentId !== replacement.student_id ||
    domReportType !== replacement.report_type
  )
    throw new Error(`RETRY_RESPONSE_UI_MISMATCH:${oldId}`);
  if ((await old.getAttribute("data-report-status")) !== expectedStatus)
    throw new Error(`OLD_STATUS_MUTATED:${oldId}`);
  if ((await old.getAttribute("data-report-release-id")) !== fixture.releaseId)
    throw new Error(`OLD_RELEASE_MUTATED:${oldId}`);
  if (
    (await old.getAttribute("data-report-created-at")) !== originalCreatedAt ||
    (await old.getAttribute("data-report-error-code")) !== originalErrorCode
  )
    throw new Error(`OLD_TERMINAL_FIELDS_MUTATED:${oldId}`);
  const oldButton = old.getByRole("button", { name: "已创建重试任务" });
  await oldButton.waitFor();
  if (await oldButton.isEnabled())
    throw new Error(`RETRY_DUPLICATE_GUARD_MISSING:${oldId}`);
  return {
    id: replacement.id,
    initialStatus: replacement.status,
    observedStatus,
    releaseId: domReleaseId,
    studentId: domStudentId,
    reportType: domReportType,
    originalCreatedAt,
    originalErrorCode,
  };
}

try {
  await page.goto(`${base}/login`);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
  await page.goto(`${base}/grading/${fixture.batchId}`);
  await page
    .locator(
      `[data-testid="grade-release-version"][data-release-id="${fixture.releaseId}"]`,
    )
    .click();
  await job(evidence.failed_job_id).waitFor();
  await job(evidence.expired_job_id).waitFor();
  evidence.browser_verified.push(
    "synthetic teacher login and report list",
    "failed and expired terminal jobs visible",
  );

  const failedReplacement = await retry(evidence.failed_job_id, "failed");
  evidence.retried_failed_job_id = failedReplacement.id;
  evidence.new_job_status.failed = {
    initial: failedReplacement.initialStatus,
    observed: failedReplacement.observedStatus,
  };
  evidence.old_failed_error_code = failedReplacement.originalErrorCode;
  evidence.browser_verified.push(
    "failed retry clicked",
    "failed replacement observed",
  );

  const expiredReplacement = await retry(evidence.expired_job_id, "expired");
  evidence.retried_expired_job_id = expiredReplacement.id;
  evidence.new_job_status.expired = {
    initial: expiredReplacement.initialStatus,
    observed: expiredReplacement.observedStatus,
  };
  evidence.browser_verified.push(
    "expired retry clicked",
    "expired replacement observed",
  );

  evidence.old_failed_status = await job(evidence.failed_job_id).getAttribute(
    "data-report-status",
  );
  evidence.old_expired_status = await job(evidence.expired_job_id).getAttribute(
    "data-report-status",
  );
  evidence.old_job_unchanged =
    evidence.old_failed_status === "failed" &&
    evidence.old_expired_status === "expired";
  const replacements = [failedReplacement, expiredReplacement];
  evidence.release_id_consistent = replacements.every(
    (item) => item.releaseId === fixture.releaseId,
  );
  evidence.student_scope_consistent = replacements.every(
    (item) => item.studentId === fixture.studentId,
  );
  evidence.report_type_consistent = replacements.every(
    (item) => item.reportType === "student_report_pdf",
  );
  evidence.assignment_id_consistent =
    bootstrap.objects.assignment_id === fixture.assignmentId;
  evidence.class_id_consistent = bootstrap.objects.class_id === fixture.classId;
  evidence.duplicate_guard_verified = true;

  const beforeRefresh = await page.getByTestId("report-job").count();
  await page.reload();
  await job(evidence.failed_job_id).waitFor();
  await job(evidence.expired_job_id).waitFor();
  await job(evidence.retried_failed_job_id).waitFor();
  await job(evidence.retried_expired_job_id).waitFor();
  evidence.old_created_at_unchanged =
    (await job(evidence.failed_job_id).getAttribute(
      "data-report-created-at",
    )) === failedReplacement.originalCreatedAt &&
    (await job(evidence.expired_job_id).getAttribute(
      "data-report-created-at",
    )) === expiredReplacement.originalCreatedAt;
  evidence.assignment_id_consistent =
    (await job(evidence.retried_failed_job_id).getAttribute(
      "data-report-assignment-id",
    )) === fixture.assignmentId;
  evidence.class_id_consistent =
    (await job(evidence.retried_expired_job_id).getAttribute(
      "data-report-class-id",
    )) === fixture.classId;
  evidence.refresh_reconciliation_verified =
    (await page.getByTestId("report-job").count()) === beforeRefresh;
  evidence.browser_verified.push(
    "old and replacement jobs reconciled after refresh",
  );

  if (
    !evidence.old_job_unchanged ||
    !evidence.old_created_at_unchanged ||
    !evidence.release_id_consistent ||
    !evidence.assignment_id_consistent ||
    !evidence.class_id_consistent ||
    !evidence.student_scope_consistent ||
    !evidence.report_type_consistent ||
    !evidence.refresh_reconciliation_verified
  ) {
    throw new Error("REPORT_RETRY_RECONCILIATION_FAILED");
  }
  evidence.result = "passed";
} catch (error) {
  evidence.failure = {
    code: error instanceof Error ? error.message.slice(0, 240) : "UNKNOWN",
    page: page.url().split("?")[0],
  };
  await page
    .screenshot({ path: screenshotPath, fullPage: true, timeout: 10_000 })
    .catch(() => undefined);
  throw error;
} finally {
  persist();
  await browser.close();
}

console.log(
  `BUSINESS_REPORT_RETRY_BROWSER_E2E_PASSED failed=${evidence.failed_job_id} expired=${evidence.expired_job_id}`,
);

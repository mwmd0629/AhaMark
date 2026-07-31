import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const base = process.env.BUSINESS_E2E_WEB_URL ?? "http://localhost:3300";
const apiBase = (
  process.env.BUSINESS_E2E_API_URL ?? "http://localhost:8800"
).replace(/\/+$/, "");
const parsedApiBase = new URL(apiBase);
assert.ok(
  ["http:", "https:"].includes(parsedApiBase.protocol),
  "BUSINESS_E2E_API_URL must use http: or https:",
);
const apiOrigin = parsedApiBase.origin;
const email =
  process.env.BUSINESS_E2E_TEACHER_EMAIL ??
  "teacher@business-e2e.synthetic.invalid";
const password =
  process.env.BUSINESS_E2E_TEACHER_PASSWORD ?? "Synthetic-Business-E2E-Only!";
const codexLocalInternalToken =
  process.env.BUSINESS_E2E_CODEX_LOCAL_INTERNAL_TOKEN ??
  "phase3-business-e2e-codex-local-token-2026-only";
assert.ok(
  codexLocalInternalToken.length >= 32,
  "BUSINESS_E2E_CODEX_LOCAL_INTERNAL_TOKEN must be at least 32 characters",
);
const runPrefix = process.env.BUSINESS_E2E_RUN_PREFIX ?? "business-e2e";
const markerSuffix =
  process.env.BUSINESS_E2E_MARKER_SUFFIX ?? "business-e2e.synthetic.invalid";
const composeProject =
  process.env.BUSINESS_E2E_COMPOSE_PROJECT ?? "ahamark-business-e2e";
const exceptionBootstrap = process.env.BUSINESS_E2E_EXCEPTION_BOOTSTRAP === "1";
const skipDbProvenance =
  process.env.BUSINESS_E2E_SKIP_DB_PROVENANCE === "1";
const singleContinueProof = true;
const runTimeoutMs = Number(process.env.BUSINESS_E2E_TIMEOUT_MS ?? "240000");
const requestedStopAfter = process.env.BUSINESS_E2E_STOP_AFTER;
if (requestedStopAfter !== undefined && requestedStopAfter !== "F") {
  throw new Error("BUSINESS_E2E_STOP_AFTER must be unset or exactly F");
}
const stopAfterF = requestedStopAfter === "F" || singleContinueProof;
const runId = `${runPrefix}-${new Date().toISOString().replace(/[-:.TZ]/g, "")}`;
const marker = `${runId}.${markerSuffix}`;
const numberSeed = Number(runId.replace(/\D/g, "").slice(-6));
const studentNumbers = [0, 1, 2].map(
  (offset) => `0${String((numberSeed + offset) % 1_000_000).padStart(6, "0")}`,
);
const artifactDir = path.resolve(
  process.env.BUSINESS_E2E_ARTIFACT_ROOT ?? "test-results/business-e2e",
  runId,
);
const evidencePath = path.resolve(
  process.env.BUSINESS_E2E_EVIDENCE_PATH ??
    "docs/business-e2e-verification.json",
);
fs.mkdirSync(artifactDir, { recursive: true });
fs.mkdirSync(path.dirname(evidencePath), { recursive: true });

const evidence = {
  result: "failed",
  code_version: execFileSync("git", ["rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim(),
  environment: {
    kind: "isolated_compose",
    compose_project: composeProject,
    web_origin: base,
    api_origin: apiOrigin,
    data_policy: "synthetic_only",
  },
  synthetic_marker: marker,
  started_at: new Date().toISOString(),
  completed_at: null,
  execution: {
    requested_stop_after: requestedStopAfter ?? null,
    completed_through: null,
    completed_stage_count: 0,
    scope: stopAfterF ? "snapshot_only" : "full_business_chain",
    grade_release_write_attempted: false,
  },
  stages: Object.fromEntries(
    "ABCDEFGH"
      .split("")
      .map((key) => [key, { status: "not_run", ui_steps: [] }]),
  ),
  objects: {},
  reconciliation: {},
  ocr: {
    provider: "fake",
    meaning: "non-production workflow test adapter",
    proves: "browser UI and durable workflow orchestration only",
    does_not_prove:
      "RapidOCR accuracy, handwriting OCR, formula OCR, or LaTeX reliability",
  },
  subjective_scoring:
    "codex-assisted suggestion -> mandatory TeacherReview -> complete snapshot",
  provider_contract: {
    ai_grading_provider: "unavailable",
    codex_assisted_mode: "local suggestion-only",
    real_provider_called: false,
  },
  api_requests: {},
  failure: null,
};

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++)
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function pngChunk(type, data) {
  const name = Buffer.from(type);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, crc]);
}
function makePng(file, [red, green, blue]) {
  const width = 160;
  const height = 120;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const rows = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x++) {
      row[1 + x * 3] = (red + x + y) % 256;
      row[2 + x * 3] = (green + y) % 256;
      row[3 + x * 3] = (blue + x) % 256;
    }
    rows.push(row);
  }
  fs.writeFileSync(
    file,
    Buffer.concat([
      Buffer.from("89504e470d0a1a0a", "hex"),
      pngChunk("IHDR", header),
      pngChunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
      pngChunk("IEND", Buffer.alloc(0)),
    ]),
  );
}

const paperFile = path.join(artifactDir, `${runId}-paper.png`);
const submissionFiles = [
  [`${studentNumbers[0]}-page-1.png`, [220, 30, 40]],
  [`${studentNumbers[0]}-page-2.png`, [210, 50, 60]],
  [`${studentNumbers[1]}-page-1.png`, [30, 150, 80]],
  [`${studentNumbers[1]}-page-2.png`, [50, 170, 100]],
].map(([name, color]) => {
  const file = path.join(artifactDir, name);
  makePng(file, color);
  return file;
});
makePng(paperFile, [40, 80, 210]);
const csvFile = path.join(artifactDir, `${runId}-students.csv`);
fs.writeFileSync(
  csvFile,
  `姓名,学号,分组,性别,邮箱,联系方式\n合成学生甲-${runId},${studentNumbers[0]},,,student-1@${marker},\n合成学生乙-${runId},${studentNumbers[1]},,,student-2@${marker},\n合成学生未完成-${runId},${studentNumbers[2]},,,student-3@${marker},\n`,
  "utf8",
);

const browser = await chromium.launch({
  headless: true,
  executablePath:
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();
let currentStage = "A";
let runTimedOut = false;
let hasPrimaryFailure = false;
let primaryFailure = null;
const runWatchdog = setTimeout(() => {
  runTimedOut = true;
  void browser.close();
}, runTimeoutMs);

function stage(key, step) {
  evidence.stages[key].ui_steps.push(step);
}
async function clickAndWait(button, responsePattern) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes(responsePattern) &&
        response.request().method() !== "OPTIONS" &&
        response.ok(),
    ),
    button.click(),
  ]);
}
async function selectByLabel(label, text) {
  await page.getByLabel(label).selectOption({ label: text });
}
async function pollUntil(label, timeoutMs, inspect) {
  const deadline = Date.now() + timeoutMs;
  let polls = 0;
  let lastState = "not observed";
  while (Date.now() < deadline) {
    const observation = await inspect();
    lastState = observation.state ?? lastState;
    if (observation.done) return observation.value;
    polls += 1;
    if (polls % 20 === 0)
      console.log(`BUSINESS_E2E_WAIT label=${label} state=${lastState}`);
    await page.waitForTimeout(250);
  }
  throw new Error(`${label} timed out after ${timeoutMs}ms; last=${lastState}`);
}
function absoluteApiUrl(requestPath) {
  assert.ok(
    requestPath === "/api" || requestPath.startsWith("/api/"),
    `API path must remain under /api: ${requestPath}`,
  );
  const resolved = new URL(requestPath, `${apiBase}/`);
  assert.ok(
    resolved.pathname === "/api" || resolved.pathname.startsWith("/api/"),
    `normalized API path escaped /api: ${resolved.pathname}`,
  );
  assert.equal(
    resolved.origin,
    apiOrigin,
    "API URL must remain on configured origin",
  );
  return resolved.href;
}
async function apiJson(apiPath, { method = "GET", body } = {}) {
  const absoluteUrl = absoluteApiUrl(apiPath);
  const result = await page.evaluate(
    async ({ requestUrl, requestMethod, requestBody }) => {
      const csrf = document.cookie
        .split("; ")
        .find((item) => item.startsWith("ahamark_csrf="))
        ?.split("=")[1];
      const response = await fetch(requestUrl, {
        method: requestMethod,
        credentials: "include",
        headers:
          requestMethod === "GET"
            ? undefined
            : {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf ?? "",
              },
        body:
          requestBody === undefined ? undefined : JSON.stringify(requestBody),
      });
      let responseBody = null;
      let contentTypeError = false;
      const contentType = response.headers.get("content-type") ?? "";
      if (
        contentType.includes("application/json") ||
        contentType.includes("+json")
      ) {
        try {
          responseBody = await response.json();
        } catch {
          contentTypeError = true;
        }
      } else {
        contentTypeError = true;
      }
      return {
        status: response.status,
        body: responseBody,
        error_code: contentTypeError
          ? "HTTP_CONTENT_TYPE_ERROR"
          : response.ok
            ? null
            : (responseBody?.code ??
              responseBody?.detail?.code ??
              "UNKNOWN_API_ERROR"),
        request_id: response.headers.get("x-request-id"),
        request_url: response.url,
        request_origin: new URL(response.url).origin,
        content_type: contentType,
      };
    },
    {
      requestUrl: absoluteUrl,
      requestMethod: method,
      requestBody: body,
    },
  );
  evidence.api_requests[`${method} ${apiPath}`] = {
    method,
    path: apiPath,
    request_url: result.request_url,
    request_origin: result.request_origin,
    status: result.status,
    error_code: result.error_code,
    request_id: result.request_id,
    content_type: result.content_type,
    observed_at: new Date().toISOString(),
  };
  return result;
}
async function internalApiJson(apiPath, { method = "POST", body } = {}) {
  const absoluteUrl = absoluteApiUrl(apiPath);
  const response = await fetch(absoluteUrl, {
    method,
    headers: {
      Authorization: `Bearer ${codexLocalInternalToken}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const contentType = response.headers.get("content-type") ?? "";
  const responseBody =
    contentType.includes("application/json") || contentType.includes("+json")
      ? await response.json()
      : null;
  const result = {
    status: response.status,
    body: responseBody,
    error_code: response.ok
      ? null
      : (responseBody?.code ??
        responseBody?.detail?.code ??
        "UNKNOWN_API_ERROR"),
    request_id: response.headers.get("x-request-id"),
    request_url: response.url,
    request_origin: new URL(response.url).origin,
    content_type: contentType,
  };
  evidence.api_requests[`${method} ${apiPath}`] = {
    method,
    path: apiPath,
    request_url: result.request_url,
    request_origin: result.request_origin,
    status: result.status,
    error_code: result.error_code,
    request_id: result.request_id,
    content_type: result.content_type,
    auth: "internal_bearer_redacted",
    observed_at: new Date().toISOString(),
  };
  return result;
}
function syntheticCodexResponse(workItem, itemIndex) {
  const bundle = workItem.request?.grading_bundle;
  const criteria = bundle?.structured_rubric?.criteria;
  const evidenceRef = bundle?.evidence_refs?.[0];
  assert.ok(Array.isArray(criteria) && criteria.length > 0);
  assert.equal(typeof evidenceRef, "string");
  let hasManual = false;
  let total = 0;
  const suggestions = criteria.map((criterion, criterionIndex) => {
    const manual =
      criterion.validation_mode === "manual" ||
      criterion.manual_review_policy?.manual_only === true;
    const maxPoints = String(criterion.max_points);
    hasManual ||= manual;
    if (!manual) total += Number(maxPoints);
    return {
      criterion_stable_key: criterion.stable_key,
      status: manual ? "manual_required" : "suggested_pass",
      suggested_points: manual ? null : maxPoints,
      max_points: maxPoints,
      confidence: manual ? null : "0.8",
      decision: manual ? "manual" : "pass",
      evidence_refs: [evidenceRef],
      validation_refs: [],
      error_codes: [],
      requires_review: true,
      matched_steps: [],
      missing_steps: [],
      detected_errors: [],
      reasoning_summary: `Synthetic Codex-assisted suggestion ${itemIndex + 1}.${criterionIndex + 1}`,
      manual_review_reason: manual
        ? "Criterion policy requires teacher review"
        : null,
      student_feedback: "",
      teacher_note: "",
      abstained: manual,
    };
  });
  return {
    schema_version: "criterion-suggestion-v1",
    criteria: suggestions,
    total_suggested_points: hasManual ? null : String(total),
    student_feedback: "",
    teacher_summary:
      "Synthetic Codex-assisted suggestion; teacher review required",
    strengths: [],
    improvements: [],
    risk_flags: [],
  };
}
function workspaceAnswers(workspace) {
  return workspace.items.flatMap((item) => item.answers);
}
const objectiveQuestionTypes = new Set([
  "single_choice",
  "multiple_choice",
  "true_false",
  "fill_blank",
]);
function gradingWorkspaceReadiness(workspace) {
  const answers = workspaceAnswers(workspace);
  const summary = answers.map((answer) => ({
    answer_id: answer.id,
    answer_status: answer.status,
    question_id: answer.question.id,
    question_type: answer.question.type,
    region_ids: answer.regions.map((region) => region.id),
    evidence_ids: answer.evidence.map((item) => item.id),
    result_id: answer.result?.id ?? null,
    result_status: answer.result?.status ?? null,
    provider: answer.result?.provider ?? null,
    provider_version: answer.result?.provider_version ?? null,
    score: answer.result?.score ?? null,
    requires_review: answer.result?.requires_review ?? null,
  }));
  const objectiveAnswers = answers.filter((answer) =>
    objectiveQuestionTypes.has(answer.question.type),
  );
  const subjectiveAnswers = answers.filter(
    (answer) => !objectiveQuestionTypes.has(answer.question.type),
  );
  const objectiveReady = objectiveAnswers.every((answer) => {
    const score = Number(answer.result?.score);
    const maximum = Number(answer.question.max_score);
    return (
      answer.status === "graded" &&
      answer.result?.provider === "objective-rule" &&
      answer.result.status === "suggested" &&
      answer.result.score !== null &&
      answer.result.score !== undefined &&
      Number.isFinite(score) &&
      Number.isFinite(maximum) &&
      score >= 0 &&
      score <= maximum &&
      typeof answer.result.requires_review === "boolean"
    );
  });
  const subjectiveReady = subjectiveAnswers.every(
    (answer) =>
      answer.status === "graded" &&
      answer.result?.provider === "unavailable" &&
      answer.result.provider_version === "none" &&
      answer.result.status === "suggested" &&
      (answer.result.score === null || answer.result.score === undefined) &&
      answer.result.requires_review === true,
  );
  const evidenceReady = answers.every(
    (answer) => answer.regions.length >= 1 && answer.evidence.length >= 1,
  );
  return {
    done:
      answers.length === 4 &&
      objectiveAnswers.length === 2 &&
      subjectiveAnswers.length === 2 &&
      objectiveReady &&
      subjectiveReady &&
      evidenceReady,
    state: `answers=${answers.length}/objective=${objectiveAnswers.length}:${objectiveReady}/subjective=${subjectiveAnswers.length}:${subjectiveReady}/evidence=${evidenceReady}/statuses=${answers.map((answer) => answer.status).join(",")}`,
    value: {
      summary,
      objective_answer_ids: objectiveAnswers.map((answer) => answer.id),
      subjective_answer_ids: subjectiveAnswers.map((answer) => answer.id),
    },
  };
}
function assertApiOk(response, label) {
  assert.equal(
    response.status,
    200,
    `${label} failed: ${response.status}/${response.error_code}`,
  );
  assert.equal(
    response.error_code,
    null,
    `${label} invalid API response: ${response.error_code}/${response.content_type}`,
  );
  assert.equal(
    response.request_origin,
    apiOrigin,
    `${label} API origin mismatch`,
  );
  return response.body;
}
async function generationReviewInputs(assignmentId, questionIds) {
  const job = await pollUntil(
    "generation terminal review input",
    120_000,
    async () => {
      const response = await apiJson(
        `/api/assignments/${assignmentId}/generation-jobs`,
      );
      const jobs = assertApiOk(response, "generation jobs GET");
      const current = jobs[0];
      const terminal = [
        "partial",
        "review_required",
        "ready",
        "completed",
      ].includes(current?.status);
      return {
        done: Boolean(terminal && current?.revision?.id),
        value: current,
        state: `${current?.status ?? "missing"}/${current?.revision?.id ?? "no-revision"}`,
      };
    },
  );
  const revisionId = job.revision.id;
  await pollUntil("file analyses materialized", 60_000, async () => {
    const response = await apiJson(
      `/api/assignment-draft-revisions/${revisionId}/file-analyses`,
    );
    const files = assertApiOk(response, "file analyses GET");
    return {
      done: files.length > 0,
      value: files,
      state: `count=${files.length}`,
    };
  });
  const versions = [];
  for (const questionId of questionIds) {
    const ready = await pollUntil(
      `confirmed answer/rubric ${questionId}`,
      60_000,
      async () => {
        const [answersResponse, rubricsResponse] = await Promise.all([
          apiJson(`/api/questions/${questionId}/reference-answers`),
          apiJson(`/api/questions/${questionId}/structured-rubrics`),
        ]);
        const answers = assertApiOk(answersResponse, "reference answers GET");
        const rubrics = assertApiOk(rubricsResponse, "structured rubrics GET");
        const answer = answers.find((item) => item.status === "confirmed");
        const rubric = rubrics.find(
          (item) =>
            item.status === "confirmed" &&
            item.reference_answer_version_id === answer?.id,
        );
        return {
          done: Boolean(answer && rubric),
          value: { question_id: questionId, answer, rubric },
          state: `answers=${answers.length}/rubrics=${rubrics.length}/confirmed=${Boolean(answer && rubric)}`,
        };
      },
    );
    versions.push({
      question_id: questionId,
      reference_answer_version_id: ready.answer.id,
      structured_rubric_version_id: ready.rubric.id,
    });
  }
  return { job, revisionId, versions };
}
async function confirmFileAnalyses(revisionId) {
  const url = `/api/assignment-draft-revisions/${revisionId}/file-analyses`;
  const writes = [];
  return pollUntil("all file analyses teacher-confirmed", 90_000, async () => {
    const beforeResponse = await apiJson(url);
    const files = assertApiOk(beforeResponse, "file analyses confirmation GET");
    if (files.length === 0)
      return { done: false, state: "analyses not materialized" };
    const invalid = files.find(
      (item) => !["suggested", "confirmed"].includes(item.analysis_status),
    );
    if (invalid)
      throw new Error(
        `FILE_ANALYSIS_NOT_CONFIRMABLE:${invalid.id}:${invalid.analysis_status}`,
      );
    const pending = files.filter(
      (item) => item.analysis_status === "suggested",
    );
    if (pending.length === 0) {
      for (const item of files) {
        assert.ok(
          item.teacher_confirmed_role,
          `file ${item.id} missing teacher_confirmed_role`,
        );
        assert.ok(
          item.teacher_confirmed_answer_source,
          `file ${item.id} missing teacher_confirmed_answer_source`,
        );
      }
      return {
        done: true,
        value: {
          reads: files.map((item) => ({
            id: item.id,
            status: item.analysis_status,
            teacher_confirmed_role: item.teacher_confirmed_role,
            teacher_confirmed_answer_source:
              item.teacher_confirmed_answer_source,
            teacher_edit_version: item.teacher_edit_version,
          })),
          writes,
        },
        state: `confirmed=${files.length}/pending=0`,
      };
    }
    const fileAnalysisRegion = page.locator("details#generation-file-analysis");
    await pollUntil("file analysis details expanded", 20_000, async () => {
      if ((await fileAnalysisRegion.count()) === 0)
        return { done: false, state: "details-missing" };
      const state = await fileAnalysisRegion.evaluate((node) => ({
        isDetails: node instanceof HTMLDetailsElement,
        open: node instanceof HTMLDetailsElement && node.open,
      }));
      assert.equal(
        state.isDetails,
        true,
        "#generation-file-analysis must be an HTMLDetailsElement",
      );
      if (!state.open) {
        const summary = fileAnalysisRegion.locator("summary").first();
        if ((await summary.count()) === 0 || !(await summary.isVisible()))
          return { done: false, state: "summary-not-clickable" };
        await summary.click();
        return { done: false, state: "summary-clicked" };
      }
      return { done: true, state: "open=true" };
    });
    const buttons = fileAnalysisRegion.getByRole("button", {
      name: "确认文件分析",
    });
    const button = buttons.first();
    if ((await buttons.count()) === 0 || !(await button.isEnabled()))
      return {
        done: false,
        state: `confirmed=${files.length - pending.length}/pending=${pending.length}/button-not-ready`,
      };
    const [writeResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/confirmation") &&
          response.request().method() === "PATCH",
      ),
      button.click(),
    ]);
    const writeBody = await writeResponse.json().catch(() => null);
    assert.ok(writeResponse.ok(), "file analysis confirmation write failed");
    const confirmed = await pollUntil(
      `file analysis write-after-GET ${pending[0].id}`,
      20_000,
      async () => {
        const afterResponse = await apiJson(url);
        const after = assertApiOk(
          afterResponse,
          "file analysis read-after-write",
        );
        const row = after.find((item) => item.id === pending[0].id);
        return {
          done: Boolean(
            row?.analysis_status === "confirmed" &&
            row.teacher_confirmed_role &&
            row.teacher_confirmed_answer_source,
          ),
          value: { response: afterResponse, row },
          state: `${row?.analysis_status ?? "missing"}/${row?.teacher_confirmed_role ?? "no-role"}`,
        };
      },
    );
    writes.push({
      id: confirmed.row.id,
      write_status: writeResponse.status(),
      write_request_id: writeResponse.headers()["x-request-id"] ?? null,
      write_error_code: writeResponse.ok()
        ? null
        : (writeBody?.code ?? writeBody?.detail?.code ?? "UNKNOWN_API_ERROR"),
      read_status: confirmed.response.status,
      read_request_id: confirmed.response.request_id,
      read_error_code: confirmed.response.error_code,
      status: confirmed.row.analysis_status,
      teacher_confirmed_role: confirmed.row.teacher_confirmed_role,
      teacher_confirmed_answer_source:
        confirmed.row.teacher_confirmed_answer_source,
    });
    return {
      done: false,
      state: `confirmed=${files.length - pending.length + 1}/pending=${pending.length - 1}`,
    };
  });
}
async function settleGeneratedSuggestions(revisionId) {
  const pagesUrl = `/api/assignment-draft-revisions/${revisionId}/page-organization-suggestions`;
  const questionsUrl = `/api/assignment-draft-revisions/${revisionId}/question-extraction-candidates`;
  const writes = [];
  return pollUntil("generated suggestions settled", 90_000, async () => {
    const [pagesResponse, questionsResponse] = await Promise.all([
      apiJson(pagesUrl),
      apiJson(questionsUrl),
    ]);
    const pages = assertApiOk(pagesResponse, "page suggestions GET");
    const questions = assertApiOk(questionsResponse, "question candidates GET");
    const suggested = [...pages, ...questions].filter(
      (item) => item.status === "suggested",
    );
    if (suggested.length === 0)
      return {
        done: true,
        value: {
          page_statuses: pages.map((item) => ({
            id: item.id,
            status: item.status,
          })),
          question_statuses: questions.map((item) => ({
            id: item.id,
            status: item.status,
            materialized_question_id: item.materialized_question_id ?? null,
          })),
          writes,
        },
        state: `pages=${pages.length}/questions=${questions.length}/suggested=0`,
      };
    const rejects = page
      .getByRole("region", { name: "页面整理与题目抽取复核" })
      .getByRole("button", { name: "拒绝", exact: true });
    const button = rejects.first();
    if ((await rejects.count()) === 0 || !(await button.isEnabled()))
      return {
        done: false,
        state: `suggested=${suggested.length}/reject-not-ready`,
      };
    const beforeCount = suggested.length;
    const [writeResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "PATCH" &&
          (response.url().includes("/disposition") ||
            response.url().includes("/page-organization-suggestions/")),
      ),
      button.click(),
    ]);
    const body = await writeResponse.json().catch(() => null);
    assert.ok(writeResponse.ok(), "generated suggestion rejection failed");
    await pollUntil(
      "generated suggestion write-after-GET",
      20_000,
      async () => {
        const [nextPagesResponse, nextQuestionsResponse] = await Promise.all([
          apiJson(pagesUrl),
          apiJson(questionsUrl),
        ]);
        const nextPages = assertApiOk(
          nextPagesResponse,
          "page suggestions read-after-write",
        );
        const nextQuestions = assertApiOk(
          nextQuestionsResponse,
          "question candidates read-after-write",
        );
        const nextCount = [...nextPages, ...nextQuestions].filter(
          (item) => item.status === "suggested",
        ).length;
        return {
          done: nextCount < beforeCount,
          value: nextCount,
          state: `before=${beforeCount}/after=${nextCount}`,
        };
      },
    );
    writes.push({
      id: body?.id ?? null,
      status: body?.status ?? null,
      http_status: writeResponse.status(),
      request_id: writeResponse.headers()["x-request-id"] ?? null,
      error_code: null,
    });
    return { done: false, state: `suggested-before=${beforeCount}` };
  });
}
async function getReviewSession(sessionId, label) {
  const response = await apiJson(
    `/api/assignment-review-sessions/${sessionId}`,
  );
  return {
    response,
    session: assertApiOk(response, `${label} session GET`),
  };
}
const sha256HexPattern = /^[0-9a-f]{64}$/;
async function getReviewBundle(assignmentId, label) {
  const response = await apiJson(
    `/api/assignments/${assignmentId}/review-bundle`,
  );
  const bundle = assertApiOk(response, `${label} bundle GET`);
  assert.equal(bundle.schema_version, "assignment-review-bundle-v1");
  assert.equal(bundle.assignment_id, assignmentId);
  assert.ok(Array.isArray(bundle.confirmations));
  return {
    response,
    bundle,
  };
}
function requireCurrentBundleConfirmation(bundle, kind) {
  const matches = bundle.confirmations.filter((item) => item.type === kind);
  assert.equal(
    matches.length,
    1,
    `Bundle must expose exactly one current ${kind} confirmation`,
  );
  assert.equal(matches[0].status, "confirmed");
  assert.match(matches[0].source_hash, sha256HexPattern);
  assert.equal(
    matches[0].fingerprint_schema_version,
    "confirmation-fingerprint-v2",
  );
  assert.ok(
    ["origin", "inherited", "system_auto"].includes(matches[0].origin),
    `Bundle exposed unsupported ${kind} confirmation origin`,
  );
  assert.equal(matches[0].inherited, matches[0].origin === "inherited");
  return matches[0];
}
async function ensureReviewConfirmation(sessionId, kind) {
  const before = await getReviewSession(sessionId, kind);
  const assignmentId = before.session.assignment_id;
  const confirmationState = page.getByTestId(
    `review-confirmation-state-${kind}`,
  );
  if (before.session.confirmations.includes(kind)) {
    const currentBundle = await getReviewBundle(
      assignmentId,
      `${kind} idempotent confirmation`,
    );
    const confirmation = requireCurrentBundleConfirmation(
      currentBundle.bundle,
      kind,
    );
    await pollUntil(
      `idempotent confirmation UI projection ${kind}`,
      15_000,
      async () => {
        const count = await confirmationState.count();
        const visible = count === 1 && (await confirmationState.isVisible());
        return {
          done: visible,
          state: `count=${count}/visible=${visible}`,
        };
      },
    );
    return {
      kind,
      reused: true,
      write_status: null,
      write_request_id: null,
      write_error_code: null,
      read_status: before.response.status,
      read_request_id: before.response.request_id,
      read_error_code: before.response.error_code,
      review_version_before: before.session.review_version,
      review_version_after: before.session.review_version,
      confirmations: before.session.confirmations,
      bundle_read_status: currentBundle.response.status,
      bundle_read_request_id: currentBundle.response.request_id,
      bundle_read_error_code: currentBundle.response.error_code,
      bundle_confirmation: confirmation,
    };
  }
  const button = page.getByTestId(`review-confirmation-${kind}`);
  const ready = await pollUntil(
    `confirmation ready-or-confirmed ${kind}`,
    30_000,
    async () => {
      const current = await getReviewSession(sessionId, kind);
      const confirmed = current.session.confirmations.includes(kind);
      return {
        done:
          confirmed ||
          ((await button.count()) > 0 && (await button.isEnabled())),
        value: { ...current, confirmed },
        state: `confirmed=${confirmed}/button=${await button.count()}/enabled=${
          (await button.count()) > 0 ? await button.isEnabled() : false
        }`,
      };
    },
  );
  if (ready.confirmed) return ensureReviewConfirmation(sessionId, kind);
  const [writeResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes(`/confirm/${kind}`) &&
        response.request().method() === "POST",
    ),
    button.click(),
  ]);
  const writeBody = await writeResponse.json().catch(() => null);
  assert.ok(writeResponse.ok(), `confirmation ${kind} write failed`);
  const after = await pollUntil(
    `confirmation session write-after-GET ${kind}`,
    30_000,
    async () => {
      const current = await getReviewSession(sessionId, kind);
      return {
        done:
          current.session.confirmations.includes(kind) &&
          current.session.review_version > ready.session.review_version,
        value: current,
        state: `version=${current.session.review_version}/confirmed=${current.session.confirmations.includes(kind)}`,
      };
    },
  );
  await pollUntil(`confirmation UI projection ${kind}`, 15_000, async () => {
    const count = await confirmationState.count();
    const visible = count === 1 && (await confirmationState.isVisible());
    return {
      done: visible,
      state: `count=${count}/visible=${visible}`,
    };
  });
  const currentBundle = await pollUntil(
    `confirmation Bundle write-after-GET ${kind}`,
    30_000,
    async () => {
      const observed = await getReviewBundle(
        assignmentId,
        `${kind} confirmation`,
      );
      const matches = observed.bundle.confirmations.filter(
        (item) => item.type === kind && item.status === "confirmed",
      );
      return {
        done: matches.length === 1,
        value: { ...observed, confirmation: matches[0] },
        state: `current-confirmations=${matches.length}`,
      };
    },
  );
  const confirmation = requireCurrentBundleConfirmation(
    currentBundle.bundle,
    kind,
  );
  assert.equal(
    confirmation.origin,
    "origin",
    `new ${kind} confirmation must be recorded in the current session`,
  );
  assert.equal(confirmation.inherited, false);
  return {
    kind,
    reused: false,
    write_status: writeResponse.status(),
    write_request_id: writeResponse.headers()["x-request-id"] ?? null,
    write_error_code: writeResponse.ok()
      ? null
      : (writeBody?.code ?? writeBody?.detail?.code ?? "UNKNOWN_API_ERROR"),
    read_status: after.response.status,
    read_request_id: after.response.request_id,
    read_error_code: after.response.error_code,
    review_version_before: ready.session.review_version,
    review_version_after: after.session.review_version,
    confirmations: after.session.confirmations,
    bundle_read_status: currentBundle.response.status,
    bundle_read_request_id: currentBundle.response.request_id,
    bundle_read_error_code: currentBundle.response.error_code,
    bundle_confirmation: confirmation,
  };
}
function assertCurrentRubricBindingProjection(
  session,
  binding,
  bundle,
  { lossy },
) {
  assert.equal(bundle.assignment_id, session.assignment_id);
  assert.equal(bundle.binding?.id, binding.id);
  assert.equal(bundle.binding.binding_version, binding.binding_version);
  assert.equal(bundle.binding.status, "confirmed");
  assert.equal(binding.status, "confirmed");
  assert.equal(bundle.binding.source_binding_hash, binding.source_binding_hash);
  assert.equal(binding.projection_profile, "structured-to-legacy");
  assert.equal(binding.projection_version, "structured-rubric-projection-v3");
  assert.equal(binding.source_semantic_hash, binding.source_binding_hash);
  assert.match(binding.source_binding_hash, sha256HexPattern);
  assert.match(binding.target_legacy_hash, sha256HexPattern);
  assert.match(binding.loss_report_hash, sha256HexPattern);
  assert.equal(binding.manual_review_required, lossy);
  assert.equal(bundle.binding.projection_current, true);
  assert.equal(bundle.binding.projection_reason, null);
  assert.equal(bundle.binding.projection_profile, "structured-to-legacy");
  assert.equal(
    bundle.binding.projection_version,
    "structured-rubric-projection-v3",
  );
  assert.equal(
    bundle.binding.source_semantic_hash,
    bundle.binding.source_binding_hash,
  );
  assert.equal(
    bundle.binding.source_semantic_hash,
    binding.source_binding_hash,
  );
  assert.equal(bundle.binding.target_legacy_hash, binding.target_legacy_hash);
  assert.equal(bundle.binding.loss_report_hash, binding.loss_report_hash);
  assert.equal(bundle.binding.projection_profile, binding.projection_profile);
  assert.equal(bundle.binding.projection_version, binding.projection_version);
  assert.equal(
    bundle.binding.expected_source_binding_hash,
    binding.source_binding_hash,
  );
  assert.deepEqual(bundle.binding.mapping, binding.mapping);
  assert.deepEqual(bundle.binding.loss_report, binding.loss_report);
  assert.equal(bundle.binding.loss_report.length > 0, lossy);
  assert.equal(bundle.binding.manual_review_required, lossy);
  if (!lossy) assert.deepEqual(binding.loss_report, []);
  const confirmation = requireCurrentBundleConfirmation(
    bundle,
    "legacy_binding",
  );
  assert.equal(confirmation.inherited, false);
  assert.equal(confirmation.origin, lossy ? "origin" : "system_auto");
  assert.equal(confirmation.binding_id, binding.id);
  assert.equal(confirmation.source_binding_hash, binding.source_binding_hash);
  return confirmation;
}
async function ensureRubricBinding(sessionId) {
  const bindingUrl = `/api/assignment-review-sessions/${sessionId}/rubric-binding`;
  let current = await getReviewSession(sessionId, "binding");
  let bindingResponse = await apiJson(bindingUrl);
  assert.ok(
    [200, 404].includes(bindingResponse.status),
    `binding GET failed: ${bindingResponse.status}/${bindingResponse.error_code}`,
  );
  let binding = bindingResponse.status === 200 ? bindingResponse.body : null;
  const writes = [];
  if (!binding) {
    const prepare = page.getByTestId("prepare-rubric-publication-binding");
    await pollUntil("binding prepare ready-or-confirmed", 30_000, async () => {
      current = await getReviewSession(sessionId, "binding prepare");
      bindingResponse = await apiJson(bindingUrl);
      binding = bindingResponse.status === 200 ? bindingResponse.body : null;
      return {
        done:
          Boolean(binding) ||
          ((await prepare.count()) > 0 && (await prepare.isEnabled())),
        state: `binding=${binding?.status ?? "missing"}/legacy=${current.session.confirmations.includes("legacy_binding")}/prepare-enabled=${
          (await prepare.count()) > 0 ? await prepare.isEnabled() : false
        }`,
      };
    });
    if (!binding) {
      const beforeVersion = current.session.review_version;
      const [prepareResponse] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.url().includes("/rubric-binding") &&
            response.request().method() === "POST",
        ),
        prepare.click(),
      ]);
      assert.ok(prepareResponse.ok(), "binding prepare write failed");
      const prepared = await pollUntil(
        "binding prepare write-after-GET",
        30_000,
        async () => {
          const [nextSession, nextBinding] = await Promise.all([
            getReviewSession(sessionId, "binding prepared"),
            apiJson(bindingUrl),
          ]);
          return {
            done:
              nextBinding.status === 200 &&
              ["draft", "validated", "confirmed"].includes(
                nextBinding.body.status,
              ) &&
              nextSession.session.review_version > beforeVersion,
            value: { nextSession, nextBinding },
            state: `http=${nextBinding.status}/status=${nextBinding.body?.status ?? "missing"}/version=${nextSession.session.review_version}`,
          };
        },
      );
      current = prepared.nextSession;
      bindingResponse = prepared.nextBinding;
      binding = bindingResponse.body;
      writes.push({
        action: "prepare",
        http_status: prepareResponse.status(),
        request_id: prepareResponse.headers()["x-request-id"] ?? null,
        error_code: null,
        read_status: bindingResponse.status,
        review_version: current.session.review_version,
        binding_id: binding.id,
        binding_status: binding.status,
      });
    }
  }
  if (binding?.status !== "confirmed") {
    assert.ok(
      Array.isArray(binding?.loss_report) && binding.loss_report.length > 0,
      "only a lossy current binding may require manual confirmation",
    );
    assert.equal(binding.manual_review_required, true);
    const confirm = page.getByTestId("rubric-binding-loss-confirm");
    await pollUntil("binding confirm enabled", 30_000, async () => ({
      done: (await confirm.count()) > 0 && (await confirm.isEnabled()),
      state: `present=${await confirm.count()}/enabled=${
        (await confirm.count()) > 0 ? await confirm.isEnabled() : false
      }`,
    }));
    const beforeVersion = current.session.review_version;
    const [confirmResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/assignment-rubric-publication-bindings/${binding.id}/confirm`,
            ) && response.request().method() === "POST",
      ),
      confirm.click(),
    ]);
    assert.ok(confirmResponse.ok(), "binding confirm write failed");
    const confirmed = await pollUntil(
      "binding confirm write-after-GET",
      30_000,
      async () => {
        const [nextSession, nextBinding] = await Promise.all([
          getReviewSession(sessionId, "binding confirmed"),
          apiJson(bindingUrl),
        ]);
        return {
          done:
            nextBinding.status === 200 &&
            nextBinding.body.status === "confirmed" &&
            nextSession.session.confirmations.includes("legacy_binding") &&
            nextSession.session.review_version > beforeVersion,
          value: { nextSession, nextBinding },
          state: `binding=${nextBinding.body?.status ?? "missing"}/legacy=${nextSession.session.confirmations.includes("legacy_binding")}/version=${nextSession.session.review_version}`,
        };
      },
    );
    current = confirmed.nextSession;
    bindingResponse = confirmed.nextBinding;
    binding = bindingResponse.body;
    writes.push({
      action: "confirm",
      http_status: confirmResponse.status(),
      request_id: confirmResponse.headers()["x-request-id"] ?? null,
      error_code: null,
      read_status: bindingResponse.status,
      review_version: current.session.review_version,
      binding_id: binding.id,
      binding_status: binding.status,
      confirmations: current.session.confirmations,
    });
  }
  const lossy = binding.loss_report.length > 0;
  const verified = await pollUntil(
    "binding and Bundle write-after-GET",
    30_000,
    async () => {
      const [nextSession, nextBinding, nextBundle] = await Promise.all([
        getReviewSession(sessionId, "binding final"),
        apiJson(bindingUrl),
        getReviewBundle(current.session.assignment_id, "binding final"),
      ]);
      const matchingConfirmation = nextBundle.bundle.confirmations.find(
        (item) =>
          item.type === "legacy_binding" &&
          item.status === "confirmed" &&
          item.binding_id === nextBinding.body?.id &&
          item.source_binding_hash === nextBinding.body?.source_binding_hash,
      );
      return {
        done:
          nextBinding.status === 200 &&
          nextBinding.body.status === "confirmed" &&
          nextBundle.bundle.binding?.projection_current === true &&
          nextBundle.bundle.binding.projection_reason === null &&
          Boolean(matchingConfirmation),
        value: { nextSession, nextBinding, nextBundle },
        state: `binding=${nextBinding.body?.status ?? "missing"}/projection=${nextBundle.bundle.binding?.projection_current ?? false}/confirmation=${Boolean(matchingConfirmation)}`,
      };
    },
  );
  current = verified.nextSession;
  bindingResponse = verified.nextBinding;
  binding = bindingResponse.body;
  const confirmation = assertCurrentRubricBindingProjection(
    current.session,
    binding,
    verified.nextBundle.bundle,
    { lossy },
  );
  if (!lossy) {
    assert.equal(
      writes.some((write) => write.action === "confirm"),
      false,
      "lossless projection must not call confirm-binding",
    );
  }
  return {
    binding,
    session: current.session,
    bundle: verified.nextBundle.bundle,
    confirmation,
    writes,
  };
}
async function drainReviewCount(
  sessionId,
  key,
  actionName,
  label,
  maxAttempts,
) {
  const writes = [];
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const before = await getReviewSession(sessionId, label);
    if (before.session.counts[key] === 0) return writes;
    const pendingSummary = page
      .locator("summary")
      .filter({ hasText: "查看全部待处理明细" });
    const pendingDetails = pendingSummary.locator("xpath=..");
    await pollUntil(`${label} pending details expanded`, 20_000, async () => {
      const summaryCount = await pendingSummary.count();
      if (summaryCount !== 1)
        return {
          done: false,
          state: `pending-summary-count=${summaryCount}`,
        };
      const state = await pendingDetails.evaluate((node) => ({
        isDetails: node instanceof HTMLDetailsElement,
        open: node instanceof HTMLDetailsElement && node.open,
      }));
      assert.equal(
        state.isDetails,
        true,
        "pending review container must be an HTMLDetailsElement",
      );
      if (!state.open) {
        await pendingSummary.click();
        return { done: false, state: "pending-summary-clicked" };
      }
      return { done: true, state: "pending-details-open=true" };
    });
    const scopedActions = pendingDetails.getByRole("button", {
      name: actionName,
      exact: true,
    });
    await pollUntil(`${label} button enabled`, 20_000, async () => ({
      done:
        (await scopedActions.count()) > 0 &&
        (await scopedActions.first().isEnabled()),
      state: `count=${before.session.counts[key]}/buttons=${await scopedActions.count()}/enabled=${
        (await scopedActions.count()) > 0
          ? await scopedActions.first().isEnabled()
          : false
      }`,
    }));
    const [writeResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/assignment-review-items/") &&
          response.request().method() === "PATCH",
      ),
      scopedActions.first().click(),
    ]);
    assert.ok(writeResponse.ok(), `${label} write failed`);
    const after = await pollUntil(
      `${label} write-after-GET`,
      20_000,
      async () => {
        const current = await getReviewSession(sessionId, label);
        return {
          done:
            current.session.counts[key] < before.session.counts[key] &&
            current.session.review_version > before.session.review_version,
          value: current,
          state: `before=${before.session.counts[key]}/after=${current.session.counts[key]}/version=${current.session.review_version}`,
        };
      },
    );
    writes.push({
      write_status: writeResponse.status(),
      write_request_id: writeResponse.headers()["x-request-id"] ?? null,
      write_error_code: null,
      read_status: after.response.status,
      read_request_id: after.response.request_id,
      read_error_code: after.response.error_code,
      review_version_before: before.session.review_version,
      review_version_after: after.session.review_version,
      count_before: before.session.counts[key],
      count_after: after.session.counts[key],
    });
  }
  const final = await getReviewSession(sessionId, label);
  assert.equal(final.session.counts[key], 0, `${label} loop must converge`);
  return writes;
}
function boundedCriterionAllocation(criteria, requestedScore) {
  const scale = 10_000;
  const maximumUnits = criteria.map((item) =>
    Math.round(Number(item.max_points) * scale),
  );
  let remaining = Math.min(
    Math.round(requestedScore * scale),
    maximumUnits.reduce((total, value) => total + value, 0),
  );
  const scores = {};
  criteria.forEach((item, index) => {
    const awarded = Math.min(remaining, maximumUnits[index]);
    scores[item.rubric_item_id] = String(awarded / scale);
    remaining -= awarded;
  });
  assert.equal(
    remaining,
    0,
    "criterion allocation must cover the target score",
  );
  return {
    score: String(
      Object.values(scores).reduce((total, value) => total + Number(value), 0),
    ),
    criterion_scores: scores,
  };
}

function normalizeDecimalString(value) {
  if (typeof value !== "string")
    throw new TypeError("decimal value must be a string");
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value))
    throw new TypeError(`invalid decimal value: ${JSON.stringify(value)}`);

  const sign = value[0] === "+" || value[0] === "-" ? value[0] : "";
  const unsigned = sign ? value.slice(1) : value;
  let [integer, fraction = ""] = unsigned.split(".");
  integer = integer.replace(/^0+(?=\d)/, "") || "0";
  fraction = fraction.replace(/0+$/, "");
  return `${sign}${integer}${fraction ? `.${fraction}` : ""}`;
}

function decimalStringsEqual(actual, expected) {
  return normalizeDecimalString(actual) === normalizeDecimalString(expected);
}

function assertDecimalStringsEqual(actual, expected, message) {
  assert.equal(
    normalizeDecimalString(actual),
    normalizeDecimalString(expected),
    message,
  );
}

async function selectQuestion(number) {
  const select = page.getByLabel("当前题目");
  const options = await select.locator("option").evaluateAll((nodes) =>
    nodes.map((node) => ({
      value: node.getAttribute("value"),
      text: node.textContent,
    })),
  );
  const option = options.find((item) =>
    item.text?.replace(/\s+/g, " ").includes(`第 ${number} 题`),
  );
  if (!option?.value) throw new Error(`QUESTION_OPTION_${number}_NOT_FOUND`);
  await select.selectOption(option.value);
  if ((await select.inputValue()) !== option.value)
    throw new Error(`QUESTION_OPTION_${number}_NOT_SELECTED`);
  await page.locator(`textarea[data-question-id="${option.value}"]`).waitFor();
  const saveTarget = await page
    .getByRole("button", { name: "保存本题评分标准" })
    .getAttribute("data-question-id");
  if (saveTarget !== option.value)
    throw new Error(
      `QUESTION_${number}_SAVE_TARGET_MISMATCH:${option.value}:${saveTarget}`,
    );
  return option.value;
}
function responseMetadata(response, body = null) {
  return {
    status: response.status(),
    request_id: response.headers()["x-request-id"] ?? null,
    error_code: response.ok() ? null : (body?.code ?? "UNKNOWN_API_ERROR"),
    body,
  };
}
function syntheticDatabaseJson(sql) {
  const output = execFileSync(
    "docker",
    [
      "compose",
      "-p",
      composeProject,
      "-f",
      "docker-compose.business-e2e.yml",
      "exec",
      "-T",
      "postgres",
      "psql",
      "-U",
      "ahamark_e2e",
      "-d",
      "ahamark_business_e2e",
      "-tA",
      "-c",
      sql,
    ],
    { encoding: "utf8" },
  ).trim();
  assert.ok(output, "synthetic database verification returned no rows");
  return JSON.parse(output);
}
async function deleteSyntheticRegionThroughUi(
  submissionCard,
  submissionId,
  region,
) {
  const pageControl = submissionCard.locator(
    `[data-testid="submission-processing-page"][data-page-id="${region.submission_page_id}"]`,
  );
  await pageControl.locator("button").first().click();
  const regionCard = submissionCard.locator(
    `[data-testid="submission-region-card"][data-region-id="${region.id}"]`,
  );
  await regionCard.waitFor();
  const [writeResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(
            `/api/submissions/${submissionId}/region-candidates/${region.id}`,
          ) && response.request().method() === "DELETE",
    ),
    regionCard.getByTestId("submission-region-delete").click(),
  ]);
  assert.equal(writeResponse.status(), 204, "region delete must return 204");
  const readResponse = await apiJson(
    `/api/submissions/${submissionId}/region-candidates`,
  );
  const remaining = assertApiOk(readResponse, "regions after UI delete GET");
  assert.equal(
    remaining.some((item) => item.id === region.id),
    false,
    `deleted region ${region.id} remained visible`,
  );
  return {
    region_id: region.id,
    page_id: region.submission_page_id,
    write: responseMetadata(writeResponse),
    read_status: readResponse.status,
    read_request_id: readResponse.request_id,
    read_error_code: readResponse.error_code,
  };
}
async function drawSyntheticRegionThroughUi(
  submissionCard,
  submissionId,
  pageId,
  questionId,
) {
  const pageControl = submissionCard.locator(
    `[data-testid="submission-processing-page"][data-page-id="${pageId}"]`,
  );
  await pageControl.locator("button").first().click();
  const questionSelect = submissionCard.getByTestId(
    "submission-question-select",
  );
  await questionSelect.selectOption(questionId);
  assert.equal(await questionSelect.inputValue(), questionId);
  const canvas = submissionCard.getByTestId("submission-region-canvas");
  let previousBox;
  let stableBoxSamples = 0;
  const box = await pollUntil(
    "segmentation canvas ready for UI drawing",
    15_000,
    async () => {
      const [cardSubmissionId, selectedQuestionId, canvasPageId, currentBox] =
        await Promise.all([
          submissionCard.getAttribute("data-submission-id"),
          questionSelect.inputValue(),
          canvas.getAttribute("data-page-id"),
          canvas.boundingBox(),
        ]);
      const layout = await canvas.evaluate((node) => {
        const computed = getComputedStyle(node);
        const image = node.querySelector('img[alt="处理后的答卷页面"]');
        const imageUrl = image?.currentSrc || image?.getAttribute("src") || "";
        let srcWithoutQuery = null;
        try {
          const parsed = new URL(imageUrl, document.baseURI);
          srcWithoutQuery = `${parsed.origin}${parsed.pathname}`;
        } catch {
          srcWithoutQuery = imageUrl ? "invalid-url" : null;
        }
        const ancestorGridTemplateColumns = [];
        let ancestor = node.parentElement;
        while (ancestor) {
          const ancestorStyle = getComputedStyle(ancestor);
          if (ancestorStyle.display === "grid") {
            ancestorGridTemplateColumns.push(ancestorStyle.gridTemplateColumns);
          }
          ancestor = ancestor.parentElement;
        }
        return {
          canvas_display: computed.display,
          canvas_width: computed.width,
          ancestor_grid_template_columns: ancestorGridTemplateColumns,
          image: image
            ? {
                complete: image.complete,
                natural_width: image.naturalWidth,
                natural_height: image.naturalHeight,
                src_without_query: srcWithoutQuery,
              }
            : null,
        };
      });
      const drawable =
        currentBox && currentBox.width >= 200 && currentBox.height >= 100;
      const imageReady =
        layout.image?.complete &&
        layout.image.natural_width > 0 &&
        layout.image.natural_height > 0;
      const unchanged =
        drawable &&
        previousBox &&
        Math.abs(currentBox.x - previousBox.x) < 0.5 &&
        Math.abs(currentBox.y - previousBox.y) < 0.5 &&
        Math.abs(currentBox.width - previousBox.width) < 0.5 &&
        Math.abs(currentBox.height - previousBox.height) < 0.5;
      stableBoxSamples = unchanged ? stableBoxSamples + 1 : drawable ? 1 : 0;
      previousBox = drawable ? currentBox : undefined;
      const state = {
        submission_id: cardSubmissionId,
        page_id: canvasPageId,
        question_id: selectedQuestionId,
        bbox: currentBox,
        stable_box_samples: stableBoxSamples,
        ...layout,
      };
      return {
        done:
          cardSubmissionId === submissionId &&
          canvasPageId === pageId &&
          selectedQuestionId === questionId &&
          drawable &&
          imageReady &&
          stableBoxSamples >= 2,
        value: currentBox,
        state: JSON.stringify(state),
      };
    },
  );
  await canvas.scrollIntoViewIfNeeded();
  const [visibleBox, viewport] = await Promise.all([
    canvas.boundingBox(),
    Promise.resolve(page.viewportSize()),
  ]);
  assert.ok(
    visibleBox,
    "segmentation canvas lost its bounding box after scroll",
  );
  assert.ok(viewport, "browser viewport must be available for UI drawing");
  const visibleRect = {
    x: Math.max(visibleBox.x, 0),
    y: Math.max(visibleBox.y, 0),
    right: Math.min(visibleBox.x + visibleBox.width, viewport.width),
    bottom: Math.min(visibleBox.y + visibleBox.height, viewport.height),
  };
  visibleRect.width = visibleRect.right - visibleRect.x;
  visibleRect.height = visibleRect.bottom - visibleRect.y;
  assert.ok(
    visibleRect.width >= 200 && visibleRect.height >= 100,
    `segmentation canvas visible area is too small for UI drawing: ${JSON.stringify({ viewport, box: visibleBox, visible_rect: visibleRect })}`,
  );
  const dragStart = {
    x: visibleRect.x + visibleRect.width * 0.1,
    y: visibleRect.y + visibleRect.height * 0.1,
  };
  const dragEnd = {
    x: visibleRect.x + visibleRect.width * 0.9,
    y: visibleRect.y + visibleRect.height * 0.9,
  };
  const pointerElements = await canvas.evaluate(
    (node, points) => {
      const inspect = ({ x, y }) => {
        const element = document.elementFromPoint(x, y);
        return {
          tag: element?.tagName ?? null,
          testid: element?.getAttribute("data-testid") ?? null,
          inside_canvas: Boolean(element && node.contains(element)),
        };
      };
      return { start: inspect(points.start), end: inspect(points.end) };
    },
    { start: dragStart, end: dragEnd },
  );
  assert.equal(
    pointerElements.start.inside_canvas,
    true,
    `drag start is not inside segmentation canvas: ${JSON.stringify(pointerElements.start)}`,
  );
  assert.equal(
    pointerElements.end.inside_canvas,
    true,
    `drag end is not inside segmentation canvas: ${JSON.stringify(pointerElements.end)}`,
  );
  const sanitizeRequestUrl = (url) => {
    try {
      const parsed = new URL(url);
      return `${parsed.origin}${parsed.pathname}`;
    } catch {
      return "invalid-url";
    }
  };
  const isRegionWrite = (request) =>
    request.method() === "POST" &&
    sanitizeRequestUrl(request.url()).endsWith(
      `/api/submissions/${submissionId}/region-candidates`,
    );
  const regionWriteEvents = {
    request: null,
    response: null,
    request_failed: null,
  };
  const recordRequest = (request) => {
    if (isRegionWrite(request))
      regionWriteEvents.request = {
        method: request.method(),
        url: sanitizeRequestUrl(request.url()),
      };
  };
  const recordResponse = (response) => {
    if (isRegionWrite(response.request()))
      regionWriteEvents.response = {
        status: response.status(),
        url: sanitizeRequestUrl(response.url()),
      };
  };
  const recordRequestFailed = (request) => {
    if (isRegionWrite(request))
      regionWriteEvents.request_failed = {
        error: request.failure()?.errorText ?? "unknown-request-failure",
        url: sanitizeRequestUrl(request.url()),
      };
  };
  page.on("request", recordRequest);
  page.on("response", recordResponse);
  page.on("requestfailed", recordRequestFailed);
  const writePromise = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(`/api/submissions/${submissionId}/region-candidates`) &&
      response.request().method() === "POST",
  );
  let writeResponse;
  try {
    const dragPromise = (async () => {
      await page.mouse.move(dragStart.x, dragStart.y);
      await page.mouse.down();
      await page.mouse.move(dragEnd.x, dragEnd.y);
      await page.waitForTimeout(50);
      await page.mouse.up();
    })();
    [writeResponse] = await Promise.all([writePromise, dragPromise]);
  } catch (error) {
    const diagnostic = {
      viewport,
      box: visibleBox,
      visible_rect: visibleRect,
      drag_start: dragStart,
      drag_end: dragEnd,
      element_from_point: pointerElements,
      post_observed: regionWriteEvents.request !== null,
      post: regionWriteEvents,
    };
    console.error(
      `BUSINESS_E2E_REGION_DRAW_DIAGNOSTIC ${JSON.stringify(diagnostic)}`,
    );
    throw new Error(
      `${error.message}; region draw diagnostic=${JSON.stringify(diagnostic)}`,
    );
  } finally {
    page.off("request", recordRequest);
    page.off("response", recordResponse);
    page.off("requestfailed", recordRequestFailed);
  }
  const writeBody = await writeResponse.json();
  assert.equal(
    writeResponse.status(),
    201,
    "manual region create must return 201",
  );
  assert.equal(writeBody.question_id, questionId);
  assert.equal(writeBody.submission_page_id, pageId);
  assert.equal(writeBody.source, "manual");
  assert.equal(writeBody.status, "confirmed");
  const readResponse = await apiJson(
    `/api/submissions/${submissionId}/region-candidates`,
  );
  const regions = assertApiOk(readResponse, "regions after UI draw GET");
  const created = regions.find((item) => item.id === writeBody.id);
  assert.ok(created, `created region ${writeBody.id} missing after GET`);
  assert.equal(created.question_id, questionId);
  assert.equal(created.submission_page_id, pageId);
  assert.equal(created.status, "confirmed");
  return {
    region: created,
    write: responseMetadata(writeResponse, writeBody),
    read_status: readResponse.status,
    read_request_id: readResponse.request_id,
    read_error_code: readResponse.error_code,
  };
}
async function processAndSegmentSyntheticSubmission(
  submissionCard,
  submissionId,
  questionPagePlan,
) {
  const [startResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/submissions/${submissionId}/processing-jobs`) &&
        response.request().method() === "POST",
    ),
    submissionCard.getByTestId("submission-processing-start").click(),
  ]);
  const startBody = await startResponse.json();
  assert.equal(startResponse.status(), 201, "processing start must return 201");
  assert.equal(startBody.submission_id, submissionId);
  const completed = await pollUntil(
    `submission processing ${submissionId}`,
    120_000,
    async () => {
      const response = await apiJson(
        `/api/submissions/${submissionId}/processing-jobs/${startBody.id}`,
      );
      const job = assertApiOk(response, "processing job GET");
      if (["failed", "partially_completed", "cancelled"].includes(job.status))
        throw new Error(
          `SUBMISSION_PROCESSING_FAILED:${submissionId}:${job.status}:${job.error_code}`,
        );
      return {
        done: job.status === "completed",
        value: { job, response },
        state: `${job.status}/${job.stage}/${job.progress}/${job.error_code ?? "none"}`,
      };
    },
  );
  assert.equal(completed.job.stage, "completed");
  assert.equal(completed.job.progress, 100);
  assert.equal(completed.job.config_version, "submission-processing-v1");
  assert.equal(completed.job.error_code, null);
  await submissionCard
    .locator(
      `[data-testid="submission-processing-job"][data-job-id="${startBody.id}"][data-status="completed"]`,
    )
    .waitFor();

  const pagesResponse = await apiJson(
    `/api/submissions/${submissionId}/processing-pages`,
  );
  const processingPages = assertApiOk(
    pagesResponse,
    "processing pages after job GET",
  );
  assert.equal(
    processingPages.length,
    questionPagePlan.length,
    "synthetic submission page count changed",
  );
  assert.ok(
    processingPages.every(
      (item) =>
        ["completed", "blank"].includes(item.processing_status) &&
        item.preprocessing_version === "submission-processing-v1" &&
        item.processed_url,
    ),
    "processed pages must expose completed artifacts",
  );
  await pollUntil(
    "processed pages visible in segmentation UI",
    20_000,
    async () => {
      const uiPages = submissionCard.getByTestId("submission-processing-page");
      const statuses = await uiPages.evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-status")),
      );
      return {
        done:
          statuses.length === processingPages.length &&
          statuses.every((status) => ["completed", "blank"].includes(status)),
        state: `count=${statuses.length}/statuses=${statuses.join(",")}`,
      };
    },
  );
  const orderedPages = [...processingPages].sort(
    (left, right) => left.page_number - right.page_number,
  );

  const initialRegionsResponse = await apiJson(
    `/api/submissions/${submissionId}/region-candidates`,
  );
  const initialRegions = assertApiOk(
    initialRegionsResponse,
    "initial region candidates GET",
  );
  const deletes = [];
  for (const region of initialRegions) {
    deletes.push(
      await deleteSyntheticRegionThroughUi(
        submissionCard,
        submissionId,
        region,
      ),
    );
  }
  const emptyRegionsResponse = await apiJson(
    `/api/submissions/${submissionId}/region-candidates`,
  );
  assert.deepEqual(
    assertApiOk(emptyRegionsResponse, "empty regions write-after-GET"),
    [],
    "all fake candidates must be removed before explicit teacher mapping",
  );

  const draws = [];
  for (let index = 0; index < questionPagePlan.length; index += 1) {
    draws.push(
      await drawSyntheticRegionThroughUi(
        submissionCard,
        submissionId,
        orderedPages[index].id,
        questionPagePlan[index],
      ),
    );
  }
  const incompleteResponse = await apiJson(
    `/api/submissions/${submissionId}/segmentation-incomplete`,
  );
  const incomplete = assertApiOk(
    incompleteResponse,
    "segmentation completeness GET",
  );
  assert.equal(incomplete.complete, true);
  assert.deepEqual(incomplete.question_ids, []);
  const finalRegionsResponse = await apiJson(
    `/api/submissions/${submissionId}/region-candidates`,
  );
  const finalRegions = assertApiOk(
    finalRegionsResponse,
    "confirmed regions final GET",
  );
  for (const [index, questionId] of questionPagePlan.entries()) {
    const matches = finalRegions.filter(
      (item) =>
        item.question_id === questionId &&
        item.submission_page_id === orderedPages[index].id &&
        item.status === "confirmed" &&
        item.source === "manual",
    );
    assert.equal(
      matches.length,
      1,
      `question ${questionId} needs one confirmed region`,
    );
  }
  return {
    submission_id: submissionId,
    processing_start: responseMetadata(startResponse, startBody),
    processing_read: {
      status: completed.response.status,
      request_id: completed.response.request_id,
      error_code: completed.response.error_code,
      job: completed.job,
    },
    pages_read: {
      status: pagesResponse.status,
      request_id: pagesResponse.request_id,
      error_code: pagesResponse.error_code,
      pages: processingPages.map((item) => ({
        id: item.id,
        page_number: item.page_number,
        processing_status: item.processing_status,
        preprocessing_version: item.preprocessing_version,
      })),
    },
    initial_regions: initialRegions,
    deletes,
    draws,
    completeness: incomplete,
    final_regions: finalRegions,
  };
}

try {
  await page.goto(`${base}/login`);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
  await page.reload();
  await page.getByRole("heading", { name: /(?:早上好|你好)/ }).waitFor();
  const storageKeys = await page.evaluate(() => Object.keys(localStorage));
  if (
    storageKeys.some((key) =>
      /token|session|password|credential|auth/i.test(key),
    )
  ) {
    throw new Error("LONG_LIVED_CREDENTIAL_IN_LOCAL_STORAGE");
  }
  stage("A", "login_form_submit");
  stage("A", "protected_dashboard_after_refresh");
  stage("A", "local_storage_credential_key_check");
  evidence.stages.A.status = "passed";

  currentStage = "B";
  await page.goto(`${base}/classes`);
  await page.getByRole("button", { name: /创建班级/ }).click();
  const className = `合成班级 ${runId}`;
  await page.getByLabel("班级名称").fill(className);
  await page.getByLabel("年级").fill("合成九年级");
  await page.getByLabel("学科").fill("合成数学");
  await page.getByRole("button", { name: "保存班级" }).click();
  await page.getByText(className, { exact: true }).waitFor();
  const closeClassDialog = page.getByRole("button", { name: "关闭对话框" });
  if (await closeClassDialog.isVisible()) await closeClassDialog.click();
  await page.getByRole("link", { name: className, exact: true }).click();
  await page.waitForURL("**/classes/*");
  const classId = page.url().split("/").pop();
  evidence.objects.class_id = classId;
  await page.getByRole("button", { name: "导入学生" }).click();
  await page
    .getByRole("dialog", { name: "导入学生名单" })
    .locator('input[name="file"]')
    .setInputFiles(csvFile);
  await page.getByRole("button", { name: "上传并预览" }).click();
  await page.getByText("可导入 3").waitFor();
  await page.getByText(studentNumbers[0], { exact: true }).waitFor();
  await page.getByRole("button", { name: "确认导入整批合法数据" }).click();
  await page.getByText(/导入完成：新建 3/).waitFor();
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await page.getByText(`合成学生甲-${runId}`, { exact: true }).waitFor();
  await page.getByText(studentNumbers[0], { exact: true }).waitFor();
  stage("B", "create_class");
  stage("B", "csv_preview_and_confirm");
  stage("B", "leading_zero_and_three_students_visible");
  evidence.stages.B.status = "passed";

  currentStage = "C";
  await page.goto(`${base}/assignments/new`);
  const assignmentName = `合成作业 ${runId}`;
  await page.getByLabel("作业名称").fill(assignmentName);
  await page.getByLabel("学科").fill("合成数学");
  await page.getByLabel("年级").fill("合成九年级");
  await page.getByText(className, { exact: true }).click();
  await page.getByRole("button", { name: "保存草稿并继续" }).click();
  await page.waitForURL("**/assignments/*/edit");
  const assignmentId = page.url().split("/").at(-2);
  evidence.objects.assignment_id = assignmentId;
  evidence.objects.assignment_name = assignmentName;
  await page.getByRole("button", { name: /步骤 1/ }).click();
  await page.getByLabel("总分").fill("10");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("选择试卷文件").setInputFiles(paperFile);
  await page.getByRole("button", { name: "开始上传" }).click();
  await page.getByRole("button", { name: "继续整理页面" }).click();
  await page.getByRole("heading", { name: "整理页面" }).waitFor();
  await page.getByRole("button", { name: /步骤 3/ }).click();
  await page.getByRole("heading", { name: "整理页面" }).waitFor();
  await page.getByRole("heading", { name: "第 1 页", exact: true }).waitFor();
  stage("C", "six_step_wizard_basics_and_real_class");
  stage("C", "runtime_synthetic_png_upload_and_paper_page_created");
  evidence.stages.C.status = "passed";

  currentStage = "D";
  await page.goto(`${base}/assignments/${assignmentId}`);
  await page
    .locator('[data-testid="recognition-workspace"][data-provider="fake"]')
    .waitFor();
  await page.getByRole("button", { name: "开始识别" }).click();
  await page.getByTestId("recognition-job").waitFor();
  await page
    .locator('[data-testid="recognition-job"][data-status="completed"]')
    .waitFor({ timeout: 90_000 });
  evidence.objects.paper_recognition_job_id = await page
    .getByTestId("recognition-job")
    .getAttribute("data-job-id");
  await page.getByTestId("recognition-candidate").waitFor();
  await page.getByLabel("OCR 文字").fill("合成主观题：说明计算过程");
  await page.getByLabel("分值").fill(exceptionBootstrap ? "" : "5");
  await clickAndWait(
    page.getByRole("button", { name: "保存修正" }),
    "/candidates/",
  );
  await clickAndWait(
    page.getByRole("button", { name: "确认生成题目" }),
    "/confirm",
  );
  stage("D", "paper_ocr_job_and_candidate_visible");
  stage("D", "candidate_manually_corrected_and_confirmed");
  if (exceptionBootstrap && !singleContinueProof) {
    await page.goto(`${base}/assignments/${assignmentId}/edit`);
    await page.getByRole("button", { name: /步骤 5/ }).click();
    await page
      .getByText("当前题目分值未知，Rubric 保存和发布会被阻止。")
      .waitFor();
    const scoreDialog = page.waitForEvent("dialog");
    const scoreClick = page
      .getByRole("button", { name: "补齐所选题目分值" })
      .click();
    await (await scoreDialog).accept("5");
    await scoreClick;
    await page.getByText("题目分值已补齐，可以继续设置 Rubric").waitFor();
    stage("D", "null_candidate_score_blocks_rubric_then_teacher_fixes_via_ui");
  }

  await page.goto(`${base}/assignments/${assignmentId}/edit`);
  await page.getByRole("button", { name: /步骤 4/ }).click();
  await page.getByLabel("题号").fill("2");
  await page.getByLabel("题型").selectOption("single_choice");
  await page.getByLabel("分值").fill("5");
  await page.getByLabel("知识点（逗号分隔）").fill("合成知识点");
  await page.getByLabel("题目内容").fill("合成客观题：选择正确答案");
  await page.getByRole("button", { name: "添加题目" }).click();
  await page.getByText("题目已创建").waitFor();
  await page.getByRole("button", { name: /步骤 4/ }).click();
  await page.getByRole("heading", { name: "为第 2 题添加页面区域" }).waitFor();
  await page.getByRole("button", { name: "保存区域" }).click();
  await page.getByText("区域已保存").waitFor();
  const continueRubric = page.getByRole("button", {
    name: "继续设置评分标准",
  });
  if (await continueRubric.isVisible()) await continueRubric.click();
  else await page.getByRole("heading", { name: "评分标准" }).waitFor();
  const objectiveQuestionId = await selectQuestion(2);
  if (singleContinueProof) {
    const assignmentRead = await apiJson(`/api/assignments/${assignmentId}`);
    const assignmentDetail = assertApiOk(
      assignmentRead,
      "assignment detail before deterministic question region",
    );
    const paperPageId = assignmentDetail.paper_version?.pages?.[0]?.id;
    assert.ok(paperPageId, "single-continue proof requires a paper page");
    const deterministicRegion = await apiJson(
      `/api/assignments/${assignmentId}/questions/${objectiveQuestionId}/regions`,
      {
        method: "POST",
        body: {
          paper_page_id: paperPageId,
          x: 0.1,
          y: 0.4,
          width: 0.8,
          height: 0.2,
        },
      },
    );
    assert.equal(
      deterministicRegion.status,
      201,
      `deterministic question region failed: ${deterministicRegion.error_code}`,
    );
  }
  await page.waitForTimeout(250);
  await page.getByLabel("标准答案").fill("1. 测试题");
  await clickAndWait(
    page.getByRole("button", { name: "保存本题评分标准" }),
    "/rubrics/",
  );
  const subjectiveQuestionId = await selectQuestion(1);
  await page.waitForTimeout(250);
  await page.getByLabel("标准答案").fill("教师人工判断");
  await clickAndWait(
    page.getByRole("button", { name: "保存本题评分标准" }),
    "/rubrics/",
  );
  for (const [questionId, answer] of [
    [subjectiveQuestionId, "教师人工判断"],
    [objectiveQuestionId, "1. 测试题"],
  ]) {
    await page.goto(
      `${base}/assignments/${assignmentId}/rubrics/${questionId}`,
    );
    await page.getByLabel("标准答案草稿").fill(answer);
    await page.getByRole("button", { name: "保存新版本" }).click();
    await page.getByRole("button", { name: "确认来源与版本" }).click();
    await page.getByText(/teacher_authored · confirmed/).waitFor();
    await page.getByRole("button", { name: "创建 Rubric 草稿" }).click();
    await page.getByRole("dialog", { name: "结构化 Rubric 编辑器" }).waitFor();
    await page.getByRole("button", { name: "校验并确认" }).click();
    await page.getByText("confirmed", { exact: true }).waitFor();
  }
  await page.goto(`${base}/assignments/${assignmentId}/edit`);
  await page.getByRole("button", { name: /步骤 5/ }).click();
  await page.getByRole("button", { name: "进入发布检查" }).click();
  await page.getByRole("button", { name: "启动生成任务" }).click();
  await page.getByText("Generation", { exact: true }).waitFor();
  await page
    .getByLabel("生成状态")
    .getByText(/(?:部分完成|需要教师复核|已完成)/)
    .waitFor({ timeout: 120_000 });
  const generationInputs = await generationReviewInputs(assignmentId, [
    subjectiveQuestionId,
    objectiveQuestionId,
  ]);
  evidence.generation_review_readiness = {
    generation_job_id: generationInputs.job.id,
    generation: generationInputs.job.generation,
    generation_status: generationInputs.job.status,
    draft_revision_id: generationInputs.revisionId,
    confirmed_versions: generationInputs.versions,
  };
  evidence.file_analysis_confirmations = await confirmFileAnalyses(
    generationInputs.revisionId,
  );
  evidence.generated_suggestion_dispositions = await settleGeneratedSuggestions(
    generationInputs.revisionId,
  );
  const startCentralReview = page.getByRole("button", {
    name: "开始集中审查",
  });
  await pollUntil("central review start enabled", 30_000, async () => ({
    done:
      (await startCentralReview.count()) > 0 &&
      (await startCentralReview.isEnabled()),
    state: `present=${await startCentralReview.count()}/enabled=${
      (await startCentralReview.count()) > 0
        ? await startCentralReview.isEnabled()
        : false
    }`,
  }));
  const [createReviewResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/assignments/${assignmentId}/review-sessions`) &&
        response.request().method() === "POST",
    ),
    startCentralReview.click(),
  ]);
  const createdReviewSession = await createReviewResponse.json();
  assert.ok(createReviewResponse.ok(), "central review session create failed");
  const reviewSessionId = createdReviewSession.id;
  evidence.objects.assignment_review_session_id = reviewSessionId;
  evidence.central_review = {
    create_status: createReviewResponse.status(),
    create_request_id: createReviewResponse.headers()["x-request-id"] ?? null,
    create_error_code: null,
    review_session_id: reviewSessionId,
    review_version: createdReviewSession.review_version,
    confirmations: [],
    binding: null,
    dispositions: {},
  };
  await page
    .getByTestId("review-confirmation-classes")
    .waitFor({ state: "visible" });
  for (const kind of [
    "classes",
    "due_at",
    "total_score",
    "file_roles",
    "answer_sources",
    "paper_version",
    "reference_answers",
    "structured_rubrics",
  ]) {
    evidence.central_review.confirmations.push(
      await ensureReviewConfirmation(reviewSessionId, kind),
    );
  }
  evidence.central_review.binding = await ensureRubricBinding(reviewSessionId);
  evidence.central_review.dispositions.blocking = await drainReviewCount(
    reviewSessionId,
    "blocking",
    "人工检查并解决",
    "manual review resolution",
    12,
  );
  evidence.central_review.dispositions.warning = await drainReviewCount(
    reviewSessionId,
    "warning",
    "确认已查看",
    "warning acknowledgement",
    20,
  );
  const requiredConfirmations = [
    "classes",
    "due_at",
    "total_score",
    "file_roles",
    "answer_sources",
    "paper_version",
    "reference_answers",
    "structured_rubrics",
  ];
  const preparePublication = page.getByRole("button", {
    name: "准备发布",
    exact: true,
  });
  const publicationReady = await pollUntil(
    "publication hard preconditions",
    30_000,
    async () => {
      const publicationSession = await getReviewSession(
        reviewSessionId,
        "publication precondition",
      );
      const publicationBinding = await apiJson(
        `/api/assignment-review-sessions/${reviewSessionId}/rubric-binding`,
      );
      const missing = requiredConfirmations.filter(
        (kind) => !publicationSession.session.confirmations.includes(kind),
      );
      const uiReady = await page
        .getByText("✓ 已满足发布条件", { exact: true })
        .isVisible();
      const prepareEnabled =
        (await preparePublication.count()) > 0 &&
        (await preparePublication.isEnabled());
      return {
        done:
          missing.length === 0 &&
          publicationSession.session.confirmations.includes("legacy_binding") &&
          publicationBinding.status === 200 &&
          publicationBinding.body.status === "confirmed" &&
          publicationSession.session.counts.blocking === 0 &&
          publicationSession.session.counts.warning === 0 &&
          uiReady &&
          prepareEnabled,
        value: {
          publicationSession,
          publicationBinding,
          uiReady,
          prepareEnabled,
        },
        state: `missing=${missing.join(",") || "none"}/legacy=${publicationSession.session.confirmations.includes("legacy_binding")}/binding=${publicationBinding.body?.status ?? publicationBinding.status}/blocking=${publicationSession.session.counts.blocking}/warning=${publicationSession.session.counts.warning}/ui=${uiReady}/enabled=${prepareEnabled}`,
      };
    },
  );
  const { publicationSession, publicationBinding } = publicationReady;
  for (const kind of requiredConfirmations)
    assert.ok(
      publicationSession.session.confirmations.includes(kind),
      `publication missing confirmation ${kind}`,
    );
  assert.ok(
    publicationSession.session.confirmations.includes("legacy_binding"),
    "publication missing legacy_binding confirmation",
  );
  assert.equal(publicationBinding.status, 200);
  assert.equal(publicationBinding.body.status, "confirmed");
  assert.equal(publicationSession.session.counts.blocking, 0);
  assert.equal(publicationSession.session.counts.warning, 0);
  assert.equal(publicationReady.uiReady, true);
  assert.equal(publicationReady.prepareEnabled, true);
  evidence.central_review.publication_precondition = {
    session_read_status: publicationSession.response.status,
    session_read_request_id: publicationSession.response.request_id,
    session_read_error_code: publicationSession.response.error_code,
    review_version: publicationSession.session.review_version,
    confirmations: publicationSession.session.confirmations,
    counts: publicationSession.session.counts,
    binding_read_status: publicationBinding.status,
    binding_read_request_id: publicationBinding.request_id,
    binding_read_error_code: publicationBinding.error_code,
    binding_id: publicationBinding.body.id,
    binding_status: publicationBinding.body.status,
    ui_ready_copy: true,
    prepare_publication_enabled: true,
  };
  await preparePublication.click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "教师确认并发布" }).click();
  await page.waitForURL(`**/assignments/${assignmentId}`);
  stage("D", "objective_and_subjective_questions_with_positive_scores");
  stage("D", "rubric_items_knowledge_point_publish_check_and_publish");
  evidence.stages.D.status = "passed";

  currentStage = "E";
  await page.goto(`${base}/grading`);
  await selectByLabel("已发布作业", assignmentName);
  await page.getByLabel("班级").selectOption(classId);
  await page.getByLabel("批次名称").fill(`合成批次 ${runId}`);
  await page.getByRole("button", { name: "创建批改批次" }).click();
  const batchCard = page.getByTestId("grading-batch").first();
  await batchCard.waitFor();
  evidence.objects.grading_batch_id =
    await batchCard.getAttribute("data-batch-id");
  await batchCard.getByRole("link", { name: "进入批次工作台" }).click();
  await page.getByLabel("选择学生作业").setInputFiles(submissionFiles);
  await page.getByRole("button", { name: "上传并自动匹配" }).click();
  await page.getByText(/学生作业已上传/).waitFor();
  await page.getByTestId("submission").nth(1).waitFor();
  if (exceptionBootstrap && !singleContinueProof) {
    await page.getByRole("button", { name: "拆出末页" }).first().click();
    await page.getByText("Submission 已拆分且原始上传文件保持不变").waitFor();
    await page
      .getByRole("button", { name: "合并回首次 Submission" })
      .first()
      .click();
    await page.getByText("Submission 已合并且页码重新连续编号").waitFor();
    stage("E", "split_and_merge_before_ocr_via_ui");
  }
  const submissionCards = page.locator(
    '[data-testid="submission"]:not([data-status="finalized"]):not([data-status="merged"]):not([data-status="voided"])',
  );
  assert.equal(
    await submissionCards.count(),
    2,
    "synthetic Stage E requires exactly two matched submissions",
  );
  evidence.objects.submission_ids = await submissionCards.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-submission-id")),
  );
  evidence.objects.student_ids = await submissionCards.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-student-id")),
  );
  assert.ok(evidence.objects.submission_ids.every(Boolean));
  if (!singleContinueProof) {
    evidence.submission_processing = [];
    for (let index = 0; index < 2; index += 1) {
      const submissionCard = submissionCards.nth(index);
      const submissionId =
        await submissionCard.getAttribute("data-submission-id");
      assert.ok(submissionId, `submission ${index} is missing its opaque id`);
      evidence.submission_processing.push(
        await processAndSegmentSyntheticSubmission(
          submissionCard,
          submissionId,
          [subjectiveQuestionId, objectiveQuestionId],
        ),
      );
    }
    stage("E", "submission_processing_completed_before_recognition");
    stage("E", "teacher_ui_confirmed_two_question_regions_per_submission");
    stage("E", "processing_page_order_read_before_recognition");

    const recognitionWritePromises = [];
    const captureRecognitionWrite = (response) => {
      const url = new URL(response.url());
      if (
        response.request().method() === "POST" &&
        /^\/api\/submissions\/[^/]+\/recognition-jobs$/.test(url.pathname)
      ) {
        recognitionWritePromises.push(
          response.json().then((body) => ({
            submission_id: url.pathname.split("/")[3],
            response,
            body,
          })),
        );
      }
    };
    page.on("response", captureRecognitionWrite);
    try {
      await page.getByTestId("submission-ocr-start").click();
      await page
        .getByText(/Submission OCR 已完成/)
        .waitFor({ timeout: 120_000 });
    } finally {
      page.off("response", captureRecognitionWrite);
    }
    const recognitionWrites = await Promise.all(recognitionWritePromises);
    assert.equal(
      recognitionWrites.length,
      2,
      "bulk OCR must create one recognition job per synthetic submission",
    );
    await page.getByText(/Submission OCR 已完成/).waitFor({ timeout: 120_000 });
    const ocrCards = page.getByTestId("submission-ocr");
    await ocrCards.nth(1).waitFor();
    evidence.objects.submission_recognition_job_ids =
      await ocrCards.evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-job-id")),
      );
    evidence.objects.submission_ids = await page
      .locator(
        '[data-testid="submission"]:not([data-status="finalized"]):not([data-status="merged"]):not([data-status="voided"])',
      )
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-submission-id")),
      );
    evidence.objects.student_ids = await page
      .locator(
        '[data-testid="submission"]:not([data-status="finalized"]):not([data-status="merged"]):not([data-status="voided"])',
      )
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-student-id")),
      );
    evidence.answer_recognition = [];
    for (const write of recognitionWrites) {
      assert.equal(write.response.status(), 201);
      assert.equal(write.body.submission_id, write.submission_id);
      const completed = await pollUntil(
        `answer recognition ${write.submission_id}`,
        120_000,
        async () => {
          const response = await apiJson(
            `/api/submissions/${write.submission_id}/recognition-jobs/${write.body.id}`,
          );
          const job = assertApiOk(response, "answer recognition job GET");
          if (
            ["failed", "partially_completed", "cancelled"].includes(job.status)
          )
            throw new Error(
              `ANSWER_RECOGNITION_FAILED:${write.submission_id}:${job.status}:${job.error_code}`,
            );
          return {
            done: job.status === "completed",
            value: { job, response },
            state: `${job.status}/${job.progress}/${job.error_code ?? "none"}`,
          };
        },
      );
      assert.equal(completed.job.provider, "fake");
      assert.equal(completed.job.provider_version, "answer-evidence-1");
      assert.equal(completed.job.config_version, "answer-evidence-v1");
      assert.equal(completed.job.error_code, null);
      assert.ok(
        completed.job.pages.every((item) => item.status === "recognized"),
        "every synthetic page must be recognized after region evidence OCR",
      );

      const regionsResponse = await apiJson(
        `/api/submissions/${write.submission_id}/region-candidates`,
      );
      const regions = assertApiOk(
        regionsResponse,
        "confirmed regions before evidence GET",
      ).filter((item) => item.status === "confirmed");
      const evidenceResponse = await apiJson(
        `/api/submissions/${write.submission_id}/question-recognition-evidence`,
      );
      const questionEvidence = assertApiOk(
        evidenceResponse,
        "question recognition evidence GET",
      );
      const blocksResponse = await apiJson(
        `/api/submissions/${write.submission_id}/recognition-blocks`,
      );
      const blocks = assertApiOk(blocksResponse, "recognition blocks GET");
      const currentEvidence = [];
      for (const questionId of [subjectiveQuestionId, objectiveQuestionId]) {
        const confirmedRegions = regions.filter(
          (item) => item.question_id === questionId,
        );
        assert.ok(
          confirmedRegions.length >= 1,
          `question ${questionId} has no confirmed teacher region`,
        );
        const versions = questionEvidence
          .filter((item) => item.question_id === questionId)
          .sort(
            (left, right) =>
              Number(right.recognition_version) -
              Number(left.recognition_version),
          );
        const latest = versions[0];
        assert.ok(latest, `question ${questionId} has no recognition evidence`);
        assert.equal(latest.stale, false);
        assert.ok(
          ["recognized", "requires_review", "confirmed"].includes(
            latest.status,
          ),
          `question ${questionId} evidence is not current`,
        );
        assert.ok(
          latest.block_sources.length > 0,
          `question ${questionId} has no evidence block sources`,
        );
        const confirmedRegionIds = new Set(
          confirmedRegions.map((item) => item.id),
        );
        for (const source of latest.block_sources) {
          assert.ok(
            confirmedRegionIds.has(source.region_id),
            `evidence source ${source.region_id} is not a confirmed region`,
          );
          const block = blocks.find((item) => item.id === source.block_id);
          assert.ok(block, `evidence block ${source.block_id} is missing`);
          assert.equal(block.region_id, source.region_id);
          assert.equal(block.provider, "fake");
          assert.equal(block.stale, false);
          assert.ok(
            ["recognized", "requires_review", "confirmed"].includes(
              block.status,
            ),
            `evidence block ${block.id} is not current`,
          );
        }
        currentEvidence.push({
          id: latest.id,
          student_answer_id: latest.student_answer_id,
          question_id: latest.question_id,
          status: latest.status,
          recognition_version: latest.recognition_version,
          stale: latest.stale,
          block_sources: latest.block_sources,
          provider_versions: latest.provider_versions,
        });
      }
      evidence.answer_recognition.push({
        submission_id: write.submission_id,
        write: responseMetadata(write.response, write.body),
        read: {
          status: completed.response.status,
          request_id: completed.response.request_id,
          error_code: completed.response.error_code,
          job: completed.job,
        },
        regions_read: {
          status: regionsResponse.status,
          request_id: regionsResponse.request_id,
          regions,
        },
        evidence_read: {
          status: evidenceResponse.status,
          request_id: evidenceResponse.request_id,
          current: currentEvidence,
        },
        blocks_read: {
          status: blocksResponse.status,
          request_id: blocksResponse.request_id,
          block_ids: blocks.map((item) => item.id),
        },
      });
    }
    const submissionsAfterRecognitionResponse = await apiJson(
      `/api/grading-batches/${evidence.objects.grading_batch_id}/submissions`,
    );
    const submissionsAfterRecognition = assertApiOk(
      submissionsAfterRecognitionResponse,
      "submissions after recognition GET",
    );
    for (const submissionId of evidence.objects.submission_ids) {
      const current = submissionsAfterRecognition.find(
        (item) => item.id === submissionId,
      );
      assert.equal(
        current?.status,
        "recognized",
        `submission ${submissionId} did not reach recognized`,
      );
    }
    await page.getByText("已形成 StudentAnswer：4").waitFor();
  }
  stage("E", "create_batch_and_upload_four_synthetic_pages");
  stage("E", "automatic_filename_matching_two_submissions");
  if (!singleContinueProof) {
    stage("E", "confirmed_regions_current_evidence_and_page_order_ui");
    evidence.stages.E.status = "passed";
  }

  currentStage = "F";
  const reviewWorkspaceUrl = `/api/grading-batches/${evidence.objects.grading_batch_id}/review-workspace`;
  let continueResponse;
  let suggestionPlans;
  let initialResults;
  if (singleContinueProof) {
    const batchId = evidence.objects.grading_batch_id;
    continueResponse = await apiJson(
      `/api/grading-batches/${batchId}/processing-runs`,
      {
        method: "POST",
        body: { idempotency_key: `business-e2e-single-continue-${runId}` },
      },
    );
    assert.equal(
      continueResponse.status,
      201,
      `single continue failed: ${continueResponse.error_code}`,
    );
    const processingRun = continueResponse.body;
    assert.equal(processingRun.provider, "codex_local");
    assert.equal(processingRun.provider_label, "Codex-assisted");
    assert.equal(processingRun.suggestion_only, true);
    assert.equal(processingRun.target_state, "awaiting_teacher_review");
    assert.ok(processingRun.steps.length > 0);

    const reconciliation = [];
    let reconcileRound = 0;
    const waitingCodex = await pollUntil(
      "single continue reaches waiting Codex",
      180_000,
      async () => {
        reconcileRound += 1;
        const response = await apiJson(
          `/api/grading-batches/${batchId}/processing-runs/${processingRun.id}/reconcile`,
          {
            method: "POST",
            body: {
              idempotency_key: `business-e2e-single-reconcile-${runId}-${reconcileRound}`,
              expected_generation: processingRun.generation,
            },
          },
        );
        assert.equal(
          response.status,
          200,
          `single reconcile failed: ${response.error_code}`,
        );
        const run = response.body;
        reconciliation.push({
          round: reconcileRound,
          status: run.status,
          request_id: response.request_id,
          steps: run.steps.map((step) => ({
            id: step.id,
            submission_id: step.submission_id,
            student_answer_id: step.student_answer_id,
            kind: step.kind,
            stage: step.stage,
            status: step.status,
            error_code: step.error_code,
          })),
        });
        const failed = run.steps.find((step) =>
          [
            "blocked_review",
            "retryable_failed",
            "terminal_failed",
            "stale",
            "cancelled",
          ].includes(step.status),
        );
        if (
          failed ||
          [
            "waiting_input",
            "partially_failed",
            "failed",
            "stale",
            "cancelled",
          ].includes(run.status)
        ) {
          throw new Error(
            `SINGLE_CONTINUE_DID_NOT_AUTO_ADVANCE:${failed?.stage ?? run.status}:${
              failed?.error_code ?? run.error_code ?? "none"
            }`,
          );
        }
        const technicalStepsComplete = run.steps
          .filter((step) => step.kind === "recognition")
          .every((step) => step.status === "succeeded");
        return {
          done:
            technicalStepsComplete &&
            ["waiting_codex", "awaiting_teacher_review"].includes(run.status),
          value: { run, response },
          state: `${run.status}/${run.completed_step_count}/${run.step_count}`,
        };
      },
    );

    const automaticConfirmations = [];
    for (const submissionId of evidence.objects.submission_ids) {
      const regionsResponse = await apiJson(
        `/api/submissions/${submissionId}/region-candidates`,
      );
      const regions = assertApiOk(
        regionsResponse,
        "system-auto regions after single continue GET",
      ).filter((item) => item.status === "confirmed");
      assert.ok(regions.length >= 2);
      const evidenceResponse = await apiJson(
        `/api/submissions/${submissionId}/question-recognition-evidence`,
      );
      const recognitionEvidence = assertApiOk(
        evidenceResponse,
        "system-auto recognition evidence GET",
      ).filter((item) => !item.stale);
      assert.ok(recognitionEvidence.length >= 2);
      assert.ok(
        recognitionEvidence.every(
          (item) =>
            item.status === "confirmed" && item.requires_review === false,
        ),
        `submission ${submissionId} contains unconfirmed recognition evidence`,
      );
      automaticConfirmations.push({
        submission_id: submissionId,
        regions_read_status: regionsResponse.status,
        regions_request_id: regionsResponse.request_id,
        regions: regions.map((item) => ({
          id: item.id,
          student_answer_id: item.student_answer_id,
          status: item.status,
          source: item.source,
        })),
        evidence_read_status: evidenceResponse.status,
        evidence_request_id: evidenceResponse.request_id,
        recognition_evidence: recognitionEvidence.map((item) => ({
          id: item.id,
          student_answer_id: item.student_answer_id,
          status: item.status,
          recognition_version: item.recognition_version,
        })),
      });
    }
    const submissionScope = evidence.objects.submission_ids
      .map((submissionId) => `'${submissionId}'::uuid`)
      .join(",");
    const automaticConfirmationOrigins = skipDbProvenance
      ? { regions: [], recognition_evidence: [], deferred_to_host_audit: true }
      : syntheticDatabaseJson(`
      SELECT json_build_object(
        'regions',
        COALESCE((
          SELECT json_agg(json_build_object(
            'id', region.id,
            'submission_id', answer.submission_id,
            'student_answer_id', region.student_answer_id,
            'status', region.status,
            'confirmation_origin', region.confirmation_origin
          ) ORDER BY region.id)
          FROM student_answer_regions AS region
          JOIN student_answers AS answer ON answer.id = region.student_answer_id
          WHERE answer.submission_id IN (${submissionScope})
            AND region.status = 'confirmed'
        ), '[]'::json),
        'recognition_evidence',
        COALESCE((
          SELECT json_agg(json_build_object(
            'id', evidence_row.id,
            'submission_id', evidence_row.submission_id,
            'student_answer_id', evidence_row.student_answer_id,
            'status', evidence_row.status,
            'confirmation_origin', evidence_row.confirmation_origin
          ) ORDER BY evidence_row.id)
          FROM question_recognition_evidence AS evidence_row
          WHERE evidence_row.submission_id IN (${submissionScope})
            AND evidence_row.stale_at IS NULL
        ), '[]'::json)
      )::text;
    `);
    if (!skipDbProvenance) {
      assert.ok(automaticConfirmationOrigins.regions.length >= 4);
      assert.ok(
        automaticConfirmationOrigins.regions.every(
          (item) =>
            item.status === "confirmed" &&
            item.confirmation_origin === "system_auto",
        ),
        "every current region must be confirmed by system_auto",
      );
      assert.ok(automaticConfirmationOrigins.recognition_evidence.length >= 4);
      assert.ok(
        automaticConfirmationOrigins.recognition_evidence.every(
          (item) =>
            item.status === "confirmed" &&
            item.confirmation_origin === "system_auto",
        ),
        "every current recognition evidence row must be confirmed by system_auto",
      );
    }

    const processedWorkItems = [];
    if (waitingCodex.run.status === "waiting_codex") {
      for (let claimRound = 0; claimRound < 20; claimRound += 1) {
        const claimResponse = await internalApiJson(
          "/api/internal/codex-local/work-items/claim",
          {
            body: { worker_id: `business-e2e-${runId}`, limit: 100 },
          },
        );
        assert.equal(claimResponse.status, 200);
        if (claimResponse.body.count === 0) break;
        for (const [
          itemIndex,
          workItem,
        ] of claimResponse.body.items.entries()) {
          assert.equal(workItem.generation, processingRun.generation);
          assert.equal(workItem.request.provider, "codex_local");
          assert.equal(workItem.request.provider_label, "Codex-assisted");
          assert.equal(workItem.request.suggestion_only, true);
          const response = syntheticCodexResponse(
            workItem,
            processedWorkItems.length + itemIndex,
          );
          const submitResponse = await internalApiJson(
            `/api/internal/codex-local/work-items/${workItem.work_item_id}/submit`,
            {
              body: {
                worker_id: `business-e2e-${runId}`,
                lease_token: workItem.lease_token,
                request_hash: workItem.request_hash,
                response,
              },
            },
          );
          assert.equal(submitResponse.status, 200);
          assert.equal(submitResponse.body.status, "submitted");
          assert.equal(submitResponse.body.suggestion_only, true);
          const applyResponse = await internalApiJson(
            `/api/internal/codex-local/work-items/${workItem.work_item_id}/apply`,
            {
              body: {
                worker_id: `business-e2e-${runId}`,
                request_hash: workItem.request_hash,
                response_hash: submitResponse.body.response_hash,
              },
            },
          );
          assert.equal(applyResponse.status, 200);
          assert.equal(applyResponse.body.status, "applied");
          assert.equal(applyResponse.body.provider, "codex_local");
          assert.equal(applyResponse.body.provider_label, "Codex-assisted");
          assert.equal(applyResponse.body.suggestion_only, true);
          processedWorkItems.push({
            work_item_id: workItem.work_item_id,
            processing_step_id: workItem.processing_step_id,
            generation: workItem.generation,
            request_hash: workItem.request_hash,
            response_hash: submitResponse.body.response_hash,
            grading_job_id: applyResponse.body.grading_job_id,
            grading_result_id: applyResponse.body.grading_result_id,
            submit_status: submitResponse.status,
            submit_request_id: submitResponse.request_id,
            apply_status: applyResponse.status,
            apply_request_id: applyResponse.request_id,
          });
        }
      }
      assert.ok(
        processedWorkItems.length > 0,
        "waiting_codex must expose Codex-local work items",
      );
    }

    const completedRun = await pollUntil(
      "single continue reaches teacher review",
      90_000,
      async () => {
        reconcileRound += 1;
        const response = await apiJson(
          `/api/grading-batches/${batchId}/processing-runs/${processingRun.id}/reconcile`,
          {
            method: "POST",
            body: {
              idempotency_key: `business-e2e-single-reconcile-${runId}-${reconcileRound}`,
              expected_generation: processingRun.generation,
            },
          },
        );
        assert.equal(response.status, 200);
        const run = response.body;
        const failed = run.steps.find((step) =>
          [
            "blocked_review",
            "retryable_failed",
            "terminal_failed",
            "stale",
            "cancelled",
          ].includes(step.status),
        );
        if (failed)
          throw new Error(
            `SINGLE_CONTINUE_FINAL_FAILED:${failed.stage}:${failed.error_code}`,
          );
        return {
          done: run.status === "awaiting_teacher_review",
          value: { run, response },
          state: `${run.status}/${run.completed_step_count}/${run.step_count}`,
        };
      },
    );
    assert.equal(completedRun.run.pending_codex_count, 0);
    assert.ok(
      completedRun.run.steps.every((step) => step.status === "succeeded"),
    );
    evidence.processing_orchestration = {
      proof: "single_continue_from_uploaded_unconfirmed_submission",
      processing_run_post_count: 1,
      manual_submission_processing_start_count: 0,
      manual_region_confirmation_count: 0,
      manual_recognition_confirmation_count: 0,
      run_id: processingRun.id,
      generation: processingRun.generation,
      provider: processingRun.provider,
      provider_label: processingRun.provider_label,
      suggestion_only: processingRun.suggestion_only,
      target_state: processingRun.target_state,
      continue_status: continueResponse.status,
      continue_request_id: continueResponse.request_id,
      final_status: completedRun.run.status,
      final_reconcile_status: completedRun.response.status,
      final_reconcile_request_id: completedRun.response.request_id,
      automatic_confirmations: automaticConfirmations,
      automatic_confirmation_origin_database_read: automaticConfirmationOrigins,
      reconciliation,
      work_items: processedWorkItems,
    };
    evidence.answer_recognition = automaticConfirmations;
    stage("E", "single_continue_auto_processing_region_and_recognition");
    evidence.stages.E.status = "passed";

    const initialWorkspaceResponse = await apiJson(reviewWorkspaceUrl);
    assertApiOk(
      initialWorkspaceResponse,
      "single continue teacher review workspace GET",
    );
    const initialWorkspaceAnswers = workspaceAnswers(
      initialWorkspaceResponse.body,
    );
    assert.equal(initialWorkspaceAnswers.length, 4);
    evidence.codex_suggestions = [];
    suggestionPlans = new Map();
    initialResults = new Map();
    for (const answer of initialWorkspaceAnswers) {
      assert.equal(answer.review, null);
      assert.equal(answer.result.provider, "codex_local");
      assert.equal(answer.result.provider_version, "local");
      assert.equal(answer.result.status, "suggested");
      assert.equal(answer.result.requires_review, true);
      initialResults.set(answer.id, {
        answer_id: answer.id,
        answer_status: answer.status,
        answer_requires_review: answer.requires_review,
        result_id: answer.result.id,
        rubric_version_id: answer.result.rubric_version_id,
        provider: answer.result.provider,
        provider_version: answer.result.provider_version,
        status: answer.result.status,
        score: answer.result.score,
        requires_review: answer.result.requires_review,
        review: answer.review,
      });
    }
    await page.goto(`${base}/grading/${batchId}/review`);
    await page.waitForURL("**/review");
  } else {
    const beforePreparationResponse = await apiJson(reviewWorkspaceUrl);
    const beforePreparation = assertApiOk(
      beforePreparationResponse,
      "grading workspace before preparation GET",
    );
    const prepareGradingInputs = page.getByTestId("prepare-grading-inputs");
    await pollUntil("prepare grading inputs enabled", 20_000, async () => ({
      done:
        (await prepareGradingInputs.count()) === 1 &&
        (await prepareGradingInputs.isVisible()) &&
        (await prepareGradingInputs.isEnabled()),
      state: `count=${await prepareGradingInputs.count()}/visible=${
        (await prepareGradingInputs.count()) === 1
          ? await prepareGradingInputs.isVisible()
          : false
      }/enabled=${
        (await prepareGradingInputs.count()) === 1
          ? await prepareGradingInputs.isEnabled()
          : false
      }`,
    }));
    await prepareGradingInputs.click();
    const preparedWorkspace = await pollUntil(
      "prepared grading workspace",
      60_000,
      async () => {
        const response = await apiJson(reviewWorkspaceUrl);
        const workspace = assertApiOk(
          response,
          "prepared grading workspace GET",
        );
        const readiness = gradingWorkspaceReadiness(workspace);
        return {
          ...readiness,
          value: { ...readiness.value, response, workspace },
        };
      },
    );
    const beforeStatuses = new Map(
      workspaceAnswers(beforePreparation).map((answer) => [
        answer.id,
        answer.status,
      ]),
    );
    const statusTransitions = preparedWorkspace.summary.map((answer) => ({
      answer_id: answer.answer_id,
      before_status: beforeStatuses.get(answer.answer_id) ?? null,
      after_status: answer.answer_status,
    }));
    for (const transition of statusTransitions) {
      if (
        ["stale", "manual_segmentation_required"].includes(
          transition.before_status,
        )
      )
        assert.equal(
          transition.after_status,
          "graded",
          `${transition.answer_id} did not converge from ${transition.before_status}`,
        );
    }
    evidence.grading_input_preparation = {
      click_count: 1,
      before_read_status: beforePreparationResponse.status,
      before_read_request_id: beforePreparationResponse.request_id,
      before_read_error_code: beforePreparationResponse.error_code,
      before_answers: workspaceAnswers(beforePreparation).map((answer) => ({
        answer_id: answer.id,
        answer_status: answer.status,
        result_id: answer.result?.id ?? null,
        result_status: answer.result?.status ?? null,
        provider: answer.result?.provider ?? null,
      })),
      after_read_status: preparedWorkspace.response.status,
      after_read_request_id: preparedWorkspace.response.request_id,
      after_read_error_code: preparedWorkspace.response.error_code,
      after_answers: preparedWorkspace.summary,
      status_transitions: statusTransitions,
    };
    await page.getByTestId("open-teacher-review").click();
    await page.waitForURL("**/review");
    const initialWorkspaceResponse = await apiJson(reviewWorkspaceUrl);
    assertApiOk(
      initialWorkspaceResponse,
      "initial teacher review workspace GET",
    );
    const initialWorkspaceAnswers = workspaceAnswers(
      initialWorkspaceResponse.body,
    );
    const subjectiveAnswers = initialWorkspaceAnswers.filter(
      (answer) => !objectiveQuestionTypes.has(answer.question.type),
    );
    assert.equal(
      subjectiveAnswers.length,
      2,
      "the synthetic chain must contain two subjective answers",
    );
    evidence.codex_suggestions = [];
    suggestionPlans = new Map();
    initialResults = new Map();
    for (const answer of initialWorkspaceAnswers) {
      assert.equal(
        answer.review,
        null,
        "suggestion must precede TeacherReview",
      );
      assert.ok(
        answer.result,
        "grading preparation must leave an auditable result",
      );
      initialResults.set(answer.id, {
        answer_id: answer.id,
        answer_status: answer.status,
        answer_requires_review: answer.requires_review,
        result_id: answer.result.id,
        rubric_version_id: answer.result.rubric_version_id,
        provider: answer.result.provider,
        provider_version: answer.result.provider_version,
        status: answer.result.status,
        score: answer.result.score,
        requires_review: answer.result.requires_review,
        review: answer.review,
      });
    }
    for (const answer of subjectiveAnswers) {
      assert.equal(answer.result.provider, "unavailable");
      assert.equal(answer.result.provider_version, "none");
      assert.equal(answer.result.status, "suggested");
      assert.equal(answer.result.score, null);
      assert.equal(answer.result.requires_review, true);
      assert.equal(
        answer.requires_review,
        false,
        "StudentAnswer recognition must not inherit GradingResult suggestion review state",
      );
      assert.ok(
        answer.criteria.length > 0,
        "subjective rubric items are required",
      );
    }
    const batchId = evidence.objects.grading_batch_id;
    const recognitionContinueResponse = await apiJson(
      `/api/grading-batches/${batchId}/processing-runs`,
      {
        method: "POST",
        body: { idempotency_key: `business-e2e-recognition-${runId}` },
      },
    );
    assert.equal(
      recognitionContinueResponse.status,
      201,
      `recognition continue failed: ${recognitionContinueResponse.error_code}`,
    );
    const recognitionRun = recognitionContinueResponse.body;
    assert.equal(recognitionRun.pending_codex_count, 0);
    assert.ok(recognitionRun.steps.length > 0);
    assert.ok(
      recognitionRun.steps.every(
        (step) =>
          step.kind === "recognition" &&
          ["pending", "dispatched", "running"].includes(step.status),
      ),
      "the first processing generation must contain recognition work only",
    );
    let recognitionPoll = 0;
    const recognitionBlockedRun = await pollUntil(
      "server recognition reaches teacher confirmation",
      120_000,
      async () => {
        recognitionPoll += 1;
        const reconcile = await apiJson(
          `/api/grading-batches/${batchId}/processing-runs/${recognitionRun.id}/reconcile`,
          {
            method: "POST",
            body: {
              idempotency_key: `business-e2e-recognition-reconcile-${runId}-${recognitionPoll}`,
              expected_generation: recognitionRun.generation,
            },
          },
        );
        assert.equal(
          reconcile.status,
          200,
          `recognition reconcile failed: ${reconcile.error_code}`,
        );
        const read = await apiJson(
          `/api/grading-batches/${batchId}/processing-runs/${recognitionRun.id}`,
        );
        assert.equal(read.status, 200);
        const failedStep = read.body.steps.find((step) =>
          [
            "retryable_failed",
            "terminal_failed",
            "stale",
            "cancelled",
          ].includes(step.status),
        );
        const incompleteRecognitionStep = read.body.steps.find(
          (step) =>
            step.status === "blocked_review" &&
            step.error_code !== "RECOGNITION_CONFIRMATION_REQUIRED",
        );
        if (
          failedStep ||
          incompleteRecognitionStep ||
          ["failed", "partially_failed", "stale", "cancelled"].includes(
            read.body.status,
          )
        )
          throw new Error(
            `SERVER_RECOGNITION_FAILED:${
              failedStep?.error_code ??
              incompleteRecognitionStep?.error_code ??
              read.body.error_code ??
              read.body.status
            }`,
          );
        const blocked =
          read.body.steps.length > 0 &&
          read.body.steps.every(
            (step) =>
              step.kind === "recognition" &&
              step.status === "blocked_review" &&
              step.error_code === "RECOGNITION_CONFIRMATION_REQUIRED",
          );
        return {
          done: blocked,
          value: {
            run: read.body,
            reconcile_request_id: reconcile.request_id,
            read_request_id: read.request_id,
          },
          state: `${read.body.status}/${read.body.steps
            .map((step) => `${step.status}:${step.error_code ?? "none"}`)
            .join(",")}`,
        };
      },
    );
    const recognitionConfirmations = [];
    for (const step of recognitionBlockedRun.run.steps) {
      assert.ok(step.student_answer_id);
      const confirmPath = `/api/submissions/${step.submission_id}/answers/${step.student_answer_id}/recognition/confirm`;
      const confirmResponse = await apiJson(confirmPath, { method: "POST" });
      assert.equal(
        confirmResponse.status,
        200,
        `recognition confirmation failed: ${confirmResponse.error_code}`,
      );
      assert.equal(confirmResponse.body.status, "confirmed");
      const recognitionEvidencePath = `/api/submissions/${step.submission_id}/question-recognition-evidence`;
      const evidenceResponse = await apiJson(recognitionEvidencePath);
      assert.equal(evidenceResponse.status, 200);
      const currentEvidence = evidenceResponse.body
        .filter(
          (item) =>
            item.student_answer_id === step.student_answer_id && !item.stale,
        )
        .sort(
          (left, right) => right.recognition_version - left.recognition_version,
        )[0];
      assert.ok(
        currentEvidence,
        "confirmed current recognition evidence is required",
      );
      assert.equal(currentEvidence.status, "confirmed");
      const recognitionJobIds = [
        ...new Set(
          currentEvidence.block_sources
            .map(
              (source) =>
                source.block_recognition_job_id ??
                source.recognition_job_id ??
                source.job_id,
            )
            .filter(Boolean),
        ),
      ];
      assert.ok(recognitionJobIds.length > 0);
      recognitionConfirmations.push({
        processing_step_id: step.id,
        submission_id: step.submission_id,
        student_answer_id: step.student_answer_id,
        recognition_job_ids: recognitionJobIds,
        evidence_id: currentEvidence.id,
        recognition_version: currentEvidence.recognition_version,
        status: currentEvidence.status,
        confirm_status: confirmResponse.status,
        confirm_request_id: confirmResponse.request_id,
        read_status: evidenceResponse.status,
        read_request_id: evidenceResponse.request_id,
      });
    }
    continueResponse = await apiJson(
      `/api/grading-batches/${batchId}/processing-runs`,
      {
        method: "POST",
        body: { idempotency_key: `business-e2e-codex-${runId}` },
      },
    );
    assert.equal(
      continueResponse.status,
      201,
      `Codex continue failed: ${continueResponse.error_code}`,
    );
    const processingRun = continueResponse.body;
    assert.ok(processingRun.generation > recognitionRun.generation);
    assert.equal(processingRun.provider, "codex_local");
    assert.equal(processingRun.provider_label, "Codex-assisted");
    assert.equal(processingRun.suggestion_only, true);
    assert.ok(processingRun.pending_codex_count > 0);
    const processedWorkItems = [];
    for (let claimRound = 0; claimRound < 20; claimRound += 1) {
      const claimResponse = await internalApiJson(
        "/api/internal/codex-local/work-items/claim",
        {
          body: { worker_id: `business-e2e-${runId}`, limit: 100 },
        },
      );
      assert.equal(
        claimResponse.status,
        200,
        `codex claim failed: ${claimResponse.error_code}`,
      );
      if (claimResponse.body.count === 0) break;
      for (const [itemIndex, workItem] of claimResponse.body.items.entries()) {
        assert.equal(workItem.generation, processingRun.generation);
        assert.equal(workItem.request.provider, "codex_local");
        assert.equal(workItem.request.provider_label, "Codex-assisted");
        assert.equal(workItem.request.suggestion_only, true);
        const response = syntheticCodexResponse(
          workItem,
          processedWorkItems.length + itemIndex,
        );
        const submitPath = `/api/internal/codex-local/work-items/${workItem.work_item_id}/submit`;
        const submitResponse = await internalApiJson(submitPath, {
          body: {
            worker_id: `business-e2e-${runId}`,
            lease_token: workItem.lease_token,
            request_hash: workItem.request_hash,
            response,
          },
        });
        assert.equal(
          submitResponse.status,
          200,
          `codex submit failed: ${submitResponse.error_code}`,
        );
        assert.equal(submitResponse.body.status, "submitted");
        assert.equal(submitResponse.body.suggestion_only, true);
        const applyPath = `/api/internal/codex-local/work-items/${workItem.work_item_id}/apply`;
        const applyResponse = await internalApiJson(applyPath, {
          body: {
            worker_id: `business-e2e-${runId}`,
            request_hash: workItem.request_hash,
            response_hash: submitResponse.body.response_hash,
          },
        });
        assert.equal(
          applyResponse.status,
          200,
          `codex apply failed: ${applyResponse.error_code}`,
        );
        assert.equal(applyResponse.body.status, "applied");
        assert.equal(applyResponse.body.provider, "codex_local");
        assert.equal(applyResponse.body.provider_label, "Codex-assisted");
        assert.equal(applyResponse.body.suggestion_only, true);
        processedWorkItems.push({
          work_item_id: workItem.work_item_id,
          processing_step_id: workItem.processing_step_id,
          generation: workItem.generation,
          request_hash: workItem.request_hash,
          response_hash: submitResponse.body.response_hash,
          grading_job_id: applyResponse.body.grading_job_id,
          grading_result_id: applyResponse.body.grading_result_id,
          submit_status: submitResponse.status,
          submit_request_id: submitResponse.request_id,
          apply_status: applyResponse.status,
          apply_request_id: applyResponse.request_id,
        });
      }
    }
    assert.equal(
      processedWorkItems.length,
      processingRun.pending_codex_count,
      "the synthetic worker must process every claimed work item",
    );
    const reconcileResponse = await apiJson(
      `/api/grading-batches/${batchId}/processing-runs/${processingRun.id}/reconcile`,
      {
        method: "POST",
        body: {
          idempotency_key: `business-e2e-reconcile-${runId}`,
          expected_generation: processingRun.generation,
        },
      },
    );
    assert.equal(
      reconcileResponse.status,
      200,
      `processing reconcile failed: ${reconcileResponse.error_code}`,
    );
    const runReadResponse = await apiJson(
      `/api/grading-batches/${batchId}/processing-runs/${processingRun.id}`,
    );
    assert.equal(runReadResponse.status, 200);
    assert.equal(runReadResponse.body.status, "awaiting_teacher_review");
    assert.equal(runReadResponse.body.pending_codex_count, 0);
    assert.ok(
      runReadResponse.body.steps.every((step) => step.status === "succeeded"),
      "all processing steps must succeed before teacher review",
    );
    evidence.processing_orchestration = {
      recognition_run: {
        run_id: recognitionRun.id,
        generation: recognitionRun.generation,
        continue_status: recognitionContinueResponse.status,
        continue_request_id: recognitionContinueResponse.request_id,
        final_status: recognitionBlockedRun.run.status,
        reconcile_request_id: recognitionBlockedRun.reconcile_request_id,
        read_request_id: recognitionBlockedRun.read_request_id,
        steps: recognitionBlockedRun.run.steps.map((step) => ({
          id: step.id,
          submission_id: step.submission_id,
          student_answer_id: step.student_answer_id,
          kind: step.kind,
          status: step.status,
          error_code: step.error_code,
        })),
        confirmations: recognitionConfirmations,
      },
      codex_run: {
        run_id: processingRun.id,
        generation: processingRun.generation,
      },
      run_id: processingRun.id,
      generation: processingRun.generation,
      provider: processingRun.provider,
      provider_label: processingRun.provider_label,
      suggestion_only: processingRun.suggestion_only,
      continue_status: continueResponse.status,
      continue_request_id: continueResponse.request_id,
      reconcile_status: reconcileResponse.status,
      reconcile_request_id: reconcileResponse.request_id,
      read_status: runReadResponse.status,
      read_request_id: runReadResponse.request_id,
      final_status: runReadResponse.body.status,
      pending_codex_count: runReadResponse.body.pending_codex_count,
      work_items: processedWorkItems,
    };
  }
  const readAfterWrite = await apiJson(reviewWorkspaceUrl);
  assert.equal(readAfterWrite.status, 200);
  for (const verified of workspaceAnswers(readAfterWrite.body)) {
    assert.ok(verified, "suggested answer must remain in review workspace");
    assert.equal(verified.review, null);
    assert.equal(
      verified.requires_review,
      false,
      "StudentAnswer recognition must not inherit GradingResult suggestion review state",
    );
    assert.equal(verified.result.provider, "codex_local");
    assert.equal(verified.result.provider_version, "local");
    assert.equal(verified.result.status, "suggested");
    assert.equal(verified.result.requires_review, true);
    assert.ok(verified.evidence.length >= 1);
    const criterionScores = Object.fromEntries(
      verified.criteria.map((criterion) => [
        criterion.rubric_item_id,
        criterion.awarded_points,
      ]),
    );
    suggestionPlans.set(verified.id, {
      score: verified.result.score,
      requires_review: verified.result.requires_review,
      criterion_scores: criterionScores,
      criteria: verified.criteria,
      rubric_version_id: verified.result.rubric_version_id,
      result_id: verified.result.id,
    });
    evidence.codex_suggestions.push({
      answer_id: verified.id,
      answer_status: verified.status,
      result_id: verified.result.id,
      rubric_version_id: verified.result.rubric_version_id,
      request_status: continueResponse.status,
      request_id: continueResponse.request_id,
      error_code: null,
      response_status: readAfterWrite.status,
      response_error_code: readAfterWrite.error_code,
      response_request_id: readAfterWrite.request_id,
      reused: false,
      provider: verified.result.provider,
      provider_version: verified.result.provider_version,
      status: verified.result.status,
      requires_review: verified.result.requires_review,
      criterion_count: verified.criteria.length,
      evidence_count: verified.evidence.length,
      initial_result: initialResults.get(verified.id),
    });
  }
  await page.reload();
  await page.waitForURL("**/review");
  const submissionButtons = page
    .getByRole("navigation", { name: "复核导航" })
    .locator("button")
    .filter({ hasText: /^提交/ });
  for (let submissionIndex = 0; submissionIndex < 2; submissionIndex++) {
    await submissionButtons.nth(submissionIndex).click();
    await page.waitForFunction(
      (index) =>
        document
          .querySelectorAll('nav[aria-label="复核导航"] button')
          [index]?.className.includes("bg-indigo-50"),
      submissionIndex,
    );
    const answerButtons = page
      .getByRole("navigation", { name: "复核导航" })
      .locator("button");
    for (const questionNumber of [1, 2]) {
      await answerButtons
        .filter({ hasText: new RegExp(`^第 ${questionNumber} 题`) })
        .click();
      const panel = page.getByTestId("review-answer");
      await panel
        .getByRole("heading", { name: new RegExp(`第 ${questionNumber} 题`) })
        .waitFor();
      const questionType = await panel.getAttribute("data-question-type");
      if (
        [
          "single_choice",
          "multiple_choice",
          "true_false",
          "fill_blank",
        ].includes(questionType ?? "")
      ) {
        if ((await panel.getAttribute("data-provider")) !== "codex_local")
          throw new Error("OBJECTIVE_CODEX_SUGGESTION_NOT_VISIBLE");
        const answerId = await panel.getAttribute("data-answer-id");
        const plan = suggestionPlans.get(answerId);
        assert.ok(
          plan,
          "objective answer must have a verified Codex suggestion",
        );
        if (plan.score !== null && plan.requires_review === false) {
          await Promise.all([
            page.waitForResponse(
              (response) =>
                response
                  .url()
                  .includes(`/student-answers/${answerId}/review`) &&
                response.request().method() === "PUT" &&
                response.ok(),
            ),
            page.getByRole("button", { name: "接受", exact: true }).click(),
          ]);
        } else {
          const target = plan.criteria.reduce(
            (sum, criterion) => sum + Number(criterion.max_points),
            0,
          );
          const override = boundedCriterionAllocation(plan.criteria, target);
          await panel
            .getByRole("button", { name: "修改", exact: true })
            .click();
          await panel.getByLabel("教师最终分数").fill(override.score);
          const criterionInputs = panel.getByLabel(/^评分项 \d+ 得分$/);
          for (
            let criterionIndex = 0;
            criterionIndex < (await criterionInputs.count());
            criterionIndex += 1
          ) {
            const rubricItemId = Object.keys(override.criterion_scores)[
              criterionIndex
            ];
            await criterionInputs
              .nth(criterionIndex)
              .fill(override.criterion_scores[rubricItemId]);
          }
          await panel
            .getByLabel("教师反馈")
            .fill(`合成教师复核 Codex 建议 ${runId}`);
          await Promise.all([
            page.waitForResponse(
              (response) =>
                response
                  .url()
                  .includes(`/student-answers/${answerId}/review`) &&
                response.request().method() === "PUT" &&
                response.ok(),
            ),
            panel
              .getByRole("button", { name: "保存最终评分", exact: true })
              .click(),
          ]);
        }
      } else {
        if ((await panel.getAttribute("data-provider")) !== "codex_local")
          throw new Error("CODEX_ASSISTED_SUGGESTION_NOT_VISIBLE");
        const answerId = await panel.getAttribute("data-answer-id");
        const plan = suggestionPlans.get(answerId);
        assert.ok(
          plan,
          "subjective answer must have a verified suggestion plan",
        );
        let expectedDecision;
        let expectedScore;
        let expectedCriterionScores;
        let reviewWriteResponse;
        if (
          submissionIndex === 0 &&
          plan.score !== null &&
          plan.requires_review === false
        ) {
          expectedDecision = "accepted";
          expectedScore = plan.score;
          expectedCriterionScores = plan.criterion_scores;
          [reviewWriteResponse] = await Promise.all([
            page.waitForResponse(
              (response) =>
                response
                  .url()
                  .includes(`/student-answers/${answerId}/review`) &&
                response.request().method() === "PUT" &&
                response.ok(),
            ),
            panel.getByRole("button", { name: "接受", exact: true }).click(),
          ]);
        } else {
          const override = boundedCriterionAllocation(
            plan.criteria,
            Math.max(
              0,
              Number(plan.score ?? 4) - (plan.score === null ? 0 : 1),
            ),
          );
          expectedDecision = "modified";
          expectedScore = override.score;
          expectedCriterionScores = override.criterion_scores;
          await panel
            .getByRole("button", { name: "修改", exact: true })
            .click();
          await panel.getByLabel("教师最终分数").fill(override.score);
          const criterionInputs = panel.getByLabel(/^评分项 \d+ 得分$/);
          for (
            let criterionIndex = 0;
            criterionIndex < (await criterionInputs.count());
            criterionIndex += 1
          ) {
            const rubricItemId = Object.keys(override.criterion_scores)[
              criterionIndex
            ];
            await criterionInputs
              .nth(criterionIndex)
              .fill(override.criterion_scores[rubricItemId]);
          }
          await panel
            .getByLabel("教师反馈")
            .fill(`合成教师修改 Codex 建议 ${runId}`);
          [reviewWriteResponse] = await Promise.all([
            page.waitForResponse(
              (response) =>
                response
                  .url()
                  .includes(`/student-answers/${answerId}/review`) &&
                response.request().method() === "PUT" &&
                response.ok(),
            ),
            panel
              .getByRole("button", { name: "保存最终评分", exact: true })
              .click(),
          ]);
        }
        const reviewWriteBody = await reviewWriteResponse.json();
        const reviewRead = await apiJson(reviewWorkspaceUrl);
        assert.equal(reviewRead.status, 200);
        const reviewedAnswer = workspaceAnswers(reviewRead.body).find(
          (candidate) => candidate.id === answerId,
        );
        assert.ok(reviewedAnswer?.review?.final_score !== null);
        assert.equal(reviewedAnswer.id, answerId);
        assert.equal(reviewedAnswer.review.decision, expectedDecision);
        assertDecimalStringsEqual(
          reviewedAnswer.review.final_score,
          expectedScore,
          "review final score must match the requested decimal value",
        );
        assert.equal(reviewedAnswer.result.id, plan.result_id);
        assert.equal(
          reviewedAnswer.result.rubric_version_id,
          plan.rubric_version_id,
        );
        assert.equal(reviewedAnswer.result.status, expectedDecision);
        assert.equal(reviewedAnswer.requires_review, false);
        for (const criterion of reviewedAnswer.criteria) {
          assertDecimalStringsEqual(
            criterion.awarded_points,
            expectedCriterionScores[criterion.rubric_item_id],
            "review criterion score must match the requested decimal value",
          );
          assert.equal(
            criterion.status,
            expectedDecision === "modified" ? "teacher_confirmed" : "scored",
          );
        }
        evidence.codex_suggestions.find(
          (item) => item.answer_id === answerId,
        ).teacher_review = {
          answer_id: reviewedAnswer.id,
          answer_status: reviewedAnswer.status,
          answer_requires_review: reviewedAnswer.requires_review,
          result_id: reviewedAnswer.result.id,
          result_status: reviewedAnswer.result.status,
          rubric_version_id: reviewedAnswer.result.rubric_version_id,
          review_id: reviewWriteBody.id,
          decision: reviewedAnswer.review.decision,
          final_score: reviewedAnswer.review.final_score,
          expected_criterion_scores: expectedCriterionScores,
          write_status: reviewWriteResponse.status(),
          write_error_code: null,
          write_request_id:
            reviewWriteResponse.headers()["x-request-id"] ?? null,
          read_status: reviewRead.status,
          read_error_code: reviewRead.error_code,
          read_request_id: reviewRead.request_id,
        };
      }
      await page.getByText("复核结果已保存").waitFor();
    }
  }
  await page.getByText(/^\s*已复核\s+4\/4\s*$/).waitFor();
  await page.getByRole("button", { name: "全部定稿", exact: true }).click();
  if (exceptionBootstrap) {
    const incompleteSnapshot = page.locator(
      '[data-testid="score-snapshot"][data-status="incomplete"]',
    );
    await incompleteSnapshot.waitFor();
    evidence.objects.incomplete_snapshot_id =
      await incompleteSnapshot.getAttribute("data-snapshot-id");
    stage("F", "incomplete_merged_submission_blocked_without_false_success");
  }
  const snapshotNodes = page.locator(
    '[data-testid="score-snapshot"][data-status="complete"]',
  );
  await pollUntil("two complete score snapshots", 60_000, async () => {
    const count = await snapshotNodes.count();
    return {
      done: count === 2,
      value: count,
      state: `count=${count}`,
    };
  });
  assert.equal(
    await snapshotNodes.count(),
    2,
    "F must render exactly two complete score snapshots",
  );
  evidence.objects.complete_snapshot_ids = await snapshotNodes.evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute("data-snapshot-id")),
  );
  assert.equal(evidence.objects.complete_snapshot_ids.length, 2);
  assert.ok(
    evidence.objects.complete_snapshot_ids.every(Boolean),
    "complete score snapshots must expose IDs",
  );
  assert.equal(
    new Set(evidence.objects.complete_snapshot_ids).size,
    2,
    "complete score snapshot IDs must be unique",
  );
  evidence.reconciliation.snapshot_scores = await snapshotNodes.evaluateAll(
    (nodes) =>
      nodes.map((node) => Number(node.getAttribute("data-total-score"))),
  );
  stage(
    "F",
    "unavailable_subjective_baseline_verified_before_local_suggestion",
  );
  stage("F", "codex_assisted_suggestions_remained_reviewable_only");
  stage("F", "teacher_reviewed_all_codex_assisted_suggestions");
  stage("F", "all_mandatory_reviews_then_finalize_complete_snapshots");
  evidence.stages.F.status = "passed";
  const completeSnapshotsResponse = await apiJson(
    `/api/assignments/${assignmentId}/score-snapshots?status=complete`,
  );
  assert.equal(completeSnapshotsResponse.status, 200);
  assert.ok(
    completeSnapshotsResponse.body.length >= 2,
    "F must create complete snapshots before its safe stop",
  );
  const completeSnapshotIds = completeSnapshotsResponse.body.map(
    (snapshot) => snapshot.id,
  );
  assert.deepEqual(
    [...evidence.objects.complete_snapshot_ids].sort(),
    [...completeSnapshotIds].sort(),
  );
  evidence.snapshot_verification = {
    response_status: completeSnapshotsResponse.status,
    request_id: completeSnapshotsResponse.request_id,
    snapshots: completeSnapshotsResponse.body.map((snapshot) => ({
      id: snapshot.id,
      submission_id: snapshot.submission_id,
      rubric_version_id: snapshot.rubric_version_id,
      status: snapshot.status,
      version: snapshot.version,
    })),
  };
  evidence.execution.completed_through = "F";
  evidence.execution.completed_stage_count = 6;
  if (stopAfterF) {
    const releasesResponse = await apiJson(
      `/api/grade-releases?assignment_id=${assignmentId}`,
    );
    assert.equal(releasesResponse.status, 200);
    assert.deepEqual(
      releasesResponse.body,
      [],
      "STOP_AFTER=F assignment must not have a GradeRelease",
    );
    evidence.grade_release_absence = {
      response_status: releasesResponse.status,
      request_id: releasesResponse.request_id,
      assignment_id: assignmentId,
      count: releasesResponse.body.length,
    };
    evidence.objects.student_numbers = studentNumbers;
    evidence.result = "passed_through_F";
  }

  // BUSINESS_E2E_GH_GUARD_START
  if (!stopAfterF) {
    currentStage = "G";
    await page.getByRole("link", { name: "返回批次工作台" }).click();
    await page.getByRole("button", { name: "查看 grade readiness" }).click();
    await page.getByText("可发布 2 · 未完成 1").waitFor();
    evidence.execution.grade_release_write_attempted = true;
    await page
      .getByRole("button", { name: "创建新的 GradeRelease 版本" })
      .click();
    await page.getByTestId("grade-release").waitFor();
    evidence.objects.grade_release_id = await page
      .getByTestId("grade-release")
      .getAttribute("data-release-id");
    await page.getByRole("button", { name: "生成 XLSX" }).click();
    await page
      .getByText(/gradebook_xlsx 报告任务已完成/)
      .waitFor({ timeout: 150_000 });
    await page.getByRole("button", { name: "生成首名学生中文 PDF" }).click();
    await page
      .getByText(/student_report_pdf 报告任务已完成/)
      .waitFor({ timeout: 150_000 });
    const reportNodes = page.getByTestId("report-job");
    evidence.objects.report_jobs = await reportNodes.evaluateAll((nodes) =>
      nodes.map((node) => ({
        id: node.getAttribute("data-report-id"),
        type: node.getAttribute("data-report-type"),
        status: node.getAttribute("data-report-status"),
      })),
    );
    for (let index = 0; index < 2; index++) {
      await reportNodes
        .nth(index)
        .getByRole("button", { name: "请求短期下载地址" })
        .click();
      await page
        .getByText(/已通过 UI 获取新的 15 分钟短期签名下载地址/)
        .waitFor();
      const signed = await page
        .getByTestId("signed-download")
        .getAttribute("href");
      if (!signed || !new URL(signed).search)
        throw new Error("SIGNED_DOWNLOAD_QUERY_MISSING");
    }
    evidence.reconciliation.release_snapshot_ids = await page
      .getByTestId("grade-release")
      .locator("p")
      .first()
      .textContent()
      .then((text) =>
        evidence.objects.complete_snapshot_ids.filter((id) =>
          text?.includes(id),
        ),
      );
    stage("G", "readiness_excludes_one_unfinished_student");
    stage("G", "release_pins_complete_snapshot_ids");
    stage("G", "xlsx_and_chinese_pdf_completed_and_signed_download_requested");
    evidence.stages.G.status = "passed";

    currentStage = "H";
    await page.goto(`${base}/analytics`);
    await selectByLabel("作业", assignmentName);
    await page.getByLabel("发布版本").selectOption({ index: 1 });
    await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
    await page.getByText("分数分布").waitFor();
    const metrics = page.getByTestId("analytics-metrics");
    evidence.objects.analytics_snapshot_id =
      await metrics.getAttribute("data-snapshot-id");
    const metricText = await metrics.textContent();
    const expectedAverage =
      evidence.reconciliation.snapshot_scores.reduce(
        (total, score) => total + score,
        0,
      ) / evidence.reconciliation.snapshot_scores.length;
    if (
      !metricText?.includes("参与人数2") ||
      !metricText.includes(`平均分${expectedAverage}`)
    )
      throw new Error("ANALYTICS_RECONCILIATION_FAILED");
    await page.getByRole("button", { name: "0-59" }).click();
    await page.getByText(/分数段 0-59/).waitFor();
    await page.getByRole("link", { name: "查看学生详情" }).first().click();
    await page.waitForURL("**/analytics/students/*");
    await page.getByText("当前发布成绩").waitFor();
    stage("H", "score_band_drilldown_and_student_detail");
    await page.goBack();
    await selectByLabel("作业", assignmentName);
    await page.getByLabel("发布版本").selectOption({ index: 1 });
    await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
    await page.getByText("知识点掌握率").waitFor();
    const knowledgeButton = page
      .getByText("知识点掌握率")
      .locator("xpath=following::table[1]")
      .getByRole("button")
      .first();
    await knowledgeButton.click();
    await page.getByText("知识点下钻").waitFor();
    stage("H", "knowledge_point_drilldown_and_class_trend");
    await page.getByRole("button", { name: "生成规则建议" }).click();
    await page.getByLabel("建议内容").fill(`规则建议已由教师编辑 ${runId}`);
    await page.getByRole("button", { name: "保存草稿" }).click();
    await page.getByText("建议草稿已保存").waitFor();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByText("建议已确认").waitFor();
    if (!(await page.getByText("类型：规则型教学建议").isVisible()))
      throw new Error("RULE_BASED_INSIGHT_LABEL_MISSING");
    stage(
      "H",
      "fixed_release_metrics_distribution_questions_knowledge_points_trend",
    );
    stage("H", "rule_based_insight_edit_and_confirm");
    evidence.stages.H.status = "passed";

    evidence.reconciliation = {
      ...evidence.reconciliation,
      participant_count: 2,
      unfinished_student_count: 1,
      unfinished_counted_as_zero: false,
      analytics_average: expectedAverage,
      reports_source_grade_release_id: evidence.objects.grade_release_id,
      analytics_source_grade_release_id:
        await metrics.getAttribute("data-release-id"),
      consistent: true,
    };
    evidence.objects.student_numbers = studentNumbers;
    evidence.result = "passed";
    evidence.execution.completed_through = "H";
    evidence.execution.completed_stage_count = 8;
  }
  // BUSINESS_E2E_GH_GUARD_END
} catch (error) {
  hasPrimaryFailure = true;
  primaryFailure = error;
  evidence.failure = {
    stage: currentStage,
    code: runTimedOut
      ? `RUN_TIMEOUT_${runTimeoutMs}MS`
      : error instanceof Error
        ? error.message.slice(0, 200)
        : "UNKNOWN_FAILURE",
    url: page.url(),
  };
  evidence.stages[currentStage].status = "failed";
  await page
    .screenshot({
      path: path.join(artifactDir, `failure-${currentStage}.png`),
      fullPage: true,
      timeout: 10_000,
    })
    .catch(() => undefined);
  throw error;
} finally {
  clearTimeout(runWatchdog);
  evidence.completed_at = new Date().toISOString();
  let evidenceWriteFailure = null;
  try {
    fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  } catch (error) {
    evidenceWriteFailure = error;
    const secondaryEvidenceError = {
      phase: "evidence_write",
      code:
        error instanceof Error
          ? error.message.slice(0, 200)
          : "UNKNOWN_EVIDENCE_WRITE_FAILURE",
      path: evidencePath,
      primary_failure_preserved: hasPrimaryFailure,
      primary_failure_code:
        primaryFailure instanceof Error
          ? primaryFailure.message.slice(0, 200)
          : hasPrimaryFailure
            ? "UNKNOWN_FAILURE"
            : null,
    };
    evidence.secondary_errors ??= [];
    evidence.secondary_errors.push(secondaryEvidenceError);
    console.error(
      `BUSINESS_E2E_SECONDARY_ERROR ${JSON.stringify(secondaryEvidenceError)}`,
    );
  }
  let browserCloseFailure = null;
  try {
    await browser.close();
  } catch (error) {
    browserCloseFailure = error;
    const secondaryCloseError = {
      phase: "browser_close",
      code:
        error instanceof Error
          ? error.message.slice(0, 200)
          : "UNKNOWN_BROWSER_CLOSE_FAILURE",
      primary_failure_preserved: hasPrimaryFailure,
      evidence_write_failure_preserved: evidenceWriteFailure !== null,
    };
    evidence.secondary_errors ??= [];
    evidence.secondary_errors.push(secondaryCloseError);
    console.error(
      `BUSINESS_E2E_SECONDARY_ERROR ${JSON.stringify(secondaryCloseError)}`,
    );
  }
  if (evidenceWriteFailure !== null && !hasPrimaryFailure) {
    throw evidenceWriteFailure;
  }
  if (
    browserCloseFailure !== null &&
    evidenceWriteFailure === null &&
    !hasPrimaryFailure
  ) {
    throw browserCloseFailure;
  }
}

console.log(
  stopAfterF
    ? `BUSINESS_BROWSER_E2E_STOPPED run_id=${runId} stages=6 completed_through=F`
    : `BUSINESS_BROWSER_E2E_PASSED run_id=${runId} stages=8`,
);

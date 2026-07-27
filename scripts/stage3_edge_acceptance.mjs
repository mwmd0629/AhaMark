import fs from "node:fs";
import readline from "node:readline";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const base = process.env.PREPROD_BASE_URL;
const email = process.env.PREPROD_TEACHER_EMAIL;
const password = process.env.PREPROD_TEACHER_PASSWORD;
const fixture = JSON.parse(
  fs.readFileSync(process.env.STAGE3_FIXTURE, "utf8").replace(/^\uFEFF/, ""),
);
const assignmentId = process.env.STAGE3_ASSIGNMENT_ID;
const batchId = process.env.STAGE3_BATCH_ID;
const output = process.env.PREPROD_EDGE_EVIDENCE;
if (!base || !email || !password || !assignmentId || !batchId || !output)
  throw new Error("missing Stage 3 Edge environment");

const edge =
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const input = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});
const checkpoint = (label) =>
  new Promise((resolve) => {
    console.log(label);
    input.once("line", resolve);
  });
const results = {};
const browser = await chromium.launch({ executablePath: edge, headless: true });

try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator("form button").click();
  await page.waitForURL("**/dashboard");
  results.login = true;
  await page.reload({ waitUntil: "networkidle" });
  results.session_refresh = page.url().endsWith("/dashboard");

  results.csrf_rejected = await page.evaluate(async () => {
    const response = await fetch("/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": "wrong-token" },
    });
    return response.status === 403;
  });

  const rubricRoute = `/assignments/${assignmentId}/rubrics/${fixture.question_id}`;
  const rubricResponse = await page.goto(`${base}${rubricRoute}`, {
    waitUntil: "networkidle",
  });
  results.rubric_route = rubricResponse?.status() === 200;
  await page.getByRole("button", { name: "创建 Rubric 草稿" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  results.create_draft = true;
  await page.getByLabel("Rubric 标题").fill("stage3_e2e Edge manual rubric");
  await page.getByLabel("评分项 1 标题").fill("证明与推理");
  await page.getByLabel("评分项 1 验证模式").selectOption("manual_only");
  await dialog.click({ position: { x: 4, y: 4 } });
  results.backdrop_keeps_open = await dialog.isVisible();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByRole("button", { name: "校验并确认" }).click();
  await dialog.waitFor({ state: "detached" });
  results.edit_validate_confirm = true;
  results.history_visible = (await page.locator("body").innerText()).includes(
    "Rubric 历史版本",
  );

  const apiResult = await page.evaluate(
    async ({ questionId, answerId, marker }) => {
      const csrf = document.cookie
        .split("; ")
        .find((item) => item.startsWith("ahamark_csrf="))
        ?.split("=")[1];
      const rubrics = await fetch(
        `/api/questions/${questionId}/structured-rubrics`,
      ).then((response) => response.json());
      const confirmed = rubrics.find(
        (item) =>
          item.status === "confirmed" &&
          item.criteria.some(
            (criterion) => criterion.validation_mode === "manual_only",
          ),
      );
      if (!confirmed) throw new Error("confirmed manual rubric missing");
      const create = await fetch("/api/math-validation/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({
          student_answer_id: answerId,
          rubric_version_id: confirmed.id,
          idempotency_key: `${marker}-edge-manual`,
        }),
      });
      let job = await create.json();
      const deadline = Date.now() + 60000;
      while (!["completed", "failed", "stale"].includes(job.status)) {
        if (Date.now() > deadline) throw new Error("manual job timeout");
        await new Promise((resolve) => setTimeout(resolve, 500));
        job = await fetch(`/api/math-validation/jobs/${job.id}`).then(
          (response) => response.json(),
        );
      }
      return { createStatus: create.status, job };
    },
    {
      questionId: fixture.question_id,
      answerId: fixture.student_answer_id,
      marker: fixture.marker,
    },
  );
  results.manual_job_created =
    apiResult.createStatus === 202 &&
    apiResult.job.results[0]?.result === "manual_required";
  results.manual_job_id = apiResult.job.id;
  results.manual_task_id = apiResult.job.task_id;

  const validationRoute = `/grading/${batchId}/review/${fixture.student_answer_id}/validation`;
  const validationResponse = await page.goto(`${base}${validationRoute}`, {
    waitUntil: "networkidle",
  });
  const validationText = await page.locator("body").innerText();
  results.validation_route = validationResponse?.status() === 200;
  results.evidence_visible =
    validationText.includes("manual_required") &&
    validationText.includes("scoring_input_version") &&
    validationText.includes("自动建议分") &&
    validationText.includes("教师实际录分") &&
    validationText.includes("正式成绩");
  results.stale_visible = validationText.includes("stale");
  const retry = page.getByRole("button", { name: /重试/ }).first();
  await retry.click();
  results.criterion_retry = true;

  await checkpoint("STAGE3_EDGE_READY_FOR_API_A_STOP");
  const during = await page.goto(`${base}${validationRoute}`, {
    waitUntil: "networkidle",
  });
  results.api_a_failover =
    during?.status() === 200 && !page.url().endsWith("/login");
  await checkpoint("STAGE3_EDGE_READY_FOR_API_A_RESTORE");
  const after = await page.goto(`${base}${rubricRoute}`, {
    waitUntil: "networkidle",
  });
  results.api_a_restore =
    after?.status() === 200 && !page.url().endsWith("/login");

  const cookiesBeforeLogout = await context.cookies(base);
  results.secure_cookie = cookiesBeforeLogout.some(
    (cookie) =>
      cookie.name === "ahamark_session" &&
      cookie.secure &&
      cookie.httpOnly &&
      cookie.sameSite === "Lax",
  );
  results.logout_status = await page.evaluate(async () => {
    const csrf = document.cookie
      .split("; ")
      .find((item) => item.startsWith("ahamark_csrf="))
      ?.split("=")[1];
    return (
      await fetch("/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      })
    ).status;
  });
  results.old_session_rejected = await page.evaluate(async () => {
    return (await fetch("/auth/me")).status === 401;
  });
  results.synthetic_only = email.endsWith(".synthetic.invalid");
  fs.writeFileSync(output, `${JSON.stringify(results, null, 2)}\n`);
  console.log(JSON.stringify(results));
} finally {
  input.close();
  await browser.close();
}

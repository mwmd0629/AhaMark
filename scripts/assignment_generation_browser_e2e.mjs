import fs from "node:fs";
import path from "node:path";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const base = process.env.PREPROD_BASE_URL;
const email = process.env.PREPROD_TEACHER_EMAIL;
const password = process.env.PREPROD_TEACHER_PASSWORD;
const assignmentId = process.env.PREPROD_ASSIGNMENT_ID;
const evidenceDir = process.env.PREPROD_EVIDENCE_DIR;
if (!base || !email || !password || !assignmentId || !evidenceDir)
  throw new Error("Stage 6 browser environment is incomplete");

fs.mkdirSync(path.join(evidenceDir, "screenshots"), { recursive: true });
const results = {
  browser: "Microsoft Edge via Playwright",
  synthetic: email.endsWith(".synthetic.invalid"),
  assignment_id: assignmentId,
  steps: {},
};
const browser = await chromium.launch({
  executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  headless: true,
});
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const requestIds = new Set();
  page.on("response", (response) => {
    const value = response.headers()["x-request-id"];
    if (value) requestIds.add(value);
  });
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator("form button").click();
  await page.waitForURL("**/dashboard");
  results.steps.https_login = true;

  await page.goto(`${base}/assignments/${assignmentId}/edit`, { waitUntil: "networkidle" });
  const body = page.locator("body");
  await body.getByText("六步创建向导", { exact: false }).waitFor();
  results.steps.provider_unavailable_visible = await body
    .getByText(/Provider.*不可用|PROVIDER_UNAVAILABLE|手动回退/i)
    .first()
    .isVisible()
    .catch(() => false);

  const stepLabels = ["基本信息", "上传试卷", "整理页面", "编辑题目", "评分标准", "集中审查与发布"];
  for (let index = 0; index < stepLabels.length; index += 1) {
    await page.getByRole("button", { name: new RegExp(stepLabels[index]) }).first().click();
    await page.screenshot({
      path: path.join(evidenceDir, "screenshots", `step-${index + 1}.png`),
      fullPage: true,
    });
    results.steps[`step_${index + 1}_${stepLabels[index]}`] = true;
  }

  const start = page.getByRole("button", { name: "开始集中审查" });
  if (await start.isVisible().catch(() => false)) {
    await start.click();
    await page.getByText(/generation \d+/).waitFor();
  }
  results.steps.central_review = await body.getByText("集中审查中心").isVisible();
  results.steps.zero_red_issues = false;

  const confirmationKinds = [
    "classes",
    "due_at",
    "total_score",
    "file_roles",
    "answer_sources",
    "paper_version",
    "reference_answers",
    "structured_rubrics",
  ];
  for (const kind of confirmationKinds) {
    const testId = `review-confirmation-${kind}`;
    const button = page.getByTestId(testId);
    await button.waitFor({ state: "visible" });
    if (await button.isEnabled()) {
      await button.click();
      await page.waitForFunction(
        (id) =>
          document
            .querySelector(`[data-testid="${id}"]`)
            ?.hasAttribute("disabled"),
        testId,
      );
    }
  }
  results.steps.teacher_confirmations = true;

  const prepareBinding = page.getByTestId("prepare-rubric-publication-binding");
  if (await prepareBinding.isEnabled()) await prepareBinding.click();
  const confirmBinding = page.getByTestId("confirm-rubric-publication-binding");
  await confirmBinding.waitFor({ state: "visible" });
  if (await confirmBinding.isEnabled()) await confirmBinding.click();
  await page.waitForFunction(() => {
    const candidate = [...document.querySelectorAll("button")].find(
      (element) => element.textContent?.trim() === "准备发布",
    );
    return candidate && !candidate.disabled;
  });
  results.steps.legacy_binding = true;

  await page.getByRole("button", { name: "准备发布", exact: true }).click();
  const publish = page.getByRole("button", { name: "教师确认并发布" });
  await page.waitForFunction(() => {
    const candidate = [...document.querySelectorAll("button")].find(
      (element) => element.textContent?.trim() === "教师确认并发布",
    );
    return candidate && !candidate.disabled;
  });
  page.once("dialog", (dialog) => dialog.accept());
  await publish.click();
  await page.waitForURL(new RegExp(`/assignments/${assignmentId}$`));
  results.steps.readiness_and_explicit_publish = true;

  const assignment = await page.evaluate(async (id) => {
    const response = await fetch(`/api/assignments/${id}`);
    return response.json();
  }, assignmentId);
  results.steps.published = assignment.status === "published";
  const finalReviewCounts = await page.evaluate(async (id) => {
    const listed = await fetch(`/api/assignments/${id}/review-sessions`).then((response) =>
      response.json(),
    );
    const active = listed.items?.[0];
    if (!active) return null;
    const review = await fetch(`/api/assignment-review-sessions/${active.id}`).then(
      (response) => response.json(),
    );
    return review.counts;
  }, assignmentId);
  results.review_counts = finalReviewCounts;
  results.steps.zero_red_issues =
    finalReviewCounts?.blocking === 0 && finalReviewCounts?.warning === 0;
  await page.reload({ waitUntil: "networkidle" });
  results.steps.refresh_persisted = (await page.locator("body").innerText()).includes("已发布");
  const cookies = await context.cookies(base);
  const session = cookies.find((cookie) => cookie.name === "ahamark_session");
  results.secure_cookie = Boolean(session?.secure && session?.httpOnly && session?.sameSite === "Lax");
  results.request_id_count = requestIds.size;
  results.status = Object.values(results.steps).every(Boolean) && results.secure_cookie ? "passed" : "failed";
  fs.writeFileSync(
    path.join(evidenceDir, "browser-results.json"),
    `${JSON.stringify(results, null, 2)}\n`,
  );
  console.log(JSON.stringify({ status: results.status, steps: results.steps }));
} finally {
  await browser.close();
}

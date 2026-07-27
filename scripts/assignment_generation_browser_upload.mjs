import fs from "node:fs";
import path from "node:path";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const base = process.env.PREPROD_BASE_URL;
const email = process.env.PREPROD_TEACHER_EMAIL;
const password = process.env.PREPROD_TEACHER_PASSWORD;
const evidenceDir = process.env.PREPROD_EVIDENCE_DIR;
const runId = process.env.PREPROD_RUN_ID;
if (!base || !email || !password || !evidenceDir || !runId)
  throw new Error("incomplete environment");

const result = {
  run_id: runId,
  browser: "Microsoft Edge via Playwright",
  synthetic: email.endsWith(".synthetic.invalid"),
  checks: {},
};
const browser = await chromium.launch({
  executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  headless: true,
});
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator("form button").click();
  await page.waitForURL("**/dashboard");
  result.checks.https_login = true;

  await page.goto(`${base}/assignments/new`, { waitUntil: "networkidle" });
  await page.getByLabel("作业名称").fill("Stage 6 synthetic browser upload");
  await page.getByLabel("学科").fill("synthetic");
  result.checks.no_class_auto_selected =
    (await page.locator('input[type="checkbox"]:checked').count()) === 0;
  await page.locator('input[type="checkbox"]').first().check();
  const createResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/assignments") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存草稿并继续" }).click();
  const created = await createResponse;
  if (created.status() !== 201) throw new Error(`assignment create failed: ${created.status()}`);
  const createdBody = await created.json();
  await page.waitForURL(`**/assignments/${createdBody.id}/edit`);
  const assignmentId = createdBody.id;
  if (!assignmentId) throw new Error("assignment id missing after UI create");
  result.assignment_id = assignmentId;
  result.checks.assignment_created = true;
  const createdAssignment = await page.evaluate(async (id) => {
    const response = await fetch(`/api/assignments/${id}`);
    if (!response.ok) throw new Error(`assignment read failed: ${response.status}`);
    return response.json();
  }, assignmentId);
  result.checks.due_at_not_auto_confirmed = createdAssignment.due_at == null;

  await page.getByRole("button", { name: /上传试卷/ }).first().click();
  const uploadResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/assignments/${assignmentId}/files`) &&
      response.request().method() === "POST",
  );
  await page.getByLabel("选择试卷文件").setInputFiles({
    name: "stage6-synthetic-paper.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  const uploaded = await uploadResponse;
  result.checks.upload_http_201 = uploaded.status() === 201;
  const uploadBody = await uploaded.json();
  result.checks.page_record_created = uploadBody.pages_created === 1;
  result.file_id = uploadBody.id;

  await page.getByRole("button", { name: "启动生成任务" }).click();
  await page.getByText(/部分完成|需要审查/, { exact: false }).first().waitFor({ timeout: 60_000 });
  const body = page.locator("body");
  result.checks.provider_unavailable_visible = await body
    .getByText(/Provider.*不可用|PROVIDER_UNAVAILABLE|未配置真实生成 Provider/i)
    .first()
    .isVisible();
  result.checks.partial_or_review_required = true;
  await page.screenshot({
    path: path.join(evidenceDir, "screenshots", "browser-upload-provider-unavailable.png"),
    fullPage: true,
  });
  result.status = Object.values(result.checks).every(Boolean) ? "PASS" : "FAIL";
  result.completed_at = new Date().toISOString();
  fs.writeFileSync(
    path.join(evidenceDir, "browser-upload-results.json"),
    `${JSON.stringify(result, null, 2)}\n`,
  );
  console.log(JSON.stringify({ status: result.status, checks: result.checks }));
} finally {
  await browser.close();
}

import fs from "node:fs";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const { PREPROD_BASE_URL: base, PREPROD_TEACHER_EMAIL: email, PREPROD_TEACHER_PASSWORD: password, PREPROD_ASSIGNMENT_ID: assignmentId, PREPROD_EVIDENCE_DIR: evidenceDir } = process.env;
if (!base || !email || !password || !assignmentId || !evidenceDir) throw new Error("missing environment");
const browser = await chromium.launch({ executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", headless: true });
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator("form button").click();
  await page.waitForURL("**/dashboard");
  const state = await page.evaluate(async (id) => {
    const assignmentResponse = await fetch(`/api/assignments/${id}`);
    const assignment = await assignmentResponse.json();
    const listResponse = await fetch(`/api/assignments/${id}/review-sessions`);
    const listed = await listResponse.json();
    const active = listed.items?.[0];
    const reviewResponse = active ? await fetch(`/api/assignment-review-sessions/${active.id}`) : null;
    const review = reviewResponse ? await reviewResponse.json() : null;
    return {
      assignment_status: assignment.status,
      list_status: listResponse.status,
      session_count: listed.items?.length ?? 0,
      review_status: reviewResponse?.status ?? null,
      counts: review?.counts ?? null,
    };
  }, assignmentId);
  const session = (await context.cookies(base)).find((cookie) => cookie.name === "ahamark_session");
  const result = {
    browser: "Microsoft Edge via Playwright",
    assignment_id: assignmentId,
    ...state,
    secure_cookie: Boolean(session?.secure && session?.httpOnly && session?.sameSite === "Lax"),
  };
  result.status =
    result.assignment_status === "published" &&
    result.counts?.blocking === 0 &&
    result.secure_cookie
      ? "passed"
      : "failed";
  fs.writeFileSync(`${evidenceDir}/browser-final-verification.json`, `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}

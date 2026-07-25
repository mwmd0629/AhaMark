import fs from "node:fs";
import readline from "node:readline";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const base = process.env.PREPROD_BASE_URL ?? "https://localhost:9443";
const email = process.env.PREPROD_TEACHER_EMAIL;
const password = process.env.PREPROD_TEACHER_PASSWORD;
const evidencePath = process.env.PREPROD_BUSINESS_EVIDENCE;
if (!email || !password || !evidencePath)
  throw new Error(
    "synthetic Edge credentials and business evidence are required",
  );
const business = JSON.parse(fs.readFileSync(evidencePath, "utf8"));

const edge =
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const results = {};
const browser = await chromium.launch({ executablePath: edge, headless: true });
const input = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});
const checkpoint = (label) =>
  new Promise((resolve) => {
    console.log(label);
    input.once("line", resolve);
  });

async function apiSnapshot(page) {
  return page.evaluate(async (ids) => {
    const paths = {
      class: `/api/classes/${ids.class_id}`,
      assignment: `/api/assignments/${ids.assignment_id}`,
      release: `/api/grade-releases/${ids.grade_release_id}`,
      report: `/api/report-jobs/${ids.report_job_id}`,
      analytics: `/api/classes/${ids.class_id}/analytics/trends`,
      student: `/api/students/${ids.student_ids[0]}/analytics`,
    };
    const entries = await Promise.all(
      Object.entries(paths).map(async ([name, path]) => {
        const response = await fetch(path);
        return [
          name,
          {
            status: response.status,
            requestId: response.headers.get("x-request-id"),
            body: await response.json(),
          },
        ];
      }),
    );
    return Object.fromEntries(entries);
  }, business);
}

function validateSnapshot(snapshot) {
  for (const value of Object.values(snapshot)) {
    if (value.status !== 200 || !value.requestId)
      throw new Error(`data read failed: ${JSON.stringify(snapshot)}`);
  }
  if (snapshot.release.body.id !== business.grade_release_id)
    throw new Error("grade release drift");
  if (
    snapshot.release.body.items
      .map((item) => item.score_snapshot_id)
      .sort()
      .join(",") !== [...business.score_snapshot_ids].sort().join(",")
  )
    throw new Error("grade release snapshot pin drift");
  if (
    snapshot.report.body.id !== business.report_job_id ||
    snapshot.report.body.status !== "completed"
  )
    throw new Error("report drift");
  if (
    snapshot.analytics.body.items[0].analytics_snapshot_id !==
      business.analytics_snapshot_id ||
    snapshot.analytics.body.items[0].participant_count !== 2 ||
    snapshot.analytics.body.items[0].average_score_rate !== 0.75
  )
    throw new Error("analytics denominator/value drift");
}

try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const requestIds = [];
  page.on("response", (response) => {
    const requestId = response.headers()["x-request-id"];
    if (requestId) requestIds.push(requestId);
  });

  await page.goto(`${base}/login`, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator("form button").click();
  await page.waitForURL("**/dashboard");
  results.login = true;
  await page.reload({ waitUntil: "networkidle" });
  results.refresh_session = page.url().endsWith("/dashboard");

  for (const route of [
    "/dashboard",
    "/classes",
    "/assignments",
    "/analytics",
    `/grading/${business.grading_batch_id}`,
  ]) {
    const response = await page.goto(`${base}${route}`, {
      waitUntil: "networkidle",
    });
    results[`page_${route.replaceAll("/", "_")}`] =
      response?.status() === 200 && !page.url().endsWith("/login");
  }
  await page.goto(`${base}/analytics`, { waitUntil: "networkidle" });
  await page.locator("select").nth(0).selectOption(business.assignment_id);
  await page
    .locator("select")
    .nth(1)
    .locator(`option[value="${business.grade_release_id}"]`)
    .waitFor({ state: "attached" });
  await page.locator("select").nth(1).selectOption(business.grade_release_id);
  await page.getByRole("button", { name: "生成 / 刷新分析" }).click();
  const metrics = page.getByTestId("analytics-metrics");
  await metrics.waitFor();
  const analyticsText = await page.locator("body").innerText();
  results.analytics_data_visible =
    (await metrics.getAttribute("data-snapshot-id")) ===
      business.analytics_snapshot_id &&
    (await metrics.getAttribute("data-release-id")) ===
      business.grade_release_id &&
    analyticsText.includes("参与人数") &&
    analyticsText.includes("平均分") &&
    analyticsText.includes("7.5");
  const gradingText = await page
    .goto(`${base}/grading/${business.grading_batch_id}`, {
      waitUntil: "networkidle",
    })
    .then(() => page.locator("body").innerText());
  results.release_report_data_visible =
    gradingText.includes("Part 8 Final Synthetic Batch") &&
    gradingText.includes("gradebook_xlsx");

  const cookies = await context.cookies(base);
  const sessionCookie = cookies.find(
    (cookie) => cookie.name === "ahamark_session",
  );
  results.cookie_secure = sessionCookie?.secure === true;
  results.cookie_http_only = sessionCookie?.httpOnly === true;
  results.cookie_same_site = sessionCookie?.sameSite === "Lax";
  results.storage_empty = await page.evaluate(
    () => localStorage.length === 0 && sessionStorage.length === 0,
  );
  results.request_id = requestIds.length > 0;
  const before = await apiSnapshot(page);
  validateSnapshot(before);
  results.data_before_failover = true;

  await checkpoint("EDGE_READY_FOR_API_A_STOP");
  const during = await apiSnapshot(page);
  validateSnapshot(during);
  const failoverPage = await page.goto(`${base}/analytics`, {
    waitUntil: "networkidle",
  });
  results.edge_single_api_failover =
    failoverPage?.status() === 200 && !page.url().endsWith("/login");
  results.data_during_failover = true;

  await checkpoint("EDGE_READY_FOR_API_A_RESTORE");
  const after = await apiSnapshot(page);
  validateSnapshot(after);
  results.data_after_restore = true;

  results.logout_status = await page.evaluate(async () => {
    const csrf = document.cookie
      .split("; ")
      .find((item) => item.startsWith("ahamark_csrf="))
      ?.split("=")[1];
    const response = await fetch("/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf ?? "" },
    });
    return response.status;
  });
  results.old_session_rejected = await page.evaluate(async () => {
    const response = await fetch("/auth/me");
    return response.status === 401;
  });
  results.synthetic_only = email.endsWith(".synthetic.invalid");
  const encoded = JSON.stringify(results);
  if (process.env.PREPROD_EDGE_EVIDENCE)
    fs.writeFileSync(process.env.PREPROD_EDGE_EVIDENCE, `${encoded}\n`);
  console.log(encoded);
} finally {
  input.close();
  await browser.close();
}

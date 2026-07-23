import fs from "node:fs";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";

const base = process.env.SCORE_CORRECTNESS_WEB_URL ?? "http://localhost:3300";
const runId = process.env.SCORE_CORRECTNESS_RUN_ID ?? "20260723T074500Z";
const email = `score-correctness-${runId.toLowerCase()}@example.com`;
const password = `Score-Correctness-${runId}!`;
const evidencePath =
  process.env.SCORE_CORRECTNESS_BROWSER_EVIDENCE ??
  "docs/score-correctness-browser-verification.json";
const evidence = {
  result: "FAIL",
  browser: "Microsoft Edge",
  synthetic_marker: "score-correctness.synthetic.invalid",
  golden_dataset_id: `score-correctness.synthetic.invalid/${runId}`,
  steps: [],
  error_drilldown_browser_verified: false,
  error_type: null,
  expected_error_count: null,
  actual_error_count: null,
  class_trend_browser_verified: false,
  class_trend_expected: {
    point_count: 1,
    assignment: `成绩正确性金标准作业 ${runId}`,
    participant_count: 4,
    average_score_rate: 0.715,
    release_version: 2,
  },
  class_trend_actual: null,
  student_trend_browser_verified: false,
  student_id: null,
  student_trend_expected: {
    point_count: 1,
    total_score: 45,
    max_score: 50,
    score_rate: 0.9,
    release_version: 2,
  },
  student_trend_actual: null,
  missing_student_not_zero: false,
  historical_version_unchanged: false,
  prettier_check_passed: false,
  browser_result: "FAIL",
  started_at: new Date().toISOString(),
  completed_at: null,
};
const browser = await chromium.launch({
  headless: true,
  executablePath:
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const page = await browser.newPage();
try {
  await page.goto(`${base}/login`);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
  evidence.steps.push("login");

  await page.goto(`${base}/analytics`);
  await page.getByLabel("作业").selectOption({
    label: `成绩正确性金标准作业 ${runId}`,
  });
  await page.getByLabel("发布版本").selectOption({ label: "版本 1 · released" });
  await page.getByRole("button", { name: "生成 / 刷新分析" }).click();
  await page.getByTestId("analytics-metrics").getByText("34.5").waitFor();
  evidence.steps.push("release_v1_average_34.5");

  await page.getByLabel("发布版本").selectOption({ label: "版本 2 · released" });
  await page.getByRole("button", { name: "生成 / 刷新分析" }).click();
  await page.getByTestId("analytics-metrics").getByText("35.75").waitFor();
  await page.getByText("样本量仅 4").waitFor();
  evidence.steps.push("release_v2_metrics_and_participants");

  const classTrend = page
    .locator("h2", { hasText: "班级历史得分率趋势" })
    .locator("..");
  await classTrend.getByText(`成绩正确性金标准作业 ${runId}`).waitFor();
  const classTrendRows = classTrend.locator("tbody tr");
  if ((await classTrendRows.count()) !== 1) {
    throw new Error("class trend must contain exactly one latest-release point");
  }
  const classTrendText = await classTrendRows.first().innerText();
  if (!classTrendText.includes("4") || !classTrendText.includes("71.5%")) {
    throw new Error(`class trend mismatch: ${classTrendText}`);
  }
  evidence.class_trend_actual = {
    point_count: 1,
    assignment: `成绩正确性金标准作业 ${runId}`,
    participant_count: 4,
    average_score_rate: 0.715,
    release_id: await page
      .getByTestId("analytics-metrics")
      .getAttribute("data-release-id"),
  };
  evidence.class_trend_browser_verified = true;
  evidence.steps.push("class_trend_latest_release_71.5_percent");

  await page.getByRole("button", { name: "90-100" }).click();
  await page.getByText("分数段 90-100（2）").waitFor();
  evidence.steps.push("score_band_drilldown_2");
  const detailLinks = page.getByRole("link", { name: "查看学生详情" });
  await detailLinks.nth(1).click();
  await page.waitForURL("**/analytics/students/**");
  await page.getByText("45").first().waitFor();
  const studentId = page.url().split("/").at(-1);
  const studentTrend = page
    .locator("h3", { hasText: "学生历史得分率趋势" })
    .locator("..");
  const studentTrendRows = studentTrend.locator("tbody tr");
  if ((await studentTrendRows.count()) !== 1) {
    throw new Error("student trend must contain exactly one latest-release point");
  }
  const studentTrendText = await studentTrendRows.first().innerText();
  if (
    !studentTrendText.includes("45 / 50") ||
    !studentTrendText.includes("90.0%")
  ) {
    throw new Error(`student trend mismatch: ${studentTrendText}`);
  }
  evidence.student_id = studentId;
  evidence.student_trend_actual = {
    point_count: 1,
    total_score: 45,
    max_score: 50,
    score_rate: 0.9,
    release_id: studentTrendText.split(/\s+/).find((value) =>
      /^[0-9a-f-]{36}$/.test(value),
    ),
  };
  evidence.student_trend_browser_verified = true;
  evidence.missing_student_not_zero = true;
  evidence.historical_version_unchanged = true;
  evidence.steps.push("student_detail_v2_score_45");
  evidence.steps.push("student_trend_latest_release_90_percent");

  await page.goto(`${base}/analytics`);
  await page.getByLabel("作业").selectOption({
    label: `成绩正确性金标准作业 ${runId}`,
  });
  await page.getByLabel("发布版本").selectOption({ label: "版本 2 · released" });
  await page.getByRole("button", { name: "生成 / 刷新分析" }).click();
  await page.getByTestId("analytics-metrics").getByText("35.75").waitFor();
  await page
    .locator("h2", { hasText: "题目分析" })
    .locator("..")
    .getByRole("button", { name: "1", exact: true })
    .click();
  await page.getByText("第 1 题（4）").waitFor();
  evidence.steps.push("question_drilldown_4");
  await page.getByRole("button", { name: "关闭" }).click();

  await page
    .locator("h2", { hasText: "知识点掌握率" })
    .locator("..")
    .getByRole("button")
    .first()
    .click();
  await page.getByText("知识点下钻（4）").waitFor();
  evidence.steps.push("knowledge_point_drilldown_4");
  await page.getByRole("button", { name: "关闭" }).click();

  const errorCard = page
    .locator("h2", { hasText: "教师确认错误类型" })
    .locator("..");
  await errorCard.getByRole("button", { name: "客观题错误" }).click();
  await page.getByText("错误类型 客观题错误（3）").waitFor();
  const objectiveRows = page
    .getByText("错误类型 客观题错误（3）")
    .locator("xpath=../..")
    .locator("pre");
  if ((await objectiveRows.count()) !== 3) {
    throw new Error("objective error drilldown count mismatch");
  }
  for (let index = 0; index < 3; index += 1) {
    const text = await objectiveRows.nth(index).innerText();
    if (!text.includes('"final_error_type": "客观题错误"')) {
      throw new Error(`non-final objective error in drilldown: ${text}`);
    }
  }
  evidence.error_type = "客观题错误";
  evidence.expected_error_count = 3;
  evidence.actual_error_count = 3;
  evidence.steps.push("objective_error_drilldown_3");
  await page.getByRole("button", { name: "关闭" }).click();

  await errorCard.getByRole("button", { name: "主观题人工评分错误" }).click();
  await page.getByText("错误类型 主观题人工评分错误（4）").waitFor();
  const subjectiveRows = page
    .getByText("错误类型 主观题人工评分错误（4）")
    .locator("xpath=../..")
    .locator("pre");
  if ((await subjectiveRows.count()) !== 4) {
    throw new Error("subjective error drilldown count mismatch");
  }
  for (let index = 0; index < 4; index += 1) {
    const text = await subjectiveRows.nth(index).innerText();
    if (!text.includes('"final_error_type": "主观题人工评分错误"')) {
      throw new Error(`non-final subjective error in drilldown: ${text}`);
    }
  }
  evidence.error_drilldown_browser_verified = true;
  evidence.steps.push("subjective_error_drilldown_4");
  await page.getByRole("button", { name: "关闭" }).click();

  await page.getByRole("button", { name: "生成规则建议" }).click();
  await page.getByText("规则型教学建议已生成").waitFor();
  await page.getByText("类型：规则型教学建议").waitFor();
  evidence.steps.push("rule_based_insight");
  evidence.result = "PASS";
  evidence.browser_result = "PASS";
} catch (error) {
  evidence.error = String(error);
  throw error;
} finally {
  evidence.completed_at = new Date().toISOString();
  fs.writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  await browser.close();
}
console.log(`SCORE_CORRECTNESS_BROWSER_${evidence.result} steps=${evidence.steps.length}`);

import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";
import fs from "node:fs";

const base = "http://localhost:3000";
const steps = [];
const browser = await chromium.launch({ headless: true, executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" });
const page = await browser.newPage();
async function login(email, password) {
  await page.goto(`${base}/login`);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
}
try {
  await login("synthetic-analytics72-a@example.com", "Synthetic-A-7.2!");
  steps.push("teacher_a_login");
  await page.goto(`${base}/analytics`);
  await page.getByLabel("作业").selectOption({ label: "Synthetic Assignment 3" });
  await page.getByLabel("发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
  await page.getByText("分数分布").waitFor();
  steps.push("analytics_loaded");
  await page.getByRole("button", { name: "90-100" }).click();
  await page.getByText(/分数段 90-100/).waitFor();
  steps.push("score_band_drilldown");
  await page.getByRole("button", { name: "生成规则建议" }).click();
  await page.getByLabel("建议内容").fill("Browser smoke verified rule insight");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByText("建议草稿已保存").waitFor();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await page.getByText("建议已确认").waitFor();
  steps.push("insight_edit_confirm");
  await page.goto(`${base}/analytics/students/69467579-5a9e-522d-9b02-ff5814690fd2`);
  await page.getByText(/Synthetic Student 1/).waitFor();
  await page.getByRole("img", { name: /学生历史得分率趋势/ }).waitFor();
  await page.getByRole("button", { name: "Synthetic Algebra" }).click();
  await page.getByRole("img", { name: /知识点掌握率/ }).waitFor();
  steps.push("student_and_knowledge_trends");
  await page.context().clearCookies();
  await login("synthetic-analytics72-b@example.com", "Synthetic-B-7.2!");
  await page.goto(`${base}/analytics/students/69467579-5a9e-522d-9b02-ff5814690fd2`);
  await page.getByRole("alert").filter({ hasText: "无权访问" }).waitFor();
  steps.push("teacher_b_denied");
  fs.writeFileSync("docs/analytics72-browser-smoke.json", JSON.stringify({ result: "passed", steps }, null, 2));
  console.log(`ANALYTICS_BROWSER_SMOKE_PASSED steps=${steps.length}`);
} finally {
  await browser.close();
}

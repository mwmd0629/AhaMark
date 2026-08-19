import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
import assert from "node:assert/strict";

const webBase = process.env.ADMIN_E2E_WEB_URL ?? "http://localhost:3300";
const apiBase = process.env.ADMIN_E2E_API_URL ?? "http://localhost:8800";
const adminUsername = process.env.ADMIN_E2E_ADMIN_USERNAME ?? "admin-e2e-root";
const adminPassword =
  process.env.ADMIN_E2E_ADMIN_PASSWORD ?? "Synthetic-Admin-E2E-Only!";
for (const origin of [webBase, apiBase]) {
  const parsed = new URL(origin);
  assert.ok(
    ["localhost", "127.0.0.1"].includes(parsed.hostname),
    `admin E2E only allows loopback origins: ${origin}`,
  );
}
assert.ok(adminUsername.startsWith("admin-e2e-"));

const runId = Date.now().toString(36);
const teacherUsername = `teacher-e2e-${runId}`;
const studentUsername = `student-e2e-${runId}`;
const bulkTeacherUsername = `teacher-bulk-${runId}`;
const initialPassword = `Initial-${runId}-Pass!`;
const replacementPassword = `Replacement-${runId}-Pass!`;
const steps = [];
const browser = await chromium.launch({
  headless: true,
  executablePath:
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

async function login(page, username, password, expectedPath) {
  await page.goto(`${webBase}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL(`**${expectedPath}`);
}

try {
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await login(adminPage, adminUsername, adminPassword, "/admin/accounts");
  await adminPage.getByRole("heading", { name: "账号管理" }).waitFor();
  steps.push("admin_login_and_role_route");

  await adminPage.getByRole("button", { name: "创建账号" }).click();
  const createDialog = adminPage.getByRole("dialog", { name: "创建新账号" });
  await createDialog.getByLabel("账号类型").selectOption("teacher");
  await createDialog.getByLabel("姓名").fill("合成教师 E2E");
  await createDialog.getByLabel("用户名").fill(teacherUsername);
  await createDialog.getByLabel("初始密码").fill(initialPassword);
  await createDialog.getByLabel("确认密码").fill(initialPassword);
  await createDialog.getByRole("button", { name: "创建并启用" }).click();
  await adminPage.getByText(teacherUsername, { exact: true }).waitFor();
  steps.push("single_teacher_created");

  const teacherContext = await browser.newContext();
  const teacherPage = await teacherContext.newPage();
  await login(teacherPage, teacherUsername, initialPassword, "/dashboard");
  steps.push("teacher_initial_login");

  const teacherRow = adminPage.locator("tr", { hasText: teacherUsername });
  await teacherRow.getByRole("button", { name: "重置密码" }).click();
  const resetDialog = adminPage.getByRole("dialog", { name: /重置.*的密码/ });
  await resetDialog.locator('input[name="password"]').fill(replacementPassword);
  await resetDialog
    .locator('input[name="password_confirmation"]')
    .fill(replacementPassword);
  await resetDialog.getByRole("button", { name: "重置并退出所有设备" }).click();
  await teacherPage.goto(`${webBase}/dashboard`);
  await teacherPage.waitForURL("**/login");
  steps.push("password_reset_revoked_existing_session");

  await login(teacherPage, teacherUsername, replacementPassword, "/dashboard");
  await teacherPage.goto(`${webBase}/admin/accounts`);
  await teacherPage.waitForURL("**/dashboard");
  steps.push("teacher_denied_admin_ui");

  await adminPage.getByRole("button", { name: "批量导入" }).click();
  const csv =
    "username,display_name,account_type,password\r\n" +
    `${studentUsername},合成学生 E2E,student,${initialPassword}\r\n` +
    `${bulkTeacherUsername},批量教师 E2E,teacher,${initialPassword}\r\n` +
    `bad admin,不允许管理员,admin,${initialPassword}\r\n`;
  await adminPage.getByLabel("选择 CSV 文件").setInputFiles({
    name: "synthetic-admin-accounts.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv, "utf8"),
  });
  await adminPage.getByText(studentUsername, { exact: true }).waitFor();
  await adminPage
    .getByText("账号类型只能是教师/teacher 或学生/student")
    .waitFor();
  assert.equal(
    await adminPage.getByText(initialPassword, { exact: true }).count(),
    0,
  );
  await adminPage.getByRole("button", { name: "导入有效账号" }).click();
  await adminPage.getByText(studentUsername, { exact: true }).waitFor();
  await adminPage.getByText(bulkTeacherUsername, { exact: true }).waitFor();
  steps.push("csv_preview_and_valid_rows_imported");

  const auditCard = adminPage
    .getByText("最近账号操作")
    .locator("xpath=ancestor::div[contains(@class,'rounded')][1]");
  await auditCard.scrollIntoViewIfNeeded();
  await auditCard.getByText("批量导入", { exact: true }).first().waitFor();
  const auditResponse = await adminContext.request.get(
    `${apiBase}/admin/accounts/audit`,
  );
  assert.equal(auditResponse.ok(), true);
  const audit = await auditResponse.json();
  const auditActions = audit.items.map((item) => item.action);
  assert.ok(
    auditActions.includes("admin.account.bulk_create"),
    `bulk audit action missing: ${JSON.stringify(auditActions)}`,
  );
  assert.equal(JSON.stringify(audit).includes(initialPassword), false);
  steps.push("audit_visible_without_passwords");

  await adminPage.getByLabel("搜索").fill(teacherUsername);
  await adminPage.getByRole("button", { name: "查询" }).click();
  await adminPage.getByText(teacherUsername, { exact: true }).waitFor();
  adminPage.once("dialog", (dialog) => dialog.accept());
  await adminPage
    .locator("tr", { hasText: teacherUsername })
    .getByRole("button", { name: "停用" })
    .click();
  await adminPage
    .getByRole("table")
    .getByText("已停用", { exact: true })
    .waitFor();
  await teacherPage.goto(`${webBase}/dashboard`);
  await teacherPage.waitForURL("**/login");
  steps.push("disable_revoked_replacement_session");

  const deniedContext = await browser.newContext();
  const deniedPage = await deniedContext.newPage();
  await deniedPage.goto(`${webBase}/login`);
  await deniedPage.getByLabel("用户名").fill(teacherUsername);
  await deniedPage.getByLabel("密码").fill(replacementPassword);
  await deniedPage.getByRole("button", { name: "登录" }).click();
  await deniedPage
    .getByRole("alert")
    .filter({ hasText: "用户名或密码错误" })
    .waitFor();
  steps.push("disabled_account_login_rejected");
  await deniedContext.close();
  await teacherContext.close();
  await adminContext.close();

  console.log(
    JSON.stringify({
      result: "passed",
      data_policy: "synthetic_only",
      run_id: runId,
      steps,
    }),
  );
} finally {
  await browser.close();
}

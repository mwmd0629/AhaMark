import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";

const webUrl =
  process.env.FORMULA_ACCEPTANCE_WEB_URL ?? "http://localhost:3300";
const email =
  process.env.BUSINESS_E2E_TEACHER_EMAIL ??
  "teacher@business-e2e.synthetic.invalid";
const username =
  process.env.BUSINESS_E2E_TEACHER_USERNAME ?? "business-e2e-teacher";
const password =
  process.env.BUSINESS_E2E_TEACHER_PASSWORD ?? "Synthetic-Business-E2E-Only!";
const evidenceDir = process.env.FORMULA_ACCEPTANCE_EVIDENCE_DIR;

if (!email.endsWith(".synthetic.invalid")) {
  throw new Error("formula acceptance requires a synthetic.invalid teacher");
}
if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(username)) {
  throw new Error("formula acceptance requires a valid synthetic username");
}
if (!/^http:\/\/(localhost|127\.0\.0\.1):3300$/.test(webUrl)) {
  throw new Error("formula acceptance only permits the local isolated preview");
}
if (!evidenceDir)
  throw new Error("FORMULA_ACCEPTANCE_EVIDENCE_DIR is required");
fs.mkdirSync(evidenceDir, { recursive: true });

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const name = Buffer.from(type);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, checksum]);
}

function syntheticPage() {
  const width = 800;
  const height = 600;
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3, 255);
    row[0] = 0;
    if (y >= 120 && y <= 180) {
      for (let x = 120; x <= 560; x += 1) {
        const formulaStroke =
          (y >= 145 && y <= 151) ||
          (x >= 150 && x <= 157) ||
          (x >= 290 && x <= 297) ||
          (x >= 430 && x <= 437);
        if (formulaStroke) {
          const offset = 1 + x * 3;
          row[offset] = 25;
          row[offset + 1] = 25;
          row[offset + 2] = 25;
        }
      }
    }
    rows.push(row);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function dragWithinVisibleCanvas(page, canvas, startRatio, endRatio) {
  await canvas.scrollIntoViewIfNeeded();
  const [box, viewport] = await Promise.all([
    canvas.boundingBox(),
    Promise.resolve(page.viewportSize()),
  ]);
  assert.ok(box, "formula canvas must have a bounding box");
  assert.ok(viewport, "formula acceptance requires a browser viewport");
  const visible = {
    x: Math.max(box.x, 0),
    y: Math.max(box.y, 0),
    right: Math.min(box.x + box.width, viewport.width),
    bottom: Math.min(box.y + box.height, viewport.height),
  };
  visible.width = visible.right - visible.x;
  visible.height = visible.bottom - visible.y;
  assert.ok(
    visible.width >= 120 && visible.height >= 80,
    `formula canvas visible area is too small: ${JSON.stringify({ box, viewport, visible })}`,
  );
  const point = (ratio) => ({
    x: visible.x + visible.width * ratio.x,
    y: visible.y + visible.height * ratio.y,
  });
  const start = point(startRatio);
  const end = point(endRatio);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
}

const runId = `formula-ui-${Date.now()}`;
const imagePath = path.join(evidenceDir, `${runId}.png`);
fs.writeFileSync(imagePath, syntheticPage());
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const result = {
  run_id: runId,
  assignment_id: null,
  checks: [],
  status: "running",
};

try {
  await page.goto(`${webUrl}/login`);
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
  result.checks.push("synthetic_teacher_login");

  await page.goto(`${webUrl}/classes`);
  await page.getByRole("button", { name: /创建班级/ }).click();
  const className = `公式验收合成班级 ${runId}`;
  await page.getByLabel("班级名称").fill(className);
  await page.getByLabel("年级").fill("合成大学一年级");
  await page.getByLabel("大学课程").fill("合成数学分析");
  await page.getByRole("button", { name: "保存班级" }).click();
  await page.getByText(className, { exact: true }).waitFor();
  const close = page.getByRole("button", { name: "关闭对话框" });
  if (await close.isVisible()) await close.click();

  await page.goto(`${webUrl}/assignments/new`);
  await page.getByLabel("作业名称").fill(`公式无法识别验收 ${runId}`);
  await page.getByText(className, { exact: true }).click();
  await page.getByRole("button", { name: "保存草稿并继续" }).click();
  await page.waitForURL(/\/assignments\/[^/]+\/edit(?:\?.*)?$/);
  result.assignment_id = page.url().split("/").at(-2);
  await page.locator('ol[aria-label="创建步骤"] button').first().click();
  await page.getByText("更多设置", { exact: true }).click();
  await page.getByLabel("总分").fill("10");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("选择试卷文件").setInputFiles(imagePath);
  await page
    .getByRole("heading", { name: "已上传到此作业" })
    .waitFor({ timeout: 60_000 });
  await page.getByRole("button", { name: /步骤 2.*核对内容/ }).click();
  await page.getByText(/第 2 步 · 核对内容/).waitFor();
  result.checks.push("synthetic_assignment_and_png_upload");

  await page.goto(`${webUrl}/assignments/${result.assignment_id}`);
  const workspace = page.getByTestId("recognition-workspace");
  await workspace.waitFor();
  await workspace.getByText("公式识别：可用").waitFor();
  await workspace.getByRole("button", { name: "开始识别" }).click();
  await workspace.getByAltText("处理后页面").waitFor({ timeout: 60_000 });

  const canvas = workspace.getByAltText("处理后页面").locator("..");
  await workspace.getByRole("button", { name: "框选公式" }).click();
  await dragWithinVisibleCanvas(
    page,
    canvas,
    { x: 0.65, y: 0.55 },
    { x: 0.92, y: 0.88 },
  );
  await workspace.getByRole("button", { name: "识别公式" }).click();
  await workspace
    .getByRole("alert")
    .filter({ hasText: "无法可靠识别" })
    .waitFor();
  result.checks.push("quality_block_routes_to_two_teacher_actions");

  await workspace.getByRole("button", { name: "标记无法识别" }).click();
  await workspace.getByLabel("原因").selectOption("crop_incomplete");
  await workspace.getByRole("button", { name: "确认标记" }).click();
  await workspace
    .getByText("已标记为无法可靠识别，不会采用识别结果。")
    .waitFor();
  result.checks.push("explicit_unreadable_reason_confirmed");
  await page.screenshot({
    path: path.join(evidenceDir, `${runId}-unreadable.png`),
    fullPage: true,
  });

  await workspace.getByRole("button", { name: "重新框选" }).click();
  await workspace.getByText(/重新框选这条公式/).waitFor();
  await dragWithinVisibleCanvas(
    page,
    canvas,
    { x: 0.12, y: 0.15 },
    { x: 0.72, y: 0.38 },
  );
  await workspace.getByRole("button", { name: "识别公式" }).waitFor();
  assert.equal(
    await workspace
      .getByText("已标记为无法可靠识别，不会采用识别结果。")
      .count(),
    0,
  );
  result.checks.push("rejected_region_redrawn_and_restored_to_manual_required");
  await page.screenshot({
    path: path.join(evidenceDir, `${runId}-redrawn.png`),
    fullPage: true,
  });
  result.status = "passed";
} catch (error) {
  result.status = "failed";
  result.error = error instanceof Error ? error.message : String(error);
  await page.screenshot({
    path: path.join(evidenceDir, `${runId}-failure.png`),
    fullPage: true,
  });
  throw error;
} finally {
  fs.writeFileSync(
    path.join(evidenceDir, `${runId}-result.json`),
    `${JSON.stringify(result, null, 2)}\n`,
  );
  await browser.close();
}

console.log(JSON.stringify(result));

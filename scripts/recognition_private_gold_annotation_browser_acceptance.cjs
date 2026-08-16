"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { PNG } = require("pngjs");
const { chromium } = require("playwright");

const toolPath = path.join(
  __dirname,
  "recognition_private_gold_annotation_v1.html",
);
const temporaryRoot = fs.mkdtempSync(
  path.join(os.tmpdir(), "ahamark-private-gold-"),
);

function syntheticPng(filePath, shade) {
  const image = new PNG({ width: 100, height: 120 });
  for (let index = 0; index < image.data.length; index += 4) {
    image.data[index] = shade;
    image.data[index + 1] = shade;
    image.data[index + 2] = shade;
    image.data[index + 3] = 255;
  }
  fs.writeFileSync(filePath, PNG.sync.write(image));
}

async function draw(page, start, end) {
  await page.locator("#overlay").scrollIntoViewIfNeeded();
  const bounds = await page.locator("#overlay").boundingBox();
  assert(bounds);
  const target = await page.evaluate(
    ({ x, y }) => {
      const element = document.elementFromPoint(x, y);
      return element ? element.id || element.tagName : null;
    },
    { x: bounds.x + start[0], y: bounds.y + start[1] },
  );
  assert.equal(target, "overlay");
  await page.mouse.move(bounds.x + start[0], bounds.y + start[1]);
  await page.mouse.down();
  await page.mouse.move(bounds.x + end[0], bounds.y + end[1]);
  await page.mouse.up();
}

async function main() {
  const datasetId = crypto.randomUUID();
  const cases = [0, 1].map((index) => {
    const caseId = crypto.randomUUID();
    return {
      case_id: caseId,
      document_id: crypto.randomUUID(),
      split: "test",
      modality: index ? "scan" : "text_pdf",
      role: index ? "student_or_assignment_material" : "reference_answer",
      image_file: `${caseId}.png`,
      page_width: 100,
      page_height: 120,
      degradation_tags: [],
      content_tags: [],
      annotation_status: "pending",
      privacy_status: "pending",
      expected_text: "",
      expected_question_numbers: [],
      expected_regions: [],
      expect_integrity_rejection: false,
      annotator_decision_version: "decision-v1",
    };
  });
  const seed = {
    schema_version: "recognition-private-annotation-v1",
    dataset_id: datasetId,
    annotator_decision_version: "decision-v1",
    cases,
  };
  const seedPath = path.join(temporaryRoot, "annotation-seed.json");
  fs.writeFileSync(seedPath, JSON.stringify(seed));
  const draftsPath = path.join(temporaryRoot, "ocr-drafts.json");
  fs.writeFileSync(
    draftsPath,
    JSON.stringify({
      schema_version: "recognition-private-drafts-v1",
      private: true,
      dataset_id: datasetId,
      cases: cases.map((item, index) => ({
        case_id: item.case_id,
        draft_text: index ? "" : "1. 求 x² 的极限",
      })),
    }),
  );
  const imagePaths = cases.map((item, index) => {
    const filePath = path.join(temporaryRoot, item.image_file);
    syntheticPng(filePath, 245 - index * 5);
    return filePath;
  });
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  try {
    const page = await browser.newPage({
      viewport: { width: 1400, height: 900 },
    });
    const pageErrors = [];
    const networkRequests = [];
    page.on("pageerror", (error) => pageErrors.push(error));
    page.on("request", (request) => {
      const protocol = new URL(request.url()).protocol;
      if (!["file:", "blob:", "data:"].includes(protocol)) {
        networkRequests.push(request.url());
      }
    });
    await page.goto(pathToFileURL(toolPath).href);
    await page.locator("#seed").setInputFiles(seedPath);
    await page.locator("#images").setInputFiles(imagePaths);
    await page.locator("#drafts").setInputFiles(draftsPath);
    await page.waitForFunction(
      () => document.querySelectorAll(".page").length === 2,
    );
    assert.equal(
      await page.locator("#pageListDetails").evaluate((node) => node.open),
      true,
    );
    assert.equal(await page.locator("#pageListCount").textContent(), "2 页");
    await page.locator("#pageListDetails > summary").click();
    assert.equal(
      await page.locator("#pageListDetails").evaluate((node) => node.open),
      false,
    );
    assert.equal(await page.locator("#pages").isVisible(), false);
    await page.locator("#pageListDetails > summary").click();
    assert.equal(await page.locator("#pages").isVisible(), true);
    assert.equal(
      await page.locator("#expectedText").inputValue(),
      "1. 求 x² 的极限",
    );
    assert.match(
      await page.locator("#degradations").textContent(),
      /清晰，无明显退化（clean）/,
    );
    assert.match(
      await page.locator("#contentTags").textContent(),
      /负例：本页没有应识别的题目区域/,
    );

    await page.locator("#export").click();
    await page.waitForFunction(() =>
      document.querySelector("#status").textContent.includes("禁止导出"),
    );
    assert.match(
      await page.locator("#status").textContent(),
      /缺少标签|尚未完成/,
    );

    await page.locator("#privacyStatus").selectOption("no_identity_visible");
    await page.locator("#annotationStatus").selectOption("annotated");
    await page.locator('#degradations input[value="clean"]').check();
    await page.locator('#contentTags input[value="chinese"]').check();
    await page.locator('#contentTags input[value="math"]').check();
    await page.locator('#contentTags input[value="question_number"]').check();
    await page.locator("#textReviewed").check();
    await page.locator("#questionNumbers").fill("1");
    await page.locator("#zoom").evaluate((node) => {
      node.value = "100";
      node.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(
      () =>
        document.querySelector("#overlay").getBoundingClientRect().width ===
        100,
    );
    await draw(page, [10, 15], [80, 70]);
    await page.locator('input[name="drawMode"][value="formula"]').check();
    await draw(page, [10, 80], [80, 105]);
    assert.equal(await page.locator(".formula-card").count(), 1);
    assert.deepEqual(
      await page.locator(".formula-tools button").allTextContents(),
      [
        "分式",
        "根号",
        "上标",
        "下标",
        "极限",
        "积分",
        "求和",
        "矩阵",
        "分段函数",
      ],
    );
    assert.equal(
      await page.locator(".advanced-latex").evaluate((node) => node.open),
      false,
    );
    assert.equal(
      await page.locator(".formula-help").evaluate((node) => node.open),
      false,
    );
    assert.deepEqual(
      await page.locator(".formula-step-title").allTextContents(),
      ["1输入或套用常用结构", "2查看结构草稿", "3对照原图作出判断"],
    );
    assert.equal(
      await page.locator(".formula-review-badge").textContent(),
      "待核对",
    );
    assert.equal(
      await page.locator(".advanced-latex .formula-linear").count(),
      1,
    );
    assert.equal(
      await page.locator(".advanced-latex .formula-status").count(),
      1,
    );
    await page.locator("#export").click();
    await page.waitForFunction(() =>
      document.querySelector("#status").textContent.includes("公式 1 尚未核对"),
    );
    const ordinary = "lim (x,y)->(0,0) [sqrt(xy+1)-1]/(x+y)";
    await page.locator(".formula-plain").fill("x+y");
    await page
      .locator(".formula-plain")
      .evaluate((node) => node.setSelectionRange(0, node.value.length));
    await page.getByRole("button", { name: "根号", exact: true }).click();
    assert.equal(
      await page.locator(".formula-plain").inputValue(),
      "sqrt(x+y)",
    );
    await page.waitForFunction(
      () => document.querySelector(".formula-latex").value === "\\sqrt{x+y}",
    );
    await page.locator(".formula-plain").fill("a+b");
    await page
      .locator(".formula-plain")
      .evaluate((node) => node.setSelectionRange(0, node.value.length));
    await page.getByRole("button", { name: "分式", exact: true }).click();
    assert.equal(
      await page.locator(".formula-plain").inputValue(),
      "[a+b]/[b]",
    );
    assert.deepEqual(
      await page
        .locator(".formula-plain")
        .evaluate((node) => [
          node.selectionStart,
          node.selectionEnd,
          node.value.slice(node.selectionStart, node.selectionEnd),
        ]),
      [7, 8, "b"],
    );
    await page.locator(".formula-plain").fill(ordinary);
    await page.waitForFunction(() =>
      document
        .querySelector(".formula-conversion-message")
        .textContent.includes("草稿已自动更新"),
    );
    assert.equal(
      await page.locator(".formula-latex").inputValue(),
      "\\lim_{(x,y)\\to(0,0)}\\frac{\\sqrt{xy+1}-1}{x+y}",
    );
    assert.equal(
      await page.locator(".formula-latex").inputValue(),
      "\\lim_{(x,y)\\to(0,0)}\\frac{\\sqrt{xy+1}-1}{x+y}",
    );
    assert.match(
      await page.locator(".formula-conversion-message").textContent(),
      /自动更新/,
    );
    assert.equal(await page.locator(".formula-status").inputValue(), "pending");
    assert.equal(await page.locator(".math-fraction").count(), 1);
    assert.equal(await page.locator(".math-radicand").count(), 1);
    assert.equal(await page.locator(".math-operator").count(), 1);
    await page.getByRole("button", { name: "与原图一致" }).click();
    assert.equal(
      await page.locator(".formula-status").inputValue(),
      "reviewed",
    );
    assert.equal(
      await page.locator(".formula-review-badge").textContent(),
      "已人工核对",
    );
    await page.locator(".formula-plain").fill(`${ordinary} `);
    assert.equal(await page.locator(".formula-status").inputValue(), "pending");
    await page.locator(".formula-plain").fill("a/b");
    await page.getByRole("button", { name: "与原图一致" }).click();
    assert.match(
      await page.locator(".formula-conversion-message").textContent(),
      /请先生成有效草稿/,
    );
    await page.locator(".formula-plain").press("Control+Enter");
    assert.match(
      await page.locator(".formula-conversion-message").textContent(),
      /停止转换.*分子必须/,
    );
    await page.locator(".formula-plain").fill(ordinary);
    await page.waitForFunction(() =>
      document
        .querySelector(".formula-conversion-message")
        .textContent.includes("草稿已自动更新"),
    );
    assert.equal(
      await page.locator(".formula-latex").inputValue(),
      "\\lim_{(x,y)\\to(0,0)}\\frac{\\sqrt{xy+1}-1}{x+y}",
    );
    await page.getByRole("button", { name: "与原图一致" }).click();
    assert.equal(
      await page.locator(".formula-status").inputValue(),
      "reviewed",
    );
    await page.locator(".page").nth(1).click();
    await page.locator(".page").nth(0).click();
    assert.equal(await page.locator(".formula-plain").inputValue(), ordinary);
    await page.getByRole("button", { name: "与原图一致" }).click();
    assert.match(
      await page.locator(".formula-conversion-message").textContent(),
      /已记录人工对照确认/,
    );
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(networkRequests, []);

    await page.locator(".page").nth(1).click();
    await page.locator("#privacyStatus").selectOption("redacted_copy");
    await page.locator("#annotationStatus").selectOption("annotated");
    await page.locator('#degradations input[value="blurred"]').check();
    await page.locator('#contentTags input[value="negative"]').check();
    await page.locator("#textReviewed").check();

    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const outcome = await Promise.race([
      downloadPromise.then((download) => ({ download })),
      page
        .waitForFunction(() =>
          document.querySelector("#status").textContent.includes("禁止导出"),
        )
        .then(() => ({ error: true })),
    ]);
    if (outcome.error) {
      throw new Error(await page.locator("#status").textContent());
    }
    const download = outcome.download;
    const exportPath = path.join(temporaryRoot, "gold.json");
    await download.saveAs(exportPath);
    const gold = JSON.parse(fs.readFileSync(exportPath, "utf8"));
    assert.equal(gold.schema_version, "recognition-private-gold-v2");
    assert.equal(gold.cases.length, 2);
    assert.equal(gold.cases[0].expected_text, "1. 求 x² 的极限");
    assert.deepEqual(gold.cases[0].expected_question_numbers, ["1"]);
    assert.equal(gold.cases[0].expected_regions.length, 1);
    assert.equal(gold.cases[0].formula_spans.length, 1);
    assert.equal(
      gold.cases[0].formula_spans[0].latex,
      "\\lim_{(x,y)\\to(0,0)}\\frac{\\sqrt{xy+1}-1}{x+y}",
    );
    assert.equal(
      gold.cases[0].formula_spans[0].linear_text,
      "lim_{(x,y)→(0,0)} [√(xy+1)−1]/(x+y)",
    );
    assert.equal(gold.cases[0].formula_spans[0].review_status, "reviewed");
    assert.equal(gold.cases[1].expected_regions.length, 0);
    assert.equal(gold.cases[1].formula_spans.length, 0);
    const serialized = JSON.stringify(gold);
    for (const forbidden of [
      "role",
      "privacy_status",
      "text_reviewed",
      "source_ref",
      "student_or_assignment_material",
      "formula-plain",
      ordinary,
    ]) {
      assert(!serialized.includes(forbidden));
    }
    process.stdout.write(
      `${JSON.stringify({ status: "passed", pages: 2, privacy_gate: true, writes_product_data: false })}\n`,
    );
  } finally {
    await browser.close();
  }
}

main()
  .catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  })
  .finally(() => fs.rmSync(temporaryRoot, { recursive: true, force: true }));

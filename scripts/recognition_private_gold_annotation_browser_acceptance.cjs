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
    page.on("pageerror", (error) => pageErrors.push(error));
    await page.goto(pathToFileURL(toolPath).href);
    await page.locator("#seed").setInputFiles(seedPath);
    await page.locator("#images").setInputFiles(imagePaths);
    await page.locator("#drafts").setInputFiles(draftsPath);
    await page.waitForFunction(
      () => document.querySelectorAll(".page").length === 2,
    );
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
    await page.locator("#export").click();
    await page.waitForFunction(() =>
      document.querySelector("#status").textContent.includes("公式 1 尚未核对"),
    );
    await page
      .locator(".formula-latex")
      .fill("\\lim_{x\\to 0}\\frac{\\sqrt{x+1}-1}{x}");
    await page.locator(".formula-linear").fill("lim_{x→0} [√(x+1)−1]/x");
    await page.locator(".formula-status").selectOption("reviewed");
    assert.match(
      await page.locator(".formula-preview").textContent(),
      /\\frac/,
    );
    assert.deepEqual(pageErrors, []);

    await page.locator(".page").nth(1).click();
    await page.locator("#privacyStatus").selectOption("redacted_copy");
    await page.locator("#annotationStatus").selectOption("annotated");
    await page.locator('#degradations input[value="blurred"]').check();
    await page.locator('#contentTags input[value="negative"]').check();
    await page.locator("#textReviewed").check();

    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const download = await downloadPromise;
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
      "\\lim_{x\\to 0}\\frac{\\sqrt{x+1}-1}{x}",
    );
    assert.equal(
      gold.cases[0].formula_spans[0].linear_text,
      "lim_{x→0} [√(x+1)−1]/x",
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

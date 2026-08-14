"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { PNG } = require("pngjs");
const { chromium } = require("playwright");

const toolPath = path.join(__dirname, "formula_region_annotation_v1.html");
const temporaryRoot = fs.mkdtempSync(
  path.join(os.tmpdir(), "ahamark-formula-region-"),
);

function syntheticPng(filePath, color) {
  const image = new PNG({ width: 100, height: 100 });
  for (let index = 0; index < image.data.length; index += 4) {
    image.data[index] = color[0];
    image.data[index + 1] = color[1];
    image.data[index + 2] = color[2];
    image.data[index + 3] = 255;
  }
  fs.writeFileSync(filePath, PNG.sync.write(image));
}

async function draw(page, start, end) {
  const bounds = await page.locator("#overlay").boundingBox();
  assert(bounds);
  await page.mouse.move(bounds.x + start[0], bounds.y + start[1]);
  await page.mouse.down();
  await page.mouse.move(bounds.x + end[0], bounds.y + end[1]);
  await page.mouse.up();
}

async function storedState(page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("ahamark-formula-region-annotation-v1")),
  );
}

async function main() {
  const imagePaths = ["page-a.png", "page-b.png", "page-c.png"].map(
    (name, index) => {
      const filePath = path.join(temporaryRoot, name);
      syntheticPng(filePath, [245 - index, 245, 245]);
      return filePath;
    },
  );
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  try {
    const page = await browser.newPage({
      viewport: { width: 1200, height: 900 },
    });
    await page.goto(pathToFileURL(toolPath).href);
    await page.locator("#files").setInputFiles(imagePaths);
    await page.waitForFunction(
      () => document.querySelectorAll(".page").length === 3,
    );
    let state = await storedState(page);
    assert.deepEqual(
      state.cases.map((item) => item.annotation_status),
      ["pending", "pending", "pending"],
      "loading pages must not create human annotation decisions",
    );

    await page.locator(".page").nth(0).click();
    await page.locator("#modality").selectOption("synthetic");
    await page.locator("#kind").selectOption("matrix");
    await page.locator("#style").selectOption("handwritten");
    await draw(page, [10, 20], [40, 40]);
    state = await storedState(page);
    let first = state.cases[0];
    assert.equal(first.annotation_status, "annotated");
    assert.equal(first.regions.length, 1);
    assert.equal(first.regions[0].kind, "matrix");
    assert(Math.abs(first.regions[0].bbox.x - 0.1) < 0.002);
    assert(Math.abs(first.regions[0].bbox.y - 0.2) < 0.002);

    await page.locator("#zoom").evaluate((node) => {
      node.value = "200";
      node.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(
      () =>
        document.querySelector("#overlay").getBoundingClientRect().width ===
        200,
    );
    await page.locator("#redraw").click();
    await draw(page, [40, 60], [120, 100]);
    state = await storedState(page);
    first = state.cases[0];
    assert.equal(first.regions.length, 1);
    assert(Math.abs(first.regions[0].bbox.x - 0.2) < 0.002);
    assert(Math.abs(first.regions[0].bbox.y - 0.3) < 0.002);
    assert(Math.abs(first.regions[0].bbox.width - 0.4) < 0.002);

    await page.locator("#deleteBox").click();
    assert.equal(await page.locator("rect.box").count(), 0);
    await draw(page, [20, 20], [100, 60]);
    await page.locator(".page").nth(1).click();
    await page.locator("#modality").selectOption("synthetic");
    await page.locator("#pageStatus").selectOption("no_formula");
    await page.locator(".page").nth(2).click();
    await page.locator("#modality").selectOption("synthetic");
    await page.locator("#pageStatus").selectOption("unjudgeable");

    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const download = await downloadPromise;
    const exportPath = path.join(temporaryRoot, "export.json");
    await download.saveAs(exportPath);
    const exported = JSON.parse(fs.readFileSync(exportPath, "utf8"));
    assert.equal(exported.schema_version, "formula-region-detection-v1");
    assert.deepEqual(
      exported.cases.map((item) => item.annotation_status),
      ["annotated", "no_formula", "unjudgeable"],
    );
    assert.equal(exported.cases[0].regions.length, 1);
    const serialized = JSON.stringify(exported);
    for (const forbidden of [
      "image",
      "path",
      "page-a.png",
      "student",
      "filename",
    ]) {
      assert(!serialized.toLowerCase().includes(forbidden));
    }
    process.stdout.write(
      `${JSON.stringify({
        status: "passed",
        pages: 3,
        source_coordinate_invariant: true,
        initial_human_decisions: 0,
        writes_product_data: false,
      })}\n`,
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

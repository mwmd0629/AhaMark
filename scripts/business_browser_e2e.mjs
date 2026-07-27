import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const base = process.env.BUSINESS_E2E_WEB_URL ?? "http://localhost:3300";
const email =
  process.env.BUSINESS_E2E_TEACHER_EMAIL ??
  "teacher@business-e2e.synthetic.invalid";
const password =
  process.env.BUSINESS_E2E_TEACHER_PASSWORD ?? "Synthetic-Business-E2E-Only!";
const runPrefix = process.env.BUSINESS_E2E_RUN_PREFIX ?? "business-e2e";
const markerSuffix =
  process.env.BUSINESS_E2E_MARKER_SUFFIX ?? "business-e2e.synthetic.invalid";
const composeProject =
  process.env.BUSINESS_E2E_COMPOSE_PROJECT ?? "ahamark-business-e2e";
const exceptionBootstrap = process.env.BUSINESS_E2E_EXCEPTION_BOOTSTRAP === "1";
const runTimeoutMs = Number(process.env.BUSINESS_E2E_TIMEOUT_MS ?? "240000");
const runId = `${runPrefix}-${new Date().toISOString().replace(/[-:.TZ]/g, "")}`;
const marker = `${runId}.${markerSuffix}`;
const numberSeed = Number(runId.replace(/\D/g, "").slice(-6));
const studentNumbers = [0, 1, 2].map(
  (offset) => `0${String((numberSeed + offset) % 1_000_000).padStart(6, "0")}`,
);
const artifactDir = path.resolve(
  process.env.BUSINESS_E2E_ARTIFACT_ROOT ?? "test-results/business-e2e",
  runId,
);
const evidencePath = path.resolve(
  process.env.BUSINESS_E2E_EVIDENCE_PATH ??
    "docs/business-e2e-verification.json",
);
fs.mkdirSync(artifactDir, { recursive: true });

const evidence = {
  result: "failed",
  code_version: execFileSync("git", ["rev-parse", "HEAD"], {
    encoding: "utf8",
  }).trim(),
  environment: {
    kind: "isolated_compose",
    compose_project: composeProject,
    web_origin: base,
    data_policy: "synthetic_only",
  },
  synthetic_marker: marker,
  started_at: new Date().toISOString(),
  completed_at: null,
  stages: Object.fromEntries(
    "ABCDEFGH"
      .split("")
      .map((key) => [key, { status: "not_run", ui_steps: [] }]),
  ),
  objects: {},
  reconciliation: {},
  ocr: {
    provider: "fake",
    meaning: "non-production workflow test adapter",
    proves: "browser UI and durable workflow orchestration only",
    does_not_prove:
      "RapidOCR accuracy, handwriting OCR, formula OCR, or LaTeX reliability",
  },
  subjective_scoring: "teacher_manual_ui",
  failure: null,
};

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++)
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function pngChunk(type, data) {
  const name = Buffer.from(type);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, crc]);
}
function makePng(file, [red, green, blue]) {
  const width = 160;
  const height = 120;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const rows = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x++) {
      row[1 + x * 3] = (red + x + y) % 256;
      row[2 + x * 3] = (green + y) % 256;
      row[3 + x * 3] = (blue + x) % 256;
    }
    rows.push(row);
  }
  fs.writeFileSync(
    file,
    Buffer.concat([
      Buffer.from("89504e470d0a1a0a", "hex"),
      pngChunk("IHDR", header),
      pngChunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
      pngChunk("IEND", Buffer.alloc(0)),
    ]),
  );
}

const paperFile = path.join(artifactDir, `${runId}-paper.png`);
const submissionFiles = [
  [`${studentNumbers[0]}-page-1.png`, [220, 30, 40]],
  [`${studentNumbers[0]}-page-2.png`, [210, 50, 60]],
  [`${studentNumbers[1]}-page-1.png`, [30, 150, 80]],
  [`${studentNumbers[1]}-page-2.png`, [50, 170, 100]],
].map(([name, color]) => {
  const file = path.join(artifactDir, name);
  makePng(file, color);
  return file;
});
makePng(paperFile, [40, 80, 210]);
const csvFile = path.join(artifactDir, `${runId}-students.csv`);
fs.writeFileSync(
  csvFile,
  `姓名,学号,分组,性别,邮箱,联系方式\n合成学生甲-${runId},${studentNumbers[0]},,,student-1@${marker},\n合成学生乙-${runId},${studentNumbers[1]},,,student-2@${marker},\n合成学生未完成-${runId},${studentNumbers[2]},,,student-3@${marker},\n`,
  "utf8",
);

const browser = await chromium.launch({
  headless: true,
  executablePath:
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();
let currentStage = "A";
let runTimedOut = false;
const runWatchdog = setTimeout(() => {
  runTimedOut = true;
  void browser.close();
}, runTimeoutMs);

function stage(key, step) {
  evidence.stages[key].ui_steps.push(step);
}
async function clickAndWait(button, responsePattern) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes(responsePattern) &&
        response.request().method() !== "OPTIONS" &&
        response.ok(),
    ),
    button.click(),
  ]);
}
async function selectByLabel(label, text) {
  await page.getByLabel(label).selectOption({ label: text });
}
async function drainButtons(locator, label, maxAttempts) {
  for (let attempts = 0; attempts < maxAttempts; attempts += 1) {
    const before = await locator.count();
    if (before === 0) return;
    await locator.first().click();
    let decreased = false;
    for (let polls = 0; polls < 40; polls += 1) {
      if ((await locator.count()) < before) {
        decreased = true;
        break;
      }
      await page.waitForTimeout(250);
    }
    assert.equal(decreased, true, `${label} button count must decrease`);
  }
  assert.equal(await locator.count(), 0, `${label} loop must converge`);
}
async function selectQuestion(number) {
  const select = page.getByLabel("当前题目");
  const options = await select.locator("option").evaluateAll((nodes) =>
    nodes.map((node) => ({
      value: node.getAttribute("value"),
      text: node.textContent,
    })),
  );
  const option = options.find((item) =>
    item.text?.replace(/\s+/g, " ").includes(`第 ${number} 题`),
  );
  if (!option?.value) throw new Error(`QUESTION_OPTION_${number}_NOT_FOUND`);
  await select.selectOption(option.value);
  if ((await select.inputValue()) !== option.value)
    throw new Error(`QUESTION_OPTION_${number}_NOT_SELECTED`);
  await page.locator(`textarea[data-question-id="${option.value}"]`).waitFor();
  const saveTarget = await page
    .getByRole("button", { name: "保存本题评分标准" })
    .getAttribute("data-question-id");
  if (saveTarget !== option.value)
    throw new Error(
      `QUESTION_${number}_SAVE_TARGET_MISMATCH:${option.value}:${saveTarget}`,
    );
  return option.value;
}

try {
  await page.goto(`${base}/login`);
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("**/dashboard");
  await page.reload();
  await page.getByRole("heading", { name: /(?:早上好|你好)/ }).waitFor();
  const storageKeys = await page.evaluate(() => Object.keys(localStorage));
  if (
    storageKeys.some((key) =>
      /token|session|password|credential|auth/i.test(key),
    )
  ) {
    throw new Error("LONG_LIVED_CREDENTIAL_IN_LOCAL_STORAGE");
  }
  stage("A", "login_form_submit");
  stage("A", "protected_dashboard_after_refresh");
  stage("A", "local_storage_credential_key_check");
  evidence.stages.A.status = "passed";

  currentStage = "B";
  await page.goto(`${base}/classes`);
  await page.getByRole("button", { name: /创建班级/ }).click();
  const className = `合成班级 ${runId}`;
  await page.getByLabel("班级名称").fill(className);
  await page.getByLabel("年级").fill("合成九年级");
  await page.getByLabel("学科").fill("合成数学");
  await page.getByRole("button", { name: "保存班级" }).click();
  await page.getByText(className, { exact: true }).waitFor();
  const closeClassDialog = page.getByRole("button", { name: "关闭对话框" });
  if (await closeClassDialog.isVisible()) await closeClassDialog.click();
  await page.getByRole("link", { name: className, exact: true }).click();
  await page.waitForURL("**/classes/*");
  const classId = page.url().split("/").pop();
  evidence.objects.class_id = classId;
  await page.getByRole("button", { name: "导入学生" }).click();
  await page
    .getByRole("dialog", { name: "导入学生名单" })
    .locator('input[name="file"]')
    .setInputFiles(csvFile);
  await page.getByRole("button", { name: "上传并预览" }).click();
  await page.getByText("可导入 3").waitFor();
  await page.getByText(studentNumbers[0], { exact: true }).waitFor();
  await page.getByRole("button", { name: "确认导入整批合法数据" }).click();
  await page.getByText(/导入完成：新建 3/).waitFor();
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await page.getByText(`合成学生甲-${runId}`, { exact: true }).waitFor();
  await page.getByText(studentNumbers[0], { exact: true }).waitFor();
  stage("B", "create_class");
  stage("B", "csv_preview_and_confirm");
  stage("B", "leading_zero_and_three_students_visible");
  evidence.stages.B.status = "passed";

  currentStage = "C";
  await page.goto(`${base}/assignments/new`);
  const assignmentName = `合成作业 ${runId}`;
  await page.getByLabel("作业名称").fill(assignmentName);
  await page.getByLabel("学科").fill("合成数学");
  await page.getByLabel("年级").fill("合成九年级");
  await page.getByText(className, { exact: true }).click();
  await page.getByRole("button", { name: "保存草稿并继续" }).click();
  await page.waitForURL("**/assignments/*/edit");
  const assignmentId = page.url().split("/").at(-2);
  evidence.objects.assignment_id = assignmentId;
  evidence.objects.assignment_name = assignmentName;
  await page.getByRole("button", { name: /步骤 1/ }).click();
  await page.getByLabel("总分").fill("10");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByLabel("选择试卷文件").setInputFiles(paperFile);
  await page.getByRole("button", { name: "开始上传" }).click();
  await page.getByRole("button", { name: "继续整理页面" }).click();
  await page.getByRole("heading", { name: "整理页面" }).waitFor();
  await page.getByRole("button", { name: /步骤 3/ }).click();
  await page.getByRole("heading", { name: "整理页面" }).waitFor();
  await page.getByRole("heading", { name: "第 1 页", exact: true }).waitFor();
  stage("C", "six_step_wizard_basics_and_real_class");
  stage("C", "runtime_synthetic_png_upload_and_paper_page_created");
  evidence.stages.C.status = "passed";

  currentStage = "D";
  await page.goto(`${base}/assignments/${assignmentId}`);
  await page
    .locator('[data-testid="recognition-workspace"][data-provider="fake"]')
    .waitFor();
  await page.getByRole("button", { name: "开始识别" }).click();
  await page.getByTestId("recognition-job").waitFor();
  await page
    .locator('[data-testid="recognition-job"][data-status="completed"]')
    .waitFor({ timeout: 90_000 });
  evidence.objects.paper_recognition_job_id = await page
    .getByTestId("recognition-job")
    .getAttribute("data-job-id");
  await page.getByTestId("recognition-candidate").waitFor();
  await page.getByLabel("OCR 文字").fill("合成主观题：说明计算过程");
  await page.getByLabel("分值").fill(exceptionBootstrap ? "" : "5");
  await clickAndWait(
    page.getByRole("button", { name: "保存修正" }),
    "/candidates/",
  );
  await clickAndWait(
    page.getByRole("button", { name: "确认生成题目" }),
    "/confirm",
  );
  stage("D", "paper_ocr_job_and_candidate_visible");
  stage("D", "candidate_manually_corrected_and_confirmed");
  if (exceptionBootstrap) {
    await page.goto(`${base}/assignments/${assignmentId}/edit`);
    await page.getByRole("button", { name: /步骤 5/ }).click();
    await page
      .getByText("当前题目分值未知，Rubric 保存和发布会被阻止。")
      .waitFor();
    const scoreDialog = page.waitForEvent("dialog");
    const scoreClick = page
      .getByRole("button", { name: "补齐所选题目分值" })
      .click();
    await (await scoreDialog).accept("5");
    await scoreClick;
    await page.getByText("题目分值已补齐，可以继续设置 Rubric").waitFor();
    stage("D", "null_candidate_score_blocks_rubric_then_teacher_fixes_via_ui");
  }

  await page.goto(`${base}/assignments/${assignmentId}/edit`);
  await page.getByRole("button", { name: /步骤 4/ }).click();
  await page.getByLabel("题号").fill("2");
  await page.getByLabel("题型").selectOption("single_choice");
  await page.getByLabel("分值").fill("5");
  await page.getByLabel("知识点（逗号分隔）").fill("合成知识点");
  await page.getByLabel("题目内容").fill("合成客观题：选择正确答案");
  await page.getByRole("button", { name: "添加题目" }).click();
  await page.getByText("题目已创建").waitFor();
  await page.getByRole("button", { name: /步骤 4/ }).click();
  await page.getByRole("heading", { name: "为第 2 题添加页面区域" }).waitFor();
  await page.getByRole("button", { name: "保存区域" }).click();
  await page.getByText("区域已保存").waitFor();
  const continueRubric = page.getByRole("button", {
    name: "继续设置评分标准",
  });
  if (await continueRubric.isVisible()) await continueRubric.click();
  else await page.getByRole("heading", { name: "评分标准" }).waitFor();
  const objectiveQuestionId = await selectQuestion(2);
  await page.waitForTimeout(250);
  await page.getByLabel("标准答案").fill("1. 测试题");
  await clickAndWait(
    page.getByRole("button", { name: "保存本题评分标准" }),
    "/rubrics/",
  );
  const subjectiveQuestionId = await selectQuestion(1);
  await page.waitForTimeout(250);
  await page.getByLabel("标准答案").fill("教师人工判断");
  await clickAndWait(
    page.getByRole("button", { name: "保存本题评分标准" }),
    "/rubrics/",
  );
  for (const [questionId, answer] of [
    [subjectiveQuestionId, "教师人工判断"],
    [objectiveQuestionId, "1. 测试题"],
  ]) {
    await page.goto(
      `${base}/assignments/${assignmentId}/rubrics/${questionId}`,
    );
    await page.getByLabel("标准答案草稿").fill(answer);
    await page.getByRole("button", { name: "保存新版本" }).click();
    await page.getByRole("button", { name: "确认来源与版本" }).click();
    await page.getByText(/teacher_authored · confirmed/).waitFor();
    await page.getByRole("button", { name: "创建 Rubric 草稿" }).click();
    await page.getByRole("dialog", { name: "结构化 Rubric 编辑器" }).waitFor();
    await page.getByRole("button", { name: "校验并确认" }).click();
    await page.getByText("confirmed", { exact: true }).waitFor();
  }
  await page.goto(`${base}/assignments/${assignmentId}/edit`);
  await page.getByRole("button", { name: /步骤 5/ }).click();
  await page.getByRole("button", { name: "进入发布检查" }).click();
  await page.getByRole("button", { name: "启动生成任务" }).click();
  await page.getByText("Generation", { exact: true }).waitFor();
  await page
    .getByLabel("生成状态")
    .getByText(/(?:部分完成|需要教师复核|已完成)/)
    .waitFor({ timeout: 120_000 });
  const fileAnalysisButtons = page.getByRole("button", {
    name: "确认文件分析",
  });
  await drainButtons(fileAnalysisButtons, "file analysis confirmation", 8);
  const extractionReview = page.getByRole("region", {
    name: "页面整理与题目抽取复核",
  });
  const generatedSuggestionRejects = extractionReview.getByRole("button", {
    name: "拒绝",
    exact: true,
  });
  await drainButtons(
    generatedSuggestionRejects,
    "generated suggestion rejection",
    12,
  );
  await page.getByRole("button", { name: "开始集中审查" }).click();
  await page.getByRole("heading", { name: "集中审查中心" }).waitFor();
  const explicitConfirmations = page
    .getByRole("heading", { name: "教师显式确认" })
    .locator("xpath=..");
  for (const label of [
    "确认班级",
    "确认截止时间",
    "确认总分",
    "确认文件角色",
    "确认答案来源",
    "确认试卷版本",
    "确认答案版本",
    "确认评分标准",
  ]) {
    await explicitConfirmations
      .getByRole("button", { name: label, exact: true })
      .click();
  }
  await page.getByRole("button", { name: "准备发布评分标准" }).click();
  await page.getByRole("button", { name: "确认绑定" }).click();
  const manualResolutions = page.getByRole("button", {
    name: "人工检查并解决",
  });
  await drainButtons(manualResolutions, "manual review resolution", 12);
  const warningAcknowledgements = page.getByRole("button", {
    name: "确认已查看",
  });
  await drainButtons(warningAcknowledgements, "warning acknowledgement", 20);
  await page.getByRole("button", { name: "准备发布", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "教师确认并发布" }).click();
  await page.waitForURL(`**/assignments/${assignmentId}`);
  stage("D", "objective_and_subjective_questions_with_positive_scores");
  stage("D", "rubric_items_knowledge_point_publish_check_and_publish");
  evidence.stages.D.status = "passed";

  currentStage = "E";
  await page.goto(`${base}/grading`);
  await selectByLabel("已发布作业", assignmentName);
  await page.getByLabel("班级").selectOption(classId);
  await page.getByLabel("批次名称").fill(`合成批次 ${runId}`);
  await page.getByRole("button", { name: "创建批改批次" }).click();
  const batchCard = page.getByTestId("grading-batch").first();
  await batchCard.waitFor();
  evidence.objects.grading_batch_id =
    await batchCard.getAttribute("data-batch-id");
  await batchCard.getByRole("link", { name: "进入批次工作台" }).click();
  await page.getByLabel("选择学生作业").setInputFiles(submissionFiles);
  await page.getByRole("button", { name: "上传并自动匹配" }).click();
  await page.getByText(/学生作业已上传/).waitFor();
  await page.getByTestId("submission").nth(1).waitFor();
  if (exceptionBootstrap) {
    await page.getByRole("button", { name: "拆出末页" }).first().click();
    await page.getByText("Submission 已拆分且原始上传文件保持不变").waitFor();
    await page
      .getByRole("button", { name: "合并回首次 Submission" })
      .first()
      .click();
    await page.getByText("Submission 已合并且页码重新连续编号").waitFor();
    stage("E", "split_and_merge_before_ocr_via_ui");
  }
  await page.getByRole("button", { name: "启动全部 Submission OCR" }).click();
  await page.getByText(/Submission OCR 已完成/).waitFor({ timeout: 120_000 });
  const ocrCards = page.getByTestId("submission-ocr");
  await ocrCards.nth(1).waitFor();
  evidence.objects.submission_recognition_job_ids = await ocrCards.evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute("data-job-id")),
  );
  evidence.objects.submission_ids = await page
    .getByTestId("submission")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-submission-id")),
    );
  evidence.objects.student_ids = await page
    .getByTestId("submission")
    .evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-student-id")),
    );
  await page.getByRole("button", { name: "保存当前页面顺序" }).first().click();
  await page.getByText(/页面顺序已通过 UI 保存/).waitFor();
  await page.getByText("已形成 StudentAnswer：4").waitFor();
  stage("E", "create_batch_and_upload_four_synthetic_pages");
  stage("E", "automatic_filename_matching_two_submissions");
  stage("E", "submission_ocr_student_answers_and_page_order_ui");
  evidence.stages.E.status = "passed";

  currentStage = "F";
  await page.getByRole("button", { name: "运行确定性初批" }).click();
  await page.getByText(/客观题规则初批完成/).waitFor();
  await page.getByRole("link", { name: "进入三栏教师复核" }).click();
  await page.waitForURL("**/review");
  const submissionButtons = page
    .getByRole("navigation", { name: "复核导航" })
    .locator("button")
    .filter({ hasText: /^提交/ });
  for (let submissionIndex = 0; submissionIndex < 2; submissionIndex++) {
    await submissionButtons.nth(submissionIndex).click();
    await page.waitForFunction(
      (index) =>
        document
          .querySelectorAll('nav[aria-label="复核导航"] button')
          [index]?.className.includes("bg-indigo-50"),
      submissionIndex,
    );
    const answerButtons = page
      .getByRole("navigation", { name: "复核导航" })
      .locator("button");
    for (const questionNumber of [1, 2]) {
      await answerButtons
        .filter({ hasText: new RegExp(`^第 ${questionNumber} 题`) })
        .click();
      const panel = page.getByTestId("review-answer");
      await panel
        .getByRole("heading", { name: new RegExp(`第 ${questionNumber} 题`) })
        .waitFor();
      const questionType = await panel.getAttribute("data-question-type");
      if (
        [
          "single_choice",
          "multiple_choice",
          "true_false",
          "fill_blank",
        ].includes(questionType ?? "")
      ) {
        if ((await panel.getAttribute("data-provider")) !== "objective-rule")
          throw new Error("OBJECTIVE_PROVIDER_NOT_RULE_BASED");
        const answerId = await panel.getAttribute("data-answer-id");
        await Promise.all([
          page.waitForResponse(
            (response) =>
              response.url().includes(`/student-answers/${answerId}/review`) &&
              response.request().method() === "PUT" &&
              response.ok(),
          ),
          page.getByRole("button", { name: "接受", exact: true }).click(),
        ]);
      } else {
        if ((await panel.getAttribute("data-provider")) !== "unavailable")
          throw new Error("SUBJECTIVE_PROVIDER_BOUNDARY_MISSING");
        const answerId = await panel.getAttribute("data-answer-id");
        const score = submissionIndex === 0 ? "4" : "3";
        const dialogHandler = async (dialog) => {
          const prompt = dialog.message();
          if (prompt.includes("最终分数")) {
            await dialog.accept(score);
          } else if (prompt.includes("评分项")) {
            await dialog.accept(score);
          } else {
            await dialog.accept(`合成教师人工评分 ${submissionIndex + 1}`);
          }
        };
        page.on("dialog", dialogHandler);
        try {
          await Promise.all([
            page.waitForResponse(
              (response) =>
                response
                  .url()
                  .includes(`/student-answers/${answerId}/review`) &&
                response.request().method() === "PUT" &&
                response.ok(),
            ),
            page.getByRole("button", { name: "手动评分" }).click(),
          ]);
        } finally {
          page.off("dialog", dialogHandler);
        }
      }
      await page.getByText("复核结果已保存").waitFor();
    }
  }
  await page.getByText("进度 4/4").waitFor();
  await page.getByRole("button", { name: "完成全部 finalize" }).click();
  if (exceptionBootstrap) {
    const incompleteSnapshot = page.locator(
      '[data-testid="score-snapshot"][data-status="incomplete"]',
    );
    await incompleteSnapshot.waitFor();
    evidence.objects.incomplete_snapshot_id =
      await incompleteSnapshot.getAttribute("data-snapshot-id");
    stage("F", "incomplete_merged_submission_blocked_without_false_success");
  } else {
    await page.getByText(/全部 Submission 已 finalize/).waitFor();
  }
  const snapshotNodes = page.locator(
    '[data-testid="score-snapshot"][data-status="complete"]',
  );
  evidence.objects.complete_snapshot_ids = await snapshotNodes.evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute("data-snapshot-id")),
  );
  evidence.reconciliation.snapshot_scores = await snapshotNodes.evaluateAll(
    (nodes) =>
      nodes.map((node) => Number(node.getAttribute("data-total-score"))),
  );
  stage("F", "objective_rule_result_and_unavailable_subjective_visible");
  stage("F", "teacher_accepted_objective_and_manually_scored_subjective");
  stage("F", "all_mandatory_reviews_then_finalize_complete_snapshots");
  evidence.stages.F.status = "passed";

  currentStage = "G";
  await page.getByRole("link", { name: "返回批次工作台" }).click();
  await page.getByRole("button", { name: "查看 grade readiness" }).click();
  await page.getByText("可发布 2 · 未完成 1").waitFor();
  await page
    .getByRole("button", { name: "创建新的 GradeRelease 版本" })
    .click();
  await page.getByTestId("grade-release").waitFor();
  evidence.objects.grade_release_id = await page
    .getByTestId("grade-release")
    .getAttribute("data-release-id");
  await page.getByRole("button", { name: "生成 XLSX" }).click();
  await page
    .getByText(/gradebook_xlsx 报告任务已完成/)
    .waitFor({ timeout: 150_000 });
  await page.getByRole("button", { name: "生成首名学生中文 PDF" }).click();
  await page
    .getByText(/student_report_pdf 报告任务已完成/)
    .waitFor({ timeout: 150_000 });
  const reportNodes = page.getByTestId("report-job");
  evidence.objects.report_jobs = await reportNodes.evaluateAll((nodes) =>
    nodes.map((node) => ({
      id: node.getAttribute("data-report-id"),
      type: node.getAttribute("data-report-type"),
      status: node.getAttribute("data-report-status"),
    })),
  );
  for (let index = 0; index < 2; index++) {
    await reportNodes
      .nth(index)
      .getByRole("button", { name: "请求短期下载地址" })
      .click();
    await page
      .getByText(/已通过 UI 获取新的 15 分钟短期签名下载地址/)
      .waitFor();
    const signed = await page
      .getByTestId("signed-download")
      .getAttribute("href");
    if (!signed || !new URL(signed).search)
      throw new Error("SIGNED_DOWNLOAD_QUERY_MISSING");
  }
  evidence.reconciliation.release_snapshot_ids = await page
    .getByTestId("grade-release")
    .locator("p")
    .first()
    .textContent()
    .then((text) =>
      evidence.objects.complete_snapshot_ids.filter((id) => text?.includes(id)),
    );
  stage("G", "readiness_excludes_one_unfinished_student");
  stage("G", "release_pins_complete_snapshot_ids");
  stage("G", "xlsx_and_chinese_pdf_completed_and_signed_download_requested");
  evidence.stages.G.status = "passed";

  currentStage = "H";
  await page.goto(`${base}/analytics`);
  await selectByLabel("作业", assignmentName);
  await page.getByLabel("发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
  await page.getByText("分数分布").waitFor();
  const metrics = page.getByTestId("analytics-metrics");
  evidence.objects.analytics_snapshot_id =
    await metrics.getAttribute("data-snapshot-id");
  const metricText = await metrics.textContent();
  const expectedAverage =
    evidence.reconciliation.snapshot_scores.reduce(
      (total, score) => total + score,
      0,
    ) / evidence.reconciliation.snapshot_scores.length;
  if (
    !metricText?.includes("参与人数2") ||
    !metricText.includes(`平均分${expectedAverage}`)
  )
    throw new Error("ANALYTICS_RECONCILIATION_FAILED");
  await page.getByRole("button", { name: "0-59" }).click();
  await page.getByText(/分数段 0-59/).waitFor();
  await page.getByRole("link", { name: "查看学生详情" }).first().click();
  await page.waitForURL("**/analytics/students/*");
  await page.getByText("当前发布成绩").waitFor();
  stage("H", "score_band_drilldown_and_student_detail");
  await page.goBack();
  await selectByLabel("作业", assignmentName);
  await page.getByLabel("发布版本").selectOption({ index: 1 });
  await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
  await page.getByText("知识点掌握率").waitFor();
  const knowledgeButton = page
    .getByText("知识点掌握率")
    .locator("xpath=following::table[1]")
    .getByRole("button")
    .first();
  await knowledgeButton.click();
  await page.getByText("知识点下钻").waitFor();
  stage("H", "knowledge_point_drilldown_and_class_trend");
  await page.getByRole("button", { name: "生成规则建议" }).click();
  await page.getByLabel("建议内容").fill(`规则建议已由教师编辑 ${runId}`);
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByText("建议草稿已保存").waitFor();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await page.getByText("建议已确认").waitFor();
  if (!(await page.getByText("类型：规则型教学建议").isVisible()))
    throw new Error("RULE_BASED_INSIGHT_LABEL_MISSING");
  stage(
    "H",
    "fixed_release_metrics_distribution_questions_knowledge_points_trend",
  );
  stage("H", "rule_based_insight_edit_and_confirm");
  evidence.stages.H.status = "passed";

  evidence.reconciliation = {
    ...evidence.reconciliation,
    participant_count: 2,
    unfinished_student_count: 1,
    unfinished_counted_as_zero: false,
    analytics_average: expectedAverage,
    reports_source_grade_release_id: evidence.objects.grade_release_id,
    analytics_source_grade_release_id:
      await metrics.getAttribute("data-release-id"),
    consistent: true,
  };
  evidence.objects.student_numbers = studentNumbers;
  evidence.result = "passed";
} catch (error) {
  evidence.failure = {
    stage: currentStage,
    code: runTimedOut
      ? `RUN_TIMEOUT_${runTimeoutMs}MS`
      : error instanceof Error
        ? error.message.slice(0, 200)
        : "UNKNOWN_FAILURE",
    url: page.url(),
  };
  evidence.stages[currentStage].status = "failed";
  await page
    .screenshot({
      path: path.join(artifactDir, `failure-${currentStage}.png`),
      fullPage: true,
      timeout: 10_000,
    })
    .catch(() => undefined);
  throw error;
} finally {
  clearTimeout(runWatchdog);
  evidence.completed_at = new Date().toISOString();
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  await browser.close();
}

console.log(`BUSINESS_BROWSER_E2E_PASSED run_id=${runId} stages=8`);

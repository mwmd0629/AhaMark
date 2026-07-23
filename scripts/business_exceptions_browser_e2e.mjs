import { chromium } from "file:///C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/.pnpm/playwright@1.61.1/node_modules/playwright/index.mjs";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const base = process.env.BUSINESS_EXCEPTIONS_WEB_URL ?? "http://localhost:3300";
const password =
  process.env.BUSINESS_E2E_TEACHER_PASSWORD ?? "Synthetic-Business-E2E-Only!";
const evidencePath = path.resolve(
  process.env.BUSINESS_EXCEPTIONS_BROWSER_EVIDENCE ??
    "docs/business-exceptions-browser-verification.json",
);
const evidence = {
  result: "failed",
  environment: "isolated APP_ENV=test six-service stack and real Edge",
  synthetic_marker: "business-exceptions.synthetic.invalid",
  started_at: new Date().toISOString(),
  completed_at: null,
  stages: {},
  objects: {},
  limitations: [
    "failed/expired ReportJob cannot be produced naturally in a short browser run; retry is API integration plus UI component tested",
  ],
  failure: null,
};

function persist() {
  evidence.completed_at = new Date().toISOString();
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
}

function bootstrapFixture() {
  const configured = process.env.BUSINESS_EXCEPTIONS_FIXTURE_JSON;
  if (configured)
    return {
      fixture: JSON.parse(fs.readFileSync(path.resolve(configured), "utf8")),
      bootstrapped: false,
    };
  const bootstrapEvidence = path.resolve(
    "test-results",
    "business-exceptions",
    `bootstrap-${Date.now()}.json`,
  );
  const teacherEmail = `teacher-${Date.now()}@business-exceptions.synthetic.invalid`;
  fs.mkdirSync(path.dirname(bootstrapEvidence), { recursive: true });
  execFileSync(
    "docker",
    [
      "compose",
      "-p",
      "ahamark-business-e2e",
      "-f",
      "docker-compose.business-e2e.yml",
      "exec",
      "-T",
      "-e",
      `BUSINESS_E2E_TEACHER_EMAIL=${teacherEmail}`,
      "-e",
      "BUSINESS_E2E_TEACHER_NAME=合成教师 Business Exceptions",
      "api",
      "python",
      "-m",
      "app.cli.seed_business_e2e_teacher",
    ],
    { cwd: process.cwd(), stdio: "inherit" },
  );
  execFileSync(process.execPath, ["scripts/business_browser_e2e.mjs"], {
    cwd: process.cwd(),
    stdio: "inherit",
    env: {
      ...process.env,
      BUSINESS_E2E_TEACHER_EMAIL: teacherEmail,
      BUSINESS_E2E_RUN_PREFIX: "business-exceptions",
      BUSINESS_E2E_MARKER_SUFFIX: "business-exceptions.synthetic.invalid",
      BUSINESS_E2E_EXCEPTION_BOOTSTRAP: "1",
      BUSINESS_E2E_ARTIFACT_ROOT:
        "test-results/business-exceptions/bootstrap-artifacts",
      BUSINESS_E2E_EVIDENCE_PATH: bootstrapEvidence,
    },
  });
  const bootstrap = JSON.parse(fs.readFileSync(bootstrapEvidence, "utf8"));
  if (bootstrap.result !== "passed")
    throw new Error("EXCEPTION_BOOTSTRAP_BROWSER_FLOW_FAILED");
  const runId = String(bootstrap.synthetic_marker).split(
    ".business-exceptions.synthetic.invalid",
  )[0];
  const [firstNumber, secondNumber] = bootstrap.objects.student_numbers;
  const ambiguousFilename = `${firstNumber}-${secondNumber}-ambiguous.png`;
  const artifactDir = path.resolve(
    "test-results",
    "business-exceptions",
    "bootstrap-artifacts",
    runId,
  );
  const ambiguousFile = path.join(artifactDir, ambiguousFilename);
  fs.copyFileSync(path.join(artifactDir, `${runId}-paper.png`), ambiguousFile);
  evidence.synthetic_marker = bootstrap.synthetic_marker;
  evidence.stages.A = {
    status: "passed",
    source: "bootstrap_real_edge",
    checks: [
      "null score preserved",
      "rubric blocked",
      "teacher repaired score",
    ],
  };
  evidence.stages.D = {
    status: "passed",
    source: "bootstrap_real_edge",
    checks: ["split", "merge", "OCR/review/finalize continued"],
  };
  evidence.objects.snapshot_v1_ids = bootstrap.objects.complete_snapshot_ids;
  evidence.objects.release_v1_id = bootstrap.objects.grade_release_id;
  evidence.objects.analytics_v1_id = bootstrap.objects.analytics_snapshot_id;
  return {
    bootstrapped: true,
    fixture: {
      email: teacherEmail,
      password,
      marker: bootstrap.synthetic_marker,
      assignment_id: bootstrap.objects.assignment_id,
      assignment_name: bootstrap.objects.assignment_name,
      batch_id: bootstrap.objects.grading_batch_id,
      ambiguous_filename: ambiguousFilename,
      ambiguous_file: ambiguousFile,
      ambiguous_student_label: `${firstNumber} · 合成学生甲-${runId}`,
    },
  };
}

const { fixture, bootstrapped } = bootstrapFixture();
for (const field of [
  "email",
  "password",
  "assignment_id",
  "assignment_name",
  "batch_id",
  "ambiguous_filename",
  "ambiguous_student_label",
]) {
  if (!fixture[field]) throw new Error(`Fixture field missing: ${field}`);
}
if (
  !String(fixture.email).endsWith(".synthetic.invalid") ||
  !String(fixture.marker).includes("business-exceptions.synthetic.invalid")
) {
  throw new Error("Fixture is outside the required synthetic namespace");
}

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.EDGE_EXECUTABLE ??
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});
const context = await browser.newContext();
const page = await context.newPage();
let currentStage = "login";

async function record(name, action) {
  currentStage = name;
  evidence.stages[name] = { status: "running" };
  await action();
  evidence.stages[name].status = "passed";
}

try {
  await record("login", async () => {
    await page.goto(`${base}/login`);
    await page.getByLabel("邮箱").fill(fixture.email);
    await page.getByLabel("密码").fill(fixture.password);
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("**/dashboard");
  });

  if (!bootstrapped)
    await record("A", async () => {
      await page.goto(`${base}/assignments/${fixture.assignment_id}/edit`);
      await page.getByRole("button", { name: /步骤 5/ }).click();
      await page
        .getByText("当前题目分值未知，Rubric 保存和发布会被阻止。")
        .waitFor();
      const prompt = page.waitForEvent("dialog");
      const click = page
        .getByRole("button", { name: "补齐所选题目分值" })
        .click();
      await (await prompt).accept(String(fixture.missing_question_score ?? 5));
      await click;
      await page.getByText("题目分值已补齐，可以继续设置 Rubric").waitFor();
    });

  await record("C", async () => {
    await page.goto(`${base}/grading/${fixture.batch_id}`);
    if (fixture.ambiguous_file) {
      await page
        .getByLabel("选择学生作业")
        .setInputFiles(fixture.ambiguous_file);
      await page.getByRole("button", { name: "上传并自动匹配" }).click();
      await page.getByText(/学生作业已上传/).waitFor();
    }
    await page
      .getByLabel(`为 ${fixture.ambiguous_filename} 选择学生`)
      .selectOption({ label: fixture.ambiguous_student_label });
    await page.getByRole("button", { name: "人工确认匹配" }).click();
    await page.getByText("匹配已由教师通过 UI 明确确认").waitFor();
  });

  if (!bootstrapped)
    await record("D", async () => {
      await page.getByRole("button", { name: "拆出末页" }).first().click();
      await page.getByText("Submission 已拆分且原始上传文件保持不变").waitFor();
      await page
        .getByRole("button", { name: "合并回首次 Submission" })
        .first()
        .click();
      await page.getByText("Submission 已合并且页码重新连续编号").waitFor();
    });

  await record("E_F", async () => {
    await page.goto(`${base}/assignments/${fixture.assignment_id}/edit`);
    await page.getByRole("button", { name: /步骤 5/ }).click();
    await page.getByLabel("当前题目").selectOption({ index: 0 });
    await page
      .getByLabel("标准答案")
      .fill("教师人工判断（Rubric v2 合成修订）");
    await page.getByRole("button", { name: "保存本题评分标准" }).click();
    await page.getByText("评分标准已保存").waitFor();
    await page.getByRole("button", { name: "进入发布检查" }).click();
    await page.getByText("后端检查通过，可以发布。").waitFor();
    await page.getByRole("button", { name: "发布作业" }).click();
    await page.waitForURL(`**/assignments/${fixture.assignment_id}`);

    await page.goto(`${base}/grading/${fixture.batch_id}/review`);
    await page.getByRole("heading", { name: "教师评分复核" }).waitFor();
    const staleSubmissions = page
      .getByRole("navigation", { name: "复核导航" })
      .locator("button")
      .filter({ hasText: /^提交/, hasNotText: "merged" })
      .filter({ hasNotText: "recognized" });
    let staleFound = false;
    for (let index = 0; index < (await staleSubmissions.count()); index++) {
      await staleSubmissions.nth(index).click();
      await page.waitForTimeout(150);
      const staleAnswer = page
        .getByRole("navigation", { name: "复核导航" })
        .locator("button")
        .filter({ hasText: /· stale/ })
        .first();
      if (await staleAnswer.count()) {
        await staleAnswer.click();
        await page.getByTestId("regrade-required").waitFor();
        staleFound = true;
        break;
      }
    }
    if (!staleFound) throw new Error("STALE_REVIEW_PROMPT_NOT_FOUND");
    if (
      await page.getByRole("button", { name: "接受", exact: true }).isEnabled()
    )
      throw new Error("STALE_RESULT_ACCEPT_ENABLED");
    evidence.objects.finalize_ui_gate = await page
      .getByRole("button", { name: "完成全部 finalize" })
      .isDisabled();

    await page.goto(`${base}/grading/${fixture.batch_id}`);
    await page.getByRole("button", { name: "仅重新批改 stale 答案" }).click();
    await page.getByText("已为 stale 答案创建新的评分结果").waitFor();
    await page.getByRole("link", { name: "进入三栏教师复核" }).click();
    await page.getByRole("heading", { name: "教师评分复核" }).waitFor();
    const submissionButtons = page
      .getByRole("navigation", { name: "复核导航" })
      .locator("button")
      .filter({ hasText: /^提交/, hasNotText: "merged" })
      .filter({ hasNotText: "recognized" });
    let reviewedSubmissionIndex = 0;
    for (
      let submissionIndex = 0;
      submissionIndex < (await submissionButtons.count());
      submissionIndex++
    ) {
      await submissionButtons.nth(submissionIndex).click();
      const answerButtons = page
        .getByRole("navigation", { name: "复核导航" })
        .locator("button")
        .filter({ hasText: /^第 \d+ 题/ });
      if ((await answerButtons.count()) === 0) continue;
      for (
        let answerIndex = 0;
        answerIndex < (await answerButtons.count());
        answerIndex++
      ) {
        const targetAnswerId = await answerButtons
          .nth(answerIndex)
          .getAttribute("data-answer-id");
        if (!targetAnswerId)
          throw new Error(`ANSWER_ID_MISSING:${submissionIndex}:${answerIndex}`);
        await answerButtons.nth(answerIndex).click();
        const panel = page.getByTestId("review-answer");
        await page.waitForFunction(
          ({ expected }) =>
            document
              .querySelector('[data-testid="review-answer"]')
              ?.getAttribute("data-answer-id") === expected,
          { expected: targetAnswerId },
        );
        if (
          (await panel.getAttribute("data-answer-status")) === "stale" ||
          (await panel.getAttribute("data-result-status")) !== "suggested"
        ) {
          await page.getByRole("button", { name: "重新批改" }).click();
          await page
            .locator(
              '[data-testid="review-answer"][data-answer-status="graded"][data-result-status="suggested"]',
            )
            .waitFor();
        }
        const type = await panel.getAttribute("data-question-type");
        if (
          [
            "single_choice",
            "multiple_choice",
            "true_false",
            "fill_blank",
          ].includes(type ?? "")
        ) {
          await page.getByRole("button", { name: "接受", exact: true }).click();
        } else {
          const scorePromise = page.waitForEvent("dialog");
          const clickPromise = page
            .getByRole("button", { name: "手动评分" })
            .click();
          const score = await scorePromise;
          const feedbackPromise = page.waitForEvent("dialog");
          await score.accept(reviewedSubmissionIndex === 0 ? "2" : "3");
          const feedback = await feedbackPromise;
          await feedback.accept("合成 Rubric v2 人工复核");
          await clickPromise;
        }
        await page.getByText("复核结果已保存").waitFor();
      }
      reviewedSubmissionIndex++;
    }
    if (reviewedSubmissionIndex !== 2)
      throw new Error(
        `EXPECTED_TWO_GRADED_SUBMISSIONS:${reviewedSubmissionIndex}`,
      );
    const progress = page.getByText(/进度 \d+\/\d+/).first();
    await progress.waitFor();
    evidence.objects.regrade_progress = await progress.innerText();
    const finalizeButton = page.getByRole("button", {
      name: "完成全部 finalize",
    });
    evidence.objects.finalize_v2_enabled = await finalizeButton.isEnabled();
    if (evidence.objects.finalize_v2_enabled) {
      await finalizeButton.click();
      await page.getByText(/finalize 已阻止/).waitFor();
      evidence.objects.snapshot_v2_ids = await page
        .locator('[data-testid="score-snapshot"][data-status="complete"]')
        .evaluateAll((nodes) =>
          nodes.map((node) => node.getAttribute("data-snapshot-id")),
        );
    } else {
      evidence.objects.snapshot_v2_ids = [];
      evidence.objects.finalize_v2_blocked_by_progress = true;
    }
  });

  await record("G", async () => {
    await page.getByRole("link", { name: "返回批次工作台" }).click();
    await page.getByRole("button", { name: "查看 grade readiness" }).click();
    await page.getByText(/可发布/).waitFor();
    await page
      .getByRole("button", { name: "创建新的 GradeRelease 版本" })
      .click();
    await page.getByText(/GradeRelease 已创建/).waitFor();
    const versions = page.getByTestId("grade-release-version");
    if ((await versions.count()) < 2)
      throw new Error("TWO_GRADE_RELEASE_VERSIONS_REQUIRED");
    evidence.objects.release_versions = await versions.evaluateAll((nodes) =>
      nodes.map((node) => ({
        id: node.getAttribute("data-release-id"),
        version: node.getAttribute("data-release-version"),
        text: node.textContent?.trim(),
      })),
    );
    await page.getByRole("button", { name: "生成 XLSX" }).click();
    await page.getByText(/gradebook_xlsx 报告任务已完成/).waitFor({
      timeout: 150_000,
    });
  });

  await record("H", async () => {
    await page.goto(`${base}/analytics`);
    await page
      .getByLabel("作业")
      .selectOption({ label: fixture.assignment_name });
    const releaseOptions = page.getByLabel("发布版本").locator("option");
    if ((await releaseOptions.count()) < 3)
      throw new Error("TWO_ANALYTICS_RELEASE_OPTIONS_REQUIRED");
    const analyticsIds = [];
    for (const index of [1, 2]) {
      await page.getByLabel("发布版本").selectOption({ index });
      await page.getByRole("button", { name: /生成 \/ 刷新分析/ }).click();
      const metrics = page.getByTestId("analytics-metrics");
      await metrics.waitFor();
      analyticsIds.push(await metrics.getAttribute("data-snapshot-id"));
    }
    evidence.objects.analytics_snapshot_ids = analyticsIds;
    await page.getByRole("button", { name: "生成规则建议" }).click();
    await page.getByLabel("建议内容").fill("合成异常业务教学建议（教师编辑）");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await page.getByText("建议草稿已保存").waitFor();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByText("建议已确认").waitFor();
    await page.getByRole("button", { name: "重新生成" }).click();
    await page.getByText("已生成新的规则建议").waitFor();
    await page.getByRole("button", { name: "标记失效" }).click();
    await page.getByText("建议已标记失效").waitFor();
  });

  evidence.result = "partial";
} catch (error) {
  evidence.failure = {
    stage: currentStage,
    code: error instanceof Error ? error.message.slice(0, 240) : "UNKNOWN",
    url: page.url(),
  };
  evidence.stages[currentStage] = { status: "failed" };
  await page
    .screenshot({
      path: path.resolve(
        "test-results",
        "business-exceptions",
        `failure-${currentStage}-${Date.now()}.png`,
      ),
      fullPage: true,
      timeout: 10_000,
    })
    .catch(() => undefined);
  throw error;
} finally {
  persist();
  await browser.close();
}

console.log(
  "BUSINESS_EXCEPTIONS_BROWSER_E2E_PARTIAL report_retry_browser=not_run",
);

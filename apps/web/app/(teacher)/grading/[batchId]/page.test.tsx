import { Suspense } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import GradingBatchPage from "./page";

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, use: () => ({ batchId: "b1" }) };
});

const mocks = vi.hoisted(() => ({
  getBatch: vi.fn(),
  submissions: vi.fn(),
  reviewWorkspace: vi.fn(),
  grade: vi.fn(),
  startRecognition: vi.fn(),
  recognition: vi.fn(),
  upload: vi.fn(),
  confirmMatch: vi.fn(),
  releases: vi.fn(),
  readiness: vi.fn(),
  reports: vi.fn(),
  retryReport: vi.fn(),
  continueProcessing: vi.fn(),
  retryProcessing: vi.fn(),
  reconcileProcessing: vi.fn(),
  publishToStudents: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode;
    href: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/submission-segmentation-workspace", () => ({
  SubmissionSegmentationWorkspace: ({
    submissionId,
  }: {
    submissionId: string;
  }) => (
    <div
      data-testid="segmentation-workspace"
      data-submission-id={submissionId}
    />
  ),
}));
vi.mock("@/lib/api", () => ({
  gradingApi: {
    getBatch: mocks.getBatch,
    submissions: mocks.submissions,
    reviewWorkspace: mocks.reviewWorkspace,
    confirmMatch: mocks.confirmMatch,
    upload: mocks.upload,
    startRecognition: mocks.startRecognition,
    recognition: mocks.recognition,
    reorderPages: vi.fn(),
    splitSubmission: vi.fn(),
    mergeSubmission: vi.fn(),
    grade: mocks.grade,
    regrade: vi.fn(),
    continueProcessing: mocks.continueProcessing,
    retryProcessing: mocks.retryProcessing,
    reconcileProcessing: mocks.reconcileProcessing,
  },
  submissionProcessingApi: {
    pages: vi.fn().mockResolvedValue([]),
    regions: vi.fn().mockResolvedValue([]),
    incomplete: vi.fn().mockResolvedValue([]),
    pageImage: vi.fn(),
    createRegion: vi.fn(),
    updateRegion: vi.fn(),
    deleteRegion: vi.fn(),
    confirmRegions: vi.fn(),
  },
  analyticsApi: {
    releases: mocks.releases,
    readiness: mocks.readiness,
    reports: mocks.reports,
    createReport: vi.fn(),
    report: vi.fn(),
    reportDownload: vi.fn(),
    retryReport: mocks.retryReport,
    publishToStudents: mocks.publishToStudents,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const processingRun = (overrides: Record<string, unknown> = {}) => ({
  id: "run-1",
  grading_batch_id: "b1",
  generation: 1,
  status: "waiting_codex",
  provider: "codex_local",
  provider_label: "Codex-assisted",
  suggestion_only: true,
  target_state: "awaiting_teacher_review",
  input_version: "input-v1",
  request_hash: "request-hash",
  input_manifest: {},
  submission_count: 1,
  step_count: 1,
  completed_step_count: 0,
  failed_step_count: 0,
  pending_codex_count: 1,
  retryable: true,
  steps: [],
  ...overrides,
});

it("confirms an ambiguous match and keeps historical release snapshots selectable", async () => {
  const batch = {
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "异常业务批次",
    matching: {
      items: [
        {
          id: "m1",
          filename: "unknown.png",
          status: "pending",
          method: "ambiguous",
          reason: "MULTIPLE_CANDIDATES",
        },
      ],
      student_options: [
        { id: "s1", student_number: "001", name: "合成学生甲" },
      ],
    },
  };
  const releases = [
    {
      id: "r2",
      class_id: "c1",
      version: 2,
      status: "released",
      meaning: "score_and_feedback",
      items: [{ student_id: "s1", score_snapshot_id: "snap-2" }],
    },
    {
      id: "r1",
      class_id: "c1",
      version: 1,
      status: "released",
      meaning: "score_and_feedback",
      items: [{ student_id: "s1", score_snapshot_id: "snap-1" }],
    },
  ];
  mocks.getBatch.mockResolvedValue(batch);
  mocks.submissions.mockResolvedValue([]);
  mocks.releases.mockResolvedValue(releases);
  mocks.reports.mockResolvedValue([]);
  mocks.confirmMatch.mockResolvedValue({});
  mocks.upload.mockResolvedValue({});

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );

  const select = await screen.findByLabelText("为 unknown.png 选择学生");
  const uploadPanel = screen.getByTestId("submission-upload-panel");
  const fileInput = screen.getByLabelText("选择学生作业");
  const filePicker = screen.getByTestId("submission-file-picker");
  expect(uploadPanel).toContainElement(fileInput);
  expect(fileInput).toHaveAttribute("name", "files");
  expect(fileInput).toHaveAttribute("type", "file");
  expect(fileInput).toHaveAttribute("multiple");
  expect(fileInput).toHaveAttribute("accept", ".png,.jpg,.jpeg,.pdf");
  expect(fileInput).toHaveClass("sr-only");
  expect(filePicker).toHaveAttribute("for", "submission-files");
  expect(filePicker).toHaveClass("border");
  expect(filePicker).toHaveTextContent("选择文件");
  expect(uploadPanel).toContainElement(
    screen.getByRole("button", { name: "上传并自动匹配" }),
  );
  expect(screen.getByRole("heading", { name: "学生作业" })).toBeInTheDocument();
  expect(screen.queryByText("未选择任何文件")).not.toBeInTheDocument();
  const selectedFiles = [
    new File(["first"], "001-张三.pdf", { type: "application/pdf" }),
    new File(["second"], "002-李四.png", { type: "image/png" }),
  ];
  fireEvent.change(fileInput, { target: { files: selectedFiles } });
  expect(screen.getByTestId("submission-file-selection")).toHaveTextContent(
    "已选择 2 个文件",
  );
  expect(screen.getByTestId("submission-file-selection")).toHaveTextContent(
    "001-张三.pdf",
  );
  fireEvent.submit(uploadPanel);
  await waitFor(() =>
    expect(mocks.upload).toHaveBeenCalledWith("b1", selectedFiles),
  );
  expect(
    await screen.findByTestId("submission-upload-status"),
  ).toHaveTextContent("上传完成，已刷新匹配结果");
  expect(
    screen.queryByTestId("submission-file-selection"),
  ).not.toBeInTheDocument();
  fireEvent.change(select, { target: { value: "s1" } });
  fireEvent.click(screen.getByRole("button", { name: "人工确认匹配" }));
  await waitFor(() =>
    expect(mocks.confirmMatch).toHaveBeenCalledWith("b1", "m1", "s1"),
  );
  expect(
    screen.getByText(/第 2 版 · 已发布 · 已确认 1 份/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/第 1 版 · 已发布 · 已确认 1 份/),
  ).toBeInTheDocument();
  expect(screen.getByText(/成绩快照 snap-2/)).toHaveClass("sr-only");
  expect(screen.getByText(/成绩快照 snap-1/)).toHaveClass("sr-only");
});

it("keeps result confirmation in the teacher review flow", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "版本批次",
    submission_count: 1,
    workflow: {
      stage_counts: {},
      completed_count: 1,
      blocked_count: 0,
      blocked: [],
    },
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([
    {
      id: "submission-1",
      student_id: "student-1",
      status: "finalized",
      attempt_number: 1,
      page_count: 1,
      workflow: {
        stage: "completed",
        stage_label: "处理完成",
        reason: "可以复核。",
        action: "进入教师复核",
      },
    },
  ]);
  mocks.reviewWorkspace.mockResolvedValue({ items: [] });
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);
  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );
  expect(await screen.findByText(/尚未确认正式结果/)).toBeInTheDocument();
  expect(
    screen.getByTestId("open-teacher-review").closest("a"),
  ).toHaveAttribute("href", "/grading/b1/review");
  expect(
    screen.queryByRole("button", { name: "检查成绩是否可发布" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "创建新的成绩发布版本" }),
  ).not.toBeInTheDocument();
  expect(mocks.readiness).not.toHaveBeenCalled();
});

it("creates a replacement report job once and keeps the failed job terminal", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "报告重试批次",
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([]);
  mocks.releases.mockResolvedValue([
    {
      id: "r1",
      class_id: "c1",
      version: 1,
      status: "released",
      meaning: "score_and_feedback",
      items: [{ student_id: "s1", score_snapshot_id: "snap-1" }],
    },
  ]);
  mocks.reports.mockResolvedValue([
    {
      id: "failed-1",
      report_type: "student_report_pdf",
      student_id: "s1",
      status: "failed",
      progress: 0,
      error_code: "SYNTHETIC_PROVIDER_FAILURE",
      grade_release_id: "r1",
    },
  ]);
  mocks.retryReport.mockResolvedValue({
    id: "replacement-1",
    report_type: "student_report_pdf",
    student_id: "s1",
    status: "queued",
    progress: 0,
    grade_release_id: "r1",
  });

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );
  const retry = await screen.findByRole("button", {
    name: "创建新任务重试",
  });
  fireEvent.click(retry);
  await waitFor(() =>
    expect(mocks.retryReport).toHaveBeenCalledWith("failed-1"),
  );
  expect(await screen.findByText(/学生报告 · 失败 · 0%/)).toBeInTheDocument();
  expect(screen.getByText(/学生报告 · 排队中 · 0%/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "已创建重试任务" })).toBeDisabled();
});

it("shows the batch progress, blocker reason, and each submission next action", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "识别进度批次",
    submission_count: 1,
    recognized_count: 0,
    graded_count: 0,
    reviewed_count: 0,
    failed_count: 0,
    workflow: {
      stage_counts: { recognition: 1 },
      completed_count: 0,
      blocked_count: 1,
      blocked: [
        {
          stage: "recognition",
          stage_label: "等待答案识别",
          reason_code: "RECOGNITION_PENDING",
          reason: "1 页尚未完成识别。",
          action: "启动或重试答案识别",
          count: 1,
        },
      ],
    },
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([
    {
      id: "submission-1",
      student_id: "student-1",
      student_name: "合成学生甲",
      student_number: "S001",
      status: "processing",
      attempt_number: 1,
      page_count: 1,
      workflow: {
        stage: "recognition",
        stage_label: "等待答案识别",
        reason_code: "RECOGNITION_PENDING",
        reason: "1 页尚未完成识别。",
        action: "启动或重试答案识别",
      },
    },
  ]);
  mocks.reviewWorkspace.mockResolvedValue({ items: [] });
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );

  expect(
    await screen.findByTestId("batch-progress-overview"),
  ).toHaveTextContent("0/1 份已完成处理");
  expect(screen.getByTestId("batch-blocker")).toHaveTextContent(
    "等待答案识别 · 1 份",
  );
  expect(screen.getByTestId("submission-workflow")).toHaveTextContent(
    "启动或重试答案识别",
  );
  expect(
    screen.getByRole("heading", { name: "合成学生甲" }),
  ).toBeInTheDocument();
  expect(screen.getByText("学号 S001 · 1 页")).toBeInTheDocument();
  expect(screen.getByTestId("submission-ocr-start")).toBeEnabled();
  expect(
    screen.getByText("高级处理工具").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByText("页面与高级操作").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByTestId("continue-processing-to-teacher-review"),
  ).toHaveTextContent("继续处理");
  expect(screen.queryByText("服务端连续处理")).not.toBeInTheDocument();
});

it("prepares grading inputs once and exposes a stable teacher-review entry", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "评分输入批次",
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([
    {
      id: "submission-1",
      student_id: "student-1",
      status: "recognized",
      attempt_number: 1,
      page_count: 1,
      workflow: {
        stage: "grading",
        stage_label: "等待评分输入",
        reason: "答案已识别。",
        action: "准备并检查评分输入",
      },
    },
  ]);
  mocks.reviewWorkspace.mockResolvedValue({
    items: [
      {
        submission_id: "submission-1",
        status: "recognized",
        pages: [],
        answers: [{ id: "answer-1" }],
      },
    ],
  });
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);
  mocks.grade.mockResolvedValue({});

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );

  const prepare = await screen.findByTestId("prepare-grading-inputs");
  await waitFor(() => expect(prepare).toBeEnabled());
  expect(screen.getByTestId("open-teacher-review")).toBeEnabled();
  fireEvent.click(prepare);

  await waitFor(() => expect(mocks.grade).toHaveBeenCalledTimes(1));
  expect(mocks.grade).toHaveBeenCalledWith("answer-1");
  expect(mocks.reviewWorkspace).toHaveBeenCalledTimes(3);
});

it("allows processing only for active submissions and skips every terminal status", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "合并后识别批次",
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([
    {
      id: "submission-active",
      student_id: "student-1",
      status: "processing",
      attempt_number: 1,
      page_count: 2,
      workflow: {
        stage: "recognition",
        stage_label: "等待答案识别",
        reason: "页面已准备。",
        action: "启动答案识别",
      },
    },
    {
      id: "submission-merged",
      student_id: "student-1",
      status: "merged",
      attempt_number: 2,
      page_count: 0,
      workflow: {
        stage: "pages",
        stage_label: "等待页面处理",
        reason: "已合并。",
        action: "无需处理",
      },
    },
  ]);
  const existingSubmissions = await mocks.submissions();
  mocks.submissions.mockResolvedValue([
    ...existingSubmissions,
    {
      id: "submission-voided",
      student_id: "student-2",
      status: "voided",
      attempt_number: 1,
      page_count: 2,
      workflow: {
        stage: "pages",
        stage_label: "Voided",
        reason: "Voided",
        action: "No processing",
      },
    },
    {
      id: "submission-finalized",
      student_id: "student-3",
      status: "finalized",
      attempt_number: 1,
      page_count: 2,
      workflow: {
        stage: "completed",
        stage_label: "Finalized",
        reason: "Finalized",
        action: "No processing",
      },
    },
  ]);
  mocks.reviewWorkspace.mockResolvedValue({ items: [] });
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);
  mocks.startRecognition.mockResolvedValue({
    id: "recognition-1",
    submission_id: "submission-active",
    status: "completed",
    provider: "fake",
    provider_version: "answer-evidence-1",
    config_version: "answer-evidence-v1",
    pages: [],
  });

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );

  fireEvent.click(await screen.findByTestId("submission-ocr-start"));
  for (const status of ["merged", "voided", "finalized"]) {
    expect(
      screen
        .getAllByTestId("submission")
        .find(
          (item) =>
            item.getAttribute("data-submission-id") === `submission-${status}`,
        ),
    ).toHaveAttribute("data-status", status);
  }
  expect(screen.getAllByTestId("segmentation-workspace")).toHaveLength(1);
  expect(screen.getByTestId("segmentation-workspace")).toHaveAttribute(
    "data-submission-id",
    "submission-active",
  );
  expect(screen.getByTestId("submission-cards")).toHaveClass("grid");
  expect(screen.getByTestId("submission-cards")).not.toHaveClass(
    "md:grid-cols-2",
  );
  await waitFor(() =>
    expect(mocks.startRecognition).toHaveBeenCalledWith("submission-active"),
  );
  expect(mocks.startRecognition).toHaveBeenCalledTimes(1);
  for (const status of ["merged", "voided", "finalized"]) {
    expect(mocks.startRecognition).not.toHaveBeenCalledWith(
      `submission-${status}`,
    );
  }
});

function setupProcessingPage() {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "连续处理批次",
    submission_count: 1,
    workflow: {
      stage_counts: { recognition: 1 },
      completed_count: 0,
      blocked_count: 1,
      blocked: [
        {
          stage: "recognition",
          stage_label: "等待答案识别",
          reason: "答案尚未识别。",
          action: "继续处理",
          count: 1,
        },
      ],
    },
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([
    {
      id: "submission-1",
      student_id: "student-1",
      status: "processing",
      attempt_number: 1,
      page_count: 1,
      workflow: {
        stage: "recognition",
        stage_label: "等待答案识别",
        reason: "答案尚未识别。",
        action: "继续处理",
      },
    },
  ]);
  mocks.reviewWorkspace.mockResolvedValue({ items: [] });
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);
  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );
}

it("starts one server-side plan and labels Codex output as suggestion-only", async () => {
  mocks.continueProcessing.mockResolvedValue(processingRun());
  setupProcessingPage();

  fireEvent.click(
    await screen.findByTestId("continue-processing-to-teacher-review"),
  );

  await waitFor(() =>
    expect(mocks.continueProcessing).toHaveBeenCalledTimes(1),
  );
  expect(mocks.continueProcessing).toHaveBeenCalledWith(
    "b1",
    expect.any(String),
  );
  expect(await screen.findByTestId("processing-run-status")).toHaveTextContent(
    "正在评分",
  );
  expect(screen.getByTestId("processing-run-status")).toHaveTextContent(
    "suggestion-only",
  );
  expect(screen.queryByTestId("open-teacher-review")).not.toBeInTheDocument();
  expect(screen.getByText("技术详情")).toBeInTheDocument();
});

it("keeps reconciling active processing states until teacher review and then stops", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mocks.continueProcessing.mockResolvedValue(
      processingRun({ status: "queued", pending_codex_count: 0 }),
    );
    mocks.reconcileProcessing
      .mockResolvedValueOnce(
        processingRun({ status: "running", pending_codex_count: 0 }),
      )
      .mockResolvedValueOnce(processingRun({ status: "waiting_codex" }))
      .mockResolvedValueOnce(
        processingRun({
          status: "awaiting_teacher_review",
          completed_step_count: 1,
          pending_codex_count: 0,
        }),
      );
    setupProcessingPage();

    fireEvent.click(
      await screen.findByTestId("continue-processing-to-teacher-review"),
    );
    await waitFor(() =>
      expect(mocks.continueProcessing).toHaveBeenCalledTimes(1),
    );

    for (let expectedCalls = 1; expectedCalls <= 3; expectedCalls += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      await waitFor(() =>
        expect(mocks.reconcileProcessing).toHaveBeenCalledTimes(expectedCalls),
      );
    }

    expect(screen.getByTestId("processing-run-status")).toHaveTextContent(
      "等待教师复核",
    );
    expect(
      screen.getByTestId("open-teacher-review").closest("a"),
    ).toHaveAttribute("href", "/grading/b1/review");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(mocks.reconcileProcessing).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
  }
});

it("falls back to safe copy for an unknown processing status", async () => {
  mocks.continueProcessing.mockResolvedValue(
    processingRun({ status: "future_processing_status" }),
  );
  setupProcessingPage();

  fireEvent.click(
    await screen.findByTestId("continue-processing-to-teacher-review"),
  );

  expect(await screen.findByTestId("processing-run-status")).toHaveTextContent(
    "请稍候。",
  );
});

it("retries only retryable failed processing steps with the current generation", async () => {
  const failed = processingRun({
    status: "partially_failed",
    pending_codex_count: 0,
    failed_step_count: 1,
    steps: [
      {
        id: "step-b",
        submission_id: "submission-1",
        scope_key: "answer:b",
        kind: "codex_suggestion",
        status: "retryable_failed",
        generation: 1,
        attempt: 1,
        max_attempts: 3,
        retryable: true,
        error_code: "CODEX_TEMPORARY_FAILURE",
      },
      {
        id: "step-a",
        submission_id: "submission-1",
        scope_key: "answer:a",
        kind: "review_readiness",
        status: "blocked_review",
        generation: 1,
        attempt: 0,
        max_attempts: 3,
        retryable: false,
      },
    ],
  });
  mocks.continueProcessing.mockResolvedValue(failed);
  mocks.retryProcessing.mockResolvedValue(
    processingRun({ id: "run-2", generation: 2 }),
  );
  setupProcessingPage();

  fireEvent.click(
    await screen.findByTestId("continue-processing-to-teacher-review"),
  );
  fireEvent.click(await screen.findByRole("button", { name: "重试失败步骤" }));

  await waitFor(() =>
    expect(mocks.retryProcessing).toHaveBeenCalledWith("b1", "run-1", {
      idempotency_key: expect.any(String),
      expected_generation: 1,
      step_ids: ["step-b"],
    }),
  );
});

it("shows a submission-level processing blocker only once", async () => {
  const message = "Every answer must have exactly one current region";
  mocks.continueProcessing.mockResolvedValue(
    processingRun({
      status: "waiting_input",
      steps: Array.from({ length: 5 }, (_, index) => ({
        id: `step-${index}`,
        submission_id: "submission-1",
        scope_key: `answer:${index}`,
        kind: "recognition",
        status: "blocked_review",
        generation: 1,
        attempt: 0,
        max_attempts: 3,
        retryable: false,
        error_code: "SEGMENTATION_AMBIGUOUS",
        error_message: message,
      })),
    }),
  );
  setupProcessingPage();

  fireEvent.click(
    await screen.findByTestId("continue-processing-to-teacher-review"),
  );

  expect(await screen.findAllByText(message)).toHaveLength(1);
});

it("links to explicit teacher review without presenting suggestions as final grades", async () => {
  mocks.continueProcessing.mockResolvedValue(
    processingRun({
      status: "awaiting_teacher_review",
      completed_step_count: 1,
      pending_codex_count: 0,
      steps: [
        {
          id: "step-1",
          submission_id: "submission-1",
          scope_key: "answer:1",
          kind: "codex_suggestion",
          status: "succeeded",
          generation: 1,
          attempt: 1,
          max_attempts: 3,
          retryable: false,
        },
      ],
    }),
  );
  setupProcessingPage();

  fireEvent.click(
    await screen.findByTestId("continue-processing-to-teacher-review"),
  );

  const reviewButton = await screen.findByTestId("open-teacher-review");
  expect(reviewButton.closest("a")).toHaveAttribute(
    "href",
    "/grading/b1/review",
  );
  expect(reviewButton).toHaveTextContent("检查结果");
});

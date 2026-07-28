import { Suspense } from "react";
import {
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
  confirmMatch: vi.fn(),
  releases: vi.fn(),
  readiness: vi.fn(),
  createRelease: vi.fn(),
  reports: vi.fn(),
  retryReport: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));
vi.mock("@/components/submission-segmentation-workspace", () => ({
  SubmissionSegmentationWorkspace: () => (
    <div data-testid="segmentation-workspace" />
  ),
}));
vi.mock("@/lib/api", () => ({
  gradingApi: {
    getBatch: mocks.getBatch,
    submissions: mocks.submissions,
    reviewWorkspace: mocks.reviewWorkspace,
    confirmMatch: mocks.confirmMatch,
    upload: vi.fn(),
    startRecognition: vi.fn(),
    recognition: vi.fn(),
    reorderPages: vi.fn(),
    splitSubmission: vi.fn(),
    mergeSubmission: vi.fn(),
    grade: vi.fn(),
    regrade: vi.fn(),
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
    createRelease: mocks.createRelease,
    reports: mocks.reports,
    createReport: vi.fn(),
    report: vi.fn(),
    reportDownload: vi.fn(),
    retryReport: mocks.retryReport,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );

  const select = await screen.findByLabelText("为 unknown.png 选择学生");
  fireEvent.change(select, { target: { value: "s1" } });
  fireEvent.click(screen.getByRole("button", { name: "人工确认匹配" }));
  await waitFor(() =>
    expect(mocks.confirmMatch).toHaveBeenCalledWith("b1", "m1", "s1"),
  );
  expect(
    screen.getByText(/第 2 版 · 已发布 · 成绩快照 snap-2/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/第 1 版 · 已发布 · 成绩快照 snap-1/),
  ).toBeInTheDocument();
});

it("allows a new release version after readiness succeeds", async () => {
  mocks.getBatch.mockResolvedValue({
    id: "b1",
    assignment_id: "a1",
    class_id: "c1",
    name: "版本批次",
    matching: { items: [], student_options: [] },
  });
  mocks.submissions.mockResolvedValue([]);
  mocks.releases.mockResolvedValue([]);
  mocks.reports.mockResolvedValue([]);
  mocks.readiness.mockResolvedValue({
    releasable_count: 1,
    unreleasable_count: 0,
  });
  mocks.createRelease.mockResolvedValue({
    id: "r1",
    class_id: "c1",
    version: 1,
    status: "released",
    meaning: "score_and_feedback",
    items: [{ student_id: "s1", score_snapshot_id: "snap-1" }],
  });

  render(
    <Suspense fallback={<div>测试加载中</div>}>
      <GradingBatchPage params={Promise.resolve({ batchId: "b1" })} />
    </Suspense>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "检查成绩是否可发布" }),
  );
  const releaseButton = screen.getByRole("button", {
    name: "创建新的成绩发布版本",
  });
  await waitFor(() => expect(releaseButton).toBeEnabled());
  fireEvent.click(releaseButton);
  await waitFor(() => expect(mocks.createRelease).toHaveBeenCalled());
  expect(await screen.findByText(/固定成绩快照 snap-1/)).toBeInTheDocument();
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
  ).toHaveTextContent("已完成 0/1");
  expect(screen.getByTestId("batch-blocker")).toHaveTextContent(
    "等待答案识别 · 1 份",
  );
  expect(screen.getByTestId("submission-workflow")).toHaveTextContent(
    "下一步：启动或重试答案识别",
  );
});

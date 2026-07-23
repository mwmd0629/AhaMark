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
    screen.getByText(/v2 · released · Snapshot snap-2/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/v1 · released · Snapshot snap-1/),
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
    await screen.findByRole("button", { name: "查看 grade readiness" }),
  );
  const releaseButton = screen.getByRole("button", {
    name: "创建新的 GradeRelease 版本",
  });
  await waitFor(() => expect(releaseButton).toBeEnabled());
  fireEvent.click(releaseButton);
  await waitFor(() => expect(mocks.createRelease).toHaveBeenCalled());
  expect(await screen.findByText(/固定快照 snap-1/)).toBeInTheDocument();
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
  expect(
    await screen.findByText(/student_report_pdf · failed · 0%/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/student_report_pdf · queued · 0%/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "已创建重试任务" }),
  ).toBeDisabled();
});

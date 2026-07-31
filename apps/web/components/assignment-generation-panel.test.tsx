import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssignmentGenerationPanel } from "./assignment-generation-panel";
import type { AssignmentGenerationJob, AssignmentRecord } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  listJobs: vi.fn(),
  listRevisions: vi.fn(),
  start: vi.fn(),
  cancel: vi.fn(),
  retryStage: vi.fn(),
  listFieldSuggestions: vi.fn(),
  listFileAnalyses: vi.fn(),
  listPageAnalyses: vi.fn(),
  dispositionField: vi.fn(),
  confirmTotalScore: vi.fn(),
  confirmFileAnalysis: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    assignmentGenerationApi: {
      ...actual.assignmentGenerationApi,
      ...mocks,
    },
  };
});

const revision = {
  id: "revision-1",
  assignment_id: "assignment-1",
  generation_job_id: "job-1",
  revision: 1,
  source_snapshot_hash: "a".repeat(64),
  status: "review_required",
  draft_payload: { generation: 1 },
  risk_summary: { info: 0, warning: 1, blocking: 2 },
  teacher_edit_version: 0,
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z",
};

function job(
  status: AssignmentGenerationJob["status"] = "partial",
): AssignmentGenerationJob {
  return {
    id: "job-1",
    assignment_id: "assignment-1",
    generation: 1,
    status,
    current_stage:
      status === "stale" ? "processing_pages" : "extracting_questions",
    progress: status === "queued" ? 0 : 100,
    source_snapshot_hash: "a".repeat(64),
    provider_mode: "unavailable",
    retryable: true,
    created_at: "2026-07-26T00:00:00Z",
    updated_at: "2026-07-26T00:00:00Z",
    revision,
    stages: [
      {
        id: "stage-1",
        stage: "analyzing",
        stage_generation: 1,
        status: "completed",
        result_payload: {},
      },
      {
        id: "stage-2",
        stage: "processing_pages",
        stage_generation: 1,
        status: "completed",
        result_payload: {},
      },
      {
        id: "stage-3",
        stage: "extracting_questions",
        stage_generation: 1,
        status: "unavailable",
        error_code: "PROVIDER_UNAVAILABLE",
        result_payload: {},
      },
    ],
    issues: [
      {
        id: "issue-1",
        stage: "extracting_questions",
        severity: "blocking",
        code: "PROVIDER_UNAVAILABLE",
        message: "真实 Provider 不可用",
        resolution_status: "open",
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.capabilities.mockResolvedValue({
    enabled: true,
    provider: "unavailable",
    provider_status: "unavailable",
    provider_error_code: "PROVIDER_UNAVAILABLE",
    external_provider_requests: false,
    teacher_start_allowed: true,
    suggestion_only: true,
    real_provider_quality_passed: false,
  });
  mocks.listJobs.mockResolvedValue([job()]);
  mocks.listRevisions.mockResolvedValue([revision]);
  mocks.retryStage.mockResolvedValue(job("queued"));
  mocks.cancel.mockResolvedValue(job("cancelled"));
  mocks.start.mockResolvedValue(job("queued"));
  mocks.listFieldSuggestions.mockResolvedValue([]);
  mocks.listFileAnalyses.mockResolvedValue([]);
  mocks.listPageAnalyses.mockResolvedValue([]);
  mocks.dispositionField.mockResolvedValue({});
  mocks.confirmTotalScore.mockResolvedValue({});
  mocks.confirmFileAnalysis.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("恢复 partial/unavailable、风险与草稿历史并允许单阶段重试", async () => {
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  expect((await screen.findByLabelText("生成状态")).textContent).toContain(
    "部分完成",
  );
  expect(screen.getAllByText("等待 Codex 代生成").length).toBeGreaterThan(0);
  expect(screen.getByText("阻断 2")).toBeInTheDocument();
  expect(screen.getByText("草稿历史版本（1）")).toBeInTheDocument();
  expect(
    screen.getByText(/由 Codex 生成可编辑草稿，不能直接发布作业/),
  ).toBeInTheDocument();
  expect(screen.queryByText("发布作业")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重试此阶段" }));
  await waitFor(() =>
    expect(mocks.retryStage).toHaveBeenCalledWith(
      "job-1",
      "extracting_questions",
    ),
  );
});

it("展示并审查基本信息与文件分析，保持班级和截止时间为教师控制", async () => {
  mocks.listFieldSuggestions.mockResolvedValue([
    {
      id: "suggestion-1",
      field_name: "title",
      suggested_value: "2026 数学期中",
      normalized_value: "2026 数学期中",
      confidence: 0.88,
      evidence: [
        {
          kind: "file_name",
          reference_id: "file-1",
          summary: "来自原始文件名",
        },
      ],
      suggestion_version: 1,
      status: "suggested",
      teacher_edit_version: 0,
    },
  ]);
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-1",
      stored_file_id: "file-1",
      source_snapshot_hash: "a".repeat(64),
      file_name: "第三方参考答案.pdf",
      file_size: 1024,
      detected_mime_type: "application/pdf",
      checksum: "b".repeat(64),
      page_count: 2,
      suggested_role: "reference_answer",
      role_confidence: 0.8,
      suggested_answer_source: "third_party",
      answer_source_confidence: 0.95,
      analysis_status: "suggested",
      evidence: [],
      warning_codes: [
        "ANSWER_SOURCE_CONFIRMATION_REQUIRED",
        "LOW_QUALITY_PAGE",
      ],
      teacher_edit_version: 0,
    },
  ]);
  mocks.listPageAnalyses.mockResolvedValue([
    {
      id: "page-analysis-1",
      paper_page_id: "paper-page-1",
      status: "low_quality",
      missing_page_suspected: false,
      low_quality: true,
      corrupted: false,
      mixed_document_suspected: false,
      warning_codes: ["LOW_QUALITY_PAGE"],
    },
  ]);
  const assignment = {
    id: "assignment-1",
    title: "教师标题",
    status: "draft",
    updated_at: "2026-07-26T00:00:00Z",
    classes: [],
    completeness: { ready: false, next_step: 1, issues: [] },
  } as AssignmentRecord;
  const onReviewInputsChanged = vi.fn();
  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      assignment={assignment}
      onReviewInputsChanged={onReviewInputsChanged}
    />,
  );

  expect(await screen.findByText("AI 建议：2026 数学期中")).toBeInTheDocument();
  expect(screen.getByText("来自原始文件名")).toBeInTheDocument();
  expect(
    screen.getByText(/不会推荐班级，也不会设置截止时间/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/AI\/第三方答案不会被标记为官方答案/),
  ).toBeInTheDocument();
  expect(screen.getAllByText(/LOW_QUALITY_PAGE/).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("基本信息建议")).not.toHaveAttribute("open");
  expect(screen.getByLabelText("文件分析")).not.toHaveAttribute("open");
  expect(screen.getByRole("option", { name: "参考答案" })).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: "第三方答案" }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "接受" }));
  await waitFor(() =>
    expect(mocks.dispositionField).toHaveBeenCalledWith("suggestion-1", {
      action: "accept",
      expected_teacher_edit_version: 0,
      expected_assignment_updated_at: assignment.updated_at,
    }),
  );
  expect(onReviewInputsChanged).toHaveBeenCalledOnce();
});

it("活动任务会轮询且可请求取消", async () => {
  vi.useFakeTimers();
  mocks.listJobs.mockResolvedValue([job("queued")]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  await vi.waitFor(() =>
    expect(
      screen.getByRole("button", { name: "请求取消" }),
    ).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "请求取消" }));
  await vi.waitFor(() => expect(mocks.cancel).toHaveBeenCalledWith("job-1"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(mocks.listJobs.mock.calls.length).toBeGreaterThan(1);
});

it("不会把已过期文件误报为待确认 0 即已完成", async () => {
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-stale",
      stored_file_id: "file-stale",
      source_snapshot_hash: "a".repeat(64),
      file_name: "旧试卷.pdf",
      file_size: 1024,
      detected_mime_type: "application/pdf",
      checksum: "b".repeat(64),
      page_count: 3,
      suggested_role: "question_paper",
      role_confidence: 0.9,
      suggested_answer_source: "not_applicable",
      answer_source_confidence: 1,
      analysis_status: "stale",
      evidence: [],
      warning_codes: [],
      teacher_edit_version: 0,
    },
  ]);

  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  expect(
    await screen.findByText(/已确认 0，待确认 0，已过期 1/),
  ).toBeInTheDocument();
  expect(screen.getByText("旧分析已过期，不能算作已确认")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "重新分析最新内容" }),
  ).toBeInTheDocument();
});

it("无任务时可启动且网络错误会停止轮询并显示重试", async () => {
  const onReviewInputsChanged = vi.fn();
  mocks.listJobs.mockResolvedValueOnce([]);
  mocks.listRevisions.mockResolvedValueOnce([]);
  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      onReviewInputsChanged={onReviewInputsChanged}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "启动生成任务" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(onReviewInputsChanged).toHaveBeenCalledOnce());
  expect(mocks.start.mock.calls[0][1]).not.toHaveProperty("provider_mode");

  mocks.start.mockRejectedValueOnce(new Error("network"));
  fireEvent.click(screen.getByRole("button", { name: "重新生成新版本" }));
  await waitFor(() =>
    expect(screen.getByText(/任务操作失败/)).toBeInTheDocument(),
  );
});

it("仅在活动任务跨入 partial 终态时通知一次且停止轮询", async () => {
  vi.useFakeTimers();
  const onReviewInputsChanged = vi.fn();
  mocks.listJobs
    .mockResolvedValueOnce([job("queued")])
    .mockResolvedValue([job("partial")]);
  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      onReviewInputsChanged={onReviewInputsChanged}
    />,
  );
  await vi.waitFor(() =>
    expect(screen.getByLabelText("生成状态").textContent).toContain(
      "等待 Worker",
    ),
  );

  await vi.advanceTimersByTimeAsync(2000);
  await vi.waitFor(() =>
    expect(screen.getByLabelText("生成状态").textContent).toContain("部分完成"),
  );
  expect(onReviewInputsChanged).toHaveBeenCalledOnce();
  const callsAtTerminal = mocks.listJobs.mock.calls.length;

  await vi.advanceTimersByTimeAsync(6000);
  expect(onReviewInputsChanged).toHaveBeenCalledOnce();
  expect(mocks.listJobs).toHaveBeenCalledTimes(callsAtTerminal);
});

it("展示服务器能力开关并在教师启动被禁用时关闭启动按钮", async () => {
  mocks.listJobs.mockResolvedValueOnce([]);
  mocks.listRevisions.mockResolvedValueOnce([]);
  mocks.capabilities.mockResolvedValueOnce({
    enabled: true,
    provider: "unavailable",
    provider_status: "unavailable",
    provider_error_code: "PROVIDER_UNAVAILABLE",
    external_provider_requests: false,
    teacher_start_allowed: false,
    suggestion_only: true,
    real_provider_quality_passed: false,
  });
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);
  expect(
    await screen.findByText(/当前草稿生成方式：Codex/),
  ).toBeInTheDocument();
  expect(screen.getByText(/不会伪造 Provider 已完成/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "启动生成任务" })).toBeDisabled();
});

it("明确显示 stale 状态且不会轮询终态", async () => {
  mocks.listJobs.mockResolvedValue([job("stale")]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);
  expect((await screen.findByLabelText("生成状态")).textContent).toContain(
    "输入已变化",
  );
  await new Promise((resolve) => window.setTimeout(resolve, 10));
  expect(mocks.listJobs).toHaveBeenCalledTimes(1);
});

it.each([
  ["failed", "失败"],
  ["cancelled", "已取消"],
] as const)("明确显示 %s 终态且不再轮询", async (status, label) => {
  vi.useFakeTimers();
  mocks.listJobs.mockResolvedValue([job(status)]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);
  await vi.waitFor(() =>
    expect(screen.getByLabelText("生成状态").textContent).toContain(label),
  );
  await vi.advanceTimersByTimeAsync(4000);
  expect(mocks.listJobs).toHaveBeenCalledTimes(1);
});

it("卸载活动任务面板时清理轮询定时器", async () => {
  vi.useFakeTimers();
  mocks.listJobs.mockResolvedValue([job("queued")]);
  const view = render(
    <AssignmentGenerationPanel assignmentId="assignment-1" />,
  );
  await vi.waitFor(() => expect(mocks.listJobs).toHaveBeenCalledTimes(1));
  const callsBeforeUnmount = mocks.listJobs.mock.calls.length;
  view.unmount();
  await vi.advanceTimersByTimeAsync(4000);
  expect(mocks.listJobs).toHaveBeenCalledTimes(callsBeforeUnmount);
});

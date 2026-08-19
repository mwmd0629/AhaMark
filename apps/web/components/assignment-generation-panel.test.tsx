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
  listTextbookLibraries: vi.fn(),
  listTextbookLibrarySelections: vi.fn(),
  replaceTextbookLibrarySelections: vi.fn(),
  removeFile: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    assignmentGenerationApi: {
      ...actual.assignmentGenerationApi,
      ...mocks,
    },
    assignmentsApi: {
      ...actual.assignmentsApi,
      removeFile: mocks.removeFile,
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
  mocks.removeFile.mockResolvedValue({
    id: "file-confirmed",
    pages_deleted: 3,
  });
  mocks.listTextbookLibraries.mockResolvedValue([]);
  mocks.listTextbookLibrarySelections.mockResolvedValue([]);
  mocks.replaceTextbookLibrarySelections.mockResolvedValue({
    selected_library_ids: [],
    created_matches: 0,
    draft_revision_edit_version: 1,
  });
});

it("允许教师选择已导入教材并自动触发出处匹配", async () => {
  mocks.listTextbookLibraries.mockResolvedValue([
    {
      id: "library-1",
      title: "数学分析讲义",
      volume_label: "第1册",
      question_count: 358,
      usable_question_count: 300,
      review_question_count: 58,
      status: "ready",
    },
    {
      id: "library-2",
      title: "数学分析讲义",
      volume_label: "第二册",
      question_count: 385,
      usable_question_count: 237,
      review_question_count: 148,
      status: "ready",
    },
  ]);
  mocks.listTextbookLibrarySelections.mockResolvedValue(["library-1"]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  fireEvent.click(await screen.findByText("教材来源（已选 1 册）"));
  fireEvent.click(screen.getByLabelText("数学分析讲义 第二册"));
  fireEvent.click(screen.getByRole("button", { name: "保存教材来源" }));

  await waitFor(() =>
    expect(mocks.replaceTextbookLibrarySelections).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({
        draft_revision_id: "revision-1",
        library_ids: ["library-1", "library-2"],
      }),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("只展示仍需老师处理且属于作业表单的字段问题", async () => {
  const currentJob = job("review_required");
  currentJob.issues = [
    {
      id: "subject-low",
      stage: "analyzing",
      severity: "warning",
      code: "BASIC_INFO_LOW_CONFIDENCE",
      message: "subject 建议置信度较低，需教师确认",
      resolution_status: "open",
    },
    {
      id: "year-low",
      stage: "analyzing",
      severity: "warning",
      code: "BASIC_INFO_LOW_CONFIDENCE",
      message: "academic_year 建议置信度较低，需教师确认",
      resolution_status: "open",
    },
    {
      id: "score-low",
      stage: "analyzing",
      severity: "warning",
      code: "BASIC_INFO_LOW_CONFIDENCE",
      message: "total_score 建议置信度较低，需教师确认",
      resolution_status: "open",
    },
    {
      id: "score-confirm",
      stage: "analyzing",
      severity: "warning",
      code: "TOTAL_SCORE_UNCONFIRMED",
      message: "总分建议尚未由教师明确确认",
      resolution_status: "open",
    },
    {
      id: "already-resolved",
      stage: "analyzing",
      severity: "warning",
      code: "SOME_RESOLVED_ISSUE",
      message: "已经处理的问题不应继续显示",
      resolution_status: "resolved",
    },
  ];
  mocks.listJobs.mockResolvedValue([currentJob]);
  mocks.listFieldSuggestions.mockResolvedValue([
    {
      id: "subject-suggestion",
      field_name: "subject",
      suggested_value: null,
      normalized_value: null,
      confidence: 0,
      evidence: [
        { kind: "derived", reference_id: "subject", summary: "空学科建议" },
      ],
      suggestion_version: 1,
      status: "suggested",
      teacher_edit_version: 0,
    },
    {
      id: "year-suggestion",
      field_name: "academic_year",
      suggested_value: null,
      normalized_value: null,
      confidence: 0,
      evidence: [
        { kind: "derived", reference_id: "year", summary: "内部学年建议" },
      ],
      suggestion_version: 1,
      status: "suggested",
      teacher_edit_version: 0,
    },
    {
      id: "score-suggestion",
      field_name: "total_score",
      suggested_value: null,
      normalized_value: null,
      confidence: 0,
      evidence: [],
      suggestion_version: 1,
      status: "suggested",
      teacher_edit_version: 0,
    },
  ]);
  const assignment = {
    id: "assignment-1",
    title: "线性代数试卷",
    subject: "线性代数",
    grade: "大一",
    total_score: "100",
    status: "draft",
    updated_at: "2026-07-26T00:00:00Z",
    classes: [],
    completeness: { ready: false, next_step: 1, issues: [] },
  } as AssignmentRecord;
  const onFieldSuggestionsChanged = vi.fn();

  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      assignment={assignment}
      onFieldSuggestionsChanged={onFieldSuggestionsChanged}
    />,
  );

  await screen.findByLabelText("处理详情");
  expect(screen.queryByText("请确认作业总分")).not.toBeInTheDocument();
  expect(screen.queryByText(/subject 建议置信度/)).not.toBeInTheDocument();
  expect(
    screen.queryByText(/academic_year 建议置信度/),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/total_score 建议置信度/)).not.toBeInTheDocument();
  expect(
    screen.queryByText("已经处理的问题不应继续显示"),
  ).not.toBeInTheDocument();

  expect(screen.queryByLabelText("基本信息建议")).not.toBeInTheDocument();
  expect(screen.queryByText("空学科建议")).not.toBeInTheDocument();
  expect(screen.queryByText("内部学年建议")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "确认总分" }),
  ).not.toBeInTheDocument();
  expect(onFieldSuggestionsChanged).toHaveBeenCalledWith([
    expect.objectContaining({ id: "subject-suggestion" }),
    expect.objectContaining({ id: "year-suggestion" }),
  ]);
});

it("恢复 partial/unavailable、风险与草稿历史并允许单阶段重试", async () => {
  const onContinueManually = vi.fn();
  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      onContinueManually={onContinueManually}
    />,
  );

  await screen.findByLabelText("处理详情");
  expect(screen.getByLabelText("处理详情").tagName).toBe("SECTION");
  const restartButton = screen.getByRole("button", { name: "重新整理" });
  expect(restartButton.parentElement).toHaveClass("justify-between");
  expect(restartButton.parentElement).toContainElement(
    screen.getByRole("heading", { name: "整理试卷" }),
  );
  expect(
    screen.getByLabelText("处理详情").firstElementChild?.nextElementSibling,
  ).toHaveClass("border-[var(--neutral-300)]");
  expect(screen.getByText("可继续手动核对")).toBeInTheDocument();
  expect(screen.getByText("可选：AI 整理页面与抽取题目")).toBeInTheDocument();
  expect(screen.getByText("可选：AI 生成答案与评分标准")).toBeInTheDocument();
  expect(screen.getByText("可跳过（AI 辅助暂不可用）")).toBeInTheDocument();
  expect(screen.getByText(/AI 辅助不会阻塞作业编辑/)).toBeInTheDocument();
  expect(screen.queryByText("查看详情")).not.toBeInTheDocument();
  expect(screen.queryByText("更多操作")).not.toBeInTheDocument();
  expect(screen.queryByText("阻断 2")).not.toBeInTheDocument();
  expect(screen.queryByText(/需要处理的问题/)).not.toBeInTheDocument();
  expect(screen.queryByText("真实 Provider 不可用")).not.toBeInTheDocument();
  expect(screen.getByText("历史记录（1）")).toBeInTheDocument();
  expect(
    screen.queryByText(/一次生成题目、参考答案和评分标准草稿/),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("发布作业")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "不等 AI，手动核对" }));
  expect(onContinueManually).toHaveBeenCalledOnce();

  fireEvent.click(screen.getByRole("button", { name: "重试此阶段" }));
  await waitFor(() =>
    expect(mocks.retryStage).toHaveBeenCalledWith(
      "job-1",
      "extracting_questions",
    ),
  );
});

it("Provider 已恢复时引导旧任务使用本地 AI 重新整理", async () => {
  mocks.capabilities.mockResolvedValue({
    enabled: true,
    provider: "local_openai_compatible",
    provider_status: "available",
    provider_error_code: null,
    external_provider_requests: false,
    teacher_start_allowed: true,
    suggestion_only: true,
    real_provider_quality_passed: false,
  });
  mocks.listJobs.mockResolvedValue([
    { ...job(), provider_mode: "codex_local" },
  ]);

  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  expect(
    await screen.findByText("本地 AI 已可用，请重新整理"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/当前记录来自旧配置，请重新整理/),
  ).toBeInTheDocument();
  expect(screen.getByText("旧任务未启用 AI")).toBeInTheDocument();
  expect(
    screen.queryByText("可跳过（AI 辅助暂不可用）"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "重试此阶段" }),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "使用本地 AI 重新整理" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce());
});

it("自动识别明确的文件用途，并把字段建议交给准备作业表单", async () => {
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
  const onFieldSuggestionsChanged = vi.fn();
  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      assignment={assignment}
      onReviewInputsChanged={onReviewInputsChanged}
      onFieldSuggestionsChanged={onFieldSuggestionsChanged}
    />,
  );

  await waitFor(() =>
    expect(onFieldSuggestionsChanged).toHaveBeenCalledWith([
      expect.objectContaining({ id: "suggestion-1" }),
    ]),
  );
  expect(screen.queryByText("建议：2026 数学期中")).not.toBeInTheDocument();
  expect(screen.queryByText("来自原始文件名")).not.toBeInTheDocument();
  expect(
    screen.queryByText(/不会推荐班级，也不会设置截止时间/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(/每个文件只需确认是题目还是答案/),
  ).toBeInTheDocument();
  expect(screen.getByText(/需要确认 1/)).toBeInTheDocument();
  expect(screen.getByText("发现 1 项需要核对的问题")).toBeInTheDocument();
  expect(screen.queryByText(/LOW_QUALITY_PAGE/)).not.toBeInTheDocument();
  expect(screen.queryByText(/checksum/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText("基本信息建议")).not.toBeInTheDocument();
  expect(screen.getByLabelText("文件分析")).toHaveAttribute("open");
  expect(screen.getByRole("option", { name: "答案" })).toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "教材" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "评分标准" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/答案来源/)).not.toBeInTheDocument();
  expect(screen.queryByText(/ANSWER_SOURCE/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "保存文件用途" }));
  await waitFor(() =>
    expect(mocks.confirmFileAnalysis).toHaveBeenCalledWith("analysis-1", {
      expected_teacher_edit_version: 0,
      confirmed_role: "reference_answer",
      confirmed_answer_source: "third_party",
    }),
  );

  expect(
    screen.queryByRole("button", { name: "接受" }),
  ).not.toBeInTheDocument();
  expect(mocks.dispositionField).not.toHaveBeenCalled();
  expect(onReviewInputsChanged).toHaveBeenCalledTimes(1);
});

it("仅在文件用途无法可靠判断时要求教师选择", async () => {
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-unknown",
      stored_file_id: "file-unknown",
      source_snapshot_hash: "a".repeat(64),
      file_name: "材料.pdf",
      file_size: 512,
      detected_mime_type: "application/pdf",
      checksum: "c".repeat(64),
      page_count: 1,
      suggested_role: "unknown",
      role_confidence: 0.25,
      suggested_answer_source: "unknown",
      answer_source_confidence: 0.2,
      analysis_status: "suggested",
      evidence: [],
      warning_codes: ["FILE_ROLE_REVIEW_REQUIRED"],
      teacher_edit_version: 0,
    },
  ]);

  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  expect(await screen.findByText(/需要确认 1/)).toBeInTheDocument();
  expect(
    screen.getByText((_, element) =>
      Boolean(
        element?.tagName === "P" &&
        element.textContent?.match(
          /^需要选择文件用途 · 用途：尚未确定（25%）$/,
        ),
      ),
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/已分析 \d+ 页/)).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "保存文件用途" }),
  ).toBeInTheDocument();
});

it("允许教师确认同一文件同时包含题目和答案", async () => {
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-mixed",
      stored_file_id: "file-mixed",
      source_snapshot_hash: "a".repeat(64),
      file_name: "习题与解答.pdf",
      file_size: 1024,
      detected_mime_type: "application/pdf",
      checksum: "b".repeat(64),
      page_count: 2,
      suggested_role: "question_and_answer",
      role_confidence: 0.78,
      suggested_answer_source: "unknown",
      answer_source_confidence: 0.3,
      analysis_status: "suggested",
      evidence: [],
      warning_codes: ["FILE_ROLE_REVIEW_REQUIRED"],
      teacher_edit_version: 0,
    },
  ]);

  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  expect(
    await screen.findByRole("option", { name: "题目和答案" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存文件用途" }));
  await waitFor(() =>
    expect(mocks.confirmFileAnalysis).toHaveBeenCalledWith("analysis-mixed", {
      expected_teacher_edit_version: 0,
      confirmed_role: "question_and_answer",
      confirmed_answer_source: "unknown",
    }),
  );
});

it("已确认的文件仍可更正用途", async () => {
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-confirmed",
      stored_file_id: "file-confirmed",
      source_snapshot_hash: "a".repeat(64),
      file_name: "误当作题目的作业.pdf",
      file_size: 1024,
      detected_mime_type: "application/pdf",
      checksum: "d".repeat(64),
      page_count: 3,
      suggested_role: "question_paper",
      role_confidence: 0.72,
      suggested_answer_source: "not_applicable",
      answer_source_confidence: 1,
      analysis_status: "confirmed",
      evidence: [],
      warning_codes: [],
      teacher_confirmed_role: "question_paper",
      teacher_confirmed_answer_source: "not_applicable",
      teacher_edit_version: 1,
    },
  ]);

  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  fireEvent.click(await screen.findByText("修改用途"));
  expect(screen.getByText(/如用途选错，可在这里更正/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("误当作题目的作业.pdf 文件角色"), {
    target: { value: "reference_answer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  await waitFor(() =>
    expect(mocks.confirmFileAnalysis).toHaveBeenCalledWith(
      "analysis-confirmed",
      {
        expected_teacher_edit_version: 1,
        confirmed_role: "reference_answer",
        confirmed_answer_source: "unknown",
      },
    ),
  );
});

it("可在文件用途确认处删除上传文件", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onReviewInputsChanged = vi.fn();
  mocks.listFileAnalyses.mockResolvedValue([
    {
      id: "analysis-delete",
      stored_file_id: "file-delete",
      source_snapshot_hash: "a".repeat(64),
      file_name: "上传错误的作业.pdf",
      detected_mime_type: "application/pdf",
      checksum: "e".repeat(64),
      page_count: 4,
      suggested_role: "question_paper",
      role_confidence: 0.72,
      suggested_answer_source: "not_applicable",
      answer_source_confidence: 1,
      analysis_status: "confirmed",
      evidence: [],
      warning_codes: [],
      teacher_confirmed_role: "question_paper",
      teacher_confirmed_answer_source: "not_applicable",
      teacher_edit_version: 1,
    },
  ]);

  render(
    <AssignmentGenerationPanel
      assignmentId="assignment-1"
      onReviewInputsChanged={onReviewInputsChanged}
    />,
  );

  fireEvent.click(
    await screen.findByRole("button", { name: "删除 上传错误的作业.pdf" }),
  );

  expect(window.confirm).toHaveBeenCalledWith(
    "确定删除“上传错误的作业.pdf”吗？对应页面也会删除，之后需要重新整理。",
  );
  await waitFor(() =>
    expect(mocks.removeFile).toHaveBeenCalledWith(
      "assignment-1",
      "file-delete",
    ),
  );
  expect(onReviewInputsChanged).toHaveBeenCalledOnce();
  expect(screen.getByRole("status")).toHaveTextContent(
    "文件已删除，请重新整理。",
  );
  confirm.mockRestore();
});

it("活动任务会轮询且可停止整理", async () => {
  vi.useFakeTimers();
  mocks.listJobs.mockResolvedValue([job("queued")]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);

  await vi.waitFor(() =>
    expect(
      screen.getByRole("button", { name: "停止整理" }),
    ).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "停止整理" }));
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

  expect(await screen.findByText(/需要确认 0，已过期 1/)).toBeInTheDocument();
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

  fireEvent.click(await screen.findByRole("button", { name: "开始整理" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(onReviewInputsChanged).toHaveBeenCalledOnce());
  expect(mocks.start.mock.calls[0][1]).not.toHaveProperty("provider_mode");

  mocks.start.mockRejectedValueOnce(new Error("network"));
  fireEvent.click(screen.getByRole("button", { name: "重新整理" }));
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
    expect(screen.getByLabelText("处理详情").textContent).toContain("等待处理"),
  );

  await vi.advanceTimersByTimeAsync(2000);
  await vi.waitFor(() =>
    expect(screen.getByLabelText("处理详情").textContent).toContain("部分完成"),
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
  await waitFor(() => expect(mocks.capabilities).toHaveBeenCalled());
  expect(screen.queryByText(/当前草稿生成方式/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Provider/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始整理" })).toBeDisabled();
});

it("明确显示 stale 状态且不会轮询终态", async () => {
  mocks.listJobs.mockResolvedValue([job("stale")]);
  render(<AssignmentGenerationPanel assignmentId="assignment-1" />);
  expect((await screen.findByLabelText("处理详情")).textContent).toContain(
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
    expect(screen.getByLabelText("处理详情").textContent).toContain(label),
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

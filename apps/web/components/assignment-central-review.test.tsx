import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssignmentCentralReview } from "./assignment-central-review";
import { assignmentReviewApi, type AssignmentRecord } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const session = {
    id: "review-1",
    assignment_id: "assignment-1",
    generation: 3,
    draft_revision_id: "revision-2",
    paper_version_id: "paper-4",
    legacy_rubric_version_id: null,
    review_version: 1,
    status: "changes_required",
    counts: { blocking: 1, warning: 1, info: 2 },
  };
  return {
    ...actual,
    assignmentReviewApi: {
      ...actual.assignmentReviewApi,
      list: vi.fn().mockResolvedValue({ items: [] }),
      create: vi.fn().mockResolvedValue(session),
      get: vi.fn().mockResolvedValue(session),
      items: vi.fn().mockResolvedValue({
        items: [
          {
            id: "risk-1",
            section: "classes",
            entity_type: "assignment",
            entity_id: "assignment-1",
            severity: "blocking",
            issue_code: "CONFIRM_CLASSES_REQUIRED",
            title: "CONFIRM CLASSES REQUIRED",
            message: "必须由教师明确确认 classes",
            evidence: { class_ids: [] },
            source_hash: "a".repeat(64),
            status: "open",
            eligibility: false,
          },
          {
            id: "risk-2",
            section: "pages",
            entity_type: "page",
            entity_id: "page-2",
            severity: "warning",
            issue_code: "PAPER_VARIANT_REVIEW",
            title: "PAPER VARIANT REVIEW",
            message: "mixed document suspected",
            evidence: { page_id: "page-2" },
            source_hash: "b".repeat(64),
            status: "resolved",
            eligibility: true,
            teacher_action: "resolve_manual",
            teacher_note: "已核对为同一份试卷",
            reviewed_by: "teacher-1",
            reviewed_at: "2026-07-27T12:00:00Z",
          },
          {
            id: "risk-3",
            section: "answers",
            entity_type: "assignment",
            entity_id: "assignment-1",
            severity: "blocking",
            issue_code: "CONFIRM_ANSWER_SOURCES_REQUIRED",
            title: "CONFIRM ANSWER SOURCES REQUIRED",
            message: "历史确认项不应再次处理",
            evidence: {},
            source_hash: "c".repeat(64),
            status: "stale",
            eligibility: false,
          },
        ],
      }),
    },
  };
});

const assignment = {
  id: "assignment-1",
  title: "线代作业",
  status: "draft",
  updated_at: "2026-07-26T00:00:00Z",
  classes: [],
  due_at: "2026-08-01T00:00:00Z",
  total_score: "10.00",
  completeness: { ready: false, next_step: 6, issues: [] },
  paper_version: undefined,
  rubric_version: undefined,
} as AssignmentRecord;

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("不会在加载时自动创建审查或发布，并由教师显式开始", async () => {
  render(
    <AssignmentCentralReview
      item={assignment}
      onNavigate={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  expect(await screen.findByText("开始集中审查")).toBeInTheDocument();
  expect(assignmentReviewApi.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "开始集中审查" }));
  await waitFor(() =>
    expect(assignmentReviewApi.create).toHaveBeenCalledOnce(),
  );
  expect(await screen.findByText("红色 1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "教师确认并发布" })).toBeDisabled();
});

it("把技术错误码转换成教师能理解的文案，并保留技术详情", async () => {
  render(
    <AssignmentCentralReview
      item={assignment}
      onNavigate={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始集中审查" }));
  expect(await screen.findByText("需要教师检查的内容")).toBeInTheDocument();
  expect(
    screen.queryByText("CONFIRM CLASSES REQUIRED"),
  ).not.toBeInTheDocument();
  expect(screen.getByText("查看技术详情")).toBeInTheDocument();
});

it("已解决问题默认收起，可在本地展开查看处理信息", async () => {
  render(
    <AssignmentCentralReview
      item={assignment}
      onNavigate={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始集中审查" }));
  const toggle = await screen.findByRole("button", { name: /已解决 1 项/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByText("请确认试卷页面属于同一份试卷"),
  ).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("请确认试卷页面属于同一份试卷")).toBeInTheDocument();
  expect(screen.getByText("已核对为同一份试卷")).toBeInTheDocument();
  expect(assignmentReviewApi.items).toHaveBeenCalledTimes(1);
});

it("不会把历史失效项当成待处理项，也不允许人工绕过结构性门禁", async () => {
  render(
    <AssignmentCentralReview
      item={assignment}
      onNavigate={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "开始集中审查" }));
  await screen.findByText("需要教师检查的内容");
  expect(screen.queryByText("历史确认项不应再次处理")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "人工检查并解决" }),
  ).not.toBeInTheDocument();
});

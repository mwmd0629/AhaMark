import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
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

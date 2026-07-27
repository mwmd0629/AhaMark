import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AIGradingReview } from "./ai-grading-review";

const { listForAnswer, create, review, editFeedback, retryCriterion } =
  vi.hoisted(() => ({
    listForAnswer: vi.fn(),
    create: vi.fn(),
    review: vi.fn(),
    editFeedback: vi.fn(),
    retryCriterion: vi.fn(),
  }));

vi.mock("@/lib/api", () => ({
  aiGradingApi: {
    listForAnswer,
    create,
    review,
    editFeedback,
    retryCriterion,
  },
}));

const job = {
  id: "job-1",
  student_answer_id: "answer-1",
  status: "partially_completed",
  generation: 2,
  provider: "fake",
  model: "test-v1",
  prompt_version: "ai-grading-v1",
  schema_version: "criterion-suggestion-v1",
  stale: false,
  usage: { input_tokens: 20, output_tokens: 10, images: 1 },
  suggestions: [
    {
      id: "suggestion-1",
      criterion_stable_key: "proof-step",
      status: "deterministic_conflict",
      max_points: "3",
      confidence: "0.7",
      evidence_refs: ["block-1"],
      missing_steps: ["关键推理"],
      detected_errors: ["invalid_inference"],
      manual_review_reason: "与确定性验证冲突",
      deterministic_conflict: true,
    },
  ],
  feedback: {
    student_feedback: "请补充关键推理。",
    teacher_summary: "优先复核 block-1。",
    disposition: "pending",
  },
  invocations: [],
};

describe("AIGradingReview", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    listForAnswer.mockResolvedValue([job]);
    review.mockResolvedValue({ id: "r1", action: "rejected" });
    editFeedback.mockResolvedValue({ status: "draft", published: false });
  });

  it("visually separates AI suggestions and shows safety states", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);
    expect(await screen.findByText("AI 分项建议")).toBeInTheDocument();
    expect(screen.getByText("deterministic_conflict")).toBeInTheDocument();
    expect(screen.getByText(/与确定性验证冲突/)).toBeInTheDocument();
    expect(screen.getByText(/教师决定（仅草稿）/)).toBeInTheDocument();
    expect(screen.getByText(/非正式成绩/)).toBeInTheDocument();
  });

  it("requires a teacher reason before disposition", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);
    await screen.findByText("AI 分项建议");
    fireEvent.click(screen.getByRole("button", { name: "拒绝建议" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "请填写教师处置原因",
    );
    expect(review).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("proof-step 修改原因"), {
      target: { value: "AI 忽略了已验证步骤" },
    });
    fireEvent.click(screen.getByRole("button", { name: "拒绝建议" }));
    await waitFor(() => expect(review).toHaveBeenCalled());
  });

  it("is read-only after finalize", async () => {
    render(
      <AIGradingReview
        answerId="answer-1"
        rubricVersionId="rubric-1"
        finalized
      />,
    );
    expect(await screen.findByText(/已 Finalize/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成新建议" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝建议" })).toBeDisabled();
  });

  it("does not close when the surrounding blank area is clicked", async () => {
    const { container } = render(
      <AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />,
    );
    await screen.findByText("AI 分项建议");
    fireEvent.click(container.firstElementChild!);
    expect(screen.getByText("proof-step")).toBeInTheDocument();
  });
});

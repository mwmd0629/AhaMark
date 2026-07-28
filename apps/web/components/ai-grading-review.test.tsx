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

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public body: {
        code: string;
        message: string;
        details: Record<string, unknown>;
        request_id: string;
      },
    ) {
      super(body.message);
    }
  }
  return {
    ApiError,
    aiGradingApi: {
      listForAnswer,
      create,
      review,
      editFeedback,
      retryCriterion,
    },
  };
});

function suggestion(overrides: Record<string, unknown> = {}) {
  return {
    id: "suggestion-1",
    criterion_id: "criterion-1",
    criterion_stable_key: "matrix-result",
    status: "scored",
    reason: "矩阵结果与标准答案一致",
    error_codes: [],
    evidence_ids: ["recognition:recognition-1", "region:region-1"],
    validation_refs: ["validation-result-1"],
    requires_review: true,
    suggested_points: "3",
    max_points: "3",
    confidence: "0.95",
    evidence_refs: ["recognition:recognition-1", "region:region-1"],
    missing_steps: [],
    detected_errors: [],
    deterministic_conflict: false,
    ...overrides,
  };
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    student_answer_id: "answer-1",
    status: "completed",
    generation: 2,
    provider: "fake",
    model: "offline-placeholder",
    prompt_version: "ai-grading-v1",
    schema_version: "criterion-suggestion-v1",
    stale: false,
    scoring_input_version: "input-v2",
    rubric_version_id: "rubric-1",
    reference_answer_version_id: "reference-1",
    evidence: [
      {
        id: "recognition:recognition-1",
        kind: "recognition",
        status: "confirmed",
        stale: false,
        version: 2,
        confirmed_revision: 1,
        target_id: "answer-recognition-workspace",
      },
      {
        id: "region:region-1",
        kind: "region",
        status: "confirmed",
        stale: false,
        version: 3,
        submission_page_id: "page-1",
        coordinates: { x: "0.1", y: "0.2", width: "0.3", height: "0.4" },
        target_id: "answer-region-region-1",
      },
    ],
    validation: {
      job_id: "validation-1",
      status: "completed",
      generation: 4,
      stale: false,
      rubric_version_id: "rubric-1",
      reference_answer_version_id: "reference-1",
      results: [
        {
          id: "validation-result-1",
          criterion_id: "criterion-1",
          generation: 4,
          result: "verified",
          comparison_method: "matrix_exact",
          stale: false,
          diagnostics: {},
        },
      ],
    },
    usage: { input_tokens: 20, output_tokens: 10, images: 1 },
    suggestions: [suggestion()],
    feedback: {
      student_feedback: "",
      teacher_summary: "",
      disposition: "pending",
    },
    invocations: [],
    ...overrides,
  };
}

describe("AIGradingReview", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    listForAnswer.mockResolvedValue([job()]);
    review.mockResolvedValue({ id: "r1", action: "accepted" });
    editFeedback.mockResolvedValue({ status: "draft", published: false });
  });

  it("accepts only a current scored suggestion after an explicit reason", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);

    const accept = await screen.findByRole("button", {
      name: "采纳 AI 分项建议",
    });
    expect(accept).toBeEnabled();
    fireEvent.change(screen.getByLabelText("matrix-result 修改原因"), {
      target: { value: "教师核对原卷与验证结果后采纳" },
    });
    fireEvent.click(accept);

    await waitFor(() =>
      expect(review).toHaveBeenCalledWith("suggestion-1", {
        action: "accepted",
        selected_points: undefined,
        reason: "教师核对原卷与验证结果后采纳",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("最终总分");
  });

  it("supports teacher-modified points and rejects invalid ranges", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);
    await screen.findByText("matrix-result");
    fireEvent.change(screen.getByLabelText("matrix-result 教师分值"), {
      target: { value: "4" },
    });
    fireEvent.change(screen.getByLabelText("matrix-result 修改原因"), {
      target: { value: "按教师判断修改" },
    });
    fireEvent.click(screen.getByRole("button", { name: "教师修改后采用" }));
    expect(await screen.findByRole("status")).toHaveTextContent("0–3");
    expect(review).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("matrix-result 教师分值"), {
      target: { value: "2.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "教师修改后采用" }));
    await waitFor(() =>
      expect(review).toHaveBeenCalledWith(
        "suggestion-1",
        expect.objectContaining({ action: "modified", selected_points: 2.5 }),
      ),
    );
  });

  it("records a rejection reason and keeps manual grading available", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);
    await screen.findByText("matrix-result");
    fireEvent.change(screen.getByLabelText("matrix-result 修改原因"), {
      target: { value: "步骤证据不足，转人工" },
    });
    fireEvent.click(screen.getByRole("button", { name: "拒绝并转人工" }));

    await waitFor(() =>
      expect(review).toHaveBeenCalledWith(
        "suggestion-1",
        expect.objectContaining({ action: "rejected" }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("人工评分");
  });

  it("disables dispositions for stale or finalized jobs", async () => {
    listForAnswer.mockResolvedValue([job({ stale: true, status: "stale" })]);
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);

    expect(await screen.findByText(/版本或证据已变化/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "采纳 AI 分项建议" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝并转人工" })).toBeDisabled();
  });

  it("disables dispositions when validation versions do not match", async () => {
    const mismatched = job();
    listForAnswer.mockResolvedValue([
      job({
        validation: {
          ...mismatched.validation,
          reference_answer_version_id: "reference-old",
        },
      }),
    ]);
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);

    expect(
      await screen.findByText(/版本与数学验证引用不一致/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "采纳 AI 分项建议" }),
    ).toBeDisabled();
  });

  it("shows conflict, manual and unavailable states without blocking manual work", async () => {
    listForAnswer.mockResolvedValue([
      job({
        provider: "unavailable",
        error_code: "PROVIDER_UNAVAILABLE",
        suggestions: [
          suggestion({
            status: "conflict",
            suggested_points: undefined,
            reason: "deterministic conflict",
            error_codes: ["VALIDATION_CONFLICT"],
          }),
          suggestion({
            id: "suggestion-2",
            criterion_stable_key: "proof-step",
            status: "manual",
            suggested_points: undefined,
            validation_refs: [],
            reason: "unsupported proof",
          }),
        ],
      }),
    ]);
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);

    expect(await screen.findByText(/Provider unavailable/)).toBeInTheDocument();
    expect(screen.getByText("与确定性验证冲突")).toBeInTheDocument();
    expect(screen.getByText("需人工评分")).toBeInTheDocument();
    expect(screen.getAllByText(/仍可独立修改分值或拒绝/)).toHaveLength(2);
  });

  it("renders locatable evidence and current validation generation", async () => {
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);

    expect(
      await screen.findByRole("link", { name: /识别证据/ }),
    ).toHaveAttribute("href", "#answer-recognition-workspace");
    expect(screen.getByRole("link", { name: /答题区域/ })).toHaveAttribute(
      "href",
      "#answer-region-region-1",
    );
    expect(screen.getByText(/generation 4 · matrix_exact/)).toBeInTheDocument();
    expect(screen.getByText(/Provider 自述不视为/)).toBeInTheDocument();
  });

  it("surfaces duplicate disposition errors and prevents rapid double submits", async () => {
    const { ApiError } = await import("@/lib/api");
    review.mockRejectedValue(
      new ApiError(409, {
        code: "AI_SUGGESTION_ALREADY_REVIEWED",
        message: "already reviewed",
        details: {},
        request_id: "request-1",
      }),
    );
    render(<AIGradingReview answerId="answer-1" rubricVersionId="rubric-1" />);
    await screen.findByText("matrix-result");
    fireEvent.change(screen.getByLabelText("matrix-result 修改原因"), {
      target: { value: "重复提交测试" },
    });
    const reject = screen.getByRole("button", { name: "拒绝并转人工" });
    fireEvent.click(reject);
    fireEvent.click(reject);

    await waitFor(() => expect(review).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("status")).toHaveTextContent("已经处置");
  });

  it("keeps no-job and offline-placeholder states honest", async () => {
    listForAnswer.mockResolvedValue([]);
    render(<AIGradingReview answerId="answer-1" />);

    expect(await screen.findByText(/尚无 AI 建议/)).toBeInTheDocument();
    expect(screen.getByText(/不调用外部 API、不上传文件/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成新建议" })).toBeDisabled();
  });
});

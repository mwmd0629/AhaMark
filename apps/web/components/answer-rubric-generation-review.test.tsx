import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerRubricGenerationReview } from "./answer-rubric-generation-review";

const generationApi = vi.hoisted(() => ({
  listRevisions: vi.fn(),
  listAnswerCandidates: vi.fn(),
  listRubricCandidates: vi.fn(),
  rubricCandidateValidation: vi.fn(),
  dispositionAnswerCandidate: vi.fn(),
  dispositionRubricCandidate: vi.fn(),
  acceptEligibleAnswers: vi.fn(),
  acceptEligibleRubrics: vi.fn(),
}));
const reviewApi = vi.hoisted(() => ({ bundle: vi.fn() }));
const formalApi = vi.hoisted(() => ({
  confirmReference: vi.fn(),
  confirm: vi.fn(),
}));

vi.mock("@/lib/api", async (load) => ({
  ...(await load()),
  assignmentGenerationApi: generationApi,
  assignmentReviewApi: reviewApi,
  structuredRubricApi: formalApi,
}));

const source = (kind: string, label: string) => ({ kind, label });
const answerVersion = (
  id: string,
  status: "draft" | "confirmed" | "retired",
  version: number,
  content: string,
) => ({
  id,
  status,
  version,
  content_hash: id.repeat(8).slice(0, 64),
  source: source("teacher_authored", "教师编写答案"),
  content,
  content_payload: {
    source_type: "teacher_authored",
    source_file: null,
    source_page: null,
    source_region: null,
    raw_content: content,
    normalized_content: content,
    structured_content: {},
    provenance: {},
  },
  visibility: "teacher",
});
const rubricVersion = (
  id: string,
  status: "draft" | "confirmed" | "retired",
  version: number,
  title: string,
) => ({
  id,
  status,
  version,
  content_hash: id.repeat(8).slice(0, 64),
  reference_answer_version_id: "answer-1",
  source: source("structured_rubric", "结构化评分标准"),
  title,
  total_points: "20.00",
  criteria: [
    {
      id: `${id}-criterion`,
      key: "result",
      title: "结果",
      description: "结果正确",
      points: "20.00",
      display_order: 0,
      criterion_type: "result",
      required: true,
      dependencies: [],
      expected_evidence: {},
      validation_mode: "manual_only",
      validation_rule: {},
      manual_review_policy: {},
      partial_credit_policy: {},
      error_category: null,
      metadata: {},
    },
  ],
  visibility: "teacher",
});
const candidateBase = {
  id: "answer-candidate-1",
  candidate_version: 1,
  teacher_edit_version: 0,
  status: "accepted",
  source_snapshot_hash: "a".repeat(64),
  materialized_formal_id: "answer-draft",
  source: source("answer_candidate", "参考答案建议"),
  content: "生成建议",
  confidence: "0.80",
  visibility: "teacher",
};
const bundle = ({
  status = "action_required",
  selectedAnswer = null,
  materializedAnswer = null,
  selectedRubric = null,
  materializedRubric = null,
  answerCandidate = null,
  answerHistory = [],
  rubricHistory = [],
}: Record<string, unknown> = {}) => ({
  schema_version: "assignment-review-bundle-v1",
  assignment_id: "assignment-1",
  version: {
    generation: 1,
    draft_revision_id: "revision-1",
    paper_version_id: "paper-1",
    source_snapshot_hash: "a".repeat(64),
    bundle_hash: "b".repeat(64),
  },
  status,
  questions: [
    {
      id: "question-1",
      number: "1",
      content_hash: "c".repeat(64),
      content: "计算",
      source: source("ocr", "试卷识别题目"),
      provenance: null,
      visibility: "teacher",
      answer: {
        candidate: answerCandidate,
        candidate_history: answerCandidate ? [answerCandidate] : [],
        materialized: materializedAnswer,
        selected: selectedAnswer,
        history: answerHistory,
        visibility: "teacher",
      },
      rubric: {
        candidate: null,
        candidate_history: [],
        materialized: materializedRubric,
        selected: selectedRubric,
        history: rubricHistory,
        visibility: "teacher",
      },
    },
  ],
  blockers: [],
  confirmations: [],
  binding: null,
});

const revision = {
  id: "revision-1",
  teacher_edit_version: 0,
  source_snapshot_hash: "a".repeat(64),
};
const props = {
  assignmentId: "assignment-1",
  questions: [
    {
      id: "question-1",
      question_number: "1",
      content_text: "计算",
      max_score: 20,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  generationApi.listRevisions.mockResolvedValue([revision]);
  generationApi.listAnswerCandidates.mockResolvedValue([]);
  generationApi.listRubricCandidates.mockResolvedValue([]);
  generationApi.rubricCandidateValidation.mockResolvedValue([]);
  reviewApi.bundle.mockResolvedValue(bundle());
  formalApi.confirmReference.mockResolvedValue({});
  formalApi.confirm.mockResolvedValue({});
});
afterEach(cleanup);

describe("AnswerRubricGenerationReview Bundle lifecycle", () => {
  it("shows a formal draft even when no review session exists", async () => {
    generationApi.listRevisions.mockResolvedValue([]);
    const draftAnswer = answerVersion("answer-draft", "draft", 1, "答案 2");
    reviewApi.bundle.mockResolvedValue(
      bundle({
        status: "missing_review",
        selectedAnswer: draftAnswer,
        answerHistory: [draftAnswer],
      }),
    );

    render(<AnswerRubricGenerationReview {...props} />);
    expect(await screen.findByText("答案 2")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "确认此参考答案" }),
    ).toBeEnabled();
  });

  it("does not let an accepted suggestion hide its saved formal draft", async () => {
    const draftAnswer = answerVersion("answer-draft", "draft", 1, "已保存答案");
    reviewApi.bundle.mockResolvedValue(
      bundle({
        materializedAnswer: draftAnswer,
        answerCandidate: candidateBase,
        answerHistory: [draftAnswer],
      }),
    );
    generationApi.listAnswerCandidates.mockResolvedValue([
      {
        id: "answer-candidate-1",
        question_id: "question-1",
        raw_content: "生成建议",
        alternative_answers: [],
        teacher_edit_version: 0,
        question_version: "q1",
      },
    ]);

    render(<AnswerRubricGenerationReview {...props} />);
    expect(await screen.findByText("已保存答案")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "确认此参考答案" }),
    ).toBeEnabled();
    expect(screen.getByText("查看生成建议并处理")).toBeInTheDocument();
  });

  it("keeps selected confirmed content primary and exposes a newer draft for confirmation", async () => {
    const confirmed = answerVersion(
      "answer-confirmed",
      "confirmed",
      1,
      "当前确认答案",
    );
    const draft = answerVersion("answer-draft", "draft", 2, "较新待确认答案");
    const retired = answerVersion("answer-retired", "retired", 0, "历史答案");
    reviewApi.bundle.mockResolvedValue(
      bundle({
        selectedAnswer: confirmed,
        materializedAnswer: draft,
        answerHistory: [draft, confirmed, retired],
      }),
    );

    render(<AnswerRubricGenerationReview {...props} />);
    expect(await screen.findByText("当前确认答案")).toBeInTheDocument();
    expect(screen.getByText("较新待确认答案")).toBeInTheDocument();
    expect(
      screen.getByText("查看历史参考答案").closest("details"),
    ).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "确认这份参考答案" }));
    await waitFor(() =>
      expect(formalApi.confirmReference).toHaveBeenCalledWith("answer-draft"),
    );
  });

  it("confirms the exact rubric draft id and keeps retired versions folded", async () => {
    const draft = rubricVersion("rubric-draft", "draft", 2, "待确认评分标准");
    const retired = rubricVersion(
      "rubric-retired",
      "retired",
      1,
      "历史评分标准",
    );
    reviewApi.bundle.mockResolvedValue(
      bundle({
        selectedRubric: draft,
        rubricHistory: [draft, retired],
      }),
    );

    render(<AnswerRubricGenerationReview {...props} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "确认此评分标准" }),
    );
    await waitFor(() =>
      expect(formalApi.confirm).toHaveBeenCalledWith("rubric-draft"),
    );
    expect(
      screen.getByText("查看历史评分标准").closest("details"),
    ).not.toHaveAttribute("open");
  });

  it("fails closed when a Bundle reload fails after authoritative content loaded", async () => {
    const draftAnswer = answerVersion("answer-draft", "draft", 1, "旧正式答案");
    reviewApi.bundle
      .mockResolvedValueOnce(
        bundle({
          materializedAnswer: draftAnswer,
          answerCandidate: candidateBase,
          answerHistory: [draftAnswer],
        }),
      )
      .mockRejectedValueOnce(new Error("network"));
    generationApi.listAnswerCandidates.mockResolvedValue([
      {
        id: "answer-candidate-1",
        question_id: "question-1",
        raw_content: "旧生成建议",
        alternative_answers: [],
        teacher_edit_version: 0,
        question_version: "q1",
      },
    ]);

    render(<AnswerRubricGenerationReview {...props} />);
    expect(await screen.findByText("旧正式答案")).toBeInTheDocument();
    expect(screen.getByText("查看生成建议并处理")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新草稿" }));

    expect(
      await screen.findByText("无法取得当前审查内容，请重试后再确认。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("旧正式答案")).not.toBeInTheDocument();
    expect(screen.queryByText("查看生成建议并处理")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认此参考答案" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "批量接受可用答案" }),
    ).not.toBeInTheDocument();
  });
});

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerRubricGenerationReview } from "./answer-rubric-generation-review";

const api = vi.hoisted(() => ({
  listRevisions: vi.fn(),
  listAnswerCandidates: vi.fn(),
  listRubricCandidates: vi.fn(),
  rubricCandidateValidation: vi.fn(),
  dispositionAnswerCandidate: vi.fn(),
  dispositionRubricCandidate: vi.fn(),
  acceptEligibleAnswers: vi.fn(),
  acceptEligibleRubrics: vi.fn(),
}));
vi.mock("@/lib/api", async (load) => ({
  ...(await load()),
  assignmentGenerationApi: api,
}));

describe("AnswerRubricGenerationReview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRevisions.mockResolvedValue([
      {
        id: "revision-1",
        assignment_id: "assignment-1",
        generation_job_id: "job-1",
        revision: 1,
        source_snapshot_hash: "a".repeat(64),
        status: "review_required",
        draft_payload: {},
        risk_summary: { info: 0, warning: 2, blocking: 0 },
        teacher_edit_version: 0,
        created_at: "2026-01-01",
        updated_at: "2026-01-01",
      },
    ]);
    api.listAnswerCandidates.mockResolvedValue([
      {
        id: "answer-1",
        question_id: "question-1",
        question_version: "question-v1",
        candidate_version: 1,
        source_type: "ai_generated",
        raw_content: "<script>自动发布</script> 答案 2",
        normalized_content: "答案 2",
        structured_content: { value: 2 },
        alternative_answers: [
          {
            content: "2.0",
            relation: "candidate",
            equivalence_status: "indeterminate",
          },
        ],
        provenance: { provider: "fake", untrusted_document_content: true },
        confidence: 0.8,
        evidence: [{ kind: "question", reference_id: "question-1" }],
        warning_codes: ["ALTERNATIVE_ANSWER_EQUIVALENCE_INDETERMINATE"],
        status: "suggested",
        manual_required: false,
        teacher_edit_version: 0,
      },
    ]);
    api.listRubricCandidates.mockResolvedValue([
      {
        id: "rubric-1",
        question_id: "question-1",
        question_version: "question-v1",
        answer_candidate_id: "answer-1",
        candidate_version: 1,
        title: "第 1 题评分标准",
        scoring_mode: "hybrid",
        total_points: "5.00",
        allow_partial_credit: true,
        domain_requirements: { unit: "m" },
        validation_config: { answer_type: "exact_scalar" },
        common_error_types: [{ code: "SIGN_ERROR" }],
        feedback_templates: { default: "检查符号" },
        confidence: 0.8,
        evidence: [],
        warning_codes: ["VALIDATION_PARTIALLY_VERIFIED"],
        status: "suggested",
        manual_required: true,
        teacher_edit_version: 0,
        criteria: [
          {
            id: "criterion-1",
            criterion_key: "result",
            display_order: 0,
            title: "结果",
            points: "5.00",
            criterion_type: "result",
            required: true,
            dependency_keys: [],
            alternative_group: null,
            partial_credit_rule: { max_points: 3 },
            deduction_rule: { max_deduction: 2 },
            validation_rule: { answer_type: "exact_scalar" },
            common_error_codes: ["SIGN_ERROR"],
            feedback_template: "检查符号",
            confidence: 0.8,
            evidence: [],
            manual_required: false,
          },
        ],
      },
    ]);
    api.rubricCandidateValidation.mockResolvedValue([
      {
        id: "validation-1",
        status: "indeterminate",
        validation_mode: "hybrid",
        deterministic_result: {},
        structural_result: { valid: true },
        issue_codes: ["VALIDATION_INDETERMINATE"],
        validator_version: "v1",
      },
    ]);
  });

  it("shows non-official source, structured criteria and indeterminate safely", async () => {
    render(
      <AnswerRubricGenerationReview
        assignmentId="assignment-1"
        questions={[
          {
            id: "question-1",
            question_number: "1",
            content_text: "计算",
            max_score: 5,
          },
        ]}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("来源：AI 生成")).toBeInTheDocument(),
    );
    expect(screen.getByText(/此答案不显示为官方答案/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByDisplayValue("<script>自动发布</script> 答案 2"),
      ).toBeInTheDocument(),
    );
    expect(document.querySelector("script")).toBeNull();
    await waitFor(() =>
      expect(screen.getByText(/无法确定（不是已验证）/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/部分分/)).toBeInTheDocument();
    expect(screen.getByText(/常见错误/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布作业" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "批量接受 eligible 答案" }),
    ).toBeEnabled();
  });
});

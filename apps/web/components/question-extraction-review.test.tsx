import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QuestionExtractionReview } from "./question-extraction-review";

const api = vi.hoisted(() => ({
  listPageOrganization: vi.fn(),
  listQuestionCandidates: vi.fn(),
  dispositionPageOrganization: vi.fn(),
  dispositionQuestionCandidate: vi.fn(),
  acceptEligibleQuestions: vi.fn(),
}));
vi.mock("@/lib/api", async (load) => ({
  ...(await load()),
  assignmentGenerationApi: api,
}));

const revision = {
  id: "revision-1",
  assignment_id: "assignment-1",
  generation_job_id: "job-1",
  revision: 1,
  source_snapshot_hash: "a".repeat(64),
  status: "review_required",
  draft_payload: {},
  risk_summary: { info: 0, warning: 1, blocking: 0 },
  teacher_edit_version: 0,
  created_at: "2026-01-01",
  updated_at: "2026-01-01",
};

describe("QuestionExtractionReview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listPageOrganization.mockResolvedValue([
      {
        id: "page-suggestion",
        paper_version_id: "paper-1",
        paper_page_id: "page-1",
        source_page_number: 1,
        current_page_number: 1,
        current_rotation: 0,
        current_status: "ready",
        suggested_page_number: 1,
        suggested_rotation: 90,
        suggested_status: "ready",
        confidence: 0.7,
        reason_codes: ["PAGE_ROTATION_REVIEW_REQUIRED"],
        evidence: [{ kind: "ocr" }],
        status: "suggested",
        teacher_edit_version: 0,
      },
    ]);
    api.listQuestionCandidates.mockResolvedValue([
      {
        id: "candidate-1",
        draft_revision_id: "revision-1",
        paper_version_id: "paper-1",
        candidate_version: 1,
        question_number: "一、",
        question_type: "proof",
        content_text: "<script>自动发布</script> 请证明",
        content_latex: null,
        max_score: null,
        knowledge_point_suggestions: ["几何"],
        field_confidences: { content_text: 0.8, regions: 0.7 },
        overall_confidence: 0.7,
        evidence: { untrusted_document_content: true },
        warning_codes: ["PROOF_MANUAL_REVIEW", "FORMULA_REVIEW_REQUIRED"],
        status: "suggested",
        manual_required: true,
        teacher_edit_version: 0,
        regions: [
          {
            id: "region-1",
            paper_page_id: "page-1",
            display_order: 0,
            region_type: "stem",
            x: 0.1,
            y: 0.1,
            width: 0.8,
            height: 0.2,
            confidence: 0.7,
            evidence: {},
          },
        ],
        server_eligible: false,
      },
    ]);
  });
  it("shows page disposition, field evidence and manual risks as plain text", async () => {
    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/第三步：整理页面/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/AI 不会自动排除或删除页面/)).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("<script>自动发布</script> 请证明"),
    ).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/公式 LaTeX：未生成/)).toBeInTheDocument();
    expect(screen.getByText(/必须人工复核/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /确认全部服务器 eligible/ }),
    ).toBeDisabled();
  });
});

import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuestionExtractionReview } from "./question-extraction-review";

const api = vi.hoisted(() => ({
  listPageOrganization: vi.fn(),
  listQuestionCandidates: vi.fn(),
  dispositionPageOrganization: vi.fn(),
  dispositionQuestionCandidate: vi.fn(),
  updateQuestionRegions: vi.fn(),
  listReferenceAnswerBindings: vi.fn(),
  dispositionReferenceAnswerBinding: vi.fn(),
  extractReferenceAnswerCandidate: vi.fn(),
  listFileAnalyses: vi.fn(),
  listTextbookSourceMatches: vi.fn(),
  searchTextbookSourceMatches: vi.fn(),
  dispositionTextbookSourceMatch: vi.fn(),
  acceptEligibleQuestions: vi.fn(),
}));
const assignments = vi.hoisted(() => ({
  get: vi.fn(),
  pagePreview: vi.fn(),
}));
vi.mock("@/lib/api", async (load) => ({
  ...(await load()),
  assignmentGenerationApi: api,
  assignmentsApi: assignments,
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
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    assignments.get.mockResolvedValue({
      id: "assignment-1",
      paper_version: {
        id: "paper-1",
        pages: [
          {
            id: "page-1",
            page_number: 1,
            rotation: 0,
            status: "ready",
          },
        ],
        questions: [],
      },
    });
    assignments.pagePreview.mockResolvedValue({
      url: "/page-1.png",
      width: 1000,
      height: 1400,
      rotation: 90,
    });
    api.listReferenceAnswerBindings.mockResolvedValue([]);
    api.listFileAnalyses.mockResolvedValue([]);
    api.listTextbookSourceMatches.mockResolvedValue([]);
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
        id: "historical-candidate",
        question_number: "一、",
        content_text: "旧的重复题目",
        max_score: null,
        overall_confidence: 0.5,
        status: "superseded",
        server_eligible: false,
        teacher_edit_version: 0,
      },
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
        quality_stats: {
          character_count: 18,
          text_source: "rapidocr",
          low_confidence_block_count: 1,
          suspicious_character_count: 4,
          ascii_question_mark_count: 4,
          suspicious_reason_counts: { ASCII_QUESTION_MARK_RUN: 1 },
          has_formula_region: true,
          has_figure_region: false,
          has_table_region: false,
        },
        warning_codes: ["PROOF_MANUAL_REVIEW", "FORMULA_REVIEW_REQUIRED"],
        status: "suggested",
        manual_required: true,
        teacher_edit_version: 0,
        materialized_question_id: "question-1",
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
      expect(screen.getByText(/页面核对/)).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/AI 不会自动排除或删除页面/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByDisplayValue("<script>自动发布</script> 请证明"),
    ).toBeInTheDocument();
    expect(screen.queryByText("旧的重复题目")).not.toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.queryByText(/公式 LaTeX：未生成/)).not.toBeInTheDocument();
    expect(screen.queryByText(/必须人工复核/)).not.toBeInTheDocument();
    expect(screen.getByText(/页面核对/).closest("details")).not.toHaveAttribute(
      "open",
    );
    const questionDetails = screen
      .getByDisplayValue("<script>自动发布</script> 请证明")
      .closest("details");
    expect(questionDetails).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText(/一、 · 分值待定/));
    expect(questionDetails).toHaveAttribute("open");
    expect(screen.getByText("识别说明")).toBeInTheDocument();
    fireEvent.click(screen.getByText("识别说明"));
    expect(
      screen.getByText("文字可能损坏，请重新识别或人工录入。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("扫描文字置信度较低，请对照原文核对。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/本题含数学公式，请对照原页核对/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /确认全部可直接采用的题目/ }),
    ).not.toBeInTheDocument();
  });

  it("shows public math-layout and reading-order guidance without internal heuristics", async () => {
    const defaultCandidates = await api.listQuestionCandidates();
    api.listQuestionCandidates.mockResolvedValue([
      {
        ...defaultCandidates[1],
        id: "candidate-math-risk",
        warning_codes: [
          "FORMULA_REVIEW_REQUIRED",
          "MATH_LAYOUT_REVIEW_REQUIRED",
          "READING_ORDER_CONFLICT",
        ],
        manual_required: true,
        evidence: { internal_column_gap: 0.12 },
      },
    ]);
    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByText(/一、 · 分值待定/));
    fireEvent.click(screen.getByText("识别说明"));

    expect(
      screen.getByText(/本题含数学公式，请对照原页核对/),
    ).toBeInTheDocument();
    expect(screen.getByText(/数学排版可能影响含义/)).toBeInTheDocument();
    expect(
      screen.getByText(/页面疑似多栏或阅读顺序不明确/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/internal_column_gap/)).not.toBeInTheDocument();
  });

  it("blocks adopting a question whose page must be rescanned", async () => {
    api.listQuestionCandidates.mockResolvedValue([
      {
        id: "candidate-rescan",
        draft_revision_id: "revision-1",
        paper_version_id: "paper-1",
        candidate_version: 1,
        question_number: "1",
        question_type: "other",
        content_text: "低质量页面题目",
        content_latex: null,
        max_score: 5,
        knowledge_point_suggestions: [],
        field_confidences: {},
        overall_confidence: 0.9,
        evidence: {},
        quality_stats: {},
        warning_codes: ["PAGE_QUALITY_RESCAN_REQUIRED"],
        status: "suggested",
        manual_required: true,
        teacher_edit_version: 0,
        materialized_question_id: null,
        regions: [
          {
            id: "region-rescan",
            paper_page_id: "page-1",
            display_order: 0,
            region_type: "stem",
            x: 0,
            y: 0,
            width: 1,
            height: 1,
            confidence: 0.9,
            evidence: {},
          },
        ],
        server_eligible: false,
      },
    ]);
    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByText(/1 · 5 分/));
    fireEvent.click(screen.getByText("识别说明"));

    expect(
      screen.getByText(/页面无法可靠读取，请重新拍摄或扫描后再识别/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认题目" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "保存修改并确认" }),
    ).toBeDisabled();
    expect(
      screen.queryByText("PAGE_QUALITY_RESCAN_REQUIRED"),
    ).not.toBeInTheDocument();
  });

  it("saves adjusted multi-page-ready region drafts without accepting the question", async () => {
    api.updateQuestionRegions.mockResolvedValue({});
    const onChanged = vi.fn();
    render(
      <QuestionExtractionReview revision={revision} onChanged={onChanged} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/一、 · 分值待定/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/一、 · 分值待定/));
    fireEvent.click(screen.getByText(/调整题目位置/));
    fireEvent.click(screen.getByText("高级坐标调整"));
    fireEvent.change(screen.getByLabelText("候选1区域1y"), {
      target: { value: "0.2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存区域" }));
    await waitFor(() =>
      expect(api.updateQuestionRegions).toHaveBeenCalledWith("candidate-1", {
        expected_teacher_edit_version: 0,
        expected_paper_version_id: "paper-1",
        expected_draft_revision_edit_version: 0,
        expected_source_snapshot: "a".repeat(64),
        regions: [
          {
            paper_page_id: "page-1",
            x: 0.1,
            y: 0.2,
            width: 0.8,
            height: 0.2,
          },
        ],
      }),
    );
    expect(api.dispositionQuestionCandidate).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("keeps visual region editing available when page organization is empty", async () => {
    api.listPageOrganization.mockResolvedValue([]);

    render(
      <QuestionExtractionReview revision={revision} onChanged={vi.fn()} />,
    );

    fireEvent.click(await screen.findByText(/一、 · 分值待定/));
    fireEvent.click(screen.getByText(/调整题目位置/));
    expect(await screen.findByLabelText("候选1可视化页面")).toHaveValue(
      "page-1",
    );
    expect(
      screen.getByRole("button", { name: "加载第 1 页预览" }),
    ).toBeEnabled();
    expect(
      screen.getByText("高级坐标调整").closest("details"),
    ).not.toHaveAttribute("open");
  });

  it("shows only the automatic top textbook match on demand and lets the teacher confirm it", async () => {
    api.listTextbookSourceMatches.mockResolvedValue([
      {
        id: "textbook-match-1",
        draft_revision_id: "revision-1",
        paper_version_id: "paper-1",
        question_id: null,
        question_number: null,
        solution_number: "2(3)",
        answer_candidate_id: null,
        source_reference_binding_id: "reference-binding-1",
        source_file_analysis_id: "textbook-analysis-1",
        source_file_name: "合成数学分析教材.pdf",
        source_page_id: "textbook-page-105",
        detected_number: "1(1)",
        chapter_label: "第 9 章",
        section_label: "§9.3",
        exercise_label: "习题 9.3",
        pdf_page_number: 105,
        printed_page_number: 95,
        match_version: 1,
        rank: 1,
        edit_version: 0,
        status: "suggested",
        confidence: 0.64,
        matching_method: "deterministic_solution_overlap_v1",
        source_snapshot_hash: "a".repeat(64),
        evidence: { shared_signals: ["x^2", "隐函数", "二阶导数"] },
        warning_codes: ["MATH_EQUIVALENCE_NOT_VERIFIED"],
      },
    ]);
    api.searchTextbookSourceMatches.mockResolvedValue([]);
    api.dispositionTextbookSourceMatch.mockResolvedValue({});

    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );

    const summary = await screen.findByText("教材出处（已自动匹配 1 题）");
    expect(
      screen.queryByRole("button", { name: "通过解答查找出处" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/^解答 2\(3\)/)).not.toBeVisible();
    fireEvent.click(summary);
    expect(await screen.findByText(/^解答 2\(3\)/)).toBeVisible();
    expect(screen.queryByText(/共同线索/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/deterministic_solution_overlap/),
    ).not.toBeInTheDocument();
    expect(api.searchTextbookSourceMatches).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认出处" }));
    await waitFor(() =>
      expect(api.dispositionTextbookSourceMatch).toHaveBeenCalledWith(
        "textbook-match-1",
        {
          action: "confirm",
          expected_edit_version: 0,
          expected_draft_revision_edit_version: 0,
          expected_paper_version_id: "paper-1",
          expected_source_snapshot: "a".repeat(64),
          explicit_confirmation: true,
        },
      ),
    );
  });

  it("loads a page overlay, groups current regions and draws another draft region", async () => {
    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/一、 · 分值待定/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/一、 · 分值待定/));
    fireEvent.click(screen.getByText(/调整题目位置/));
    fireEvent.click(screen.getByRole("button", { name: "加载第 1 页预览" }));
    await waitFor(() =>
      expect(assignments.pagePreview).toHaveBeenCalledWith(
        "assignment-1",
        "page-1",
      ),
    );
    expect(
      screen.getByRole("img", { name: "候选1第 1 页区域预览" }),
    ).toHaveAttribute("src", "/page-1.png");
    const existing = screen.getByRole("button", { name: "候选1区域1" });
    expect(existing).toHaveStyle({
      left: "70%",
      top: "10%",
      width: "20%",
      height: "80%",
    });

    const canvas = screen.getByLabelText("候选1区域画布");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 100,
      width: 100,
      height: 100,
      toJSON: () => ({}),
    });
    fireEvent.click(screen.getByRole("button", { name: "框选新增区域" }));
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 40, clientY: 60, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 40, clientY: 60, pointerId: 1 });

    fireEvent.click(screen.getByText("高级坐标调整"));
    expect(screen.getByLabelText("候选1区域2x")).toHaveValue(0.2);
    expect(screen.getByLabelText("候选1区域2y")).toHaveValue(0.6);
    expect(screen.getByLabelText("候选1区域2width")).toHaveValue(0.4);
    expect(screen.getByLabelText("候选1区域2height")).toHaveValue(0.3);
    expect(api.updateQuestionRegions).not.toHaveBeenCalled();
    expect(api.dispositionQuestionCandidate).not.toHaveBeenCalled();
  });

  it("requires an explicit teacher action to bind reference PDF regions", async () => {
    api.listReferenceAnswerBindings.mockResolvedValue([
      {
        id: "binding-1",
        draft_revision_id: "revision-1",
        paper_version_id: "paper-1",
        source_file_analysis_id: "analysis-1",
        source_file_name: "synthetic-reference.pdf",
        source_recognition_block_id: "block-1",
        detected_number: "2(3)",
        question_id: "question-1",
        question_number: "一、",
        binding_version: 1,
        edit_version: 0,
        status: "suggested",
        confidence: 0.99,
        warning_codes: [],
        source_snapshot_hash: "a".repeat(64),
        regions: [
          {
            id: "source-region-1",
            paper_page_id: "page-1",
            display_order: 0,
            x: 0,
            y: 0.2,
            width: 1,
            height: 0.4,
            source: "pdf_text_anchor",
            confidence: 0.99,
            evidence: {},
          },
        ],
      },
    ]);
    api.dispositionReferenceAnswerBinding.mockResolvedValue({});
    render(
      <QuestionExtractionReview
        revision={revision}
        onChanged={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/参考答案来源绑定（1）/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/参考答案来源绑定（1）/));
    expect(screen.getByText(/不接受答案、不生成评分/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "明确确认绑定" }));
    await waitFor(() =>
      expect(api.dispositionReferenceAnswerBinding).toHaveBeenCalledWith(
        "binding-1",
        {
          action: "confirm",
          expected_edit_version: 0,
          expected_draft_revision_edit_version: 0,
          expected_paper_version_id: "paper-1",
          expected_source_snapshot: "a".repeat(64),
          explicit_confirmation: true,
          question_id: "question-1",
        },
      ),
    );
    expect(api.dispositionQuestionCandidate).not.toHaveBeenCalled();
  });

  it("explicitly creates only an editable candidate from a confirmed binding", async () => {
    api.listReferenceAnswerBindings.mockResolvedValue([
      {
        id: "binding-confirmed",
        draft_revision_id: "revision-1",
        paper_version_id: "paper-1",
        source_file_analysis_id: "analysis-1",
        source_file_name: "synthetic-reference.pdf",
        source_recognition_block_id: "block-1",
        detected_number: "2(3)",
        question_id: "question-1",
        question_number: "2(3)",
        binding_version: 1,
        edit_version: 1,
        status: "confirmed",
        confidence: 0.99,
        warning_codes: [],
        source_snapshot_hash: "a".repeat(64),
        regions: [],
      },
    ]);
    api.extractReferenceAnswerCandidate.mockResolvedValue({});
    render(
      <QuestionExtractionReview
        revision={{ ...revision, teacher_edit_version: 1 }}
        onChanged={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/参考答案来源绑定（1）/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/参考答案来源绑定（1）/));
    fireEvent.click(screen.getByRole("button", { name: "生成可编辑答案候选" }));
    await waitFor(() =>
      expect(api.extractReferenceAnswerCandidate).toHaveBeenCalledWith(
        "binding-confirmed",
        {
          expected_binding_edit_version: 1,
          expected_draft_revision_edit_version: 1,
          expected_source_snapshot: "a".repeat(64),
        },
      ),
    );
    expect(api.dispositionReferenceAnswerBinding).not.toHaveBeenCalled();
    expect(api.dispositionQuestionCandidate).not.toHaveBeenCalled();
  });
});

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  assignmentGenerationApi,
  type QuestionStructureReview,
} from "@/lib/api";

import { QuestionStructureReviewPanel } from "./question-structure-review";

const initial: QuestionStructureReview = {
  id: null,
  version: 0,
  edit_version: 0,
  status: "unreviewed",
  score_policy: "unconfirmed",
  content_hash: "a".repeat(64),
  answer_unit_count: 4,
  has_missing_scores: true,
  can_confirm: false,
  items: ["2(3)", "2(5)", "12(1)", "12(2)"].map((display_number, index) => ({
    question_id: `question-${index + 1}`,
    display_number,
    parent_number: display_number.split("(")[0],
    sub_number: display_number.slice(display_number.indexOf("(") + 1, -1),
    display_order: index + 1,
    action: "keep" as const,
    max_score: null,
    source_kind: "pdf_text" as const,
    confidence: "0.99",
    regions:
      index === 0
        ? [
            {
              id: "region-1",
              paper_page_id: "page-1",
              page_number: 1,
              x: "0.1",
              y: "0.2",
              width: "0.8",
              height: "0.2",
              source: "pdf_text",
            },
            {
              id: "region-2",
              paper_page_id: "page-2",
              page_number: 2,
              x: "0.1",
              y: "0.1",
              width: "0.8",
              height: "0.2",
              source: "pdf_text",
            },
          ]
        : [],
    region_count: index === 0 ? 2 : 0,
    page_count: index === 0 ? 2 : 0,
    spans_pages: index === 0,
  })),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("QuestionStructureReviewPanel", () => {
  it("shows hierarchical answer units and autosaves teacher edits", async () => {
    vi.spyOn(assignmentGenerationApi, "getQuestionStructure").mockResolvedValue(
      initial,
    );
    const save = vi
      .spyOn(assignmentGenerationApi, "saveQuestionStructure")
      .mockImplementation(async (_assignmentId, data) => ({
        ...initial,
        id: "review-1",
        version: 1,
        edit_version: 1,
        status: "draft",
        content_hash: "b".repeat(64),
        score_policy: data.score_policy,
        items: data.items,
      }));
    render(<QuestionStructureReviewPanel assignmentId="assignment-1" />);

    expect(await screen.findByDisplayValue("2(3)")).toBeInTheDocument();
    expect(screen.getByDisplayValue("12(2)")).toBeInTheDocument();
    expect(
      screen.getByText(/PDF 未提供可信分值时，系统不会自动生成正式总分/),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("第 1 个作答单元题号"), {
      target: { value: "2(4)" },
    });
    await waitFor(() => expect(save).toHaveBeenCalledOnce(), {
      timeout: 2500,
    });
    expect(save).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({
        expected_content_hash: "a".repeat(64),
        score_policy: "unconfirmed",
      }),
    );
    expect(save.mock.calls[0][1].items[0]).toMatchObject({
      display_number: "2(4)",
      source_kind: "manual",
    });
    expect(await screen.findByText("题目清单已自动保存")).toBeInTheDocument();
  });

  it("keeps confirmation disabled until a score policy is saved", async () => {
    vi.spyOn(assignmentGenerationApi, "getQuestionStructure").mockResolvedValue(
      initial,
    );
    vi.spyOn(
      assignmentGenerationApi,
      "saveQuestionStructure",
    ).mockImplementation(async (_assignmentId, data) => ({
      ...initial,
      id: "review-1",
      version: 1,
      edit_version: 1,
      status: "draft",
      content_hash: "c".repeat(64),
      score_policy: data.score_policy,
      items: data.items,
      can_confirm: true,
    }));
    render(<QuestionStructureReviewPanel assignmentId="assignment-1" />);
    const confirm = await screen.findByRole("button", {
      name: "确认题目清单",
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("分值处理"), {
      target: { value: "equal_weight" },
    });
    await waitFor(() => expect(confirm).not.toBeDisabled(), {
      timeout: 2500,
    });
  });

  it("splits by explicit regions and merges selected units", async () => {
    vi.spyOn(assignmentGenerationApi, "getQuestionStructure").mockResolvedValue(
      initial,
    );
    const split = vi
      .spyOn(assignmentGenerationApi, "splitQuestionStructure")
      .mockResolvedValue({
        ...initial,
        id: "review-1",
        version: 1,
        edit_version: 1,
        status: "draft",
        content_hash: "d".repeat(64),
      });
    const merge = vi
      .spyOn(assignmentGenerationApi, "mergeQuestionStructure")
      .mockResolvedValue({
        ...initial,
        id: "review-1",
        version: 1,
        edit_version: 2,
        status: "draft",
        content_hash: "e".repeat(64),
      });
    render(<QuestionStructureReviewPanel assignmentId="assignment-1" />);

    expect(await screen.findByText(/跨页延续/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "按区域拆分" }));
    expect(screen.getByLabelText("区域 region-1 的新题号")).toHaveValue("2(1)");
    fireEvent.click(screen.getByRole("button", { name: "确认拆分" }));
    await waitFor(() => expect(split).toHaveBeenCalledOnce());
    expect(split).toHaveBeenCalledWith("assignment-1", {
      expected_content_hash: "a".repeat(64),
      source_question_id: "question-1",
      parts: [
        { display_number: "2(1)", region_ids: ["region-1"] },
        { display_number: "2(2)", region_ids: ["region-2"] },
      ],
      explicit_confirmation: true,
    });

    fireEvent.click(screen.getByLabelText("选择合并 2(3)"));
    fireEvent.click(screen.getByLabelText("选择合并 2(5)"));
    fireEvent.change(screen.getByLabelText("合并后的题号"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "合并所选（2）" }));
    await waitFor(() => expect(merge).toHaveBeenCalledOnce());
    expect(merge).toHaveBeenCalledWith("assignment-1", {
      expected_content_hash: "d".repeat(64),
      question_ids: ["question-1", "question-2"],
      display_number: "2",
      explicit_confirmation: true,
    });
  });
});

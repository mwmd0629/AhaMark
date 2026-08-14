import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { rubricTemplateApi, type RubricTemplate } from "@/lib/api";

import { RubricTemplateActions } from "./rubric-template-actions";

const criterion = {
  stable_key: "method",
  title: "方法",
  description: "方法正确",
  max_points: "3",
  criterion_type: "method",
  required: true,
  dependencies: [],
  validation_mode: "ai_suggestion" as const,
  manual_review_policy: {},
  partial_credit_policy: {},
  validation_rule: {},
  metadata: {},
};

const template: RubricTemplate = {
  id: "template-1",
  name: "计算题模板",
  subject: "数学",
  grade: "八年级",
  question_type: "calculation",
  status: "confirmed",
  criterion_count: 1,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  current_version: {
    id: "version-1",
    version: 1,
    title: "计算题模板",
    scoring_basis: "proportional",
    total_points: "100",
    status: "confirmed",
    content_hash: "a".repeat(64),
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    criteria: [criterion],
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RubricTemplateActions", () => {
  it("previews conversion and applies a new draft with stale guards", async () => {
    vi.spyOn(rubricTemplateApi, "list").mockResolvedValue([template]);
    vi.spyOn(rubricTemplateApi, "preview").mockResolvedValue({
      template_content_hash: "a".repeat(64),
      question_version: "question-v1",
      reference_answer_version_id: "answer-1",
      reference_answer_content_hash: "b".repeat(64),
      total_points: "5",
      criteria: [{ ...criterion, max_points: "5" }],
      blockers: [],
    });
    const apply = vi.spyOn(rubricTemplateApi, "apply").mockResolvedValue({
      structured_rubric_version_id: "rubric-2",
      replayed: false,
    });
    const onApplied = vi.fn();
    render(
      <RubricTemplateActions
        questionId="question-1"
        rubricId="rubric-1"
        onApplied={onApplied}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "使用评分模板" }));
    expect(await screen.findByText("计算题模板（1 项）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览换算" }));
    expect(await screen.findByText("换算到本题 5 分")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "应用为本题草稿" }));

    await vi.waitFor(() => expect(apply).toHaveBeenCalledOnce());
    expect(apply.mock.calls[0][1]).toMatchObject({
      template_version_id: "version-1",
      expected_template_content_hash: "a".repeat(64),
      expected_question_version: "question-v1",
      reference_answer_version_id: "answer-1",
      expected_reference_answer_content_hash: "b".repeat(64),
    });
    expect(onApplied).toHaveBeenCalledOnce();
  });

  it("saves a structured rubric as a de-identified draft template", async () => {
    vi.spyOn(rubricTemplateApi, "saveStructured").mockResolvedValue({
      ...template,
      status: "draft",
      current_version: { ...template.current_version, status: "draft" },
    });
    render(
      <RubricTemplateActions
        questionId="question-1"
        rubricId="rubric-1"
        onApplied={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "保存为模板" }));
    fireEvent.change(screen.getByLabelText("模板名称"), {
      target: { value: "代数结构" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await vi.waitFor(() =>
      expect(rubricTemplateApi.saveStructured).toHaveBeenCalledWith(
        "rubric-1",
        {
          name: "代数结构",
          scoring_basis: "proportional",
        },
      ),
    );
  });
});

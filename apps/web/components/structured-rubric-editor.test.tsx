import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StructuredRubricEditor } from "./structured-rubric-editor";

const rubric = {
  id: "rubric-1",
  question_id: "question-1",
  reference_answer_version_id: "answer-1",
  rubric_version: 1,
  title: "矩阵计算",
  total_points: "5",
  status: "draft" as const,
  criteria: [
    {
      stable_key: "result",
      title: "最终结果",
      max_points: "5",
      criterion_type: "final_answer",
      required: true,
      dependencies: [],
      validation_mode: "deterministic" as const,
      validation_rule: {
        answer_type: "diagonalization",
        domain: "rational",
        variables: ["x"],
        limits: { timeout_ms: 500 },
      },
    },
  ],
};

afterEach(cleanup);

describe("StructuredRubricEditor", () => {
  it("keeps the editor open on backdrop clicks and requires explicit cancel", () => {
    const cancel = vi.fn();
    render(
      <StructuredRubricEditor
        initial={rubric}
        onSave={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={cancel}
      />,
    );
    fireEvent.click(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("validates totals and edits mode, domain, variables, ordering and dependencies", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(
      <StructuredRubricEditor
        initial={rubric}
        onSave={save}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("评分项 1 分值"), {
      target: { value: "4" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "分项总分必须等于题目满分",
    );
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("评分项 1 数域"), {
      target: { value: "real" },
    });
    fireEvent.change(screen.getByLabelText("评分项 1 显式变量"), {
      target: { value: "x,y" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加评分项" }));
    expect(screen.getByLabelText("评分项 2 标题")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "上移" })[1]);
    expect(screen.getByLabelText("评分项 1 标题")).toHaveValue("新评分项");
    fireEvent.click(screen.getAllByRole("button", { name: "删除评分项" })[0]);
    expect(screen.queryByDisplayValue("新评分项")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("评分项 1 验证模式"), {
      target: { value: "manual_only" },
    });
    fireEvent.change(screen.getByLabelText("评分项 1 分值"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await vi.waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0].criteria[0].validation_rule).toEqual({});
  });

  it("makes confirmed versions read-only", () => {
    render(
      <StructuredRubricEditor
        initial={{ ...rubric, status: "confirmed" }}
        onSave={vi.fn()}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Rubric 标题")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "保存草稿" }),
    ).not.toBeInTheDocument();
  });

  it("offers AI suggestion scoring and clears deterministic-only rules", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(
      <StructuredRubricEditor
        initial={rubric}
        onSave={save}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("评分项 1 验证模式"), {
      target: { value: "ai_suggestion" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await vi.waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0].criteria[0]).toMatchObject({
      validation_mode: "ai_suggestion",
      validation_rule: {},
    });
  });
});

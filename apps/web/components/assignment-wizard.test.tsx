import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AssignmentWizard } from "./assignment-wizard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const question = (id: string, number: string) => ({
    id,
    question_number: number,
    display_order: Number(number),
    question_type: "proof",
    max_score: "10.00",
    difficulty: "medium" as const,
    knowledge_points: [],
    regions: [],
  });
  return {
    ...actual,
    classesApi: {
      ...actual.classesApi,
      list: vi.fn().mockResolvedValue({ items: [] }),
    },
    assignmentGenerationApi: {
      ...actual.assignmentGenerationApi,
      listJobs: vi.fn().mockResolvedValue([]),
      listRevisions: vi.fn().mockResolvedValue([]),
    },
    assignmentsApi: {
      ...actual.assignmentsApi,
      get: vi.fn().mockResolvedValue({
        id: "assignment-1",
        title: "线代期末",
        status: "draft",
        updated_at: "2026-07-25T00:00:00Z",
        classes: [],
        completeness: { ready: true, next_step: 5, issues: [] },
        paper_version: {
          id: "paper-1",
          version: 1,
          status: "draft",
          pages: [],
          questions: [question("q1", "1"), question("q2", "2")],
        },
        rubric_version: {
          id: "rubric-1",
          version: 1,
          status: "draft",
          question_rubrics: [
            {
              id: "qr1",
              question_id: "q1",
              standard_answer: "第一题已保存答案",
              items: [],
            },
            {
              id: "qr2",
              question_id: "q2",
              standard_answer: "第二题已保存答案",
              items: [],
            },
          ],
        },
      }),
    },
  };
});

it("回填标准答案并在切换题目时同步更新", async () => {
  render(<AssignmentWizard assignmentId="assignment-1" />);

  expect(
    await screen.findByDisplayValue("第一题已保存答案"),
  ).toBeInTheDocument();
  expect(screen.getByText(/AI 仅生成草稿，不能发布作业/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("当前题目"), {
    target: { value: "q2" },
  });
  await waitFor(() =>
    expect(screen.getByDisplayValue("第二题已保存答案")).toBeInTheDocument(),
  );
});

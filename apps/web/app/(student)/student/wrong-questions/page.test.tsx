import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import WrongQuestionsPage from "./page";
import { studentApi } from "@/lib/student-api";

vi.mock("@/lib/student-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/student-api")>(
      "@/lib/student-api",
    );
  return {
    ...actual,
    studentApi: {
      ...actual.studentApi,
      wrongQuestions: vi.fn(),
      teacherReviewRequests: vi.fn(),
    },
  };
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("does not link a historical review to a question that left the current wrong book", async () => {
  vi.mocked(studentApi.wrongQuestions).mockResolvedValue({ items: [] });
  vi.mocked(studentApi.teacherReviewRequests).mockResolvedValue({
    items: [
      {
        id: "review-1",
        student_id: "student-1",
        status: "resolved",
        assignment_title: "第一单元作业",
        student_question: "请复核这道题",
        student_answer_id: "answer-1",
      },
    ],
  });

  render(<WrongQuestionsPage />);
  expect(await screen.findByText("请复核这道题")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "查看对应错题" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/已不在当前错题本/)).toBeInTheDocument();
});

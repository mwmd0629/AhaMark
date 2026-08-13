import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import WrongQuestionDetailPage from "./page";
import { studentApi } from "@/lib/student-api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ wrongQuestionId: "answer-1" }),
}));
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
      messages: vi.fn(),
      askAI: vi.fn(),
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(studentApi.wrongQuestions).mockResolvedValue({
    items: [
      {
        id: "answer-1",
        answer_id: "answer-1",
        assignment_id: "assignment-1",
        assignment_title: "第一单元作业",
        question_number: "1",
        question_text: "题目内容",
        score: "0",
        max_score: "10",
        student_answer: "学生答案",
        error_reason: "计算错误",
        thread_id: "thread-1",
        thread_status: "teacher_review",
        review_request_id: "review-1",
        review_status: "pending",
        review_decision: null,
        teacher_response: null,
      },
    ],
  });
  vi.mocked(studentApi.messages).mockResolvedValue({ items: [] });
});
afterEach(cleanup);

it("disables AI questions while the thread is in teacher review", async () => {
  render(<WrongQuestionDetailPage />);
  expect(
    await screen.findByText(/当前对话已转入教师复核或已经结束/),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("你的问题")).toBeDisabled();
  expect(screen.getByRole("button", { name: "发送给 AI" })).toBeDisabled();
  expect(studentApi.askAI).not.toHaveBeenCalled();
});

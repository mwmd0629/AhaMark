import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import PracticePage from "./page";
import {
  teacherPracticeApi,
  type TeacherWrongQuestionResponse,
} from "@/lib/student-api";

vi.mock("@/lib/student-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/student-api")>(
      "@/lib/student-api",
    );
  return {
    ...actual,
    teacherPracticeApi: { wrongQuestions: vi.fn() },
  };
});

const response: TeacherWrongQuestionResponse = {
  items: [
    {
      id: "snapshot-1:answer-1",
      student_answer_id: "answer-1",
      student_id: "student-1",
      student_name: "学生甲",
      student_number: "S001",
      class_id: "class-1",
      class_name: "高一一班",
      assignment_id: "assignment-1",
      assignment_title: "函数单元测验",
      submission_id: "submission-1",
      grading_batch_id: "batch-1",
      question_id: "question-1",
      question_number: "3",
      question_type: "short_answer",
      question_content: "说明函数单调性的判断依据。",
      student_answer: "只看函数值是否为正。",
      score: "2",
      max_score: "5",
      feedback: "需要比较自变量增大时函数值的变化。",
      error_type: "concept",
      knowledge_point_ids: ["函数单调性"],
      score_snapshot_id: "snapshot-1",
      score_snapshot_version: 2,
      grade_release_id: "release-1",
      grade_release_version: 3,
      release_mode: "score_and_feedback",
      released_at: "2026-08-19T08:00:00Z",
      thread_id: "thread-1",
      thread_status: "teacher_review",
      review_request_id: "review-1",
      review_status: "pending",
    },
  ],
  page: 1,
  page_size: 30,
  total: 1,
  pages: 1,
  summary: {
    total_wrong_questions: 1,
    affected_students: 1,
    knowledge_point_count: 1,
    pending_review_count: 1,
  },
  facets: {
    classes: [{ id: "class-1", name: "高一一班" }],
    assignments: [
      {
        id: "assignment-1",
        title: "函数单元测验",
        class_ids: ["class-1"],
      },
    ],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(teacherPracticeApi.wrongQuestions).mockResolvedValue(response);
});

afterEach(cleanup);

it("renders released wrong questions and applies real teacher filters", async () => {
  render(<PracticePage />);

  expect(
    screen.getByRole("heading", { name: "错题与练习" }),
  ).toBeInTheDocument();
  expect(await screen.findByText("学生甲（S001）")).toBeInTheDocument();
  expect(screen.getByText("说明函数单调性的判断依据。")).toBeInTheDocument();
  expect(
    screen.getByText("需要比较自变量增大时函数值的变化。"),
  ).toBeInTheDocument();
  expect(screen.getByText("待教师处理")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看原批改证据" })).toHaveAttribute(
    "href",
    "/grading/batch-1/review",
  );
  expect(screen.getByRole("link", { name: "处理学生申疑" })).toHaveAttribute(
    "href",
    "/review-requests",
  );

  fireEvent.change(screen.getByLabelText("班级"), {
    target: { value: "class-1" },
  });
  await waitFor(() =>
    expect(teacherPracticeApi.wrongQuestions).toHaveBeenLastCalledWith(
      expect.stringContaining("class_id=class-1"),
    ),
  );

  fireEvent.change(screen.getByLabelText("搜索"), {
    target: { value: "concept" },
  });
  fireEvent.click(screen.getByRole("button", { name: "筛选" }));
  await waitFor(() =>
    expect(teacherPracticeApi.wrongQuestions).toHaveBeenLastCalledWith(
      expect.stringContaining("search=concept"),
    ),
  );
});

it("explains that only finalized and released wrong questions are listed", async () => {
  vi.mocked(teacherPracticeApi.wrongQuestions).mockResolvedValue({
    ...response,
    items: [],
    total: 0,
    pages: 0,
    summary: {
      total_wrong_questions: 0,
      affected_students: 0,
      knowledge_point_count: 0,
      pending_review_count: 0,
    },
  });

  render(<PracticePage />);
  expect(
    await screen.findByText("当前筛选下没有已确认错题"),
  ).toBeInTheDocument();
  expect(screen.getByText(/已定稿并发布的完整成绩快照/)).toBeInTheDocument();
});

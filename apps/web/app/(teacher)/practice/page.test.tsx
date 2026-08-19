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
} from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    teacherPracticeApi: { wrongQuestions: vi.fn() },
  };
});

const response: TeacherWrongQuestionResponse = {
  items: [
    {
      id: "snapshot-1:question-1",
      grade_release_id: "release-1",
      grade_release_version: 3,
      released_at: "2026-08-19T08:00:00Z",
      assignment_id: "assignment-1",
      assignment_title: "函数单元测验",
      class_id: "class-1",
      class_name: "高一一班",
      student_id: "student-1",
      student_name: "学生甲",
      student_number: "S001",
      submission_id: "submission-1",
      grading_batch_id: "batch-1",
      student_answer_id: "answer-1",
      question_id: "question-1",
      question_number: "3",
      question_type: "short_answer",
      question_content: "说明函数单调性的判断依据。",
      student_answer: "只看函数值是否为正。",
      score: "2",
      max_score: "5",
      score_rate: 0.4,
      feedback: "需要比较自变量增大时函数值的变化。",
      error_type: "concept",
      knowledge_point_ids: ["point-1"],
      knowledge_points: [{ id: "point-1", name: "函数单调性" }],
      snapshot_id: "snapshot-1",
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
    average_score_rate: 0.4,
  },
  facets: {
    classes: [{ id: "class-1", name: "高一一班" }],
    assignments: [{ id: "assignment-1", title: "函数单元测验" }],
    error_types: ["concept"],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(teacherPracticeApi.wrongQuestions).mockResolvedValue(response);
});

afterEach(cleanup);

it("renders formal wrong questions and applies teacher filters", async () => {
  render(<PracticePage />);

  expect(
    screen.getByRole("heading", { name: "错题与练习" }),
  ).toBeInTheDocument();
  expect(await screen.findByText("学生甲（S001）")).toBeInTheDocument();
  expect(screen.getByText("说明函数单调性的判断依据。")).toBeInTheDocument();
  expect(screen.getByText("函数单调性")).toBeInTheDocument();
  expect(screen.getByText("40%")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看原批改证据" })).toHaveAttribute(
    "href",
    "/grading/batch-1/review",
  );

  fireEvent.change(screen.getByLabelText("班级"), {
    target: { value: "class-1" },
  });
  await waitFor(() =>
    expect(teacherPracticeApi.wrongQuestions).toHaveBeenLastCalledWith(
      expect.stringContaining("class_id=class-1"),
    ),
  );

  fireEvent.change(screen.getByLabelText("错误类型"), {
    target: { value: "concept" },
  });
  await waitFor(() =>
    expect(teacherPracticeApi.wrongQuestions).toHaveBeenLastCalledWith(
      expect.stringContaining("error_type=concept"),
    ),
  );

  fireEvent.change(screen.getByLabelText("搜索"), {
    target: { value: "函数" },
  });
  fireEvent.click(screen.getByRole("button", { name: "筛选" }));
  await waitFor(() =>
    expect(teacherPracticeApi.wrongQuestions).toHaveBeenLastCalledWith(
      expect.stringContaining("search=%E5%87%BD%E6%95%B0"),
    ),
  );
});

it("explains that only finalized formal snapshots are listed", async () => {
  vi.mocked(teacherPracticeApi.wrongQuestions).mockResolvedValue({
    ...response,
    items: [],
    total: 0,
    pages: 0,
    summary: {
      total_wrong_questions: 0,
      affected_students: 0,
      knowledge_point_count: 0,
      average_score_rate: null,
    },
  });

  render(<PracticePage />);
  expect(
    await screen.findByText("当前筛选下没有已确认错题"),
  ).toBeInTheDocument();
  expect(screen.getByText(/完整正式成绩快照/)).toBeInTheDocument();
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import StudentAnalyticsPage from "./page";

const mocks = vi.hoisted(() => ({
  student: vi.fn(),
  retry: vi.fn(),
  kp: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ studentId: "student-1" }),
}));
vi.mock("@/lib/api", () => ({
  analyticsApi: {
    student: mocks.student,
    retryReport: mocks.retry,
    studentKnowledgeTrend: mocks.kp,
    reportDownload: vi.fn(),
  },
}));

it("renders student trend, revision, knowledge trend, and recreates a failed report", async () => {
  mocks.student.mockResolvedValueOnce({
    student: { name: "合成学生", student_number: "001", status: "active" },
    current: {
      total_score: 8,
      max_score: 10,
      score_rate: 0.8,
      grade_release_id: "r2",
      score_snapshot_id: "s2",
    },
    history: [
      {
        assignment_name: "作业一",
        released_at: "2026-01-01",
        total_score: 8,
        max_score: 10,
        score_rate: 0.8,
        grade_release_id: "r1",
      },
    ],
    questions: [{ knowledge_points: [{ id: "kp1", name: "代数" }] }],
    teacher_comments: [],
    score_revisions: [{ reason: "人工修订" }],
    report_jobs: [
      {
        id: "j1",
        report_type: "student_report_pdf",
        status: "failed",
        progress: 20,
        grade_release_id: "r2",
      },
    ],
  });
  mocks.kp.mockResolvedValueOnce({
    items: [
      {
        assignment_name: "作业一",
        released_at: "2026-01-01",
        score: 8,
        max_score: 10,
        mastery_rate: 0.8,
        grade_release_id: "r1",
        question_ids: ["q1"],
      },
    ],
    scoring_rule: "按 ID",
  });
  mocks.retry.mockResolvedValueOnce({
    id: "j2",
    status: "queued",
    report_type: "student_report_pdf",
  });
  render(<StudentAnalyticsPage />);
  expect(await screen.findByText(/合成学生/)).toBeInTheDocument();
  expect(screen.getByText(/人工修订/)).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /学生历史得分率趋势/ }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "代数" }));
  await screen.findByRole("img", { name: /知识点掌握率/ });
  fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
  await waitFor(() => expect(mocks.retry).toHaveBeenCalledWith("j1"));
  expect(await screen.findByText(/原任务不会恢复/)).toBeInTheDocument();
});

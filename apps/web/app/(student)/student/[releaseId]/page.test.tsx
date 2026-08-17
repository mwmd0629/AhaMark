import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import StudentAssignmentPage from "./page";

const mocks = vi.hoisted(() => ({ assignment: vi.fn() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ releaseId: "release-1" }),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/lib/api", () => ({
  studentPortalApi: {
    assignment: mocks.assignment,
    reportUrl: (releaseId: string) =>
      `/api/student/assignments/${releaseId}/report.pdf`,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("renders the formal personal score without class ranking or AI details", async () => {
  mocks.assignment.mockResolvedValue({
    release_id: "release-1",
    release_version: 2,
    assignment_title: "第一次作业",
    class_name: "高等数学",
    subject: "数学",
    total_score: 8,
    max_score: 10,
    score_rate: 0.8,
    versions: [
      { release_id: "release-1", version: 2, current: true },
      { release_id: "release-0", version: 1, current: false },
    ],
    questions: [
      {
        question_id: "question-1",
        question_number: 1,
        question_type: "short_answer",
        score: 8,
        max_score: 10,
        feedback: "步骤正确，结论需要写完整。",
        error_type: null,
        knowledge_points: [{ id: "kp-1", name: "极限" }],
      },
    ],
  });

  render(<StudentAssignmentPage />);

  expect(await screen.findByText("第一次作业")).toBeInTheDocument();
  expect(screen.getAllByText("8 / 10")).toHaveLength(2);
  expect(screen.getByText("步骤正确，结论需要写完整。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载个人报告" })).toHaveAttribute(
    "href",
    "/api/student/assignments/release-1/report.pdf",
  );
  expect(screen.queryByText(/排名|AI|置信度/)).not.toBeInTheDocument();
});

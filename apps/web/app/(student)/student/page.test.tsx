import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import StudentHomePage from "./page";

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  assignments: vi.fn(),
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
    me: mocks.me,
    assignments: mocks.assignments,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("shows only assignments explicitly returned by the student portal", async () => {
  mocks.me.mockResolvedValue({
    profiles: [{ name: "张同学", student_number: "20260001" }],
  });
  mocks.assignments.mockResolvedValue([
    {
      release_id: "release-1",
      release_version: 2,
      assignment_title: "第一次作业",
      class_name: "高等数学",
      subject: "数学",
      student_id: "student-1",
    },
  ]);

  render(<StudentHomePage />);

  expect(await screen.findByText("张同学的作业")).toBeInTheDocument();
  expect(screen.getByText("第一次作业")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /第一次作业/ })).toHaveAttribute(
    "href",
    "/student/release-1",
  );
  expect(screen.queryByText(/排名/)).not.toBeInTheDocument();
});

it("uses a clear empty state before a teacher opens any grade", async () => {
  mocks.me.mockResolvedValue({
    profiles: [{ name: "李同学", student_number: "20260002" }],
  });
  mocks.assignments.mockResolvedValue([]);

  render(<StudentHomePage />);

  expect(await screen.findByText("暂无已开放成绩")).toBeInTheDocument();
});

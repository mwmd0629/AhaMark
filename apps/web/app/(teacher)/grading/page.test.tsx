import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import GradingPage from "./page";

const api = vi.hoisted(() => ({
  listAssignments: vi.fn(),
  jointPool: vi.fn(),
  ensureJointPool: vi.fn(),
  jointInvitations: vi.fn(),
  jointWork: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("assignmentId=joint-1"),
}));

vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return {
    ...actual,
    assignmentsApi: {
      ...actual.assignmentsApi,
      list: api.listAssignments,
      jointInvitations: api.jointInvitations,
    },
    gradingApi: {
      ...actual.gradingApi,
      jointPool: api.jointPool,
      ensureJointPool: api.ensureJointPool,
      jointWork: api.jointWork,
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  api.listAssignments.mockResolvedValue({
    items: [
      {
        id: "joint-1",
        title: "大学物理联考",
        delivery_mode: "joint_exam",
        status: "published",
        classes: [
          { id: "class-1", name: "一班", status: "active" },
          { id: "class-2", name: "二班", status: "active" },
        ],
      },
    ],
  });
  api.jointPool.mockResolvedValue({ items: [], questions: [] });
  api.jointInvitations.mockResolvedValue([]);
  api.jointWork.mockResolvedValue([]);
  api.ensureJointPool.mockResolvedValue({
    questions: [
      {
        id: "question-1",
        number: "1",
        total: 0,
        reviewed: 0,
        assignment_mixed: false,
      },
    ],
    items: [
      {
        id: "batch-1",
        assignment_id: "joint-1",
        class_id: "class-1",
        class_name: "一班",
        name: "大学物理联考 · 一班",
        status: "collecting",
        submission_count: 0,
        reviewed_count: 0,
        matching: { unmatched: 0 },
      },
    ],
  });
});

afterEach(cleanup);

it("为联考创建统一批改池并显示班级边界", async () => {
  render(<GradingPage />);

  const create = await screen.findByRole("button", { name: "创建联考统批池" });
  expect(screen.getByText(/2 个班级共用一套试卷/)).toBeInTheDocument();
  fireEvent.click(create);

  await waitFor(() =>
    expect(api.ensureJointPool).toHaveBeenCalledWith("joint-1"),
  );
  expect(await screen.findByText("大学物理联考 · 一班")).toBeInTheDocument();
  expect(screen.getByText("一班")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "开始统批" })).toHaveAttribute(
    "href",
    "/grading/batch-1/review?questionId=question-1&joint=1",
  );
});

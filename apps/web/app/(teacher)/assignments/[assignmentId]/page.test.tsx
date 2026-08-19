import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AssignmentDetailPage from "./page";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, use: () => ({ assignmentId: "assignment-1" }) };
});

vi.mock("@/components/recognition-workspace", () => ({
  RecognitionWorkspace: () => null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    assignmentsApi: { ...actual.assignmentsApi, get: mocks.get },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("shows the saved assignment description and practice scope", async () => {
  mocks.get.mockResolvedValue({
    id: "assignment-1",
    title: "错题巩固练习",
    status: "published",
    updated_at: "2026-08-19T08:00:00Z",
    total_score: "10.00",
    description: "由教师从正式成绩错题中创建的未发布草稿。",
    instructions: "重点复习函数单调性，并完成教师确认后的练习题。",
    classes: [{ id: "class-1", name: "高一一班", status: "active" }],
    paper_version: {
      id: "paper-1",
      version: 1,
      status: "active",
      pages: [],
      questions: [],
    },
  });

  render(
    <AssignmentDetailPage
      params={Promise.resolve({ assignmentId: "ignored" })}
    />,
  );

  expect(await screen.findByText("错题巩固练习")).toBeInTheDocument();
  expect(
    screen.getByText("由教师从正式成绩错题中创建的未发布草稿。"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("重点复习函数单调性，并完成教师确认后的练习题。"),
  ).toBeInTheDocument();
});

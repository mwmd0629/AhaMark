import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import StudentAssignmentsPage from "./page";
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
      assignments: vi.fn(),
      submitAssignment: vi.fn(),
    },
  };
});

beforeEach(() => vi.clearAllMocks());

it("shows a truthful empty state when no assignment is published", async () => {
  vi.mocked(studentApi.assignments).mockResolvedValue({ items: [] });
  render(<StudentAssignmentsPage />);
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "暂无已发布作业" }),
    ).toBeInTheDocument(),
  );
});

it("uploads selected files through the student submission client", async () => {
  const assignment = {
    id: "assignment-1",
    class_id: "class-1",
    title: "第一单元作业",
    status: "published",
    due_at: "2099-01-01T12:00:00Z",
    submission_id: null,
    submission_status: null,
    submitted_at: null,
    max_files: 3,
    allowed_file_types: ["application/pdf", "image/png"],
  };
  vi.mocked(studentApi.assignments).mockResolvedValue({ items: [assignment] });
  vi.mocked(studentApi.submitAssignment).mockResolvedValue({
    id: "submission-1",
    assignment_id: assignment.id,
    class_id: assignment.class_id,
    status: "submitted",
  });
  render(<StudentAssignmentsPage />);
  await screen.findByRole("heading", { name: assignment.title });
  const file = new File(["synthetic answer"], "answer.pdf", {
    type: "application/pdf",
  });
  const fileInput = screen.getByLabelText("选择作业文件");
  expect(fileInput).toHaveAttribute("accept", "application/pdf,image/png");
  fireEvent.change(fileInput, {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
  await waitFor(() =>
    expect(studentApi.submitAssignment).toHaveBeenCalledWith(assignment, [
      file,
    ]),
  );
});

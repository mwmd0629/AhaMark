import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import NotificationsPage from "./page";

const listClasses = vi.hoisted(() => vi.fn());
const listAssignments = vi.hoisted(() => vi.fn());

vi.mock("@/components/auth-gate", () => ({
  useAuthUser: () => ({ id: "teacher-1", display_name: "陈老师" }),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    classesApi: { ...actual.classesApi, list: listClasses },
    assignmentsApi: { ...actual.assignmentsApi, list: listAssignments },
  };
});

beforeEach(() => {
  localStorage.clear();
  listClasses.mockResolvedValue({
    items: [
      {
        id: "class-1",
        name: "一班",
        status: "active",
        active_student_count: 0,
        student_count: 0,
        group_count: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-09T00:00:00Z",
      },
    ],
  });
  listAssignments.mockResolvedValue({
    items: [
      {
        id: "draft-1",
        title: "线性代数练习",
        status: "draft",
        updated_at: "2026-08-10T00:00:00Z",
        classes: [{ id: "class-1", name: "一班", status: "active" }],
      },
    ],
  });
});
afterEach(cleanup);

it("builds specific actionable reminders and persists read state per account", async () => {
  render(<NotificationsPage />);
  await screen.findByText("继续完善“线性代数练习”");
  expect(screen.getByRole("link", { name: "继续编辑" })).toHaveAttribute(
    "href",
    "/assignments/draft-1/edit",
  );
  expect(screen.getByText("“一班”还没有学生")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "未读 2" })).toBeInTheDocument();

  fireEvent.click(screen.getAllByRole("button", { name: "标为已读" })[0]);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "未读 1" })).toBeInTheDocument(),
  );
  expect(
    localStorage.getItem("ahamark.notifications.read.teacher-1"),
  ).toBeTruthy();
  expect(localStorage.getItem("ahamark.notifications.unread.teacher-1")).toBe(
    "1",
  );
});

it("can mark everything read and still show it in the all view", async () => {
  render(<NotificationsPage />);
  await screen.findByText("继续完善“线性代数练习”");
  fireEvent.click(screen.getByRole("button", { name: "全部标为已读" }));
  expect(screen.getByText("没有未读提醒")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "全部 2" }));
  expect(screen.getByText("继续完善“线性代数练习”")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "标为未读" })).toHaveLength(2);
});

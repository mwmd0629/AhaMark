import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import SearchPage from "./page";

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
  listClasses.mockResolvedValue({
    items: [
      {
        id: "class-1",
        name: "高一代数班",
        grade: "高一",
        subject: "数学",
        status: "active",
        student_count: 30,
        active_student_count: 30,
        group_count: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-09T00:00:00Z",
      },
    ],
  });
  listAssignments.mockResolvedValue({
    items: [
      {
        id: "assignment-1",
        title: "矩阵运算练习",
        subject: "数学",
        grade: "高一",
        status: "draft",
        updated_at: "2026-08-10T00:00:00Z",
        classes: [{ id: "class-1", name: "高一代数班", status: "active" }],
      },
    ],
  });
});
afterEach(cleanup);

it("shows concise common actions before searching and searches account data by multiple fields", async () => {
  render(<SearchPage />);
  await waitFor(() => expect(screen.getByText("常用入口")).toBeInTheDocument());
  expect(screen.getByText("创建作业")).toBeInTheDocument();
  expect(screen.queryByText("矩阵运算练习")).not.toBeInTheDocument();

  fireEvent.change(
    screen.getByPlaceholderText("输入作业名、班级名或想做的操作"),
    {
      target: { value: "高一 数学" },
    },
  );
  expect(screen.getByText("高一代数班")).toBeInTheDocument();
  expect(screen.getByText("矩阵运算练习")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /矩阵运算练习/ })).toHaveAttribute(
    "href",
    "/assignments/assignment-1/edit",
  );
});

it("supports category filtering, clearing, and action-language search", async () => {
  render(<SearchPage />);
  await screen.findByText("常用入口");
  const input = screen.getByPlaceholderText("输入作业名、班级名或想做的操作");
  fireEvent.change(input, { target: { value: "确认建议分" } });
  expect(screen.getByText("批改与复核")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^作业 / }));
  expect(screen.getByText("没有找到匹配内容")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清空" }));
  expect(input).toHaveValue("");
});

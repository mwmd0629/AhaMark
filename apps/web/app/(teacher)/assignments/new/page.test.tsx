import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import NewAssignmentPage from "./page";

const api = vi.hoisted(() => ({
  listClasses: vi.fn(),
  createAssignment: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return {
    ...actual,
    classesApi: { ...actual.classesApi, list: api.listClasses },
    assignmentsApi: {
      ...actual.assignmentsApi,
      create: api.createAssignment,
    },
  };
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("等待班级请求完成后才判断是否为空", async () => {
  const request = deferred<{ items: never[] }>();
  api.listClasses.mockReturnValue(request.promise);
  render(<NewAssignmentPage />);

  expect(screen.getByText("正在加载活动班级…")).toBeInTheDocument();
  expect(screen.queryByText("没有活动班级")).not.toBeInTheDocument();

  request.resolve({ items: [] });
  expect(await screen.findByText("没有活动班级")).toBeInTheDocument();
});

it("班级请求失败时提供可重试的错误状态", async () => {
  api.listClasses.mockRejectedValue(new Error("network"));
  render(<NewAssignmentPage />);

  expect(
    await screen.findByText("无法加载班级，请稍后重试。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
});

it("从所选班级继承一致的大学课程信息", async () => {
  api.listClasses.mockResolvedValue({
    items: [
      {
        id: "class-1",
        name: "高等代数 1 班",
        subject: "高等代数",
        grade: "研究生",
        academic_year: "2026-2027",
        semester: "秋季",
        status: "active",
        student_count: 20,
        active_student_count: 20,
        group_count: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
      },
    ],
  });
  api.createAssignment.mockResolvedValue({ id: "assignment-1" });
  render(<NewAssignmentPage />);

  fireEvent.change(await screen.findByRole("textbox", { name: /作业名称/ }), {
    target: { value: "第一次作业" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /高等代数 1 班/ }));
  fireEvent.click(screen.getByRole("button", { name: "保存草稿并继续" }));

  await waitFor(() =>
    expect(api.createAssignment).toHaveBeenCalledWith({
      title: "第一次作业",
      delivery_mode: "class_assignment",
      subject: "高等代数",
      grade: "研究生",
      class_ids: ["class-1"],
    }),
  );
});

it("联考统批至少选择两个班级并提交联考模式", async () => {
  api.listClasses.mockResolvedValue({
    items: [
      {
        id: "class-1",
        name: "大学物理 1 班",
        subject: "大学物理",
        grade: "大一",
        status: "active",
      },
      {
        id: "class-2",
        name: "大学物理 2 班",
        subject: "大学物理",
        grade: "大一",
        status: "active",
      },
    ],
  });
  api.createAssignment.mockResolvedValue({ id: "joint-1" });
  render(<NewAssignmentPage />);

  fireEvent.change(await screen.findByRole("textbox", { name: /作业名称/ }), {
    target: { value: "大学物理联考" },
  });
  fireEvent.click(screen.getByRole("radio", { name: /联考统批/ }));
  const save = screen.getByRole("button", { name: "保存草稿并继续" });
  fireEvent.click(screen.getByRole("checkbox", { name: /大学物理 1 班/ }));
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /大学物理 2 班/ }));
  expect(save).toBeEnabled();
  fireEvent.click(save);

  await waitFor(() =>
    expect(api.createAssignment).toHaveBeenCalledWith({
      title: "大学物理联考",
      delivery_mode: "joint_exam",
      subject: "大学物理",
      grade: "大一",
      class_ids: ["class-1", "class-2"],
    }),
  );
});

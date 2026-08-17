import { Suspense } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui";
import ClassDetailPage from "./page";

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, use: () => ({ classId: "class-1" }) };
});

const mocks = vi.hoisted(() => ({
  getClass: vi.fn(),
  listStudents: vi.fn(),
  addStudent: vi.fn(),
  removeStudent: vi.fn(),
  linkAccount: vi.fn(),
  listGroups: vi.fn(),
  createGroup: vi.fn(),
  removeGroup: vi.fn(),
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  listResources: vi.fn(),
  uploadResource: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    body = { message: "请求失败" };
  },
  classesApi: {
    get: mocks.getClass,
    resources: mocks.listResources,
    uploadResource: mocks.uploadResource,
  },
  studentsApi: {
    list: mocks.listStudents,
    add: mocks.addStudent,
    remove: mocks.removeStudent,
    linkAccount: mocks.linkAccount,
    unlinkAccount: vi.fn(),
  },
  groupsApi: {
    list: mocks.listGroups,
    create: mocks.createGroup,
    remove: mocks.removeGroup,
  },
  importsApi: {
    preview: mocks.previewImport,
    confirm: mocks.confirmImport,
    templateUrl: vi.fn(),
  },
}));

describe("class detail student creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getClass.mockResolvedValue({
      id: "class-1",
      name: "线性代数演示班",
      status: "active",
      student_count: 0,
      active_student_count: 0,
      group_count: 0,
      created_at: "2026-07-28T00:00:00Z",
      updated_at: "2026-07-28T00:00:00Z",
    });
    mocks.listStudents.mockResolvedValue({
      items: [],
      page: 1,
      page_size: 50,
      total: 0,
      pages: 0,
    });
    mocks.listGroups.mockResolvedValue([]);
    mocks.listResources.mockResolvedValue([
      {
        id: "resource-1",
        title: "矩阵习题",
        file_name: "矩阵习题.pdf",
        resource_type: "exercise",
        page_count: 3,
        status: "ready",
        created_at: "2026-08-12T00:00:00Z",
      },
    ]);
    mocks.addStudent.mockResolvedValue({
      id: "student-1",
      name: "演示学生",
      student_number: "0001",
      status: "active",
      membership_status: "active",
      joined_at: "2026-07-28T00:00:00Z",
      groups: [],
      assignment_history: [],
    });
  });

  it("reports success and closes the dialog after the student is created", async () => {
    render(
      <ToastProvider>
        <Suspense fallback={<div>加载中</div>}>
          <ClassDetailPage params={Promise.resolve({ classId: "class-1" })} />
        </Suspense>
      </ToastProvider>,
    );

    expect(await screen.findByText("线性代数演示班")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "添加学生" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "演示学生" },
    });
    fireEvent.change(screen.getByLabelText(/学号/), {
      target: { value: "0001" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认添加" }));

    await waitFor(() =>
      expect(mocks.addStudent).toHaveBeenCalledWith("class-1", {
        name: "演示学生",
        student_number: "0001",
        email: undefined,
      }),
    );
    expect(await screen.findByText("学生已加入班级")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("添加失败")).not.toBeInTheDocument();
    expect(mocks.getClass).toHaveBeenCalledTimes(2);
    expect(mocks.listStudents).toHaveBeenCalledTimes(2);
  });

  it("shows organized class resources and the reusable upload controls", async () => {
    render(
      <ToastProvider>
        <Suspense fallback={<div>加载中</div>}>
          <ClassDetailPage params={Promise.resolve({ classId: "class-1" })} />
        </Suspense>
      </ToastProvider>,
    );

    expect(await screen.findByText("班级资料")).toBeInTheDocument();
    expect(screen.getByText("矩阵习题")).toBeInTheDocument();
    expect(screen.getByLabelText("选择班级资料文件")).toHaveAttribute(
      "accept",
      ".pdf,.png,.jpg,.jpeg",
    );
    expect(screen.getByLabelText("资料类型")).toHaveValue("exercise");
    expect(screen.getByRole("button", { name: "添加资料" })).toBeEnabled();
  });
});

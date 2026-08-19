import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import AdminAccountsPage from "./page";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  resetPassword: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/components/auth-gate", () => ({
  useAuthUser: () => ({ id: "admin-1", username: "root-admin" }),
}));
vi.mock("@/components/ui", async (load) => {
  const actual = await load<typeof import("@/components/ui")>();
  return { ...actual, useToast: () => mocks.toast };
});
vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return {
    ...actual,
    adminAccountsApi: {
      list: mocks.list,
      create: mocks.create,
      update: mocks.update,
      resetPassword: mocks.resetPassword,
    },
  };
});

const listing = {
  items: [
    {
      id: "admin-1",
      username: "root-admin",
      display_name: "平台主管",
      account_type: "admin",
      status: "active",
      active_session_count: 1,
      last_seen_at: "2026-08-19T08:00:00Z",
      created_at: "2026-08-19T08:00:00Z",
      updated_at: "2026-08-19T08:00:00Z",
    },
    {
      id: "teacher-1",
      username: "teacher-one",
      display_name: "王老师",
      account_type: "teacher",
      status: "active",
      active_session_count: 2,
      last_seen_at: null,
      created_at: "2026-08-19T08:00:00Z",
      updated_at: "2026-08-19T08:00:00Z",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
  summary: {
    teacher: { total: 1, active: 1 },
    student: { total: 0, active: 0 },
    admin: { total: 1, active: 1 },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue(listing);
  mocks.create.mockResolvedValue({});
  mocks.update.mockResolvedValue({});
});
afterEach(cleanup);

it("shows three account categories and protects the current administrator", async () => {
  render(<AdminAccountsPage />);
  expect(await screen.findByText("王老师")).toBeInTheDocument();
  expect(screen.getByText("教师账号")).toBeInTheDocument();
  expect(screen.getByText("学生账号")).toBeInTheDocument();
  expect(screen.getByText("管理员账号")).toBeInTheDocument();
  expect(screen.getByText("2 个在线会话")).toBeInTheDocument();

  const rootRow = screen.getByText("平台主管").closest("tr");
  expect(rootRow).not.toBeNull();
  expect(rootRow!.querySelector("button")!).not.toBeDisabled();
  expect(rootRow!.querySelectorAll("button")[1]).toBeDisabled();
  expect(rootRow!.querySelectorAll("button")[2]).toBeDisabled();
});

it("creates an account without echoing its password", async () => {
  render(<AdminAccountsPage />);
  await screen.findByText("王老师");
  fireEvent.click(screen.getByRole("button", { name: "创建账号" }));
  fireEvent.change(screen.getByLabelText(/姓名/), {
    target: { value: "李老师" },
  });
  fireEvent.change(screen.getByLabelText(/用户名/), {
    target: { value: "teacher-two" },
  });
  const passwordInputs = screen.getAllByLabelText(/密码/);
  fireEvent.change(passwordInputs[0], { target: { value: "new-pass-456" } });
  fireEvent.change(passwordInputs[1], { target: { value: "new-pass-456" } });
  fireEvent.click(screen.getByRole("button", { name: "创建并启用" }));

  await waitFor(() =>
    expect(mocks.create).toHaveBeenCalledWith({
      username: "teacher-two",
      display_name: "李老师",
      password: "new-pass-456",
      account_type: "teacher",
    }),
  );
  expect(mocks.toast).toHaveBeenCalledWith("账号已创建，可立即使用用户名登录");
});

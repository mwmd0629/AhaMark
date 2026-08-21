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
  bulkCreate: vi.fn(),
  bulkAction: vi.fn(),
  audit: vi.fn(),
  security: vi.fn(),
  revokeSession: vi.fn(),
  exportUrl: vi.fn(),
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
      bulkCreate: mocks.bulkCreate,
      bulkAction: mocks.bulkAction,
      audit: mocks.audit,
      security: mocks.security,
      revokeSession: mocks.revokeSession,
      exportUrl: mocks.exportUrl,
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
  mocks.bulkCreate.mockResolvedValue({
    created: [{}],
    errors: [],
    requested_count: 1,
  });
  mocks.bulkAction.mockResolvedValue({
    action: "deactivate",
    requested_count: 1,
    processed: [
      {
        account_id: "teacher-1",
        username: "teacher-one",
        status: "inactive",
        changed: true,
        sessions_revoked: 2,
      },
    ],
    errors: [],
  });
  mocks.security.mockResolvedValue({
    failed_logins_24h: 2,
    active_sessions: 2,
    accounts_with_multiple_sessions: 1,
    never_logged_in_accounts: 3,
    stale_accounts_90d: 1,
    sessions: [
      {
        id: "session-current",
        user_id: "admin-1",
        username: "root-admin",
        created_at: "2026-08-19T08:00:00Z",
        last_seen_at: "2026-08-19T08:00:00Z",
        expires_at: "2026-08-19T20:00:00Z",
        is_current: true,
      },
      {
        id: "session-teacher",
        user_id: "teacher-1",
        username: "teacher-one",
        created_at: "2026-08-19T08:00:00Z",
        last_seen_at: "2026-08-19T08:00:00Z",
        expires_at: "2026-08-19T20:00:00Z",
        is_current: false,
      },
    ],
  });
  mocks.revokeSession.mockResolvedValue({ ok: true });
  mocks.exportUrl.mockReturnValue(
    "http://localhost:8000/api/admin/accounts/export.csv",
  );
  mocks.audit.mockResolvedValue({
    items: [
      {
        id: "audit-1",
        action: "admin.account.create",
        actor_username: "root-admin",
        target_username: "teacher-one",
        details: {},
        created_at: "2026-08-19T08:00:00Z",
      },
    ],
    total: 1,
    limit: 30,
    offset: 0,
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows three account categories and protects the current administrator", async () => {
  render(<AdminAccountsPage />);
  expect(await screen.findByText("王老师")).toBeInTheDocument();
  expect(screen.getByText("教师账号")).toBeInTheDocument();
  expect(screen.getByText("学生账号")).toBeInTheDocument();
  expect(screen.getByText("管理员账号")).toBeInTheDocument();
  expect(screen.getByText("2 个在线会话")).toBeInTheDocument();
  expect(screen.getByText("最近账号操作")).toBeInTheDocument();
  expect(screen.getByText(/root-admin → teacher-one/)).toBeInTheDocument();

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

it("previews a CSV without displaying passwords and imports valid rows", async () => {
  render(<AdminAccountsPage />);
  await screen.findByText("王老师");
  fireEvent.click(screen.getByRole("button", { name: "批量导入" }));
  const file = new File(["placeholder"], "accounts.csv", { type: "text/csv" });
  Object.defineProperty(file, "text", {
    value: vi
      .fn()
      .mockResolvedValue(
        "username,display_name,account_type,password\n" +
          "teacher-bulk,批量教师,teacher,secret-pass-123",
      ),
  });
  fireEvent.change(screen.getByLabelText("选择 CSV 文件"), {
    target: { files: [file] },
  });

  expect(await screen.findByText("teacher-bulk")).toBeInTheDocument();
  expect(screen.getByText("可以导入")).toBeInTheDocument();
  expect(screen.queryByText("secret-pass-123")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "导入有效账号" }));
  await waitFor(() =>
    expect(mocks.bulkCreate).toHaveBeenCalledWith([
      {
        username: "teacher-bulk",
        display_name: "批量教师",
        password: "secret-pass-123",
        account_type: "teacher",
      },
    ]),
  );
});

it("requires confirmation for batch operations and shows partial result totals", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminAccountsPage />);
  await screen.findByText("王老师");
  fireEvent.click(screen.getByLabelText("选择账号 teacher-one"));
  fireEvent.click(screen.getByRole("button", { name: "批量停用" }));

  await waitFor(() =>
    expect(mocks.bulkAction).toHaveBeenCalledWith(["teacher-1"], "deactivate"),
  );
  expect(window.confirm).toHaveBeenCalledWith(
    expect.stringContaining("撤销其全部会话"),
  );
  expect(mocks.toast).toHaveBeenCalledWith(
    "已处理 1 个账号，撤销 2 个会话",
    "success",
  );
});

it("shows security metrics and revokes a non-current session", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminAccountsPage />);
  expect(await screen.findByText("账号安全")).toBeInTheDocument();
  expect(screen.getByText("24 小时失败登录")).toBeInTheDocument();
  expect(screen.getByText("多设备账号")).toBeInTheDocument();
  expect(screen.getByText("当前会话")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "撤销会话" }));
  await waitFor(() =>
    expect(mocks.revokeSession).toHaveBeenCalledWith("session-teacher"),
  );
  expect(mocks.toast).toHaveBeenCalledWith("会话已撤销，该设备需要重新登录");
});

it("exports the account list with the current filters", async () => {
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  render(<AdminAccountsPage />);
  await screen.findByText("王老师");
  fireEvent.change(screen.getByLabelText("搜索"), {
    target: { value: "teacher" },
  });
  fireEvent.click(screen.getByRole("button", { name: "导出当前清单" }));
  expect(mocks.exportUrl).toHaveBeenCalledWith({
    query: "teacher",
    account_type: "",
    status: "",
  });
  expect(click).toHaveBeenCalledOnce();
});

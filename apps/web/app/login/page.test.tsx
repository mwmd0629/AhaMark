import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import LoginPage from "./page";
import { ApiError } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: { ...actual.authApi, login: mocks.login },
  };
});

beforeEach(() => {
  mocks.login.mockReset();
  mocks.replace.mockReset();
});
afterEach(cleanup);

it("logs in with an administrator-issued username instead of an email", async () => {
  mocks.login.mockResolvedValue({
    id: "teacher-1",
    username: "teacher01",
    email: "teacher01@ahamark.local",
    display_name: "测试教师",
    roles: ["teacher"],
  });

  render(<LoginPage />);

  expect(screen.getByText("账号由管理员统一发放")).toBeInTheDocument();
  expect(screen.queryByLabelText("邮箱")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("用户名"), {
    target: { value: "teacher01" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "secure-pass-123" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "登录" }).closest("form")!,
  );

  await waitFor(() =>
    expect(mocks.login).toHaveBeenCalledWith("teacher01", "secure-pass-123"),
  );
  expect(mocks.replace).toHaveBeenCalledWith("/dashboard");
});

it("routes administrators to the account operations center", async () => {
  mocks.login.mockResolvedValue({
    id: "admin-1",
    username: "root-admin",
    email: "root-admin@ahamark.local",
    display_name: "平台主管",
    roles: ["admin"],
  });
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("用户名"), {
    target: { value: "root-admin" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "secure-pass-123" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "登录" }).closest("form")!,
  );
  await waitFor(() =>
    expect(mocks.replace).toHaveBeenCalledWith("/admin/accounts"),
  );
});

it("shows a useful message and re-enables login when the API is unreachable", async () => {
  mocks.login.mockRejectedValueOnce(
    new ApiError(0, {
      code: "NETWORK_ERROR",
      message: "无法连接服务器，请检查网络或确认后端服务已启动。",
      details: {},
      request_id: "",
    }),
  );
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("用户名"), {
    target: { value: "teacher01" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "secure-pass-123" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "登录" }).closest("form")!,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "无法连接服务器，请检查网络或确认后端服务已启动。",
  );
  expect(screen.getByRole("button", { name: "登录" })).toBeEnabled();
  expect(mocks.replace).not.toHaveBeenCalled();
});

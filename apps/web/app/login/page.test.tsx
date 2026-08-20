import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import LoginPage from "./page";
import { ApiError, authApi } from "@/lib/api";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: { ...actual.authApi, login: vi.fn(), logout: vi.fn() },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.login).mockResolvedValue({
    id: "user-1",
    email: "student@example.com",
    login_name: "S001",
    recovery_email_verified: false,
    display_name: "学生甲",
    must_change_password: false,
    roles: ["student"],
    active_student_link: true,
    landing_surface: "student",
  });
  vi.mocked(authApi.logout).mockResolvedValue(undefined);
});

afterEach(cleanup);

function submitLogin(identifier = "S001") {
  fireEvent.change(screen.getByLabelText("账号（教师邮箱或学生学号）"), {
    target: { value: identifier },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "password123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
}

it("routes a linked student account to the student portal", async () => {
  render(<LoginPage />);
  expect(screen.getByRole("heading", { name: "师生登录" })).toBeInTheDocument();
  submitLogin();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
  expect(authApi.login).toHaveBeenCalledWith("S001", "password123");
  expect(screen.getByRole("link", { name: "忘记密码？" })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
});

it("routes a temporary-password account to mandatory password change", async () => {
  vi.mocked(authApi.login).mockResolvedValue({
    id: "user-1",
    email: "student@example.com",
    login_name: "S001",
    recovery_email_verified: false,
    display_name: "学生甲",
    must_change_password: true,
    roles: ["student"],
    active_student_link: true,
    landing_surface: "change_password",
  });
  render(<LoginPage />);
  submitLogin();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
});

it("routes a non-student authenticated account to the teacher dashboard", async () => {
  vi.mocked(authApi.login).mockResolvedValue({
    id: "teacher-1",
    email: "teacher@example.com",
    login_name: "teacher@example.com",
    recovery_email_verified: true,
    display_name: "教师甲",
    must_change_password: false,
    roles: ["teacher"],
    active_student_link: false,
    landing_surface: "teacher",
  });
  render(<LoginPage />);
  submitLogin("teacher@example.com");
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
  expect(authApi.login).toHaveBeenCalledWith(
    "teacher@example.com",
    "password123",
  );
});

it("ends an unavailable account session instead of routing it to the teacher shell", async () => {
  vi.mocked(authApi.login).mockResolvedValue({
    id: "student-1",
    email: "student@example.com",
    login_name: "S001",
    recovery_email_verified: false,
    display_name: "学生甲",
    must_change_password: false,
    roles: ["student"],
    active_student_link: false,
    landing_surface: "account_unavailable",
  });
  render(<LoginPage />);
  submitLogin();
  await screen.findByRole("alert");
  expect(authApi.logout).toHaveBeenCalledTimes(1);
  expect(replace).not.toHaveBeenCalled();
});

it("shows a server connection message when the API cannot be reached", async () => {
  vi.mocked(authApi.login).mockRejectedValueOnce(
    new ApiError(0, {
      code: "NETWORK_ERROR",
      message: "无法连接服务器，请检查网络或确认后端服务已启动。",
      details: {},
      request_id: "",
    }),
  );
  render(<LoginPage />);
  submitLogin();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "无法连接服务器，请检查网络或确认后端服务已启动。",
  );
  expect(screen.getByRole("button", { name: "登录" })).toBeEnabled();
  expect(replace).not.toHaveBeenCalled();
  expect(authApi.logout).not.toHaveBeenCalled();
});

it("keeps the backend authentication message for invalid credentials", async () => {
  vi.mocked(authApi.login).mockRejectedValueOnce(
    new ApiError(401, {
      code: "HTTP_401",
      message: "账号或密码错误",
      details: {},
      request_id: "request-1",
    }),
  );
  render(<LoginPage />);
  submitLogin();
  expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码错误");
});

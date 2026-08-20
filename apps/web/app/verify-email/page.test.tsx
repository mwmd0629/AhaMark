import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import VerifyEmailPage from "./page";
import { authApi } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      me: vi.fn(),
      updateRecoveryEmail: vi.fn(),
      requestEmailVerification: vi.fn(),
      confirmEmailVerification: vi.fn(),
    },
  };
});

const studentUser = {
  id: "student-user",
  email: "student@example.com",
  login_name: "20260001",
  recovery_email_verified: false,
  display_name: "学生甲",
  must_change_password: false,
  roles: ["student"],
  active_student_link: true,
  landing_surface: "student" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.me).mockResolvedValue(studentUser);
  vi.mocked(authApi.updateRecoveryEmail).mockResolvedValue(studentUser);
  vi.mocked(authApi.requestEmailVerification).mockResolvedValue({
    challenge_id: "challenge-1",
    message: "验证码已发送至安全邮箱。",
    expires_in_seconds: 600,
    development_code: "654321",
  });
  vi.mocked(authApi.confirmEmailVerification).mockResolvedValue({
    ...studentUser,
    recovery_email_verified: true,
  });
});

afterEach(cleanup);

it("sends and confirms a recovery email verification code", async () => {
  render(<VerifyEmailPage />);
  fireEvent.click(
    await screen.findByRole("button", { name: "发送邮箱验证码" }),
  );
  expect(await screen.findByText(/开发环境验证码：654321/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("邮箱验证码"), {
    target: { value: "654321" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认验证" }));

  await waitFor(() =>
    expect(authApi.confirmEmailVerification).toHaveBeenCalledWith(
      "challenge-1",
      "654321",
    ),
  );
  expect(await screen.findByText(/安全邮箱验证成功/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "返回学生端" })).toHaveAttribute(
    "href",
    "/student",
  );
});

it("lets a student add or replace the optional recovery email", async () => {
  vi.mocked(authApi.me).mockResolvedValueOnce({
    ...studentUser,
    email: null,
  });
  vi.mocked(authApi.updateRecoveryEmail).mockResolvedValueOnce({
    ...studentUser,
    email: "new@example.com",
  });

  render(<VerifyEmailPage />);
  expect(await screen.findByText("尚未设置")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("新安全邮箱（选填）"), {
    target: { value: "new@example.com" },
  });
  fireEvent.change(screen.getByLabelText("当前登录密码"), {
    target: { value: "Current-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加安全邮箱" }));

  await waitFor(() =>
    expect(authApi.updateRecoveryEmail).toHaveBeenCalledWith(
      "new@example.com",
      "Current-password-123",
    ),
  );
  expect(await screen.findByText(/安全邮箱已保存/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "发送邮箱验证码" }),
  ).toBeInTheDocument();
});

it("lets a student clear the recovery email", async () => {
  vi.mocked(authApi.updateRecoveryEmail).mockResolvedValueOnce({
    ...studentUser,
    email: null,
  });

  render(<VerifyEmailPage />);
  const email = await screen.findByLabelText("新安全邮箱（选填）");
  fireEvent.change(email, { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("当前登录密码"), {
    target: { value: "Current-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "清除安全邮箱" }));

  await waitFor(() =>
    expect(authApi.updateRecoveryEmail).toHaveBeenCalledWith(
      null,
      "Current-password-123",
    ),
  );
  expect(await screen.findByText(/安全邮箱已清除/)).toBeInTheDocument();
});

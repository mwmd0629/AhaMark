import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ForgotPasswordPage from "./page";
import { authApi } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      requestPasswordReset: vi.fn(),
      confirmPasswordReset: vi.fn(),
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.requestPasswordReset).mockResolvedValue({
    challenge_id: "challenge-1",
    message: "验证码已发送至安全邮箱。",
    expires_in_seconds: 600,
    development_code: "123456",
  });
  vi.mocked(authApi.confirmPasswordReset).mockResolvedValue({
    message: "密码已重置，请使用新密码登录。",
  });
});

afterEach(cleanup);

async function requestChallenge() {
  fireEvent.change(screen.getByLabelText("学号"), {
    target: { value: "20260001" },
  });
  fireEvent.change(screen.getByLabelText("安全邮箱"), {
    target: { value: "student@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));
  await screen.findByText(/开发环境验证码：123456/);
}

it("requests a challenge and resets the password in the second stage", async () => {
  render(<ForgotPasswordPage />);
  await requestChallenge();
  expect(authApi.requestPasswordReset).toHaveBeenCalledWith(
    "20260001",
    "student@example.com",
  );

  fireEvent.change(screen.getByLabelText("邮箱验证码"), {
    target: { value: "123456" },
  });
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "New-password-123" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "New-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认重置密码" }));

  await waitFor(() =>
    expect(authApi.confirmPasswordReset).toHaveBeenCalledWith(
      "challenge-1",
      "123456",
      "New-password-123",
    ),
  );
  expect(await screen.findByRole("status")).toHaveTextContent("密码已重置");
  expect(screen.getByRole("link", { name: "返回登录" })).toHaveAttribute(
    "href",
    "/login",
  );
});

it("rejects mismatched password confirmation before calling the API", async () => {
  render(<ForgotPasswordPage />);
  await requestChallenge();
  fireEvent.change(screen.getByLabelText("邮箱验证码"), {
    target: { value: "123456" },
  });
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "New-password-123" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "Different-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认重置密码" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "两次输入的新密码不一致",
  );
  expect(authApi.confirmPasswordReset).not.toHaveBeenCalled();
});

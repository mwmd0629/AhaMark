import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import LoginPage from "./page";

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

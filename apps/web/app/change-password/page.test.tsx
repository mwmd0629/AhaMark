import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ChangePasswordPage from "./page";

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  changePassword: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      me: mocks.me,
      changePassword: mocks.changePassword,
    },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.me.mockResolvedValue({
    id: "student-1",
    username: "student01",
    email: "student01@ahamark.local",
    display_name: "测试学生",
    roles: ["student"],
    must_change_password: true,
  });
  mocks.changePassword.mockResolvedValue({
    id: "student-1",
    username: "student01",
    email: "student01@ahamark.local",
    display_name: "测试学生",
    roles: ["student"],
    must_change_password: false,
  });
});
afterEach(cleanup);

it("changes a password voluntarily and routes the student home", async () => {
  render(<ChangePasswordPage />);
  expect(await screen.findByText(/修改密码是自愿操作/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("当前密码"), {
    target: { value: "initial-pass-123" },
  });
  fireEvent.change(screen.getByLabelText(/^新密码/), {
    target: { value: "new-secure-pass-456" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "new-secure-pass-456" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));
  await waitFor(() =>
    expect(mocks.changePassword).toHaveBeenCalledWith(
      "initial-pass-123",
      "new-secure-pass-456",
    ),
  );
  expect(mocks.replace).toHaveBeenCalledWith("/student");
});

it("rejects mismatched confirmation without calling the API", async () => {
  render(<ChangePasswordPage />);
  await screen.findByText(/修改密码是自愿操作/);
  fireEvent.change(screen.getByLabelText("当前密码"), {
    target: { value: "initial-pass-123" },
  });
  fireEvent.change(screen.getByLabelText(/^新密码/), {
    target: { value: "new-secure-pass-456" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "different-pass-789" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "两次输入的新密码不一致",
  );
  expect(mocks.changePassword).not.toHaveBeenCalled();
});

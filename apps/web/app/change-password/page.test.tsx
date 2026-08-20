import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ChangePasswordPage from "./page";
import { authApi } from "@/lib/api";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      changePassword: vi.fn(),
      logout: vi.fn(),
    },
  };
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("sends an unverified student to recovery email verification after changing the temporary password", async () => {
  vi.mocked(authApi.changePassword).mockResolvedValue({
    id: "student-user",
    email: "student@example.com",
    login_name: "S001",
    recovery_email_verified: false,
    display_name: "学生甲",
    must_change_password: false,
    roles: ["student"],
    active_student_link: true,
    landing_surface: "student",
  });
  render(<ChangePasswordPage />);
  fireEvent.change(screen.getByLabelText("当前临时密码"), {
    target: { value: "temporary-password" },
  });
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码并继续" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/verify-email"));
  expect(authApi.changePassword).toHaveBeenCalledWith(
    "temporary-password",
    "new-password-123",
  );
});

it("uses the student landing surface when the recovery email is already verified", async () => {
  vi.mocked(authApi.changePassword).mockResolvedValue({
    id: "student-user",
    email: "student@example.com",
    login_name: "S001",
    recovery_email_verified: true,
    display_name: "学生甲",
    must_change_password: false,
    roles: ["student"],
    active_student_link: true,
    landing_surface: "student",
  });
  render(<ChangePasswordPage />);
  fireEvent.change(screen.getByLabelText("当前临时密码"), {
    target: { value: "temporary-password" },
  });
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码并继续" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
});

it("does not require email verification when the optional recovery email is unset", async () => {
  vi.mocked(authApi.changePassword).mockResolvedValue({
    id: "student-user",
    email: null,
    login_name: "S001",
    recovery_email_verified: false,
    display_name: "学生甲",
    must_change_password: false,
    roles: ["student"],
    active_student_link: true,
    landing_surface: "student",
  });
  render(<ChangePasswordPage />);
  fireEvent.change(screen.getByLabelText("当前临时密码"), {
    target: { value: "temporary-password" },
  });
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.change(screen.getByLabelText("再次输入新密码"), {
    target: { value: "new-password-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码并继续" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
});

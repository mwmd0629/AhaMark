import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudentAccountDialog } from "@/components/student-account-dialog";
import { studentAccountsApi } from "@/lib/student-api";

vi.mock("@/lib/student-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/student-api")>(
      "@/lib/student-api",
    );
  return {
    ...actual,
    studentAccountsApi: { ...actual.studentAccountsApi, link: vi.fn() },
  };
});

const student = {
  id: "student-1",
  name: "学生甲",
  student_number: "20260001",
  email: "student@example.com",
  status: "active" as const,
  membership_status: "active" as const,
  joined_at: "2026-08-20T00:00:00Z",
  groups: [],
  assignment_history: [] as [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(studentAccountsApi.link).mockResolvedValue({
    id: "link-1",
    user_id: "user-1",
    student_id: student.id,
    login_name: student.student_number,
    recovery_email: student.email,
    recovery_email_verified: false,
    student_name: student.name,
    status: "active",
    created_at: "2026-08-20T00:00:00Z",
    created_user: true,
  });
});

afterEach(cleanup);

it("creates a student account with the student number and a recovery email", async () => {
  render(<StudentAccountDialog student={student} />);
  fireEvent.click(screen.getByRole("button", { name: "开通账号" }));

  const loginName = screen.getByLabelText(/登录账号（学生学号）/);
  expect(loginName).toHaveValue("20260001");
  expect(loginName).toHaveAttribute("readonly");
  expect(screen.getByLabelText(/安全邮箱/)).toHaveValue(student.email);

  const password = screen.getByLabelText(/一次性临时密码/);
  password.focus();
  for (const value of ["T", "Te", "Tem", "Temp-pass-123"]) {
    fireEvent.change(password, { target: { value } });
    expect(password).toHaveFocus();
  }
  fireEvent.click(screen.getByRole("button", { name: "创建并绑定" }));

  await waitFor(() =>
    expect(studentAccountsApi.link).toHaveBeenCalledWith(student.id, {
      recovery_email: student.email,
      display_name: student.name,
      temporary_password: "Temp-pass-123",
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "登录账号：20260001",
  );
  expect(screen.getByRole("status")).toHaveTextContent("待学生验证");
  expect(screen.getByRole("status")).toHaveTextContent("Temp-pass-123");
});

it("allows the teacher to create an account without a recovery email", async () => {
  vi.mocked(studentAccountsApi.link).mockResolvedValueOnce({
    id: "link-2",
    user_id: "user-2",
    student_id: student.id,
    login_name: student.student_number,
    recovery_email: null,
    recovery_email_verified: false,
    student_name: student.name,
    status: "active",
    created_at: "2026-08-20T00:00:00Z",
    created_user: true,
  });

  render(<StudentAccountDialog student={{ ...student, email: "" }} />);
  fireEvent.click(screen.getByRole("button", { name: "开通账号" }));
  expect(screen.getByLabelText(/安全邮箱（选填）/)).not.toBeRequired();

  fireEvent.change(screen.getByLabelText(/一次性临时密码/), {
    target: { value: "Temp-pass-456" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建并绑定" }));

  await waitFor(() =>
    expect(studentAccountsApi.link).toHaveBeenCalledWith(student.id, {
      recovery_email: null,
      display_name: student.name,
      temporary_password: "Temp-pass-456",
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "未设置（学生可稍后添加）",
  );
});

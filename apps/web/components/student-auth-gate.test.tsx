import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudentAuthGate, useStudent } from "@/components/student-auth-gate";
import { authApi, type AuthUser } from "@/lib/api";
import { studentApi } from "@/lib/student-api";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: { ...actual.authApi, me: vi.fn(), logout: vi.fn() },
  };
});
vi.mock("@/lib/student-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/student-api")>(
      "@/lib/student-api",
    );
  return {
    ...actual,
    studentApi: { ...actual.studentApi, me: vi.fn() },
  };
});

const studentUser: AuthUser = {
  id: "user-1",
  email: "student@example.com",
  login_name: "S001",
  recovery_email_verified: false,
  display_name: "学生甲",
  must_change_password: false,
  roles: ["student"],
  active_student_link: true,
  landing_surface: "student",
};

function StudentName() {
  return <div>{useStudent()?.name}</div>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.logout).mockResolvedValue(undefined);
});
afterEach(cleanup);

it("loads a student profile only after the auth surface confirms student access", async () => {
  vi.mocked(authApi.me).mockResolvedValue(studentUser);
  vi.mocked(studentApi.me).mockResolvedValue({
    id: "student-1",
    name: "学生甲",
    student_number: "S001",
    email: "student@example.com",
    recovery_email_verified: false,
    profiles: [
      {
        student_id: "student-1",
        student_number: "S001",
        name: "学生甲",
        teacher_id: "teacher-1",
      },
    ],
  });
  render(
    <StudentAuthGate>
      <StudentName />
    </StudentAuthGate>,
  );
  expect(await screen.findByText("学生甲")).toBeInTheDocument();
  expect(studentApi.me).toHaveBeenCalled();
});

it("redirects teachers without probing the student profile endpoint", async () => {
  vi.mocked(authApi.me).mockResolvedValue({
    ...studentUser,
    id: "teacher-1",
    roles: ["teacher"],
    active_student_link: false,
    landing_surface: "teacher",
  });
  render(
    <StudentAuthGate>
      <StudentName />
    </StudentAuthGate>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
  expect(studentApi.me).not.toHaveBeenCalled();
});

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthGate } from "@/components/auth-gate";
import { authApi, type AuthUser } from "@/lib/api";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: { ...actual.authApi, me: vi.fn(), logout: vi.fn() },
  };
});

const teacher: AuthUser = {
  id: "teacher-1",
  email: "teacher@example.com",
  login_name: "teacher@example.com",
  recovery_email_verified: true,
  display_name: "教师甲",
  must_change_password: false,
  roles: ["teacher"],
  active_student_link: false,
  landing_surface: "teacher",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.logout).mockResolvedValue(undefined);
});
afterEach(cleanup);

it("renders the teacher surface only for a teacher landing decision", async () => {
  vi.mocked(authApi.me).mockResolvedValue(teacher);
  render(
    <AuthGate>
      <div>教师内容</div>
    </AuthGate>,
  );
  expect(await screen.findByText("教师内容")).toBeInTheDocument();
  expect(replace).not.toHaveBeenCalled();
});

it("redirects a student away from the teacher shell", async () => {
  vi.mocked(authApi.me).mockResolvedValue({
    ...teacher,
    id: "student-1",
    roles: ["student"],
    active_student_link: true,
    landing_surface: "student",
  });
  render(
    <AuthGate>
      <div>教师内容</div>
    </AuthGate>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/student"));
  expect(screen.queryByText("教师内容")).not.toBeInTheDocument();
});

it("honors the forced password flag even if a stale surface is returned", async () => {
  vi.mocked(authApi.me).mockResolvedValue({
    ...teacher,
    must_change_password: true,
  });
  render(
    <AuthGate>
      <div>教师内容</div>
    </AuthGate>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
});

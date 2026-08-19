import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthGate } from "./auth-gate";

const mocks = vi.hoisted(() => ({ me: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return { ...actual, authApi: { ...actual.authApi, me: mocks.me } };
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("admits only administrators to the administration audience", async () => {
  mocks.me.mockResolvedValue({ id: "admin", roles: ["admin"] });
  render(<AuthGate audience="admin">管理内容</AuthGate>);
  expect(await screen.findByText("管理内容")).toBeInTheDocument();
  expect(mocks.replace).not.toHaveBeenCalled();
});

it("redirects students away from teacher routes", async () => {
  mocks.me.mockResolvedValue({ id: "student", roles: ["student"] });
  render(<AuthGate audience="teacher">教师内容</AuthGate>);
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/student"));
  expect(screen.queryByText("教师内容")).not.toBeInTheDocument();
});

it("redirects administrators away from teacher routes", async () => {
  mocks.me.mockResolvedValue({ id: "admin", roles: ["admin"] });
  render(<AuthGate audience="teacher">教师内容</AuthGate>);
  await waitFor(() =>
    expect(mocks.replace).toHaveBeenCalledWith("/admin/accounts"),
  );
  expect(screen.queryByText("教师内容")).not.toBeInTheDocument();
});

it("keeps legacy roleless teachers compatible", async () => {
  mocks.me.mockResolvedValue({ id: "legacy-teacher", roles: [] });
  render(<AuthGate audience="teacher">历史教师内容</AuthGate>);
  expect(await screen.findByText("历史教师内容")).toBeInTheDocument();
});

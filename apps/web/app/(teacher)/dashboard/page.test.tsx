import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import DashboardPage from "./page";

vi.mock("@/components/auth-gate", () => ({
  useAuthUser: () => ({
    id: "blank-id",
    email: "blank01@ahamark.local",
    display_name: "空白教师",
  }),
}));
vi.mock("@/components/health-status", () => ({
  HealthStatus: () => <div>真实健康状态</div>,
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    classesApi: {
      ...actual.classesApi,
      list: vi.fn().mockResolvedValue({
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
        pages: 0,
      }),
    },
    assignmentsApi: {
      ...actual.assignmentsApi,
      list: vi.fn().mockResolvedValue({
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
        pages: 0,
      }),
    },
  };
});

it("renders the authenticated teacher and an empty real-data dashboard", async () => {
  render(<DashboardPage />);
  expect(
    screen.getByRole("heading", { name: "你好，空白教师" }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "这是一个空白教师账号" }),
    ).toBeInTheDocument(),
  );
  expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  expect(screen.getByText("真实健康状态")).toBeInTheDocument();
  expect(screen.queryByText("林老师")).not.toBeInTheDocument();
});

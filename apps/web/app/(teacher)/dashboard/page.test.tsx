import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import DashboardPage from "./page";
vi.mock("@/components/health-status", () => ({
  HealthStatus: () => <div>真实健康状态</div>,
}));
it("renders dashboard modules and marks demo statistics", () => {
  render(<DashboardPage />);
  expect(screen.getByRole("heading", { name: /林老师/ })).toBeInTheDocument();
  expect(screen.getByText("待处理工作")).toBeInTheDocument();
  expect(screen.getByText("班级概览")).toBeInTheDocument();
  expect(screen.getAllByText("演示数据").length).toBeGreaterThan(0);
  expect(screen.getByText("真实健康状态")).toBeInTheDocument();
});

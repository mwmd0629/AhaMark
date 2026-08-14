import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";
const pathnameMock = vi.hoisted(() => vi.fn());
const routerMock = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: pathnameMock,
  useRouter: () => routerMock,
}));
vi.mock("@/components/auth-gate", () => ({
  useAuthUser: () => ({
    id: "teacher-id",
    email: "teacher@ahamark.local",
    display_name: "测试教师",
  }),
}));
beforeEach(() => {
  pathnameMock.mockReturnValue("/classes");
  localStorage.clear();
});
afterEach(cleanup);
it("renders every teacher navigation item and highlights current route", () => {
  render(
    <AppShell>
      <div>工作台内容</div>
    </AppShell>,
  );
  expect(
    screen.getAllByRole("navigation", { name: "教师端主导航" })[0],
  ).toBeInTheDocument();
  for (const label of [
    "工作台",
    "AI 批改",
    "作业管理",
    "班级与学生",
    "学情分析",
    "错题与练习",
    "评分模板",
    "设置",
  ])
    expect(
      screen.getAllByRole("link", { name: new RegExp(label) }).length,
    ).toBeGreaterThan(0);
  expect(
    screen.getAllByRole("link", { name: "班级与学生" })[0],
  ).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "搜索" })).toHaveAttribute(
    "href",
    "/search",
  );
  expect(screen.getByRole("link", { name: "消息" })).toHaveAttribute(
    "href",
    "/notifications",
  );
  expect(screen.getByRole("link", { name: "使用帮助" })).toHaveAttribute(
    "href",
    "/help",
  );
  expect(screen.getByText("测试教师")).toBeInTheDocument();
  expect(screen.getByText("teacher@ahamark.local")).toBeInTheDocument();
});

it("shows the locally known unread reminder count and updates it without reload", async () => {
  localStorage.setItem("ahamark.notifications.unread.teacher-id", "3");
  render(
    <AppShell>
      <div>工作台内容</div>
    </AppShell>,
  );
  expect(
    screen.getByRole("link", { name: "消息，3 条未读" }),
  ).toBeInTheDocument();
  window.dispatchEvent(
    new CustomEvent("ahamark:notifications", { detail: { unreadCount: 1 } }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("link", { name: "消息，1 条未读" }),
    ).toBeInTheDocument(),
  );
});

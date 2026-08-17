import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import HelpPage from "./page";

afterEach(cleanup);

it("explains the teacher workflow in plain language", () => {
  render(<HelpPage />);
  expect(screen.getByRole("heading", { name: "完整流程" })).toBeInTheDocument();
  expect(screen.getByText("检查并批改")).toBeInTheDocument();
  expect(screen.getByText("教师确认")).toBeInTheDocument();
  expect(screen.getByText("可以一次上传多个文件吗？")).toBeInTheDocument();
  expect(screen.queryByText(/request ID/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/finalize/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Rubric/i)).not.toBeInTheDocument();
});

it("filters help by the teacher's problem and opens matching answers", () => {
  render(<HelpPage />);
  fireEvent.change(
    screen.getByPlaceholderText("例如：切题、确认分数、一次上传多个文件"),
    { target: { value: "切题" } },
  );
  expect(screen.getByText("答题框没有框准，怎么办？")).toBeInTheDocument();
  expect(screen.getByText(/删除错误框后重新拖动框选/)).toBeVisible();
  expect(
    screen.queryByText("缺交学生会按零分统计吗？"),
  ).not.toBeInTheDocument();
});

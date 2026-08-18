import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import Home from "./page";
it("renders product positioning", () => {
  render(<Home />);
  expect(screen.getByText(/教师与学生协作/)).toBeInTheDocument();
  expect(screen.getByText(/关闭后不影响核心教学流程/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "登录系统" })).toHaveAttribute(
    "href",
    "/login",
  );
});

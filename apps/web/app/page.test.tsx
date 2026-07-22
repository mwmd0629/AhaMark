import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import Home from "./page";
it("renders product positioning", () => {
  render(<Home />);
  expect(screen.getByText(/AhaMark 是面向教师/)).toBeInTheDocument();
  expect(screen.getByText(/面向教师的 AI 作业批改/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "进入教师工作台" })).toHaveAttribute(
    "href",
    "/dashboard",
  );
});

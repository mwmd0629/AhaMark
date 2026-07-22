import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button, Dialog, EmptyState } from "./ui";
describe("shared UI", () => {
  it("renders loading and disabled button states", () => {
    render(<Button loading>保存</Button>);
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });
  it("renders a contextual empty state", () => {
    render(<EmptyState title="还没有班级" description="请先创建班级" />);
    expect(screen.getByText("还没有班级")).toBeInTheDocument();
  });
  it("opens and closes a dialog with Escape", () => {
    render(
      <Dialog title="创建班级" trigger={<Button>打开</Button>}>
        内容
      </Dialog>,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button, Dialog, Dropdown, EmptyState } from "./ui";
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
  it("keeps a form dialog open until its close button is clicked", () => {
    render(
      <Dialog
        title="大量信息表单"
        dismissible={false}
        trigger={<Button>填写</Button>}
      >
        表单内容
      </Dialog>,
    );
    fireEvent.click(screen.getByRole("button", { name: "填写" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.mouseDown(dialog.parentElement as HTMLElement);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭对话框" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("closes a dropdown when clicking outside", () => {
    render(
      <Dropdown label="账号">
        <button role="menuitem">退出登录</button>
      </Dropdown>,
    );
    fireEvent.click(screen.getByRole("button", { name: "账号" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

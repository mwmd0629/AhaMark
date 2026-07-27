import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ClassesPage from "./page";

const empty = { items: [], page: 1, page_size: 20, total: 0, pages: 0 };

describe("classes API page", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders loading then empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue({ ok: true, status: 200, json: async () => empty }),
    );
    render(<ClassesPage />);
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(
      await screen.findByText("\u8fd8\u6ca1\u6709\u73ed\u7ea7"),
    ).toBeInTheDocument();
  });

  it("renders real API data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          ...empty,
          total: 1,
          pages: 1,
          items: [
            {
              id: "c1",
              name: "Class A",
              status: "active",
              student_count: 2,
              active_student_count: 2,
              group_count: 1,
              created_at: "2026-07-22",
              updated_at: "2026-07-22",
            },
          ],
        }),
      }),
    );
    render(<ClassesPage />);
    expect(await screen.findByText("Class A")).toBeInTheDocument();
    expect(screen.getByText("2 \u540d\u5b66\u751f")).toBeInTheDocument();
  });

  it("renders API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({
          code: "INTERNAL_ERROR",
          message: "API unavailable",
          details: {},
          request_id: "r1",
        }),
      }),
    );
    render(<ClassesPage />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
  });

  it("offers academic year choices and moves there from subject with Enter", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue({ ok: true, status: 200, json: async () => empty }),
    );
    const { container } = render(<ClassesPage />);
    await within(container).findByText("还没有班级");
    fireEvent.click(
      within(container).getByRole("button", { name: "创建班级" }),
    );
    const subject = within(container).getByLabelText("学科");
    const academicYear = within(container).getByLabelText("学年");
    expect(academicYear).toHaveDisplayValue("请选择学年");
    expect(
      within(container).getByRole("option", { name: "2026-2027" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(subject, { key: "Enter" });
    expect(academicYear).toHaveFocus();
  });

  it("closes the create dialog after a successful class creation", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((_url: string, init?: RequestInit) =>
        Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          json: async () =>
            init?.method === "POST"
              ? {
                  id: "created-class",
                  name: "八年级（5）班",
                  status: "active",
                }
              : empty,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ClassesPage />);
    await within(container).findByText("还没有班级");
    fireEvent.click(
      within(container).getByRole("button", { name: "创建班级" }),
    );
    fireEvent.change(within(container).getByLabelText(/班级名称/), {
      target: { value: "八年级（5）班" },
    });
    fireEvent.click(
      within(container).getByRole("button", { name: "保存班级" }),
    );
    await waitFor(() =>
      expect(within(container).queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
    ).toBe(true);
  });
});

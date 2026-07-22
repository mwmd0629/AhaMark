import { render, screen } from "@testing-library/react";
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
});

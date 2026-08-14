import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { rubricTemplateApi, type RubricTemplate } from "@/lib/api";

import RubricsPage from "./page";

const saved: RubricTemplate = {
  id: "template-1",
  name: "计算题模板",
  subject: "数学",
  grade: "八年级",
  question_type: "calculation",
  status: "draft",
  criterion_count: 2,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  current_version: {
    id: "version-1",
    version: 1,
    title: "计算题模板",
    scoring_basis: "proportional",
    total_points: "100",
    status: "draft",
    content_hash: "a".repeat(64),
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    criteria: [
      {
        stable_key: "item_1",
        title: "评分项 1",
        max_points: "60",
        criterion_type: "computation",
        required: true,
        dependencies: [],
        validation_mode: "ai_suggestion",
        manual_review_policy: {},
        partial_credit_policy: {},
        validation_rule: {},
        metadata: {},
      },
      {
        stable_key: "item_2",
        title: "评分项 2",
        max_points: "40",
        criterion_type: "computation",
        required: true,
        dependencies: [],
        validation_mode: "ai_suggestion",
        manual_review_policy: {},
        partial_credit_policy: {},
        validation_rule: {},
        metadata: {},
      },
    ],
  },
  versions: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RubricsPage", () => {
  it("shows a real empty state and creates a teacher-facing draft", async () => {
    vi.spyOn(rubricTemplateApi, "list").mockResolvedValue([]);
    const create = vi
      .spyOn(rubricTemplateApi, "create")
      .mockResolvedValue(saved);
    render(<RubricsPage />);
    expect(await screen.findByText("还没有评分模板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建评分模板" }));
    fireEvent.change(screen.getByLabelText("模板名称"), {
      target: { value: "计算题模板" },
    });
    const leave = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(create).toHaveBeenCalledOnce(), {
      timeout: 1600,
    });
    expect(screen.queryByText("stable_key")).not.toBeInTheDocument();
    expect(screen.queryByText("content_hash")).not.toBeInTheDocument();
  });

  it("renders status and filters from persisted templates", async () => {
    const list = vi.spyOn(rubricTemplateApi, "list").mockResolvedValue([
      {
        ...saved,
        status: "confirmed",
        current_version: { ...saved.current_version, status: "confirmed" },
      },
    ]);
    render(<RubricsPage />);
    expect(await screen.findByText("计算题模板")).toBeInTheDocument();
    expect(screen.getByText("已确认")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索模板"), {
      target: { value: "计算" },
    });
    await vi.waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "计算" }),
      ),
    );
  });

  it("autosaves an existing draft and protects both reload and in-app navigation", async () => {
    vi.spyOn(rubricTemplateApi, "list").mockResolvedValue([saved]);
    vi.spyOn(rubricTemplateApi, "get").mockResolvedValue({
      ...saved,
      versions: [saved.current_version],
    });
    const update = vi.spyOn(rubricTemplateApi, "update").mockResolvedValue({
      ...saved,
      name: "更新后的模板",
      current_version: {
        ...saved.current_version,
        title: "更新后的模板",
        content_hash: "b".repeat(64),
      },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RubricsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看与编辑" }));
    fireEvent.change(await screen.findByLabelText("模板名称"), {
      target: { value: "更新后的模板" },
    });
    const leave = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(true);
    const link = document.createElement("a");
    link.href = "/assignments";
    link.textContent = "离开";
    document.body.appendChild(link);
    expect(
      link.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      ),
    ).toBe(false);
    expect(confirm).toHaveBeenCalled();
    link.remove();
    await vi.waitFor(() => expect(update).toHaveBeenCalledOnce(), {
      timeout: 1600,
    });
  });

  it("shows visible history, copy and archive entries", async () => {
    const oldVersion = {
      ...saved.current_version,
      id: "version-1",
      version: 1,
      status: "archived" as const,
    };
    const currentVersion = {
      ...saved.current_version,
      id: "version-2",
      version: 2,
      status: "confirmed" as const,
    };
    const confirmed = {
      ...saved,
      status: "confirmed" as const,
      current_version: currentVersion,
      versions: [currentVersion, oldVersion],
    };
    vi.spyOn(rubricTemplateApi, "list").mockResolvedValue([confirmed]);
    vi.spyOn(rubricTemplateApi, "get").mockResolvedValue(confirmed);
    render(<RubricsPage />);
    fireEvent.click(await screen.findByText("更多"));
    expect(screen.getByRole("button", { name: "复制" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "停用并归档" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看历史版本" }));
    expect(await screen.findByText("历史版本（2）")).toBeInTheDocument();
    expect(screen.getByText(/版本 2/)).toBeInTheDocument();
    expect(screen.getByText(/版本 1/)).toBeInTheDocument();
  });
});

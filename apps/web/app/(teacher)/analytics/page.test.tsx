import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AnalyticsPage from "./page";

const mocks = vi.hoisted(() => ({
  assignments: vi.fn(),
  releases: vi.fn(),
  generate: vi.fn(),
  classTrends: vi.fn(),
  insight: vi.fn(),
  edit: vi.fn(),
  confirm: vi.fn(),
  regenerate: vi.fn(),
  invalidate: vi.fn(),
  scoreBand: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  assignmentsApi: { list: mocks.assignments },
  analyticsApi: {
    releases: mocks.releases,
    generate: mocks.generate,
    classTrends: mocks.classTrends,
    insight: mocks.insight,
    editInsight: mocks.edit,
    confirmInsight: mocks.confirm,
    regenerateInsight: mocks.regenerate,
    invalidateInsight: mocks.invalidate,
    scoreBand: mocks.scoreBand,
    question: vi.fn(),
    knowledgePoint: vi.fn(),
    errorType: vi.fn(),
  },
}));
afterEach(() => cleanup());

it("renders the analytics empty state after loading", async () => {
  mocks.assignments.mockResolvedValueOnce({ items: [] });
  render(<AnalyticsPage />);
  await waitFor(() =>
    expect(screen.getByText("请选择成绩发布版本")).toBeInTheDocument(),
  );
  expect(screen.getByText(/complete 成绩快照/)).toBeInTheDocument();
});

it("loads a release, drills into a score band, and edits and confirms an insight", async () => {
  mocks.assignments.mockResolvedValueOnce({
    items: [{ id: "a1", title: "合成作业" }],
  });
  mocks.releases.mockResolvedValueOnce([
    { id: "r1", class_id: "c1", version: 1, status: "released" },
  ]);
  mocks.generate.mockResolvedValueOnce({
    id: "s1",
    metrics: {
      participant_count: 3,
      average_score: 8,
      highest_score: 10,
      lowest_score: 6,
      median_score: 8,
      score_distribution: { "90-100": 2 },
      questions: [],
      knowledge_points: [],
      error_types: [],
    },
  });
  mocks.classTrends.mockResolvedValueOnce({
    items: [
      {
        analytics_snapshot_id: "s1",
        assignment_name: "合成作业",
        average_score_rate: 0.8,
        participant_count: 3,
      },
    ],
  });
  mocks.scoreBand.mockResolvedValueOnce({
    items: [{ student_number: "001" }],
    total: 1,
  });
  const generated = {
    id: "i1",
    provider: "rule_based",
    status: "generated",
    content: { recommendations: ["原建议"] },
    evidence: [{ question_id: "q1" }],
  };
  mocks.insight.mockResolvedValueOnce(generated);
  mocks.edit.mockResolvedValueOnce({
    ...generated,
    status: "draft",
    content: {
      recommendations: ["修改建议"],
      original_recommendations: ["原建议"],
    },
  });
  mocks.confirm.mockResolvedValueOnce({ ...generated, status: "confirmed" });
  render(<AnalyticsPage />);
  fireEvent.change(await screen.findByLabelText("作业"), {
    target: { value: "a1" },
  });
  fireEvent.change(await screen.findByLabelText("发布版本"), {
    target: { value: "r1" },
  });
  fireEvent.click(screen.getByRole("button", { name: /生成 \/ 刷新分析/ }));
  await screen.findByText("分数分布");
  fireEvent.click(screen.getByRole("button", { name: "90-100" }));
  await screen.findByText(/分数段 90-100/);
  fireEvent.click(screen.getByRole("button", { name: "生成规则建议" }));
  const textarea = await screen.findByLabelText("建议内容");
  fireEvent.change(textarea, { target: { value: "修改建议" } });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() =>
    expect(mocks.edit).toHaveBeenCalledWith("i1", ["修改建议"]),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith("i1"));
});

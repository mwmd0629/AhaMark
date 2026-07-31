import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ReviewPage from "./page";

const mocks = vi.hoisted(() => ({
  reviewWorkspace: vi.fn(),
  finalize: vi.fn(),
  correctAnswer: vi.fn(),
  grade: vi.fn(),
  review: vi.fn(),
  bulkAcceptEligibility: vi.fn(),
  bulkAccept: vi.fn(),
}));
const recognitionMocks = vi.hoisted(() => ({
  blocks: vi.fn().mockResolvedValue([]),
  edit: vi.fn(),
  split: vi.fn(),
  merge: vi.fn(),
  reorder: vi.fn(),
  confirm: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ batchId: "b1" }),
}));
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));
vi.mock("@/lib/api", () => ({
  gradingApi: mocks,
  answerRecognitionApi: recognitionMocks,
}));
vi.mock("@/components/ai-grading-review", () => ({
  AIGradingReview: ({ answerId }: { answerId: string }) => (
    <section aria-label="AI 分项评分建议" data-answer-id={answerId}>
      本地 Codex / AI 建议，需教师确认
    </section>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function workspace({
  stale = false,
  reviewed = 1,
}: {
  stale?: boolean;
  reviewed?: number;
} = {}) {
  return {
    provider_notice: "合成测试 Provider",
    progress: { reviewed, total: 1 },
    items: [
      {
        submission_id: "sub-1",
        status: stale ? "review_required" : "reviewed",
        pages: [],
        answers: [
          {
            id: "ans-1",
            status: stale ? "stale" : "reviewed",
            requires_review: stale,
            recognized_text: "A",
            corrected_text: undefined,
            question: {
              number: 1,
              type: "single_choice",
              content: "合成题目",
              max_score: "10",
            },
            result: {
              score: "10" as string | null,
              confidence: "0.99",
              provider: "objective-rule",
              provider_version: "1",
              status: stale ? "stale" : "suggested",
              rubric_version_id: stale ? "rubric-2" : "rubric-1",
              requires_review: false,
            },
            review: stale ? undefined : { final_score: "10", feedback: "" },
            criteria: [] as Array<{
              rubric_item_id: string;
              status: string;
              awarded_points?: string;
              max_points: string;
              reason?: string;
            }>,
            evidence: [],
          },
        ],
      },
    ],
  };
}

function mockEligibility(eligible = true) {
  mocks.bulkAcceptEligibility.mockResolvedValue({
    eligible_count: eligible ? 1 : 0,
    excluded_count: eligible ? 0 : 1,
    reason_counts: eligible ? {} : { REVIEW_REQUIRED: 1 },
    items: [
      {
        answer_id: "ans-1",
        eligible,
        reasons: eligible ? [] : ["REVIEW_REQUIRED"],
      },
    ],
  });
}

it("blocks accepting a stale result and offers explicit regrading", async () => {
  mockEligibility(false);
  mocks.reviewWorkspace.mockResolvedValue(
    workspace({ stale: true, reviewed: 0 }),
  );
  mocks.grade.mockResolvedValue({});
  render(<ReviewPage />);

  expect(await screen.findByTestId("regrade-required")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "接受" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "重新批改" }));
  await waitFor(() => expect(mocks.grade).toHaveBeenCalledWith("ans-1"));
});

it("blocks direct acceptance when the answer itself requires teacher review", async () => {
  mockEligibility(false);
  const data = workspace();
  data.items[0].answers[0].requires_review = true;
  data.items[0].answers[0].status = "review_required";
  mocks.reviewWorkspace.mockResolvedValue(data);
  render(<ReviewPage />);

  const accept = await screen.findByRole("button", { name: "接受" });
  expect(accept).toBeDisabled();
  expect(screen.getByTestId("acceptance-blocked-hint")).toHaveTextContent(
    "当前建议已标记为需要教师复核",
  );
  fireEvent.click(accept);
  expect(mocks.review).not.toHaveBeenCalled();
});

const reviewBlockCases: Array<
  [
    string,
    {
      resultRequiresReview?: boolean;
      nullScore?: boolean;
      incompleteCriterion?: boolean;
    },
    string,
  ]
> = [
  [
    "result requires review",
    { resultRequiresReview: true },
    "当前建议已标记为需要教师复核",
  ],
  ["score is null", { nullScore: true }, "建议中没有有效分数"],
  [
    "criterion is incomplete",
    { incompleteCriterion: true },
    "存在需要人工处理或尚未完成的评分项",
  ],
];

it.each(reviewBlockCases)(
  "includes %s in needs-review filtering and disables acceptance",
  async (_label, condition, expectedReason) => {
    mockEligibility(false);
    const data = workspace({ reviewed: 0 });
    const answer = data.items[0].answers[0];
    answer.requires_review = false;
    answer.review = undefined;
    if (condition.resultRequiresReview) answer.result.requires_review = true;
    if (condition.nullScore) answer.result.score = null;
    if (condition.incompleteCriterion) {
      answer.criteria = [
        {
          rubric_item_id: "criterion-incomplete",
          status: "incomplete",
          awarded_points: undefined,
          max_points: "10",
        },
      ];
    }
    mocks.reviewWorkspace.mockResolvedValue(data);
    render(<ReviewPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "必须逐题复核" }),
    );
    expect(screen.getByRole("button", { name: /第 1 题/ })).toBeInTheDocument();
    expect(screen.getByTestId("review-answer")).toHaveTextContent(
      "需要教师复核",
    );
    expect(screen.getByRole("button", { name: "接受" })).toBeDisabled();
    expect(screen.getByTestId("acceptance-blocked-hint")).toHaveTextContent(
      expectedReason,
    );
  },
);

it("shows the readable backend error when saving a review fails", async () => {
  mockEligibility();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.review.mockRejectedValue(
    new Error("建议分缺失，不能接受，请改为手动评分"),
  );
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "接受" }));

  expect(await screen.findByRole("status")).toHaveTextContent(
    "保存失败：建议分缺失，不能接受，请改为手动评分",
  );
});

it("embeds the Codex suggestion review in the existing teacher workflow", async () => {
  mockEligibility();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  expect(
    await screen.findByRole("region", { name: "AI 分项评分建议" }),
  ).toHaveAttribute("data-answer-id", "ans-1");
  expect(
    screen.getByText("本地 Codex / AI 建议，需教师确认"),
  ).toBeInTheDocument();
});

it("uses an inline teacher scoring form and validates criterion totals", async () => {
  mockEligibility(false);
  const data = workspace({ stale: true, reviewed: 0 });
  data.items[0].answers[0].criteria = [
    {
      rubric_item_id: "criterion-1",
      status: "manual",
      awarded_points: undefined,
      max_points: "4",
      reason: "计算过程",
    },
    {
      rubric_item_id: "criterion-2",
      status: "manual",
      awarded_points: undefined,
      max_points: "6",
      reason: "最终答案",
    },
  ];
  mocks.reviewWorkspace.mockResolvedValue(data);
  mocks.review.mockResolvedValue({});
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "手动评分" }));
  expect(
    screen.getByRole("region", { name: "教师评分表单" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("评分项 1 得分"), {
    target: { value: "4" },
  });
  fireEvent.change(screen.getByLabelText("评分项 2 得分"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存最终评分" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "分项合计 9 分，必须等于最终分 10 分",
  );
  expect(mocks.review).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("教师最终分数"), {
    target: { value: "9" },
  });
  fireEvent.change(screen.getByLabelText("教师反馈"), {
    target: { value: "过程正确，最后一步有误" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存最终评分" }));

  await waitFor(() =>
    expect(mocks.review).toHaveBeenCalledWith("ans-1", {
      decision: "manual_scored",
      final_score: "9",
      final_feedback: "过程正确，最后一步有误",
      criterion_scores: {
        "criterion-1": "4",
        "criterion-2": "5",
      },
      reason: "教师手动评分",
    }),
  );
});

it("surfaces incomplete finalize blockers instead of a false success", async () => {
  mockEligibility();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.finalize.mockResolvedValue({
    id: "snapshot-incomplete",
    submission_id: "sub-1",
    status: "incomplete",
    problems: [{ code: "RUBRIC_VERSION_STALE", question_id: "q1" }],
  });
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "全部定稿" }));
  expect(
    await screen.findByText(/定稿已阻止 1 份未完成提交/),
  ).toHaveTextContent("评分标准版本已变化");
  expect(screen.getByTestId("score-snapshot")).toHaveAttribute(
    "data-status",
    "incomplete",
  );
});

it("explains centralized review and only bulk-accepts eligible answers", async () => {
  mockEligibility();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.bulkAccept.mockResolvedValue({
    accepted_answer_ids: ["ans-1"],
    excluded: [],
  });
  render(<ReviewPage />);

  expect(
    await screen.findByRole("region", { name: "集中审查概览" }),
  ).toHaveTextContent("可批量接受 1 题");
  fireEvent.click(screen.getByRole("button", { name: "批量接受低风险建议" }));
  await waitFor(() =>
    expect(mocks.bulkAccept).toHaveBeenCalledWith("b1", ["ans-1"]),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "已批量接受 1 题",
  );
});

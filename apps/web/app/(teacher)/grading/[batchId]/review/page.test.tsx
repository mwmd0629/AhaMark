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
  correctAnswer: vi.fn(),
  grade: vi.fn(),
  review: vi.fn(),
  confirmResultsReadiness: vi.fn(),
  confirmResults: vi.fn(),
  addCollaborator: vi.fn(),
  removeCollaborator: vi.fn(),
  assignQuestion: vi.fn(),
  assignJointQuestion: vi.fn(),
}));
const navigation = vi.hoisted(() => ({
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
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
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
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
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  navigation.search = "";
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
    collaboration: undefined as
      | {
          is_owner: boolean;
          can_confirm_results: boolean;
          owner: { id: string; display_name: string; email: string };
          collaborators: Array<{
            id: string;
            display_name: string;
            email: string;
            role: "grader";
          }>;
          questions: Array<{
            id: string;
            number: string;
            assignee_id?: string;
            total: number;
            reviewed: number;
          }>;
        }
      | undefined,
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
              structured_rubric_set_id: "set-1",
              structured_rubric_version_id: stale ? "rubric-2" : "rubric-1",
              requires_review: false,
              quality_flags: [] as string[],
            },
            review: stale ? undefined : { final_score: "10", feedback: "" },
            criteria: [] as Array<{
              criterion_id: string;
              status: string;
              awarded_points?: string;
              max_points: string;
              title?: string;
              description?: string;
              reason?: string;
              evidence_quotes?: string[];
            }>,
            evidence: [],
          },
        ],
      },
    ],
  };
}

it("lets the owner assign questions while keeping final confirmation owner-only", async () => {
  mockReadiness();
  const data = workspace();
  data.collaboration = {
    is_owner: true,
    can_confirm_results: true,
    owner: {
      id: "owner-1",
      display_name: "主责老师",
      email: "owner@example.com",
    },
    collaborators: [
      {
        id: "teacher-2",
        display_name: "协作老师",
        email: "collaborator@example.com",
        role: "grader",
      },
    ],
    questions: [
      {
        id: "question-1",
        number: "1",
        total: 30,
        reviewed: 12,
      },
    ],
  };
  mocks.reviewWorkspace.mockResolvedValue(data);
  mocks.assignQuestion.mockResolvedValue(data.collaboration);
  render(<ReviewPage />);

  expect(
    await screen.findByRole("button", { name: "确认结果" }),
  ).toBeInTheDocument();
  expect(screen.getByText("12/30")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "teacher-2" },
  });
  await waitFor(() =>
    expect(mocks.assignQuestion).toHaveBeenCalledWith(
      "b1",
      "question-1",
      "teacher-2",
    ),
  );
});

it("shows only the assigned-work message and no release action for collaborators", async () => {
  const data = workspace();
  data.collaboration = {
    is_owner: false,
    can_confirm_results: false,
    owner: {
      id: "owner-1",
      display_name: "主责老师",
      email: "owner@example.com",
    },
    collaborators: [],
    questions: [
      {
        id: "question-1",
        number: "1",
        assignee_id: "teacher-2",
        total: 30,
        reviewed: 12,
      },
    ],
  };
  mocks.reviewWorkspace.mockResolvedValue(data);
  render(<ReviewPage />);

  expect(
    await screen.findByText(/这里只显示分配给你的题目/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "确认结果" }),
  ).not.toBeInTheDocument();
  expect(mocks.confirmResultsReadiness).not.toHaveBeenCalled();
});

function readinessPayload(
  ready = true,
  reviewHash = "a".repeat(64),
  confirmedResult?: {
    status: "released";
    review_hash: string;
    submission_count: number;
    auto_accepted_count: number;
    new_snapshot_count?: number;
    reused_snapshot_count?: number;
    previous_grade_release_id?: string | null;
    teacher_review_ids: string[];
    snapshot_ids: string[];
    grade_release_id: string;
    grade_release_version: number;
  },
) {
  return {
    ready,
    review_hash: reviewHash,
    blockers: ready
      ? []
      : [
          {
            code: "ANSWER_NOT_REVIEWED",
            submission_id: "sub-1",
            answer_id: "ans-1",
          },
        ],
    confirmed_result: confirmedResult,
  };
}

function mockReadiness(ready = true) {
  mocks.confirmResultsReadiness.mockResolvedValue(readinessPayload(ready));
}

it("blocks accepting a stale result and offers explicit regrading", async () => {
  mockReadiness(false);
  mocks.reviewWorkspace.mockResolvedValue(
    workspace({ stale: true, reviewed: 0 }),
  );
  mocks.grade.mockResolvedValue({});
  render(<ReviewPage />);

  expect(await screen.findByTestId("regrade-required")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "接受" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新批改" }));
  await waitFor(() => expect(mocks.grade).toHaveBeenCalledWith("ans-1"));
});

it("keeps exception editing actions without exposing direct acceptance", async () => {
  mockReadiness(false);
  const data = workspace();
  data.items[0].answers[0].requires_review = true;
  data.items[0].answers[0].status = "review_required";
  mocks.reviewWorkspace.mockResolvedValue(data);
  render(<ReviewPage />);

  expect(
    await screen.findByRole("button", { name: "手动评分" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "接受" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "拒绝" }),
  ).not.toBeInTheDocument();
  expect(screen.getByTestId("confirm-results-blockers")).toBeInTheDocument();
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
  ]
> = [
  ["result requires review", { resultRequiresReview: true }],
  ["score is null", { nullScore: true }],
  ["criterion is incomplete", { incompleteCriterion: true }],
];

it.each(reviewBlockCases)(
  "includes %s in needs-review filtering without a second confirmation action",
  async (_label, condition) => {
    mockReadiness(false);
    const data = workspace({ reviewed: 0 });
    const answer = data.items[0].answers[0];
    answer.requires_review = false;
    answer.review = undefined;
    if (condition.resultRequiresReview) answer.result.requires_review = true;
    if (condition.nullScore) answer.result.score = null;
    if (condition.incompleteCriterion) {
      answer.criteria = [
        {
          criterion_id: "criterion-incomplete",
          status: "incomplete",
          awarded_points: undefined,
          max_points: "10",
        },
      ];
    }
    mocks.reviewWorkspace.mockResolvedValue(data);
    render(<ReviewPage />);

    fireEvent.click(await screen.findByRole("button", { name: "需检查" }));
    expect(screen.getByRole("button", { name: /第 1 题/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "接受" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "手动评分" }),
    ).toBeInTheDocument();
  },
);

it("hides technical grading source and status fields from teachers", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  const detail = await screen.findByTestId("review-answer");
  expect(detail).toHaveTextContent("建议分 / 满分");
  expect(detail).toHaveTextContent("教师最终分");
  expect(detail).toHaveTextContent("学生答案");
  expect(detail.textContent).not.toContain("评分来源");
  expect(detail.textContent).not.toContain("评分结果状态");
  expect(detail.textContent).not.toContain("需要教师复核");
  expect(detail.textContent).not.toContain("置信度");
  expect(screen.queryByText(/评分服务不可用/)).not.toBeInTheDocument();
});

it("shows the readable backend error when saving an exception edit fails", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.review.mockRejectedValue(new Error("复核内容已变化，请刷新后重试"));
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "修改" }));
  fireEvent.click(await screen.findByRole("button", { name: "保存最终评分" }));

  expect(await screen.findByRole("status")).toHaveTextContent(
    "保存失败：复核内容已变化，请刷新后重试",
  );
});

it("does not expose a nested AI confirmation workflow", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  expect(
    await screen.findByRole("button", { name: "确认结果" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "AI 分项评分建议" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "接受" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "拒绝" }),
  ).not.toBeInTheDocument();
});

it("uses an inline teacher scoring form and validates criterion totals", async () => {
  mockReadiness(false);
  const data = workspace({ stale: true, reviewed: 0 });
  data.items[0].answers[0].criteria = [
    {
      criterion_id: "criterion-1",
      status: "manual",
      awarded_points: undefined,
      max_points: "4",
      reason: "计算过程",
    },
    {
      criterion_id: "criterion-2",
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

it("shows readiness blockers and prevents a formal write", async () => {
  mocks.confirmResultsReadiness.mockResolvedValue({
    ...readinessPayload(false),
    blockers: [
      {
        code: "FINALIZED_SNAPSHOT_NOT_REUSABLE",
        submission_id: "sub-1",
      },
      {
        code: "FINALIZED_SNAPSHOT_NOT_REUSABLE",
        submission_id: "sub-2",
      },
    ],
  });
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  const confirm = await screen.findByRole("button", { name: "确认结果" });
  expect(confirm).toBeDisabled();
  expect(screen.getAllByText("已有结果需要重新确认")).toHaveLength(1);
  expect(screen.getByTestId("confirm-results-blockers")).not.toHaveTextContent(
    "sub-1",
  );
  fireEvent.click(confirm);
  expect(mocks.confirmResults).not.toHaveBeenCalled();
});

it("opens the exception queue first and moves across filters", async () => {
  mockReadiness(false);
  const data = workspace();
  const firstAnswer = data.items[0].answers[0];
  data.items[0].answers.push({
    ...firstAnswer,
    id: "ans-2",
    status: "stale",
    requires_review: true,
    question: {
      ...firstAnswer.question,
      number: 2,
      content: "需要重新批改的题目",
    },
    result: {
      ...firstAnswer.result,
      status: "stale",
    },
    review: undefined,
  });
  data.items.push({
    ...structuredClone(data.items[0]),
    submission_id: "sub-2",
    answers: [
      { ...structuredClone(firstAnswer), id: "ans-3" },
      {
        ...structuredClone(data.items[0].answers[1]),
        id: "ans-4",
        question: {
          ...structuredClone(firstAnswer.question),
          number: 4,
          content: "另一名学生的异常题",
        },
      },
    ],
  });
  mocks.reviewWorkspace.mockResolvedValue(data);
  render(<ReviewPage />);

  await waitFor(() =>
    expect(screen.getByTestId("review-answer")).toHaveAttribute(
      "data-answer-id",
      "ans-2",
    ),
  );
  expect(screen.getByRole("button", { name: "需检查" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(
    screen.queryByRole("button", { name: /第 1 题/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /第 2 题/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "学生 2" }));
  await waitFor(() =>
    expect(screen.getByTestId("review-answer")).toHaveAttribute(
      "data-answer-id",
      "ans-4",
    ),
  );

  fireEvent.click(screen.getByRole("button", { name: "已复核" }));
  await waitFor(() =>
    expect(screen.getByTestId("review-answer")).toHaveAttribute(
      "data-answer-id",
      "ans-1",
    ),
  );
});

it("moves to the next exception after saving a score", async () => {
  mockReadiness(false);
  const data = workspace({ reviewed: 0 });
  const first = data.items[0].answers[0];
  first.status = "review_required";
  first.requires_review = true;
  first.review = undefined;
  data.items[0].answers.push({
    ...first,
    id: "ans-2",
    question: { ...first.question, number: 2, content: "第二道异常题" },
  });
  const refreshed = structuredClone(data);
  refreshed.items[0].answers[0].status = "reviewed";
  refreshed.items[0].answers[0].requires_review = false;
  refreshed.items[0].answers[0].review = { final_score: "10", feedback: "" };
  mocks.reviewWorkspace
    .mockResolvedValueOnce(data)
    .mockResolvedValue(refreshed);
  mocks.review.mockResolvedValue({});
  render(<ReviewPage />);

  expect(await screen.findByTestId("review-answer")).toHaveAttribute(
    "data-answer-id",
    "ans-1",
  );
  fireEvent.click(screen.getByRole("button", { name: "手动评分" }));
  fireEvent.click(screen.getByRole("button", { name: "保存最终评分" }));

  await waitFor(() =>
    expect(screen.getByTestId("review-answer")).toHaveAttribute(
      "data-answer-id",
      "ans-2",
    ),
  );
  expect(screen.getByRole("status")).toHaveTextContent("已保存，已进入下一题");
});

it("regrades automatically after the teacher corrects an answer", async () => {
  mockReadiness();
  const data = workspace();
  mocks.reviewWorkspace.mockResolvedValue(data);
  mocks.correctAnswer.mockResolvedValue({});
  mocks.grade.mockResolvedValue({});
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("B");
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "修正答案" }));

  await waitFor(() =>
    expect(mocks.correctAnswer).toHaveBeenCalledWith("ans-1", {
      corrected_text: "B",
    }),
  );
  expect(mocks.grade).toHaveBeenCalledWith("ans-1");
  expect(screen.getByRole("status")).toHaveTextContent("答案已修改并重新批改");
  prompt.mockRestore();
});

it("uses one confirm-results command and reports the released formal chain", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.confirmResults.mockResolvedValue({
    status: "released",
    review_hash: "a".repeat(64),
    submission_count: 2,
    auto_accepted_count: 3,
    teacher_review_ids: ["review-1", "review-2"],
    snapshot_ids: ["snapshot-1", "snapshot-2"],
    grade_release_id: "release-1",
    grade_release_version: 4,
  });
  render(<ReviewPage />);

  const confirm = await screen.findByRole("button", { name: "确认结果" });
  expect(
    screen.queryByRole("button", { name: "全部定稿" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "批量接受低风险建议" }),
  ).not.toBeInTheDocument();
  fireEvent.click(confirm);

  await waitFor(() => expect(mocks.confirmResults).toHaveBeenCalledTimes(1));
  expect(mocks.confirmResults).toHaveBeenCalledWith("b1", {
    idempotency_key: expect.any(String),
    expected_review_hash: "a".repeat(64),
  });
  expect(screen.getByTestId("confirmed-results")).toHaveAttribute(
    "data-release-id",
    "release-1",
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "已确认 2 份作业，更新 2 份，保留 0 份",
  );
  expect(screen.getByTestId("confirmed-results")).not.toHaveTextContent(
    "自动接受",
  );
});

it("previews a one-submission reopen plan before the sole formal confirmation", async () => {
  mocks.confirmResultsReadiness.mockResolvedValue({
    ready: true,
    review_hash: "a".repeat(64),
    blockers: [],
    submission_count: 2,
    new_snapshot_count: 1,
    reused_snapshot_count: 1,
    previous_grade_release_id: "release-previous",
    plan: [
      {
        submission_id: "sub-reopened",
        student_id: "student-reopened",
        student_name: "张同学",
        student_number: "2026001",
        action: "create_snapshot",
        snapshot_id: null,
        snapshot_version: null,
        changed_questions: [
          { question_id: "question-2", question_number: "2" },
          { question_id: "question-5", question_number: "5" },
        ],
      },
      {
        submission_id: "sub-reused",
        student_id: "student-reused",
        student_name: "李同学",
        student_number: "2026002",
        action: "reuse_snapshot",
        snapshot_id: "snapshot-reused",
        snapshot_version: 1,
        changed_questions: [],
      },
    ],
    confirmed_result: null,
  });
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  expect(await screen.findByTestId("confirm-results-plan")).toHaveTextContent(
    "本次更新 1 份，保留 1 份",
  );
  expect(screen.getByTestId("confirm-results-plan")).toHaveTextContent(
    "未修改的结果保持不变",
  );
  expect(screen.getByTestId("confirm-results-plan")).toHaveTextContent(
    "张同学（2026001）：第 2、5 题有变化",
  );
  expect(screen.getByTestId("confirm-results-plan")).not.toHaveTextContent(
    "李同学",
  );
  expect(screen.getAllByRole("button", { name: "确认结果" })).toHaveLength(1);
  expect(mocks.confirmResults).not.toHaveBeenCalled();
});

it("summarizes one reopened submission without presenting another confirmation action", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.confirmResults.mockResolvedValue({
    status: "released",
    review_hash: "a".repeat(64),
    submission_count: 2,
    auto_accepted_count: 0,
    new_snapshot_count: 1,
    reused_snapshot_count: 1,
    previous_grade_release_id: "release-previous",
    teacher_review_ids: ["review-reopened", "review-reused"],
    snapshot_ids: ["snapshot-new", "snapshot-reused"],
    grade_release_id: "release-current",
    grade_release_version: 5,
  });
  render(<ReviewPage />);

  expect(
    await screen.findAllByRole("button", { name: "确认结果" }),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "确认结果" }));

  expect(await screen.findByRole("status")).toHaveTextContent(
    "更新 1 份，保留 1 份",
  );
  expect(screen.getByTestId("confirmed-results")).toHaveTextContent(
    "更新 1 份，保留 1 份",
  );
  expect(screen.getByTestId("confirmed-results")).toHaveAttribute(
    "data-previous-release-id",
    "release-previous",
  );
  expect(
    screen.queryByRole("button", { name: "创建新的成绩发布版本" }),
  ).not.toBeInTheDocument();
});

it("restores an existing confirm-results success returned by readiness", async () => {
  const confirmedResult = {
    status: "released" as const,
    review_hash: "a".repeat(64),
    submission_count: 2,
    auto_accepted_count: 3,
    teacher_review_ids: ["review-1", "review-2"],
    snapshot_ids: ["snapshot-1", "snapshot-2"],
    grade_release_id: "release-1",
    grade_release_version: 4,
  };
  mocks.confirmResultsReadiness.mockResolvedValue(
    readinessPayload(false, confirmedResult.review_hash, confirmedResult),
  );
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  render(<ReviewPage />);

  const confirmed = await screen.findByRole("button", {
    name: "结果已确认",
  });
  expect(confirmed).toBeDisabled();
  expect(screen.getByTestId("confirmed-results")).toHaveAttribute(
    "data-release-id",
    "release-1",
  );
  expect(screen.getByTestId("confirmed-results")).toHaveTextContent(
    "更新 2 份，保留 0 份",
  );
  expect(
    screen.queryByTestId("confirm-results-blockers"),
  ).not.toBeInTheDocument();
  expect(mocks.confirmResults).not.toHaveBeenCalled();
});

it("shows a concrete stale reason and reuses the key while the hash is unchanged", async () => {
  mockReadiness();
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.confirmResults.mockRejectedValue(
    Object.assign(new Error("review hash changed"), {
      body: {
        code: "CONFIRM_RESULTS_STALE",
        message: "复核内容已经变化",
        details: { current_review_hash: "b".repeat(64) },
      },
    }),
  );
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "确认结果" }));

  expect(await screen.findByRole("status")).toHaveTextContent(
    "内容已变化，请重新检查",
  );
  await waitFor(() =>
    expect(mocks.confirmResultsReadiness).toHaveBeenCalledTimes(2),
  );

  fireEvent.click(screen.getByRole("button", { name: "确认结果" }));
  await waitFor(() => expect(mocks.confirmResults).toHaveBeenCalledTimes(2));
  expect(mocks.confirmResults.mock.calls[1][1].idempotency_key).toBe(
    mocks.confirmResults.mock.calls[0][1].idempotency_key,
  );
});

it("rotates the idempotency key when refreshed readiness changes review_hash", async () => {
  mocks.confirmResultsReadiness
    .mockResolvedValueOnce(readinessPayload(true, "a".repeat(64)))
    .mockResolvedValue(readinessPayload(true, "b".repeat(64)));
  mocks.reviewWorkspace.mockResolvedValue(workspace());
  mocks.confirmResults.mockRejectedValue(
    Object.assign(new Error("review hash changed"), {
      body: {
        code: "CONFIRM_RESULTS_STALE",
        message: "复核内容已经变化",
        details: { current_review_hash: "b".repeat(64) },
      },
    }),
  );
  render(<ReviewPage />);

  fireEvent.click(await screen.findByRole("button", { name: "确认结果" }));
  await waitFor(() =>
    expect(mocks.confirmResultsReadiness).toHaveBeenCalledTimes(2),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认结果" }));
  await waitFor(() => expect(mocks.confirmResults).toHaveBeenCalledTimes(2));

  expect(mocks.confirmResults.mock.calls[0][1].expected_review_hash).toBe(
    "a".repeat(64),
  );
  expect(mocks.confirmResults.mock.calls[1][1].expected_review_hash).toBe(
    "b".repeat(64),
  );
  expect(mocks.confirmResults.mock.calls[1][1].idempotency_key).not.toBe(
    mocks.confirmResults.mock.calls[0][1].idempotency_key,
  );
});

it("puts consistency differences in the exception queue", async () => {
  mockReadiness(false);
  const data = workspace({ reviewed: 0 });
  const answer = data.items[0].answers[0];
  answer.review = undefined;
  answer.result.quality_flags = ["CONSISTENCY_REVIEW_REQUIRED"];
  mocks.reviewWorkspace.mockResolvedValue(data);

  render(<ReviewPage />);

  expect(
    await screen.findByText("相同答案出现不同评分，请检查。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "需检查" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

it("shows concise rubric evidence for each scoring item", async () => {
  mockReadiness();
  const data = workspace();
  data.items[0].answers[0].criteria = [
    {
      criterion_id: "criterion-1",
      title: "方法正确",
      status: "evaluated",
      awarded_points: "4",
      max_points: "5",
      reason: "方法正确，计算有一处遗漏。",
      evidence_quotes: ["先列出方程，再代入求解。"],
    },
  ];
  mocks.reviewWorkspace.mockResolvedValue(data);

  render(<ReviewPage />);

  expect(await screen.findByText("评分依据")).toBeInTheDocument();
  expect(screen.getByText("方法正确")).toBeInTheDocument();
  expect(screen.getByText("4 / 5")).toBeInTheDocument();
  expect(
    screen.getByText("依据：先列出方程，再代入求解。"),
  ).toBeInTheDocument();
});

it("按题统批遇到空班级时自动进入下一班", async () => {
  navigation.search = "questionId=question-1&joint=1";
  mockReadiness();
  const data = workspace();
  data.items = [];
  data.progress = { reviewed: 0, total: 0 };
  Object.assign(data, {
    joint_navigation: {
      assignment_id: "joint-1",
      batches: [
        { id: "b1", class_id: "class-1", class_name: "一班" },
        { id: "b2", class_id: "class-2", class_name: "二班" },
      ],
    },
  });
  mocks.reviewWorkspace.mockResolvedValue(data);

  render(<ReviewPage />);

  await waitFor(() =>
    expect(navigation.replace).toHaveBeenCalledWith(
      "/grading/b2/review?questionId=question-1&joint=1",
    ),
  );
  expect(mocks.reviewWorkspace).toHaveBeenCalledWith("b1", "question-1");
});

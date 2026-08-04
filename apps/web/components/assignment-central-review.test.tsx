import "@testing-library/jest-dom/vitest";
import {
  act as testingAct,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssignmentCentralReview } from "./assignment-central-review";
import {
  ApiError,
  type AssignmentReadinessRecord,
  type AssignmentReviewBundle,
  type AssignmentReviewItemRecord,
} from "@/lib/api";

const reviewApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  items: vi.fn(),
  bundle: vi.fn(),
  confirm: vi.fn(),
  autoConfirm: vi.fn(),
  refresh: vi.fn(),
  disposition: vi.fn(),
  createBinding: vi.fn(),
  confirmBinding: vi.fn(),
  prepare: vi.fn(),
  publish: vi.fn(),
}));
const manualApi = vi.hoisted(() => ({
  manualPublishReadiness: vi.fn(),
  publishManual: vi.fn(),
}));
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (load) => {
  const actual = await load<typeof import("@/lib/api")>();
  return {
    ...actual,
    assignmentReviewApi: reviewApi,
    assignmentsApi: { ...actual.assignmentsApi, ...manualApi },
  };
});
vi.mock("@/components/ui", async (load) => {
  const actual = await load<typeof import("@/components/ui")>();
  return { ...actual, useToast: () => toast };
});

const confirmationKinds = [
  "classes",
  "due_at",
  "total_score",
  "file_roles",
  "answer_sources",
  "paper_version",
  "reference_answers",
  "structured_rubrics",
];
const session = (id = "review-1", overrides = {}) => ({
  id,
  assignment_id: "assignment-1",
  generation: 1,
  draft_revision_id: "revision-1",
  paper_version_id: "paper-1",
  legacy_rubric_version_id: null,
  review_version: 1,
  status: "ready",
  counts: { blocking: 0, warning: 0, info: 0 },
  confirmations: [],
  ...overrides,
});
const confirmation = (
  type: string,
  overrides: Partial<AssignmentReviewBundle["confirmations"][number]> = {},
): AssignmentReviewBundle["confirmations"][number] => ({
  id: `confirmation-${type}`,
  type,
  status: "confirmed",
  source_hash: "a".repeat(64),
  origin: "origin",
  inherited: false,
  fingerprint_schema_version: "confirmation-fingerprint-v2",
  binding_id: type === "legacy_binding" ? "binding-1" : null,
  source_binding_hash: type === "legacy_binding" ? "a".repeat(64) : null,
  confirmed_at: "2026-07-27T12:00:00Z",
  visibility: "teacher",
  ...overrides,
});
const blocker = (
  code: string,
  message = "请处理当前问题",
  severity: "blocking" | "warning" = "blocking",
): AssignmentReviewBundle["blockers"][number] => ({
  id: `blocker-${code}`,
  code,
  section: code.includes("FILE") ? "files" : "validation",
  message,
  entity: "assignment",
  entity_id: "assignment-1",
  severity,
  source_hash: code.padEnd(64, "b"),
  status: "open",
  visibility: "teacher",
});
const source = (kind: string, label: string) => ({ kind, label });
const question: AssignmentReviewBundle["questions"][number] = {
  id: "question-1",
  number: "1",
  content_hash: "c".repeat(64),
  content: "计算 1 + 1",
  source: source("ocr", "试卷识别题目"),
  provenance: null,
  visibility: "teacher",
  answer: {
    candidate: null,
    candidate_history: [],
    materialized: null,
    selected: {
      id: "answer-1",
      status: "confirmed",
      version: 1,
      content_hash: "d".repeat(64),
      source: source("teacher_authored", "教师编写答案"),
      content: "2",
      content_payload: {
        source_type: "teacher_authored",
        source_file: null,
        source_page: null,
        source_region: null,
        raw_content: "2",
        normalized_content: "2",
        structured_content: {},
        provenance: {},
      },
      visibility: "teacher",
    },
    history: [],
    visibility: "teacher",
  },
  rubric: {
    candidate: null,
    candidate_history: [],
    materialized: null,
    selected: {
      id: "rubric-1",
      status: "confirmed",
      version: 1,
      content_hash: "e".repeat(64),
      reference_answer_version_id: "answer-1",
      source: source("structured_rubric", "结构化评分标准"),
      title: "结果正确",
      total_points: "10.00",
      criteria: [],
      visibility: "teacher",
    },
    history: [],
    visibility: "teacher",
  },
};
const questionWithCriterion: AssignmentReviewBundle["questions"][number] = {
  ...question,
  rubric: {
    ...question.rubric,
    selected: {
      ...question.rubric.selected!,
      criteria: [
        {
          id: "criterion-1",
          key: "result",
          title: "最终答案",
          description: null,
          points: "10.00",
          display_order: 1,
          criterion_type: "result",
          required: true,
          dependencies: [],
          expected_evidence: {},
          validation_mode: "manual",
          validation_rule: {},
          manual_review_policy: {},
          partial_credit_policy: {},
          error_category: null,
          metadata: {},
        },
      ],
    },
  },
};
const bindingLoss = (
  code: string,
  overrides: Partial<
    NonNullable<
      NonNullable<AssignmentReviewBundle["binding"]>["loss_report"]
    >[number]
  > = {},
): NonNullable<
  NonNullable<AssignmentReviewBundle["binding"]>["loss_report"]
>[number] => ({
  code,
  question_id: "question-1",
  question_number: "1",
  criterion_key: "result",
  teacher_message: "后端教师说明",
  technical: { projection_profile: "structured-to-legacy" },
  ...overrides,
});
type BundleOptions = {
  assignmentId?: string;
  hash?: string;
  status?: AssignmentReviewBundle["status"];
  blockers?: AssignmentReviewBundle["blockers"];
  confirmations?: AssignmentReviewBundle["confirmations"];
  binding?: AssignmentReviewBundle["binding"];
  questions?: AssignmentReviewBundle["questions"];
};
const reviewBinding = (
  overrides: Partial<NonNullable<AssignmentReviewBundle["binding"]>> = {},
): NonNullable<AssignmentReviewBundle["binding"]> => ({
  id: "binding-1",
  status: "confirmed",
  binding_version: 1,
  source_binding_hash: "a".repeat(64),
  source_semantic_hash: "a".repeat(64),
  target_legacy_hash: "c".repeat(64),
  projection_profile: "structured-to-legacy",
  projection_version: "structured-rubric-projection-v3",
  mapping: [],
  loss_report: [],
  loss_report_hash: "d".repeat(64),
  manual_review_required: false,
  projection_current: true,
  projection_reason: null,
  expected_source_binding_hash: "a".repeat(64),
  visibility: "teacher",
  ...overrides,
});
const reviewBundle = ({
  assignmentId = "assignment-1",
  hash = "bundle-a",
  status = "ready_to_publish",
  blockers = [],
  confirmations = [
    ...confirmationKinds.map((kind) => confirmation(kind)),
    confirmation("legacy_binding", { origin: "system_auto" }),
  ],
  binding = reviewBinding(),
  questions = [question],
}: BundleOptions = {}): AssignmentReviewBundle => ({
  schema_version: "assignment-review-bundle-v1",
  assignment_id: assignmentId,
  version: {
    generation: 1,
    draft_revision_id: "revision-1",
    paper_version_id: "paper-1",
    source_snapshot_hash: "a".repeat(64),
    bundle_hash: hash,
  },
  status,
  questions,
  blockers,
  confirmations,
  binding,
});
const reviewItem = ({
  id = "risk-1",
  status = "open",
  severity = "blocking",
  issue_code = "TOTAL_SCORE_MISMATCH",
  section = "total_score",
  message = "题目分值合计与作业总分不一致",
  ...overrides
}: Partial<AssignmentReviewItemRecord> = {}): AssignmentReviewItemRecord => ({
  id,
  section,
  entity_type: "assignment",
  entity_id: "assignment-1",
  severity,
  issue_code,
  title: issue_code,
  message,
  evidence: {},
  source_hash: id.padEnd(64, "f"),
  status,
  eligibility: false,
  ...overrides,
});
const assignment = (overrides = {}) =>
  ({
    id: "assignment-1",
    title: "线代作业",
    status: "draft",
    updated_at: "2026-07-26T00:00:00Z",
    classes: [],
    due_at: "2026-08-01T00:00:00Z",
    total_score: "10.00",
    completeness: { ready: false, next_step: 6, issues: [] },
    ...overrides,
  }) as never;
const renderReview = (item = assignment(), onNavigate = vi.fn()) =>
  render(
    <AssignmentCentralReview
      item={item}
      onNavigate={onNavigate}
      onPublished={vi.fn()}
    />,
  );
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, reject, resolve };
};

beforeEach(() => {
  vi.clearAllMocks();
  reviewApi.list.mockResolvedValue({ items: [session()] });
  reviewApi.create.mockResolvedValue(session("review-created"));
  reviewApi.get.mockResolvedValue(session());
  reviewApi.items.mockResolvedValue({ items: [] });
  reviewApi.bundle.mockResolvedValue(reviewBundle());
  reviewApi.confirm.mockResolvedValue({ review_version: 2 });
  reviewApi.autoConfirm.mockResolvedValue({
    confirmed: [],
    skipped: {},
    review_version: 2,
  });
  reviewApi.refresh.mockResolvedValue(session());
  reviewApi.disposition.mockResolvedValue({ review_version: 2 });
  reviewApi.createBinding.mockResolvedValue({
    id: "binding-1",
    status: "draft",
    mapping: [],
    conversion_warnings: [],
    manual_review_required: false,
  });
  reviewApi.confirmBinding.mockResolvedValue({ review_version: 2 });
  reviewApi.prepare.mockResolvedValue({
    id: "readiness-1",
    readiness_hash: "r".repeat(64),
    status: "ready",
    expires_at: "2026-08-01T00:00:00Z",
    class_ids: [],
    due_at: null,
    total_score: "10.00",
    paper_version_id: "paper-1",
    legacy_rubric_version_id: "legacy-1",
  });
  manualApi.manualPublishReadiness.mockResolvedValue({
    mode: "manual",
    ready: true,
    issues: [],
    state_hash: "m".repeat(64),
    expected_assignment_updated_at: "2026-07-26T00:00:00Z",
    class_ids: ["class-1"],
    due_at: null,
    total_score: "10.00",
  });
  manualApi.publishManual.mockResolvedValue({ status: "published" });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("allows a teacher-authored assignment to publish without an AI generation job", async () => {
  reviewApi.list.mockResolvedValue({ items: [] });
  reviewApi.bundle.mockRejectedValue(
    new ApiError(409, {
      code: "GENERATION_REQUIRED",
      message: "尚无可审查的生成任务",
      details: {},
      request_id: "request-1",
    }),
  );
  const onPublished = vi.fn();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(
    <AssignmentCentralReview
      item={assignment()}
      onNavigate={vi.fn()}
      onPublished={onPublished}
    />,
  );

  expect(
    await screen.findByText(/教师手工整理的作业，不需要运行 AI 生成/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "确认发布" }));
  await waitFor(() =>
    expect(manualApi.publishManual).toHaveBeenCalledWith(
      "assignment-1",
      expect.objectContaining({ state_hash: "m".repeat(64) }),
    ),
  );
  expect(onPublished).toHaveBeenCalled();
});

it("groups many manual publish checks into at most three teacher tasks", async () => {
  reviewApi.list.mockResolvedValue({ items: [] });
  reviewApi.bundle.mockRejectedValue(
    new ApiError(409, {
      code: "GENERATION_REQUIRED",
      message: "尚无可审查的生成任务",
      details: {},
      request_id: "request-1",
    }),
  );
  manualApi.manualPublishReadiness.mockResolvedValue({
    mode: "manual",
    ready: false,
    issues: [
      { code: "NO_CLASSES", message: "请选择班级", step: 1 },
      { code: "FILE_ROLE_UNCONFIRMED", message: "文件用途未确认", step: 2 },
      { code: "PAPER_VARIANT_REVIEW", message: "试卷页面待核对", step: 3 },
      ...Array.from({ length: 8 }, (_, index) => ({
        code: "QUESTION_SCORE_REQUIRED",
        message: `第 ${index + 1} 题分值未设置`,
        step: 4,
        question_id: `question-${index + 1}`,
      })),
      { code: "NO_RUBRIC", message: "请设置评分标准", step: 5 },
    ],
    state_hash: "m".repeat(64),
    expected_assignment_updated_at: "2026-07-26T00:00:00Z",
    class_ids: [],
    due_at: null,
    total_score: null,
  });

  renderReview();

  expect(await screen.findByText("还有 3 件事")).toBeInTheDocument();
  expect(screen.getByText("1. 完善发布范围")).toBeInTheDocument();
  expect(screen.getByText("2. 核对试卷文件")).toBeInTheDocument();
  expect(screen.getByText("3. 完善题目与评分标准")).toBeInTheDocument();
  expect(screen.queryByText("还需完成 12 项")).not.toBeInTheDocument();
  expect(screen.getAllByText(/查看系统检查记录/)).toHaveLength(3);
});

describe("AssignmentCentralReview preserved behavior", () => {
  it("folds question details and technical blockers behind concise review tasks", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        questions: [
          {
            ...questionWithCriterion,
            rubric: {
              ...questionWithCriterion.rubric,
              selected: {
                ...questionWithCriterion.rubric.selected!,
                criteria: [
                  {
                    ...questionWithCriterion.rubric.selected!.criteria[0],
                    description: "核对学生的最终计算结果。",
                  },
                ],
              },
            },
          },
          {
            ...question,
            id: "question-2",
            number: "2",
            content: "说明计算过程",
            answer: {
              ...question.answer,
              selected: {
                ...question.answer.selected!,
                id: "answer-2",
                content: "步骤清楚",
              },
            },
            rubric: {
              ...question.rubric,
              selected: {
                ...question.rubric.selected!,
                id: "rubric-2",
                reference_answer_version_id: "answer-2",
                title: "过程说明",
                total_points: "5.00",
              },
            },
          },
        ],
        blockers: [blocker("TOTAL_SCORE_MISMATCH")],
      }),
    );

    renderReview();

    expect(await screen.findByText("计算 1 + 1")).toBeInTheDocument();
    expect(screen.getByText("参考答案：2")).toBeInTheDocument();
    expect(screen.getByText("评分标准：结果正确")).toBeInTheDocument();
    expect(screen.getByText("总分：10.00")).toBeInTheDocument();
    expect(
      screen.getByText("result · 最终答案 · 10.00 分"),
    ).toBeInTheDocument();
    expect(screen.getByText("核对学生的最终计算结果。")).toBeInTheDocument();
    expect(screen.getByText("暂无具体评分项")).toBeInTheDocument();
    expect(
      screen.getByText("请修改作业总分或题目分值，使二者完全一致。"),
    ).toBeInTheDocument();
    expect(screen.getByText("还有 1 件事")).toBeInTheDocument();
    const audit = screen.getByText(/查看检查记录/).closest("details");
    expect(audit).not.toHaveAttribute("open");
    expect(audit).toHaveTextContent("TOTAL_SCORE_MISMATCH");
  });

  it("requires an explicit start and never creates or publishes on load", async () => {
    reviewApi.list.mockResolvedValue({ items: [] });
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "missing_review",
        blockers: [blocker("REVIEW_SESSION_REQUIRED")],
        confirmations: [],
        binding: null,
      }),
    );
    renderReview();
    expect(await screen.findByText("开始集中审查")).toBeInTheDocument();
    expect(reviewApi.create).not.toHaveBeenCalled();
    expect(reviewApi.publish).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "开始集中审查" }));
    await waitFor(() => expect(reviewApi.create).toHaveBeenCalledOnce());
  });

  it("uses teacher-readable issue copy and keeps codes in technical details", async () => {
    reviewApi.items.mockResolvedValue({
      items: [reviewItem({ message: "raw technical message" })],
    });
    renderReview();
    fireEvent.click(await screen.findByText(/查看全部待处理明细/));
    expect(screen.queryByText("TOTAL_SCORE_MISMATCH")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByText("查看技术详情")[0]);
    expect(screen.getByText(/TOTAL_SCORE_MISMATCH/)).toBeInTheDocument();
  });

  it("shows the current issue object, cause, impact, action, and step contract", async () => {
    reviewApi.items.mockResolvedValue({
      items: [
        reviewItem({
          message:
            "第 3 题评分标准缺少 2 分；未修复前不能发布；去第 5 步修改对应题目的评分标准。",
          evidence: {
            teacher_guidance: {
              object: "question:question-3",
              reason: "评分项合计 8 分但题目满分 10 分",
              impact: "未修复前不能发布",
              action: "去第 5 步修改对应题目的评分标准",
              step: 5,
              anchor: "answer-rubric-editor",
            },
          },
        }),
      ],
    });
    renderReview();
    fireEvent.click(await screen.findByText(/查看全部待处理明细/));
    expect(
      screen.getAllByText(/第 3 题评分标准缺少 2 分.*未修复前不能发布.*第 5 步/)
        .length,
    ).toBeGreaterThan(0);
  });

  it("keeps resolved items folded until the teacher expands them", async () => {
    reviewApi.items.mockResolvedValue({
      items: [
        reviewItem({
          status: "resolved",
          teacher_action: "resolve_manual",
          teacher_note: "已核对",
        }),
      ],
    });
    renderReview();
    const toggle = await screen.findByRole("button", { name: /已解决 1 项/ });
    expect(screen.queryByText("已核对")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText("已核对")).toBeInTheDocument();
  });

  it("ignores stale rows and does not offer a manual bypass for structural gates", async () => {
    reviewApi.items.mockResolvedValue({
      items: [
        reviewItem({ id: "stale", status: "stale" }),
        reviewItem({ id: "current", issue_code: "TOTAL_SCORE_MISMATCH" }),
      ],
    });
    renderReview();
    expect(
      await screen.findByText("查看全部待处理明细（1 项）"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "人工检查并解决" }),
    ).not.toBeInTheDocument();
  });

  it("opens the file area only for a genuinely ambiguous role", async () => {
    const target = document.createElement("details");
    target.id = "generation-file-analysis";
    target.scrollIntoView = vi.fn();
    document.body.append(target);
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("FILE_ROLE_UNCONFIRMED")],
      }),
    );
    renderReview();
    fireEvent.click(
      await screen.findByRole("button", { name: "处理异常文件" }),
    );
    expect(target.open).toBe(true);
  });

  it("restarts a stale review with the newly created session, not a closed-over old one", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("SOURCE_STALE")],
      }),
    );
    reviewApi.create.mockResolvedValue(session("review-new"));
    renderReview();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "基于最新内容重新开始审查",
      }),
    );
    await waitFor(() =>
      expect(reviewApi.get).toHaveBeenCalledWith("review-new"),
    );
  });

  it("keeps the page-review confirmation flow", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("PAPER_VARIANT_REVIEW")],
      }),
    );
    renderReview();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "页面无误，完成核对",
      }),
    );
    await waitFor(() =>
      expect(reviewApi.confirm).toHaveBeenCalledWith(
        "review-1",
        "paper_version",
        1,
      ),
    );
  });

  it("automatically processes a complete total score", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("CONFIRM_TOTAL_SCORE_REQUIRED")],
        confirmations: confirmationKinds
          .filter((kind) => kind !== "total_score")
          .map((kind) => confirmation(kind)),
      }),
    );
    renderReview(
      assignment({
        paper_version: {
          questions: [{ max_score: "4" }, { max_score: "6" }],
        },
      }),
    );
    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());
    expect(
      screen.queryByRole("button", { name: /确认本次总分/ }),
    ).not.toBeInTheDocument();
  });

  it("does not let informational rows block publication preparation", async () => {
    reviewApi.items.mockResolvedValue({
      items: [reviewItem({ severity: "info", status: "open" })],
    });
    renderReview();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );
  });

  it("leaves the preparing state after a delayed readiness response", async () => {
    const pending = deferred<AssignmentReadinessRecord>();
    reviewApi.prepare.mockReturnValueOnce(pending.promise);
    renderReview();

    expect(
      await screen.findByRole("button", { name: "正在核对发布状态…" }),
    ).toBeDisabled();
    await testingAct(async () => {
      pending.resolve({
        id: "readiness-delayed",
        readiness_hash: "r".repeat(64),
        status: "ready",
        expires_at: "2026-08-01T00:00:00Z",
        class_ids: [],
        due_at: null,
        total_score: "10.00",
        paper_version_id: "paper-1",
        legacy_rubric_version_id: "rubric-1",
      });
      await pending.promise;
    });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );
    expect(reviewApi.prepare).toHaveBeenCalledOnce();
  });

  it("blocks when a required Bundle confirmation is absent", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        confirmations: confirmationKinds
          .filter((kind) => kind !== "due_at")
          .map((kind) => confirmation(kind)),
      }),
    );
    renderReview();
    expect(
      await screen.findByRole("button", { name: "确认发布" }),
    ).toBeDisabled();
  });

  it("does not attempt a compatibility binding while rubric content is incomplete", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        binding: null,
        confirmations: confirmationKinds.map((kind) => confirmation(kind)),
        blockers: [
          blocker("STRUCTURED_RUBRIC_UNCONFIRMED", "第 1 题缺少已确认评分标准"),
        ],
      }),
    );

    renderReview();

    expect(
      await screen.findByRole("button", { name: "生成兼容版本" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/请先完成上方仍影响发布的参考答案和评分标准/),
    ).toBeInTheDocument();
    await waitFor(() => expect(reviewApi.createBinding).not.toHaveBeenCalled());
  });

  it("offers to rebuild a stale binding", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        binding: reviewBinding({
          id: "binding-old",
          status: "stale",
          source_binding_hash: "x".repeat(64),
          expected_source_binding_hash: "y".repeat(64),
        }),
      }),
    );
    renderReview();
    expect(await screen.findByText("需要重新生成兼容版本")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成兼容版本" }));
    await waitFor(() => expect(reviewApi.createBinding).toHaveBeenCalledOnce());
  });

  it("continues from a refreshed draft binding and allows confirmation", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        binding: reviewBinding({
          id: "binding-draft",
          status: "draft",
          binding_version: 2,
          loss_report: [
            {
              code: "DEPENDENCY_NOT_LOSSLESS",
              question_id: "question-1",
              question_number: "1",
              criterion_key: "result",
              teacher_message: "依赖关系无法完整表达",
              technical: {},
            },
          ],
          manual_review_required: true,
        }),
      }),
    );
    renderReview();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "确认按上述人工核查方式发布兼容版",
      }),
    );
    await waitFor(() =>
      expect(reviewApi.confirmBinding).toHaveBeenCalledWith("binding-draft", 1),
    );
  });

  it("reloads Bundle after preparing a binding that requires manual review", async () => {
    reviewApi.createBinding.mockResolvedValue({
      id: "binding-manual",
      status: "draft",
      mapping: [],
      conversion_warnings: ["DEPENDENCY_NOT_LOSSLESS"],
      manual_review_required: true,
    });
    reviewApi.bundle
      .mockResolvedValueOnce(
        reviewBundle({
          status: "action_required",
          binding: null,
        }),
      )
      .mockResolvedValue(
        reviewBundle({
          status: "action_required",
          binding: reviewBinding({
            id: "binding-manual",
            status: "draft",
            loss_report: [
              {
                code: "DEPENDENCY_NOT_LOSSLESS",
                question_id: "question-1",
                question_number: "1",
                criterion_key: "result",
                teacher_message: "依赖关系无法完整表达",
                technical: {},
              },
            ],
            manual_review_required: true,
          }),
        }),
      );
    renderReview();
    fireEvent.click(
      await screen.findByRole("button", { name: "生成兼容版本" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "确认按上述人工核查方式发布兼容版",
        }),
      ).toBeEnabled(),
    );
    expect(reviewApi.bundle.mock.calls.length).toBeGreaterThan(1);
  });
});

describe("AssignmentCentralReview Bundle authority and races", () => {
  it("uses Bundle blockers even when the old session says ready", async () => {
    reviewApi.get.mockResolvedValue(
      session("review-1", {
        status: "ready",
        counts: { blocking: 0, warning: 0, info: 0 },
      }),
    );
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("TOTAL_SCORE_MISMATCH", "分值不一致")],
      }),
    );
    renderReview();
    expect(
      await screen.findByText("请修改作业总分或题目分值，使二者完全一致。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
  });

  it("fails closed when Bundle loading fails", async () => {
    reviewApi.bundle.mockRejectedValue(new Error("network"));
    renderReview();
    expect(
      await screen.findByText("暂时无法确认当前发布条件"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
  });

  it("clears an old readiness snapshot when the Bundle hash changes", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-1" }))
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-1" }))
      .mockResolvedValue(reviewBundle({ hash: "hash-2" }));
    renderReview();
    await screen.findByRole("button", { name: "确认发布" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新扫描最新状态" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled(),
    );
  });

  it("clears an enabled readiness snapshot when the next Bundle reload fails", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-ready" }))
      .mockRejectedValueOnce(new Error("network"));
    renderReview();
    await screen.findByRole("button", { name: "确认发布" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: "重新扫描最新状态" }));

    expect(
      await screen.findByText("暂时无法确认当前发布条件"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
  });

  it("ignores late responses from an older load", async () => {
    const oldSession = deferred<ReturnType<typeof session>>();
    const oldItems = deferred<{ items: ReturnType<typeof reviewItem>[] }>();
    const oldBundle = deferred<ReturnType<typeof reviewBundle>>();
    reviewApi.get.mockReturnValueOnce(oldSession.promise);
    reviewApi.items.mockReturnValueOnce(oldItems.promise);
    reviewApi.bundle.mockReturnValueOnce(oldBundle.promise);
    renderReview();
    await screen.findByRole("button", { name: "重新扫描最新状态" });

    reviewApi.get.mockResolvedValueOnce(session());
    reviewApi.items.mockResolvedValueOnce({ items: [] });
    reviewApi.bundle.mockResolvedValueOnce(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("NEW_STATE", "最新状态")],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新扫描最新状态" }));
    expect(
      await screen.findByText(
        "系统发现一项需要确认的问题，请查看说明并完成处理。",
      ),
    ).toBeInTheDocument();

    oldSession.resolve(session());
    oldItems.resolve({ items: [] });
    oldBundle.resolve(
      reviewBundle({
        status: "action_required",
        blockers: [blocker("OLD_STATE", "过期状态")],
      }),
    );
    await Promise.resolve();
    expect(screen.queryByText("过期状态")).not.toBeInTheDocument();
    expect(
      screen.getByText("系统发现一项需要确认的问题，请查看说明并完成处理。"),
    ).toBeInTheDocument();
  });

  it("renders the new blocking Bundle after automatic confirmation", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(
        reviewBundle({
          hash: "before-confirmation",
          status: "action_required",
          confirmations: confirmationKinds
            .filter((kind) => kind !== "due_at")
            .map((kind) => confirmation(kind)),
        }),
      )
      .mockResolvedValueOnce(
        reviewBundle({
          hash: "after-confirmation",
          status: "action_required",
          blockers: [
            blocker("TOTAL_SCORE_MISMATCH", "自动核对后发现分值不一致"),
          ],
        }),
      );
    renderReview();
    await screen.findByText("当前答案与评分标准");
    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());
    expect(
      await screen.findByText("请修改作业总分或题目分值，使二者完全一致。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
  });
});

describe("AssignmentCentralReview semantic confirmations and compatibility", () => {
  it("automatically processes routine confirmations without individual actions", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        binding: null,
        confirmations: [
          confirmation("classes", { origin: "inherited", inherited: true }),
        ],
      }),
    );

    renderReview();

    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());
    expect(
      screen.queryByTestId("review-confirmation-classes"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("review-confirmation-due_at"),
    ).not.toBeInTheDocument();
  });

  it("continues to automatic binding after routine confirmation reloads the bundle", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(
        reviewBundle({
          status: "action_required",
          binding: null,
          confirmations: confirmationKinds
            .filter((kind) => kind !== "due_at")
            .map((kind) => confirmation(kind)),
        }),
      )
      .mockResolvedValue(
        reviewBundle({
          status: "action_required",
          binding: null,
          confirmations: confirmationKinds.map((kind) => confirmation(kind)),
        }),
      );

    renderReview();

    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());
    await waitFor(() => expect(reviewApi.createBinding).toHaveBeenCalledOnce());
    await waitFor(() => expect(reviewApi.bundle).toHaveBeenCalledTimes(3));
  });

  it("reports an automatic confirmation failure and retries only after a teacher rescan", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        confirmations: confirmationKinds
          .filter((kind) => kind !== "due_at")
          .map((kind) => confirmation(kind)),
      }),
    );
    reviewApi.autoConfirm
      .mockRejectedValueOnce(new Error("temporary confirmation outage"))
      .mockResolvedValueOnce({
        confirmed: ["due_at"],
        skipped: {},
        review_version: 2,
      });

    renderReview();

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        "自动核对暂时失败，请重新扫描。",
        "error",
      ),
    );
    const rescan = screen.getByRole("button", {
      name: "重新扫描最新状态",
    });
    expect(rescan).toBeEnabled();
    expect(reviewApi.autoConfirm).toHaveBeenCalledOnce();
    await testingAct(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reviewApi.autoConfirm).toHaveBeenCalledOnce();

    fireEvent.click(rescan);
    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledTimes(2));
    await testingAct(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reviewApi.autoConfirm).toHaveBeenCalledTimes(2);
  });

  it("reports a lossless binding failure and allows a teacher-triggered retry", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        binding: null,
        confirmations: confirmationKinds.map((kind) => confirmation(kind)),
      }),
    );
    reviewApi.createBinding
      .mockRejectedValueOnce(new Error("temporary binding outage"))
      .mockResolvedValueOnce({
        id: "binding-1",
        status: "draft",
        mapping: [],
        conversion_warnings: [],
        manual_review_required: false,
      });

    renderReview();

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        "评分标准兼容检查暂时失败，请重新扫描。",
        "error",
      ),
    );
    const rescan = screen.getByRole("button", {
      name: "重新扫描最新状态",
    });
    expect(rescan).toBeEnabled();
    expect(reviewApi.createBinding).toHaveBeenCalledOnce();

    fireEvent.click(rescan);
    await waitFor(() =>
      expect(reviewApi.createBinding).toHaveBeenCalledTimes(2),
    );
    await testingAct(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reviewApi.createBinding).toHaveBeenCalledTimes(2);
  });

  it("ignores a late automatic confirmation failure from the previous assignment", async () => {
    const oldConfirmation = deferred<{
      confirmed: string[];
      skipped: Record<string, string>;
      review_version: number;
    }>();
    reviewApi.autoConfirm.mockReturnValueOnce(oldConfirmation.promise);
    reviewApi.list.mockImplementation(async (assignmentId: string) => ({
      items: [
        session(`review-${assignmentId}`, {
          assignment_id: assignmentId,
        }),
      ],
    }));
    reviewApi.get.mockImplementation(async (sessionId: string) =>
      session(sessionId, {
        assignment_id: sessionId.endsWith("assignment-2")
          ? "assignment-2"
          : "assignment-1",
      }),
    );
    reviewApi.bundle.mockImplementation(async (assignmentId: string) =>
      reviewBundle({
        assignmentId,
        status:
          assignmentId === "assignment-2"
            ? "ready_to_publish"
            : "action_required",
        confirmations: [
          ...(assignmentId === "assignment-2"
            ? confirmationKinds.map((kind) => confirmation(kind))
            : confirmationKinds
                .filter((kind) => kind !== "due_at")
                .map((kind) => confirmation(kind))),
          confirmation("legacy_binding", { origin: "system_auto" }),
        ],
      }),
    );

    const view = renderReview();
    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());

    view.rerender(
      <AssignmentCentralReview
        item={assignment({ id: "assignment-2", title: "作业 B" })}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    expect(await screen.findByText("✓ 可自动兼容")).toBeInTheDocument();

    await testingAct(async () => {
      oldConfirmation.reject(new Error("late assignment A failure"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(toast).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "重新扫描最新状态" }),
    ).toBeEnabled();
  });

  it.each(["resolve", "reject"] as const)(
    "ignores an automatic confirmation that settles after unmount (%s)",
    async (outcome) => {
      const pendingConfirmation = deferred<{
        confirmed: string[];
        skipped: Record<string, string>;
        review_version: number;
      }>();
      reviewApi.autoConfirm.mockReturnValueOnce(pendingConfirmation.promise);
      reviewApi.bundle.mockResolvedValue(
        reviewBundle({
          status: "action_required",
          confirmations: confirmationKinds
            .filter((kind) => kind !== "due_at")
            .map((kind) => confirmation(kind)),
        }),
      );

      const view = renderReview();
      await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());
      const callsBeforeUnmount = {
        bundle: reviewApi.bundle.mock.calls.length,
        get: reviewApi.get.mock.calls.length,
        items: reviewApi.items.mock.calls.length,
      };

      view.unmount();
      await testingAct(async () => {
        if (outcome === "resolve") {
          pendingConfirmation.resolve({
            confirmed: ["due_at"],
            skipped: {},
            review_version: 2,
          });
        } else {
          pendingConfirmation.reject(new Error("late unmounted confirmation"));
        }
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(toast).not.toHaveBeenCalled();
      expect(reviewApi.bundle).toHaveBeenCalledTimes(callsBeforeUnmount.bundle);
      expect(reviewApi.get).toHaveBeenCalledTimes(callsBeforeUnmount.get);
      expect(reviewApi.items).toHaveBeenCalledTimes(callsBeforeUnmount.items);
    },
  );

  it.each(["resolve", "reject"] as const)(
    "ignores a lossless binding that settles after unmount (%s)",
    async (outcome) => {
      const pendingBinding = deferred<{
        id: string;
        status: string;
        mapping: never[];
        conversion_warnings: never[];
        manual_review_required: boolean;
      }>();
      reviewApi.createBinding.mockReturnValueOnce(pendingBinding.promise);
      reviewApi.bundle.mockResolvedValue(
        reviewBundle({
          status: "action_required",
          binding: null,
          confirmations: confirmationKinds.map((kind) => confirmation(kind)),
        }),
      );

      const view = renderReview();
      await waitFor(() =>
        expect(reviewApi.createBinding).toHaveBeenCalledOnce(),
      );
      const callsBeforeUnmount = {
        bundle: reviewApi.bundle.mock.calls.length,
        get: reviewApi.get.mock.calls.length,
        items: reviewApi.items.mock.calls.length,
      };

      view.unmount();
      await testingAct(async () => {
        if (outcome === "resolve") {
          pendingBinding.resolve({
            id: "binding-1",
            status: "draft",
            mapping: [],
            conversion_warnings: [],
            manual_review_required: false,
          });
        } else {
          pendingBinding.reject(new Error("late unmounted binding"));
        }
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(toast).not.toHaveBeenCalled();
      expect(reviewApi.bundle).toHaveBeenCalledTimes(callsBeforeUnmount.bundle);
      expect(reviewApi.get).toHaveBeenCalledTimes(callsBeforeUnmount.get);
      expect(reviewApi.items).toHaveBeenCalledTimes(callsBeforeUnmount.items);
    },
  );

  it("shows a fresh lossless binding as automatically compatible without a confirmation action", async () => {
    renderReview();

    expect(await screen.findByText("✓ 可自动兼容")).toBeInTheDocument();
    expect(
      screen.queryByTestId("rubric-binding-loss-confirm"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("内容未变，已沿用确认", { exact: false }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );
  });

  it.each([
    [
      "DEPENDENCY_NOT_LOSSLESS",
      "兼容版不能自动约束此评分项与前置步骤的关系，批改时需要人工核查先后条件。",
    ],
    [
      "ALTERNATIVE_PATH_NOT_LOSSLESS",
      "多条可选得分路径会在兼容版中合并展示，批改时需要人工判断学生满足了哪条路径。",
    ],
    [
      "VALIDATION_RULE_NOT_LOSSLESS",
      "自动验证规则不会在兼容版中执行，批改时需要人工核查答案条件。",
    ],
    [
      "EXPECTED_EVIDENCE_NOT_LOSSLESS",
      "兼容版不能完整保留该评分项要求查看的证据，批改时需要人工核对学生是否提供了指定依据。",
    ],
    [
      "MANUAL_REVIEW_POLICY_NOT_LOSSLESS",
      "兼容版不能自动执行该评分项的人工复核策略，批改时需要按原评分标准逐项复核。",
    ],
    [
      "PARTIAL_CREDIT_POLICY_NOT_LOSSLESS",
      "兼容版不能完整执行该评分项的部分得分规则，批改时需要人工判断应给的部分分。",
    ],
    [
      "ERROR_CATEGORY_NOT_LOSSLESS",
      "兼容版不能完整保留该评分项的错误分类，批改时需要人工判断学生错误所属类别。",
    ],
    [
      "CRITERION_METADATA_NOT_LOSSLESS",
      "该评分项包含兼容版无法完整表达的扩展要求，批改时需要对照原评分标准人工核查。",
    ],
    [
      "DEDUCTION_RULE_NOT_LOSSLESS",
      "兼容版不能自动执行该评分项的结构化扣分规则，批改时需要人工计算扣分。",
    ],
    [
      "COMMON_ERROR_CODES_NOT_LOSSLESS",
      "兼容版不能完整保留该评分项的多项常见错误标记，批改时需要人工识别对应错误。",
    ],
    [
      "FEEDBACK_TEMPLATE_NOT_LOSSLESS",
      "兼容版不能自动套用该评分项的反馈模板，批改后需要人工补充相应反馈。",
    ],
  ])(
    "explains the known %s loss in teacher language",
    async (code, message) => {
      reviewApi.bundle.mockResolvedValue(
        reviewBundle({
          status: "action_required",
          questions: [questionWithCriterion],
          confirmations: confirmationKinds.map((kind) => confirmation(kind)),
          binding: reviewBinding({
            status: "draft",
            loss_report: [bindingLoss(code)],
            manual_review_required: true,
          }),
        }),
      );

      renderReview();

      expect(
        await screen.findByText("需要教师确认的具体业务损失"),
      ).toBeInTheDocument();
      expect(screen.getByText("第 1 题 · 最终答案")).toBeInTheDocument();
      expect(screen.getByText(message)).toBeInTheDocument();
      expect(screen.getByTestId("rubric-binding-loss-confirm")).toBeEnabled();
      expect(
        screen.getByTestId("rubric-binding-compatibility-summary"),
      ).not.toHaveTextContent(code);
      expect(
        screen.getByTestId("rubric-binding-compatibility-summary"),
      ).not.toHaveTextContent("structured-to-legacy");
    },
  );

  it("renders combined known losses as separate business impacts", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        questions: [questionWithCriterion],
        confirmations: confirmationKinds.map((kind) => confirmation(kind)),
        binding: reviewBinding({
          status: "draft",
          loss_report: [
            bindingLoss("DEPENDENCY_NOT_LOSSLESS"),
            bindingLoss("ALTERNATIVE_PATH_NOT_LOSSLESS"),
            bindingLoss("VALIDATION_RULE_NOT_LOSSLESS"),
          ],
          manual_review_required: true,
        }),
      }),
    );

    renderReview();

    expect(
      await screen.findAllByTestId("rubric-binding-loss-item"),
    ).toHaveLength(3);
    expect(
      screen.getByText(/不能自动约束此评分项与前置步骤/),
    ).toBeInTheDocument();
    expect(screen.getByText(/多条可选得分路径/)).toBeInTheDocument();
    expect(screen.getByText(/自动验证规则不会/)).toBeInTheDocument();
  });

  it("shows a completed state after the teacher confirms known lossy compatibility", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        questions: [questionWithCriterion],
        confirmations: [
          ...confirmationKinds.map((kind) => confirmation(kind)),
          confirmation("legacy_binding"),
        ],
        binding: reviewBinding({
          status: "confirmed",
          loss_report: [bindingLoss("VALIDATION_RULE_NOT_LOSSLESS")],
          manual_review_required: true,
        }),
      }),
    );

    renderReview();

    expect(
      await screen.findByText("✓ 已确认按上述人工核查方式使用兼容版"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("rubric-binding-loss-confirm"),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认发布" })).toBeEnabled(),
    );
  });

  it("fails closed for an unknown loss and exposes it only in closed technical details", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        status: "action_required",
        questions: [questionWithCriterion],
        confirmations: confirmationKinds.map((kind) => confirmation(kind)),
        binding: reviewBinding({
          status: "draft",
          loss_report: [bindingLoss("UNKNOWN_LOSS")],
          manual_review_required: true,
        }),
      }),
    );

    renderReview();

    const summary = await screen.findByTestId(
      "rubric-binding-compatibility-summary",
    );
    expect(summary).toHaveTextContent("存在尚无法安全说明的兼容差异");
    expect(summary).not.toHaveTextContent("UNKNOWN_LOSS");
    expect(
      screen.queryByTestId("rubric-binding-loss-confirm"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
    const details = screen.getByTestId("rubric-binding-technical-details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("UNKNOWN_LOSS");
  });

  it("keeps legacy binding fresh for the new review instead of presenting it as inherited", async () => {
    reviewApi.bundle.mockResolvedValue(
      reviewBundle({
        confirmations: [
          ...confirmationKinds.map((kind) =>
            confirmation(kind, { origin: "inherited", inherited: true }),
          ),
          confirmation("legacy_binding", { origin: "system_auto" }),
        ],
      }),
    );

    renderReview();

    expect(await screen.findByText("常规内容已自动核对")).toBeInTheDocument();
    expect(screen.getByText("✓ 可自动兼容")).toBeInTheDocument();
    expect(screen.getByTestId("rubric-binding-automatic")).toHaveTextContent(
      "本次重新生成",
    );
    expect(
      screen.getByTestId("rubric-binding-automatic"),
    ).not.toHaveTextContent("内容未变，已沿用确认");
    const details = screen.getByTestId("rubric-binding-technical-details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("system_auto");
  });
});

describe("AssignmentCentralReview fail-closed publication contract", () => {
  it.each([
    [
      "mismatched source binding hash",
      { expected_source_binding_hash: "e".repeat(64) },
      {},
    ],
    [
      "mismatched semantic and source hashes",
      { source_semantic_hash: "e".repeat(64) },
      {},
    ],
    ["mismatched confirmation binding id", {}, { binding_id: "binding-other" }],
    [
      "mismatched confirmation source hash",
      {},
      { source_binding_hash: "e".repeat(64) },
    ],
    ["nullable migrated projection profile", { projection_profile: null }, {}],
    ["nullable migrated semantic hash", { source_semantic_hash: null }, {}],
    ["nullable migrated loss report", { loss_report: null }, {}],
    [
      "missing server projection evidence",
      { projection_current: undefined, projection_reason: undefined },
      {},
    ],
    [
      "missing server confirmation association",
      {},
      { binding_id: undefined, source_binding_hash: undefined },
    ],
    ["lossless human confirmation", {}, { origin: "origin", inherited: false }],
    [
      "missing inherited flag",
      {},
      { origin: "system_auto", inherited: undefined },
    ],
    ["stale binding", { status: "stale" }, {}],
    [
      "unknown loss code",
      {
        loss_report: [bindingLoss("FUTURE_UNKNOWN_LOSS")],
        manual_review_required: true,
      },
      { origin: "origin", inherited: false },
    ],
  ])(
    "blocks a malicious ready Bundle with %s",
    async (_label, bindingOverrides, confirmationOverrides) => {
      reviewApi.bundle.mockResolvedValue(
        reviewBundle({
          status: "ready_to_publish",
          confirmations: [
            ...confirmationKinds.map((kind) => confirmation(kind)),
            confirmation("legacy_binding", confirmationOverrides as never),
          ],
          binding: reviewBinding(bindingOverrides as never),
        }),
      );

      renderReview();

      expect(
        await screen.findByRole("button", { name: "确认发布" }),
      ).toBeDisabled();
      expect(screen.getByRole("button", { name: "确认发布" })).toBeDisabled();
    },
  );

  it.each([
    {
      schema_version: "assignment-review-bundle-v0",
      assignment_id: "assignment-1",
    },
    {
      schema_version: "assignment-review-bundle-v1",
      assignment_id: "assignment-other",
    },
  ])(
    "rejects a Bundle outside the current assignment contract",
    async (bad) => {
      reviewApi.bundle.mockResolvedValue({
        ...reviewBundle(),
        ...bad,
      } as AssignmentReviewBundle);

      renderReview();

      expect(
        await screen.findByText(
          "审查内容版本与当前作业不一致，请重新加载后再继续发布。",
        ),
      ).toBeInTheDocument();
    },
  );

  it("keeps an unknown backend fallback message inside technical details", async () => {
    reviewApi.items.mockResolvedValue({
      items: [
        reviewItem({
          issue_code: "FUTURE_PRIVATE_BLOCKER",
          message: "internal table assignment_projection failed",
        }),
      ],
    });

    renderReview();
    fireEvent.click(await screen.findByText(/查看全部待处理明细/));

    expect(
      screen.getByText("系统发现一项需要确认的问题，请查看说明并完成处理。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("internal table assignment_projection failed"),
    ).not.toBeInTheDocument();
    const details = screen
      .getByText("错误码：FUTURE_PRIVATE_BLOCKER")
      .closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent(
      "internal table assignment_projection failed",
    );
  });

  it("clears an assignment-mismatched session and exposes no confirmation action", async () => {
    reviewApi.get.mockResolvedValue(
      session("review-wrong-assignment", {
        assignment_id: "assignment-other",
      }),
    );

    renderReview();

    expect(
      await screen.findByText(
        "审查内容版本与当前作业不一致，请重新加载后再继续发布。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("review-confirmation-due_at"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认发布" }),
    ).not.toBeInTheDocument();
    expect(reviewApi.confirm).not.toHaveBeenCalled();
  });
});

describe("AssignmentCentralReview assignment epoch", () => {
  it("recovers from an initial Bundle failure when review inputs revision changes", async () => {
    reviewApi.list.mockResolvedValue({ items: [] });
    reviewApi.bundle
      .mockRejectedValueOnce(new Error("GENERATION_REQUIRED"))
      .mockResolvedValueOnce(
        reviewBundle({
          status: "missing_review",
          blockers: [blocker("REVIEW_SESSION_REQUIRED")],
          confirmations: [],
          binding: null,
        }),
      );
    const view = render(
      <AssignmentCentralReview
        item={assignment()}
        reviewInputsRevision={0}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    expect(
      await screen.findByText("无法取得当前审查内容，请重试后再继续发布。"),
    ).toBeInTheDocument();

    view.rerender(
      <AssignmentCentralReview
        item={assignment()}
        reviewInputsRevision={1}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );

    expect(await screen.findByText("开始集中审查")).toBeInTheDocument();
    expect(reviewApi.bundle).toHaveBeenCalledTimes(2);
  });

  it("does not let a late old Bundle failure replace the refreshed Bundle", async () => {
    const oldBundle = deferred<AssignmentReviewBundle>();
    reviewApi.list.mockResolvedValue({ items: [] });
    reviewApi.bundle
      .mockReturnValueOnce(oldBundle.promise)
      .mockResolvedValueOnce(
        reviewBundle({
          status: "missing_review",
          blockers: [blocker("REVIEW_SESSION_REQUIRED")],
          confirmations: [],
          binding: null,
        }),
      );
    const view = render(
      <AssignmentCentralReview
        item={assignment()}
        reviewInputsRevision={0}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    await waitFor(() => expect(reviewApi.bundle).toHaveBeenCalledOnce());

    view.rerender(
      <AssignmentCentralReview
        item={assignment()}
        reviewInputsRevision={1}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    expect(await screen.findByText("开始集中审查")).toBeInTheDocument();

    await testingAct(async () => {
      oldBundle.reject(new Error("GENERATION_REQUIRED"));
      await Promise.resolve();
    });
    expect(screen.getByText("开始集中审查")).toBeInTheDocument();
    expect(
      screen.queryByText("无法取得当前审查内容，请重试后再继续发布。"),
    ).not.toBeInTheDocument();
  });

  it("does not reload assignment A after its pending mutation finishes on assignment B", async () => {
    const confirmationMutation = deferred<{
      confirmed: string[];
      skipped: Record<string, string>;
      review_version: number;
    }>();
    reviewApi.autoConfirm.mockReturnValueOnce(confirmationMutation.promise);
    reviewApi.list.mockImplementation(async (assignmentId: string) => ({
      items: [
        session(`review-${assignmentId}`, {
          assignment_id: assignmentId,
        }),
      ],
    }));
    reviewApi.get.mockImplementation(async (sessionId: string) =>
      session(sessionId, {
        assignment_id: sessionId.endsWith("assignment-2")
          ? "assignment-2"
          : "assignment-1",
      }),
    );
    reviewApi.bundle.mockImplementation(async (assignmentId: string) =>
      reviewBundle({
        assignmentId,
        status: "action_required",
        blockers:
          assignmentId === "assignment-2"
            ? [blocker("TOTAL_SCORE_MISMATCH", "assignment B blocker")]
            : [],
        confirmations: confirmationKinds
          .filter((kind) => kind !== "due_at")
          .map((kind) => confirmation(kind)),
      }),
    );

    const view = renderReview(assignment());
    await waitFor(() => expect(reviewApi.autoConfirm).toHaveBeenCalledOnce());

    view.rerender(
      <AssignmentCentralReview
        item={assignment({ id: "assignment-2", title: "作业 B" })}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    expect(
      await screen.findByText("请修改作业总分或题目分值，使二者完全一致。"),
    ).toBeInTheDocument();
    const assignmentACallsBeforeCompletion = reviewApi.bundle.mock.calls.filter(
      ([assignmentId]) => assignmentId === "assignment-1",
    ).length;

    confirmationMutation.resolve({
      confirmed: ["due_at"],
      skipped: {},
      review_version: 2,
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(
      reviewApi.bundle.mock.calls.filter(
        ([assignmentId]) => assignmentId === "assignment-1",
      ),
    ).toHaveLength(assignmentACallsBeforeCompletion);
    expect(
      screen.getByText("请修改作业总分或题目分值，使二者完全一致。"),
    ).toBeInTheDocument();
  });
});

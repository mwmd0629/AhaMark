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
import type {
  AssignmentReviewBundle,
  AssignmentReviewItemRecord,
} from "@/lib/api";

const reviewApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  items: vi.fn(),
  bundle: vi.fn(),
  confirm: vi.fn(),
  refresh: vi.fn(),
  disposition: vi.fn(),
  createBinding: vi.fn(),
  confirmBinding: vi.fn(),
  prepare: vi.fn(),
  publish: vi.fn(),
}));
vi.mock("@/lib/api", async (load) => ({
  ...(await load()),
  assignmentReviewApi: reviewApi,
}));

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
  issue_code = "CONFIRM_CLASSES_REQUIRED",
  section = "classes",
  message = "必须由教师确认",
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
});
afterEach(cleanup);

describe("AssignmentCentralReview preserved behavior", () => {
  it("shows each question answer, full rubric, and teacher-readable blocker together", async () => {
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
        blockers: [blocker("CONFIRM_REFERENCE_ANSWERS_REQUIRED")],
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
      screen.getByText("参考答案已经生成，但还需要确认本次发布使用这些版本。"),
    ).toBeInTheDocument();
    const details = screen
      .getByText("当前阻塞项")
      .parentElement?.querySelector("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("CONFIRM_REFERENCE_ANSWERS_REQUIRED");
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
    expect(
      screen.queryByText("CONFIRM_CLASSES_REQUIRED"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByText("查看技术详情")[0]);
    expect(screen.getByText(/CONFIRM_CLASSES_REQUIRED/)).toBeInTheDocument();
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

  it("opens and scrolls to the real file confirmation area", async () => {
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
      await screen.findByRole("button", { name: "打开文件确认区" }),
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

  it("keeps the total-score confirmation flow", async () => {
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
    fireEvent.click(
      await screen.findByRole("button", {
        name: "确认本次总分为 10 分",
      }),
    );
    await waitFor(() =>
      expect(reviewApi.confirm).toHaveBeenCalledWith(
        "review-1",
        "total_score",
        1,
      ),
    );
  });

  it("does not let informational rows block publication preparation", async () => {
    reviewApi.items.mockResolvedValue({
      items: [reviewItem({ severity: "info", status: "open" })],
    });
    renderReview();
    expect(
      await screen.findByRole("button", { name: "准备发布" }),
    ).toBeEnabled();
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
      await screen.findByRole("button", { name: "准备发布" }),
    ).toBeDisabled();
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
        blockers: [
          blocker("CONFIRM_REFERENCE_ANSWERS_REQUIRED", "请确认参考答案"),
        ],
      }),
    );
    renderReview();
    expect(
      await screen.findByText(
        "参考答案已经生成，但还需要确认本次发布使用这些版本。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "教师确认并发布" }),
    ).toBeDisabled();
  });

  it("fails closed when Bundle loading fails", async () => {
    reviewApi.bundle.mockRejectedValue(new Error("network"));
    renderReview();
    expect(
      await screen.findByText("暂时无法确认当前发布条件"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "教师确认并发布" }),
    ).toBeDisabled();
  });

  it("clears an old readiness snapshot when the Bundle hash changes", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-1" }))
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-1" }))
      .mockResolvedValue(reviewBundle({ hash: "hash-2" }));
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "准备发布" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "教师确认并发布" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新扫描最新状态" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "教师确认并发布" }),
      ).toBeDisabled(),
    );
  });

  it("clears an enabled readiness snapshot when the next Bundle reload fails", async () => {
    reviewApi.bundle
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-ready" }))
      .mockResolvedValueOnce(reviewBundle({ hash: "hash-ready" }))
      .mockRejectedValueOnce(new Error("network"));
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "准备发布" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "教师确认并发布" }),
      ).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: "重新扫描最新状态" }));

    expect(
      await screen.findByText("暂时无法确认当前发布条件"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "教师确认并发布" }),
    ).toBeDisabled();
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

  it("renders the new blocking Bundle after a confirmation mutation", async () => {
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
            blocker(
              "CONFIRM_REFERENCE_ANSWERS_REQUIRED",
              "确认后发现新的参考答案阻断",
            ),
          ],
        }),
      );
    renderReview();
    await screen.findByText("当前答案与评分标准");
    fireEvent.click(screen.getByRole("button", { name: "确认截止时间" }));
    await waitFor(() => expect(reviewApi.confirm).toHaveBeenCalledOnce());
    expect(
      await screen.findByText(
        "参考答案已经生成，但还需要确认本次发布使用这些版本。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "教师确认并发布" }),
    ).toBeDisabled();
  });
});

describe("AssignmentCentralReview semantic confirmations and compatibility", () => {
  it("shows inherited confirmations as read-only and leaves changed content actionable", async () => {
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

    expect(
      await screen.findByText("班级：内容未变，已沿用确认"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("review-confirmation-classes"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("review-confirmation-due_at")).toBeEnabled();
  });

  it("shows a fresh lossless binding as automatically compatible without a confirmation action", async () => {
    renderReview();

    expect(await screen.findByText("✓ 可自动兼容")).toBeInTheDocument();
    expect(
      screen.queryByTestId("rubric-binding-loss-confirm"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("内容未变，已沿用确认", { exact: false }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "准备发布" })).toBeEnabled();
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
    expect(screen.getByRole("button", { name: "准备发布" })).toBeEnabled();
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
    expect(
      screen.getByRole("button", { name: "教师确认并发布" }),
    ).toBeDisabled();
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

    expect(
      await screen.findByText("班级：内容未变，已沿用确认"),
    ).toBeInTheDocument();
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
        await screen.findByRole("button", { name: "准备发布" }),
      ).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "教师确认并发布" }),
      ).toBeDisabled();
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
      screen.queryByRole("button", { name: "准备发布" }),
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
    const confirmationMutation = deferred<{ review_version: number }>();
    reviewApi.confirm.mockReturnValueOnce(confirmationMutation.promise);
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
            ? [
                blocker(
                  "CONFIRM_REFERENCE_ANSWERS_REQUIRED",
                  "assignment B blocker",
                ),
              ]
            : [],
        confirmations: confirmationKinds
          .filter((kind) => kind !== "due_at")
          .map((kind) => confirmation(kind)),
      }),
    );

    const view = renderReview(assignment());
    fireEvent.click(
      await screen.findByRole("button", { name: "确认截止时间" }),
    );
    await waitFor(() => expect(reviewApi.confirm).toHaveBeenCalledOnce());

    view.rerender(
      <AssignmentCentralReview
        item={assignment({ id: "assignment-2", title: "作业 B" })}
        onNavigate={vi.fn()}
        onPublished={vi.fn()}
      />,
    );
    expect(
      await screen.findByText(
        "参考答案已经生成，但还需要确认本次发布使用这些版本。",
      ),
    ).toBeInTheDocument();
    const assignmentACallsBeforeCompletion = reviewApi.bundle.mock.calls.filter(
      ([assignmentId]) => assignmentId === "assignment-1",
    ).length;

    confirmationMutation.resolve({ review_version: 2 });
    await Promise.resolve();
    await Promise.resolve();

    expect(
      reviewApi.bundle.mock.calls.filter(
        ([assignmentId]) => assignmentId === "assignment-1",
      ),
    ).toHaveLength(assignmentACallsBeforeCompletion);
    expect(
      screen.getByText("参考答案已经生成，但还需要确认本次发布使用这些版本。"),
    ).toBeInTheDocument();
  });
});

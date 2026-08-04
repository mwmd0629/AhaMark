"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Card, Select, useToast } from "@/components/ui";
import {
  ApiError,
  assignmentReviewApi,
  assignmentsApi,
  type AssignmentReadinessRecord,
  type AssignmentReviewBundle,
  type AssignmentRecord,
  type AssignmentReviewItemRecord,
  type AssignmentReviewSessionRecord,
  type ManualPublishReadiness,
} from "@/lib/api";
import { getReviewCopy } from "@/lib/review-copy";

const confirmations = [
  ["classes", "确认班级"],
  ["due_at", "确认截止时间"],
  ["total_score", "确认总分"],
  ["reference_answers", "确认答案版本"],
  ["structured_rubrics", "确认评分标准"],
] as const;
const routineConfirmationIssueCodes = new Set(
  confirmations.map(([kind]) => `CONFIRM_${kind.toUpperCase()}_REQUIRED`),
);

const bindingLossMessages: Record<string, string> = {
  DEPENDENCY_NOT_LOSSLESS:
    "兼容版不能自动约束此评分项与前置步骤的关系，批改时需要人工核查先后条件。",
  ALTERNATIVE_PATH_NOT_LOSSLESS:
    "多条可选得分路径会在兼容版中合并展示，批改时需要人工判断学生满足了哪条路径。",
  VALIDATION_RULE_NOT_LOSSLESS:
    "自动验证规则不会在兼容版中执行，批改时需要人工核查答案条件。",
  EXPECTED_EVIDENCE_NOT_LOSSLESS:
    "兼容版不能完整保留该评分项要求查看的证据，批改时需要人工核对学生是否提供了指定依据。",
  MANUAL_REVIEW_POLICY_NOT_LOSSLESS:
    "兼容版不能自动执行该评分项的人工复核策略，批改时需要按原评分标准逐项复核。",
  PARTIAL_CREDIT_POLICY_NOT_LOSSLESS:
    "兼容版不能完整执行该评分项的部分得分规则，批改时需要人工判断应给的部分分。",
  ERROR_CATEGORY_NOT_LOSSLESS:
    "兼容版不能完整保留该评分项的错误分类，批改时需要人工判断学生错误所属类别。",
  CRITERION_METADATA_NOT_LOSSLESS:
    "该评分项包含兼容版无法完整表达的扩展要求，批改时需要对照原评分标准人工核查。",
  DEDUCTION_RULE_NOT_LOSSLESS:
    "兼容版不能自动执行该评分项的结构化扣分规则，批改时需要人工计算扣分。",
  COMMON_ERROR_CODES_NOT_LOSSLESS:
    "兼容版不能完整保留该评分项的多项常见错误标记，批改时需要人工识别对应错误。",
  FEEDBACK_TEMPLATE_NOT_LOSSLESS:
    "兼容版不能自动套用该评分项的反馈模板，批改后需要人工补充相应反馈。",
};

const bundleSchemaVersion = "assignment-review-bundle-v1";
const projectionProfile = "structured-to-legacy";
const projectionVersion = "structured-rubric-projection-v3";
const confirmationFingerprintVersion = "confirmation-fingerprint-v2";
const sha256Pattern = /^[0-9a-f]{64}$/i;

const isCurrentBundleContract = (
  value: AssignmentReviewBundle,
  assignmentId: string,
) =>
  value.schema_version === bundleSchemaVersion &&
  value.assignment_id === assignmentId &&
  Array.isArray(value.blockers) &&
  Array.isArray(value.confirmations) &&
  Array.isArray(value.questions);

const hasFreshProjectionEvidence = (
  binding: NonNullable<AssignmentReviewBundle["binding"]>,
) => {
  const losses = binding.loss_report;
  return (
    binding.status !== "stale" &&
    sha256Pattern.test(binding.source_binding_hash) &&
    binding.expected_source_binding_hash === binding.source_binding_hash &&
    typeof binding.source_semantic_hash === "string" &&
    sha256Pattern.test(binding.source_semantic_hash) &&
    binding.source_semantic_hash === binding.source_binding_hash &&
    typeof binding.target_legacy_hash === "string" &&
    sha256Pattern.test(binding.target_legacy_hash) &&
    binding.projection_profile === projectionProfile &&
    binding.projection_version === projectionVersion &&
    binding.projection_current === true &&
    binding.projection_reason === null &&
    Array.isArray(binding.mapping) &&
    Array.isArray(losses) &&
    typeof binding.loss_report_hash === "string" &&
    sha256Pattern.test(binding.loss_report_hash) &&
    binding.manual_review_required === losses.length > 0
  );
};

const bindingIssueCodes = new Set([
  "LEGACY_CONVERSION_REVIEW",
  "LEGACY_BINDING_REQUIRED",
  "CONFIRM_LEGACY_BINDING_REQUIRED",
  "LEGACY_BINDING_STALE",
]);

const manuallyResolvableBlockingCodes = new Set([
  "GENERATION_PARTIAL",
  "PAPER_VARIANT_REVIEW",
  "QUESTION_CONFIRMATION_REQUIRED",
  "QUESTION_PAPER_ROLE_UNCONFIRMED",
]);

type ManualPublishIssue = ManualPublishReadiness["issues"][number];

const manualTaskMeta = {
  scope: {
    title: "完善发布范围",
    detail: "确认发布班级、联考授权和学生范围。",
  },
  files: {
    title: "核对试卷文件",
    detail: "补齐或核对上传文件、试卷页面和文件用途。",
  },
  content: {
    title: "完善题目与评分标准",
    detail: "集中处理题目、分值、参考答案和评分标准。",
  },
} as const;

function groupManualPublishIssues(issues: ManualPublishIssue[]) {
  const groups = new Map<
    keyof typeof manualTaskMeta,
    { issues: ManualPublishIssue[]; steps: number[] }
  >();
  for (const issue of issues) {
    const key: keyof typeof manualTaskMeta =
      issue.step <= 1 ? "scope" : issue.step <= 3 ? "files" : "content";
    const group = groups.get(key) ?? { issues: [], steps: [] };
    group.issues.push(issue);
    if (!group.steps.includes(issue.step)) group.steps.push(issue.step);
    groups.set(key, group);
  }
  return [...groups.entries()].map(([key, group]) => ({
    key,
    ...manualTaskMeta[key],
    ...group,
    steps: group.steps.sort((left, right) => left - right),
  }));
}

export function AssignmentCentralReview({
  item,
  reviewInputsRevision = 0,
  onNavigate,
  onPublished,
}: {
  item: AssignmentRecord;
  reviewInputsRevision?: number;
  onNavigate: (step: number) => void;
  onPublished: () => void;
}) {
  const toast = useToast();
  const [session, setSession] = useState<AssignmentReviewSessionRecord>();
  const [items, setItems] = useState<AssignmentReviewItemRecord[]>([]);
  const [readiness, setReadiness] = useState<AssignmentReadinessRecord>();
  const [bundle, setBundle] = useState<AssignmentReviewBundle>();
  const [bundleError, setBundleError] = useState("");
  const [manualReadiness, setManualReadiness] =
    useState<ManualPublishReadiness>();
  const [manualMode, setManualMode] = useState(false);
  const [preparingReadiness, setPreparingReadiness] = useState(false);
  const [severity, setSeverity] = useState("all");
  const [section, setSection] = useState("all");
  const [busy, setBusy] = useState(false);
  const [automating, setAutomating] = useState(false);
  const [resolvedOpen, setResolvedOpen] = useState(false);
  const requestGeneration = useRef(0);
  const mutationGeneration = useRef(0);
  const autoConfirmationAttempt = useRef("");
  const autoBindingAttempt = useRef("");
  const automationRequest = useRef(0);
  const preparationRequest = useRef(0);
  const preparationInFlight = useRef(false);
  const assignmentEpoch = useRef({
    id: item.id,
    reviewInputsRevision,
    value: 0,
  });
  if (
    assignmentEpoch.current.id !== item.id ||
    assignmentEpoch.current.reviewInputsRevision !== reviewInputsRevision
  ) {
    assignmentEpoch.current = {
      id: item.id,
      reviewInputsRevision,
      value: assignmentEpoch.current.value + 1,
    };
    requestGeneration.current += 1;
    mutationGeneration.current += 1;
    automationRequest.current += 1;
  }

  const isCurrentRequest = useCallback(
    (assignmentId: string, epoch: number, generation?: number) =>
      assignmentEpoch.current.id === assignmentId &&
      assignmentEpoch.current.value === epoch &&
      (generation === undefined || requestGeneration.current === generation),
    [],
  );

  const commitBundle = useCallback(
    (next: AssignmentReviewBundle, assignmentId: string) => {
      if (!isCurrentBundleContract(next, assignmentId)) {
        setBundle(undefined);
        setReadiness(undefined);
        setBundleError(
          "审查内容版本与当前作业不一致，请重新加载后再继续发布。",
        );
        return false;
      }
      setBundle((previous) => {
        if (
          next.status !== "ready_to_publish" ||
          previous?.version.bundle_hash !== next.version.bundle_hash
        ) {
          setReadiness(undefined);
        }
        return next;
      });
      setBundleError("");
      return true;
    },
    [],
  );

  const loadBundle = useCallback(async () => {
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const generation = ++requestGeneration.current;
    try {
      const next = await assignmentReviewApi.bundle(assignmentId);
      if (!isCurrentRequest(assignmentId, epoch, generation)) return next;
      commitBundle(next, assignmentId);
      return next;
    } catch (error) {
      if (!isCurrentRequest(assignmentId, epoch, generation)) return;
      if (
        error instanceof ApiError &&
        ["GENERATION_REQUIRED", "DRAFT_INPUT_REQUIRED"].includes(
          error.body.code,
        )
      ) {
        try {
          const readiness =
            await assignmentsApi.manualPublishReadiness(assignmentId);
          if (!isCurrentRequest(assignmentId, epoch, generation)) return;
          setManualReadiness(readiness);
          setManualMode(true);
          setBundleError("");
          return;
        } catch (manualError) {
          if (!isCurrentRequest(assignmentId, epoch, generation)) return;
          setBundleError(
            manualError instanceof ApiError
              ? manualError.message
              : "无法检查当前作业，请稍后重试。",
          );
          return;
        }
      }
      setBundle(undefined);
      setReadiness(undefined);
      setBundleError("无法取得当前审查内容，请重试后再继续发布。");
      throw error;
    }
  }, [commitBundle, isCurrentRequest, item.id]);

  const load = useCallback(
    async (active: AssignmentReviewSessionRecord) => {
      const assignmentId = item.id;
      const epoch = assignmentEpoch.current.value;
      const generation = ++requestGeneration.current;
      try {
        const [fresh, rows, nextBundle] = await Promise.all([
          assignmentReviewApi.get(active.id),
          assignmentReviewApi.items(active.id),
          assignmentReviewApi.bundle(assignmentId),
        ]);
        if (!isCurrentRequest(assignmentId, epoch, generation)) return;
        if (
          fresh.assignment_id !== assignmentId ||
          !isCurrentBundleContract(nextBundle, assignmentId)
        ) {
          setSession(undefined);
          setItems([]);
          setBundle(undefined);
          setReadiness(undefined);
          setBundleError(
            "审查内容版本与当前作业不一致，请重新加载后再继续发布。",
          );
          return;
        }
        setSession(fresh);
        setItems(rows.items);
        commitBundle(nextBundle, assignmentId);
      } catch (error) {
        if (!isCurrentRequest(assignmentId, epoch, generation)) return;
        setBundle(undefined);
        setReadiness(undefined);
        setBundleError("无法取得当前审查内容，请重试后再继续发布。");
        throw error;
      }
    },
    [commitBundle, isCurrentRequest, item.id],
  );

  useEffect(() => {
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const generation = ++requestGeneration.current;
    setSession(undefined);
    setItems([]);
    setReadiness(undefined);
    setBundle(undefined);
    setBundleError("");
    setManualMode(false);
    setManualReadiness(undefined);
    preparationRequest.current += 1;
    preparationInFlight.current = false;
    setPreparingReadiness(false);
    setBusy(false);
    setAutomating(false);
    autoConfirmationAttempt.current = "";
    autoBindingAttempt.current = "";
    assignmentReviewApi
      .list(assignmentId)
      .then((result) => {
        if (!isCurrentRequest(assignmentId, epoch, generation)) return;
        const active = result.items.find(
          (row) =>
            row.assignment_id === assignmentId &&
            !["stale", "invalidated"].includes(row.status),
        );
        if (active) {
          setSession(active);
          void load(active).catch(() => undefined);
        } else {
          void loadBundle().catch(() => undefined);
        }
      })
      .catch(() => undefined);
    return () => {
      requestGeneration.current += 1;
      mutationGeneration.current += 1;
      automationRequest.current += 1;
    };
  }, [isCurrentRequest, item.id, load, loadBundle, reviewInputsRevision]);

  const act = async <T,>(
    fn: () => Promise<T>,
    message: string,
    reloadFromResult?: (result: T) => AssignmentReviewSessionRecord,
  ) => {
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const mutation = ++mutationGeneration.current;
    const mutationIsCurrent = () =>
      isCurrentRequest(assignmentId, epoch) &&
      mutationGeneration.current === mutation;
    setBusy(true);
    try {
      const result = await fn();
      if (!mutationIsCurrent()) return result;
      toast(message);
      const reloadSession = reloadFromResult?.(result) ?? session;
      if (reloadSession?.assignment_id === assignmentId)
        await load(reloadSession);
      else await loadBundle();
      return result;
    } catch (error) {
      if (mutationIsCurrent())
        toast(error instanceof ApiError ? error.message : "操作失败", "error");
    } finally {
      if (mutationIsCurrent()) setBusy(false);
    }
  };
  const visible = useMemo(
    () =>
      items.filter(
        (row) =>
          !["stale", "superseded"].includes(row.status) &&
          row.issue_code !== "LEGACY_CONVERSION_REVIEW" &&
          !routineConfirmationIssueCodes.has(row.issue_code) &&
          (severity === "all" || row.severity === severity) &&
          (section === "all" || row.section === section),
      ),
    [items, severity, section],
  );
  const isResolved = (row: AssignmentReviewItemRecord) =>
    ["acknowledged", "resolved", "rejected"].includes(row.status);
  const unresolved = visible
    .filter((row) => !isResolved(row))
    .sort(
      (a, b) =>
        ({ blocking: 0, warning: 1, info: 2 })[a.severity] -
        { blocking: 0, warning: 1, info: 2 }[b.severity],
    );
  const resolved = visible.filter(isResolved);
  const sections = [...new Set(items.map((row) => row.section))].sort();
  const sectionLabels: Record<string, string> = {
    validation: "内容版本",
    classes: "发布班级",
    due_at: "截止时间",
    files: "试卷文件",
    pages: "试卷页面",
    questions: "题目",
    answers: "参考答案",
    answer_sources: "答案文件",
    rubrics: "评分标准",
    file_roles: "文件用途",
    publication: "发布绑定",
    total_score: "分值",
  };
  const openCodes = new Set(bundle?.blockers.map((row) => row.code) ?? []);
  const bundleConfirmationsByType = useMemo(
    () =>
      new Map(
        bundle?.confirmations.map((confirmation) => [
          confirmation.type,
          confirmation,
        ]) ?? [],
      ),
    [bundle?.confirmations],
  );
  const bundleConfirmations = useMemo(
    () => new Set(bundleConfirmationsByType.keys()),
    [bundleConfirmationsByType],
  );
  const requiredConfirmationsComplete = confirmations.every(([kind]) =>
    bundleConfirmations.has(kind),
  );
  const hasPreBindingBlocker =
    bundle?.blockers.some(
      (blocker) =>
        blocker.severity === "blocking" && !bindingIssueCodes.has(blocker.code),
    ) ?? true;
  const bindingPrerequisitesComplete =
    requiredConfirmationsComplete && !hasPreBindingBlocker;
  const legacyBindingConfirmation =
    bundleConfirmationsByType.get("legacy_binding");
  const legacyBindingIssueOpen =
    openCodes.has("LEGACY_BINDING_REQUIRED") ||
    openCodes.has("CONFIRM_LEGACY_BINDING_REQUIRED") ||
    openCodes.has("LEGACY_BINDING_STALE");
  const bindingLosses = bundle?.binding?.loss_report ?? [];
  const unknownBindingLosses = bindingLosses.filter(
    (loss) => !bindingLossMessages[loss.code],
  );
  const bindingHasUnknownLoss = unknownBindingLosses.length > 0;
  const bindingIsStale = bundle?.binding?.status === "stale";
  const bindingProjectionIsFresh =
    !!bundle?.binding && hasFreshProjectionEvidence(bundle.binding);
  const bindingIsLossless =
    bindingProjectionIsFresh &&
    !bundle!.binding!.manual_review_required &&
    bindingLosses.length === 0;
  const legacyBindingConfirmationIsCurrent =
    legacyBindingConfirmation?.status === "confirmed" &&
    legacyBindingConfirmation.inherited === false &&
    legacyBindingConfirmation.fingerprint_schema_version ===
      confirmationFingerprintVersion &&
    sha256Pattern.test(legacyBindingConfirmation.source_hash) &&
    legacyBindingConfirmation.binding_id === bundle?.binding?.id &&
    legacyBindingConfirmation.source_binding_hash ===
      bundle?.binding?.source_binding_hash;
  const bindingIsAutoCompatible =
    bindingProjectionIsFresh &&
    !bindingHasUnknownLoss &&
    bundle?.binding?.status === "confirmed" &&
    legacyBindingConfirmationIsCurrent &&
    legacyBindingConfirmation?.origin === "system_auto" &&
    legacyBindingConfirmation.inherited === false;
  const bindingHasKnownLosses =
    bindingProjectionIsFresh &&
    bindingLosses.length > 0 &&
    !bindingHasUnknownLoss;
  const bindingLossesConfirmed =
    bindingHasKnownLosses &&
    bundle?.binding?.status === "confirmed" &&
    legacyBindingConfirmationIsCurrent &&
    legacyBindingConfirmation?.origin === "origin" &&
    legacyBindingConfirmation.inherited === false;
  const bindingCanBeConfirmed =
    bindingHasKnownLosses &&
    !bindingLossesConfirmed &&
    !bindingIsStale &&
    ["draft", "validated"].includes(bundle?.binding?.status ?? "");
  const bindingPublicationReady =
    !legacyBindingIssueOpen &&
    !bindingIsStale &&
    !bindingHasUnknownLoss &&
    (bindingIsAutoCompatible || bindingLossesConfirmed);
  const bundleContractIsCurrent =
    !!bundle && isCurrentBundleContract(bundle, item.id);
  const teacherVisibleBlockers =
    bundle?.blockers.filter(
      (blocker) =>
        !bindingIssueCodes.has(blocker.code) &&
        !routineConfirmationIssueCodes.has(blocker.code),
    ) ?? [];
  const publicationBlocked =
    !bundle ||
    !!bundleError ||
    !bundleContractIsCurrent ||
    bundle.status !== "ready_to_publish" ||
    bundle.blockers.length > 0 ||
    !requiredConfirmationsComplete ||
    !bindingPublicationReady;
  useEffect(() => {
    if (
      !session ||
      !bundle ||
      bundleError ||
      busy ||
      automating ||
      requiredConfirmationsComplete
    )
      return;
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const key = `${session.id}:${session.review_version}:${[
      ...bundleConfirmations,
    ]
      .sort()
      .join(",")}`;
    if (autoConfirmationAttempt.current === key) return;
    autoConfirmationAttempt.current = key;
    const request = ++automationRequest.current;
    const automationIsCurrent = () =>
      automationRequest.current === request &&
      isCurrentRequest(assignmentId, epoch);
    setAutomating(true);
    assignmentReviewApi
      .autoConfirm(session.id, session.review_version)
      .then(() => {
        if (automationIsCurrent()) return load(session);
      })
      .catch((error) => {
        if (automationIsCurrent()) {
          toast(
            error instanceof ApiError
              ? error.message
              : "自动核对暂时失败，请重新扫描。",
            "error",
          );
        }
      })
      .finally(() => {
        if (automationIsCurrent()) setAutomating(false);
      });
  }, [
    automating,
    bundle,
    bundleConfirmations,
    bundleError,
    busy,
    isCurrentRequest,
    item.id,
    load,
    requiredConfirmationsComplete,
    session,
    toast,
  ]);
  useEffect(() => {
    if (
      !session ||
      !bundle ||
      bundle.binding ||
      bundleError ||
      busy ||
      automating ||
      !bindingPrerequisitesComplete
    )
      return;
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const key = `${session.id}:${session.review_version}`;
    if (autoBindingAttempt.current === key) return;
    autoBindingAttempt.current = key;
    const request = ++automationRequest.current;
    const automationIsCurrent = () =>
      automationRequest.current === request &&
      isCurrentRequest(assignmentId, epoch);
    setAutomating(true);
    assignmentReviewApi
      .createBinding(session.id, session.review_version)
      .then(() => {
        if (automationIsCurrent()) return load(session);
      })
      .catch((error) => {
        if (automationIsCurrent()) {
          toast(
            error instanceof ApiError
              ? error.message
              : "评分标准兼容检查暂时失败，请重新扫描。",
            "error",
          );
        }
      })
      .finally(() => {
        if (automationIsCurrent()) setAutomating(false);
      });
  }, [
    automating,
    bundle,
    bundleError,
    busy,
    isCurrentRequest,
    item.id,
    load,
    bindingPrerequisitesComplete,
    session,
    toast,
  ]);
  useEffect(() => {
    if (
      publicationBlocked ||
      !session ||
      readiness ||
      manualMode ||
      preparationInFlight.current
    )
      return;
    const assignmentId = item.id;
    const epoch = assignmentEpoch.current.value;
    const request = ++preparationRequest.current;
    preparationInFlight.current = true;
    setPreparingReadiness(true);
    assignmentReviewApi
      .prepare(session.id, session.review_version)
      .then((next) => {
        if (
          preparationRequest.current === request &&
          isCurrentRequest(assignmentId, epoch)
        ) {
          setReadiness(next);
        }
      })
      .catch((error) => {
        if (
          preparationRequest.current === request &&
          isCurrentRequest(assignmentId, epoch)
        ) {
          setBundleError(
            error instanceof ApiError
              ? error.message
              : "无法核对发布状态，请重试。",
          );
        }
      })
      .finally(() => {
        if (preparationRequest.current === request) {
          preparationInFlight.current = false;
        }
        if (
          preparationRequest.current === request &&
          isCurrentRequest(assignmentId, epoch)
        ) {
          setPreparingReadiness(false);
        }
      });
  }, [
    isCurrentRequest,
    item.id,
    manualMode,
    publicationBlocked,
    readiness,
    session,
  ]);
  const bundleBlockingCount = teacherVisibleBlockers.filter(
    (blocker) => blocker.severity === "blocking",
  ).length;
  const bundleWarningCount = teacherVisibleBlockers.filter(
    (blocker) => blocker.severity === "warning",
  ).length;
  const questionTotal =
    item.paper_version?.questions.reduce(
      (sum, question) => sum + Number(question.max_score ?? 0),
      0,
    ) ?? 0;
  const questionScores =
    item.paper_version?.questions.map((question) =>
      Number(question.max_score ?? 0),
    ) ?? [];
  const scoreCalculation = questionScores.length
    ? `${questionScores.length} 道题：${questionScores.join(" + ")} = ${questionTotal}`
    : "尚未读取题目分值";
  const sourceIsStale =
    openCodes.has("SOURCE_STALE") ||
    openCodes.has("REVIEW_SOURCE_STALE") ||
    openCodes.has("SOURCE_CHANGED");
  const priorityActions: {
    title: string;
    detail: string;
    step?: number;
    actionLabel?: string;
    targetId?: string;
    secondaryStep?: number;
    secondaryActionLabel?: string;
    confirmationKind?: string;
    confirmationSuccess?: string;
  }[] = [
    ...(openCodes.has("TOTAL_SCORE_MISMATCH")
      ? [
          {
            title: "题目分值合计与作业总分不一致",
            detail: `${scoreCalculation}；当前作业总分：${item.total_score ?? "未设置"}`,
            step: 4,
            actionLabel: "核对题目分值",
            secondaryStep: 1,
            secondaryActionLabel: "设置作业总分",
          },
        ]
      : []),
    ...(openCodes.has("FILE_ROLE_UNCONFIRMED") ||
    openCodes.has("FILE_ROLE_CONFLICT_REVIEW_REQUIRED")
      ? [
          {
            title: "处理无法识别的文件",
            detail: "仅需为系统无法判断或用途冲突的文件选择用途。",
            targetId: "generation-file-analysis",
            actionLabel: "处理异常文件",
          },
        ]
      : []),
    ...(openCodes.has("PAPER_VARIANT_REVIEW")
      ? [
          {
            title: "核对试卷页面",
            detail:
              "先逐页查看顺序和方向；确认三页属于同一份试卷后，点击“页面无误，完成核对”。",
            step: 3,
            actionLabel: "查看全部页面",
            confirmationKind: "paper_version",
            secondaryActionLabel: "页面无误，完成核对",
          },
        ]
      : []),
    ...(bindingIsStale ||
    (bindingHasKnownLosses && !bindingIsAutoCompatible) ||
    bindingHasUnknownLoss
      ? [
          {
            title: bindingIsStale
              ? "重新生成评分标准兼容版本"
              : bindingHasKnownLosses
                ? "确认评分标准兼容方式"
                : "生成评分标准兼容版本",
            detail:
              "完整评分标准保持不变；系统只为现有批改流程生成本次发布使用的兼容版本。",
          },
        ]
      : []),
  ];
  const priorityCoveredCodes = new Set([
    "TOTAL_SCORE_MISMATCH",
    "FILE_ROLE_UNCONFIRMED",
    "ANSWER_SOURCE_UNCONFIRMED",
    "ANSWER_SOURCE_CONFIRMATION_REQUIRED",
    "PAPER_VARIANT_REVIEW",
  ]);
  const hasOtherVisibleBlockers = teacherVisibleBlockers.some(
    (blocker) => !priorityCoveredCodes.has(blocker.code),
  );
  const blockerMessages = [
    ...new Set(
      teacherVisibleBlockers.map(
        (blocker) => getReviewCopy(blocker.code).message,
      ),
    ),
  ];
  const actionTasks = [
    ...priorityActions,
    ...(hasOtherVisibleBlockers
      ? [
          {
            title: "处理其他内容问题",
            detail: "相关检查已合并到记录中，打开后按提示修改即可。",
            targetId: "review-audit",
            actionLabel: "查看检查记录",
          },
        ]
      : []),
  ];

  const renderReview = (review: AssignmentReviewItemRecord) => {
    const copy = getReviewCopy(review.issue_code);
    const guidance = review.evidence?.teacher_guidance as
      | {
          reason?: string;
          impact?: string;
          action?: string;
          step?: number;
          anchor?: string;
        }
      | undefined;
    return (
      <li key={review.id} className="rounded-xl border p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-slate-500">
              {review.severity === "blocking"
                ? "影响发布"
                : review.severity === "warning"
                  ? "建议处理"
                  : "提示"}{" "}
              · {sectionLabels[review.section] ?? "其他"}
            </p>
            <strong className="mt-1 block">{copy.title}</strong>
          </div>
          <span
            className={`rounded-full px-2 py-1 text-xs ${
              review.severity === "blocking"
                ? "bg-red-100 text-red-700"
                : review.severity === "warning"
                  ? "bg-amber-100 text-amber-800"
                  : "bg-emerald-100 text-emerald-800"
            }`}
          >
            {isResolved(review) ? "已解决" : "待处理"}
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-700">
          {guidance ? review.message : copy.message}
        </p>
        {isResolved(review) && (
          <dl className="mt-3 grid gap-1 rounded-lg bg-slate-50 p-3 text-sm">
            {review.teacher_action && (
              <div>
                <dt className="inline font-medium">处理方式：</dt>
                <dd className="inline">
                  {review.teacher_action === "acknowledge"
                    ? "已确认查看"
                    : review.teacher_action === "resolve_manual"
                      ? "人工检查并解决"
                      : review.teacher_action}
                </dd>
              </div>
            )}
            {review.teacher_note && (
              <div>
                <dt className="inline font-medium">教师备注：</dt>
                <dd className="inline">{review.teacher_note}</dd>
              </div>
            )}
            {review.reviewed_by && (
              <div>
                <dt className="inline font-medium">处理人：</dt>
                <dd className="inline">{review.reviewed_by}</dd>
              </div>
            )}
            {review.reviewed_at && (
              <div>
                <dt className="inline font-medium">处理时间：</dt>
                <dd className="inline">
                  {new Date(review.reviewed_at).toLocaleString("zh-CN")}
                </dd>
              </div>
            )}
          </dl>
        )}
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-slate-600">
            查看技术详情
          </summary>
          <div className="mt-2 rounded bg-slate-950 p-3 text-xs text-slate-100">
            <p>错误码：{review.issue_code}</p>
            <p>问题 ID：{review.id}</p>
            <p>后端说明：{review.message}</p>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap">
              {JSON.stringify(review.evidence, null, 2)}
            </pre>
          </div>
        </details>
        {!isResolved(review) && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() =>
                onNavigate(
                  review.section === "files"
                    ? 2
                    : review.section === "pages"
                      ? 3
                      : review.section === "questions"
                        ? 4
                        : ["answers", "rubrics"].includes(review.section)
                          ? 5
                          : 1,
                )
              }
            >
              {copy.action}
            </Button>
            {review.severity === "warning" && (
              <Button
                disabled={busy}
                onClick={() =>
                  act(
                    () =>
                      assignmentReviewApi.disposition(
                        review.id,
                        session!.review_version,
                        "acknowledge",
                      ),
                    "问题已标记为已查看",
                  )
                }
              >
                确认已查看
              </Button>
            )}
            {review.severity === "blocking" &&
              manuallyResolvableBlockingCodes.has(review.issue_code) && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () =>
                        assignmentReviewApi.disposition(
                          review.id,
                          session!.review_version,
                          "resolve_manual",
                        ),
                      "问题已由教师人工检查并解决",
                    )
                  }
                >
                  人工检查并解决
                </Button>
              )}
          </div>
        )}
      </li>
    );
  };

  if (manualMode) {
    const issues = manualReadiness?.issues ?? [];
    const tasks = groupManualPublishIssues(issues);
    return (
      <Card className="space-y-5 p-6">
        <div>
          <h2 className="font-bold">发布作业</h2>
          <p className="text-sm text-slate-600">
            这是教师手工整理的作业，不需要运行 AI
            生成。系统会在发布时再次核对当前内容。
          </p>
        </div>
        {tasks.length > 0 ? (
          <section className="space-y-3" aria-label="发布前需要处理的问题">
            <div>
              <h3 className="font-semibold">还有 {tasks.length} 件事</h3>
              <p className="mt-1 text-sm text-slate-600">
                系统已把重复检查合并；每件事处理完成后会自动重新核对。
              </p>
            </div>
            <ul className="space-y-2">
              {tasks.map((task, index) => (
                <li className="rounded-xl border p-4" key={task.key}>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <strong>
                        {index + 1}. {task.title}
                      </strong>
                      <p className="mt-1 text-sm text-slate-600">
                        {task.detail}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {task.steps.map((step) => (
                        <Button
                          key={step}
                          variant="outline"
                          onClick={() => onNavigate(step)}
                        >
                          {step === 1
                            ? "设置班级"
                            : step === 2
                              ? "检查文件"
                              : step === 3
                                ? "检查页面"
                                : step === 4
                                  ? "编辑题目与分值"
                                  : "编辑答案与评分标准"}
                        </Button>
                      ))}
                    </div>
                  </div>
                  <details className="mt-3 text-sm text-slate-600">
                    <summary className="cursor-pointer">
                      查看系统检查记录（{task.issues.length} 条）
                    </summary>
                    <ul className="mt-2 list-inside list-disc space-y-1">
                      {task.issues.map((issue, issueIndex) => (
                        <li
                          key={`${issue.code}-${issue.question_id ?? issueIndex}`}
                        >
                          {issue.message}
                        </li>
                      ))}
                    </ul>
                  </details>
                </li>
              ))}
            </ul>
          </section>
        ) : manualReadiness ? (
          <section className="space-y-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
            <div>
              <h3 className="font-semibold text-emerald-900">发布条件已满足</h3>
              <p className="mt-1 text-sm text-emerald-800">
                {manualReadiness.class_ids.length} 个班级 · 总分{" "}
                {manualReadiness.total_score} · 截止时间{" "}
                {manualReadiness.due_at
                  ? new Date(manualReadiness.due_at).toLocaleString("zh-CN")
                  : "无"}
              </p>
            </div>
            <Button
              loading={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "确认发布这份作业？发布后不能直接修改题目和评分标准。",
                  )
                )
                  return;
                setBusy(true);
                void assignmentsApi
                  .publishManual(item.id, manualReadiness)
                  .then(() => {
                    toast("作业已由教师确认发布");
                    onPublished();
                  })
                  .catch(async (error) => {
                    toast(
                      error instanceof ApiError ? error.message : "发布失败",
                      "error",
                    );
                    try {
                      setManualReadiness(
                        await assignmentsApi.manualPublishReadiness(item.id),
                      );
                    } catch {
                      // Keep the original error visible through the toast.
                    }
                  })
                  .finally(() => setBusy(false));
              }}
            >
              确认发布
            </Button>
          </section>
        ) : (
          <p>正在检查发布条件…</p>
        )}
      </Card>
    );
  }

  if (!session) {
    if (bundleError) {
      return (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">集中审查中心</h2>
          <p role="alert" className="text-sm text-red-800">
            {bundleError}
          </p>
          <Button disabled={busy} onClick={() => void loadBundle()}>
            重新加载当前作业
          </Button>
        </Card>
      );
    }
    return (
      <Card className="space-y-4 p-6">
        <h2 className="font-bold">集中审查中心</h2>
        <p>创建会话会固定当前生成与版本输入，不会自动发布。</p>
        <Button
          loading={busy}
          onClick={() =>
            act(
              () => assignmentReviewApi.create(item.id),
              "集中审查会话已创建",
              (created) => created,
            )
          }
        >
          开始集中审查
        </Button>
      </Card>
    );
  }

  return (
    <Card className="space-y-5 p-6">
      <div>
        <h2 className="font-bold">集中审查中心</h2>
        <p className="text-sm text-slate-600">
          系统会自动核对常规内容；这里只需处理真正影响发布的问题。
        </p>
      </div>
      {bundleError ? (
        <section
          className="rounded-xl border border-red-300 bg-red-50 p-4"
          aria-label="当前审查内容加载失败"
        >
          <h3 className="font-semibold text-red-900">
            暂时无法确认当前发布条件
          </h3>
          <p className="mt-1 text-sm text-red-800">{bundleError}</p>
          <Button
            className="mt-3"
            disabled={busy}
            onClick={() => void load(session)}
          >
            重试
          </Button>
        </section>
      ) : bundle ? (
        <section
          className="space-y-3 rounded-xl border p-4"
          aria-label="当前答案与评分标准"
        >
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold">当前答案与评分标准</h3>
            <span className="text-sm text-slate-600">
              {bundle.status === "ready_to_publish"
                ? "可以进入发布准备"
                : "仍需处理"}
            </span>
          </div>
          <details className="rounded-lg border bg-slate-50">
            <summary className="cursor-pointer p-3 text-sm font-medium">
              查看题目、答案与评分标准（{bundle.questions.length} 道题）
            </summary>
            <ul className="grid gap-3 border-t p-3">
              {bundle.questions.map((question) => {
                const pendingAnswer =
                  question.answer.materialized?.status === "draft" &&
                  question.answer.materialized.id !==
                    question.answer.selected?.id;
                const pendingRubric =
                  question.rubric.materialized?.status === "draft" &&
                  question.rubric.materialized.id !==
                    question.rubric.selected?.id;
                return (
                  <li
                    key={question.id}
                    className="rounded-lg bg-slate-50 p-3 text-sm"
                  >
                    <strong>第 {question.number} 题</strong>
                    <p className="mt-1">
                      {question.content ?? "题目内容待补充"}
                    </p>
                    <p className="mt-2">
                      参考答案：
                      {question.answer.selected?.content ?? "尚未确定"}
                    </p>
                    <p className="text-xs text-slate-600">
                      {question.answer.selected?.status === "draft"
                        ? "等待确认"
                        : question.answer.selected
                          ? "已确认"
                          : "未提供"}
                    </p>
                    <p className="mt-2">
                      评分标准：{question.rubric.selected?.title ?? "尚未确定"}
                    </p>
                    <p className="text-xs text-slate-600">
                      {question.rubric.selected?.status === "draft"
                        ? "等待确认"
                        : question.rubric.selected
                          ? "已确认"
                          : "未提供"}
                    </p>
                    {question.rubric.selected && (
                      <div className="mt-2 rounded-lg border bg-white p-3">
                        <p className="font-medium">
                          总分：{question.rubric.selected.total_points}
                        </p>
                        {question.rubric.selected.criteria.length > 0 ? (
                          <ul
                            className="mt-2 space-y-2"
                            aria-label={`第 ${question.number} 题 Rubric 评分项`}
                          >
                            {question.rubric.selected.criteria.map(
                              (criterion) => (
                                <li
                                  key={criterion.id}
                                  className="rounded-md bg-slate-50 p-2"
                                >
                                  <strong>
                                    {criterion.key} · {criterion.title} ·{" "}
                                    {criterion.points} 分
                                  </strong>
                                  {criterion.description && (
                                    <p className="mt-1 whitespace-pre-wrap text-slate-600">
                                      {criterion.description}
                                    </p>
                                  )}
                                </li>
                              ),
                            )}
                          </ul>
                        ) : (
                          <p className="mt-2 text-slate-600">暂无具体评分项</p>
                        )}
                      </div>
                    )}
                    {(pendingAnswer || pendingRubric) && (
                      <p className="mt-2 text-amber-800">
                        有较新的内容等待教师确认。
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          </details>
        </section>
      ) : null}
      {teacherVisibleBlockers.length > 0 ? (
        <section
          className="rounded-xl border border-red-200 bg-red-50 p-4"
          aria-label="发布阻断说明"
        >
          <h3 className="font-semibold text-red-900">
            还有 {Math.max(actionTasks.length, 1)} 件事
          </h3>
          <p className="mt-1 text-sm text-red-800">
            同类检查已经合并。请按下面顺序处理；修改内容后系统会重新核对。
          </p>
          {sourceIsStale && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-300 bg-white p-3">
              <div>
                <strong className="text-sm text-red-900">
                  当前审查仍绑定旧版草稿
                </strong>
                <p className="text-xs text-red-700">
                  最新文件分析已经生成，必须创建一份绑定最新草稿的审查，旧审查不会自动变绿。
                </p>
              </div>
              <Button
                disabled={busy}
                onClick={() =>
                  act(
                    () => assignmentReviewApi.create(item.id),
                    "已切换到最新内容并重新开始审查",
                    (created) => created,
                  )
                }
              >
                基于最新内容重新开始审查
              </Button>
            </div>
          )}
          {actionTasks.length > 0 && (
            <ol className="mt-3 grid gap-2">
              {actionTasks.map((action, index) => (
                <li
                  key={action.title}
                  className="flex items-center justify-between gap-3 rounded-lg bg-white p-3"
                >
                  <div>
                    <strong className="text-sm">
                      {index + 1}. {action.title}
                    </strong>
                    <p className="text-xs text-slate-600">{action.detail}</p>
                  </div>
                  {(action.step || action.targetId) && (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (action.targetId) {
                            const target = document.getElementById(
                              action.targetId,
                            );
                            if (target instanceof HTMLDetailsElement) {
                              target.open = true;
                            }
                            target?.scrollIntoView({
                              behavior: "smooth",
                              block: "start",
                            });
                            return;
                          }
                          onNavigate(action.step!);
                        }}
                      >
                        {action.actionLabel ?? "去处理"}
                      </Button>
                      {action.secondaryStep && (
                        <Button
                          variant="outline"
                          onClick={() => onNavigate(action.secondaryStep!)}
                        >
                          {action.secondaryActionLabel ?? "继续处理"}
                        </Button>
                      )}
                      {action.confirmationKind && (
                        <Button
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                assignmentReviewApi.confirm(
                                  session.id,
                                  action.confirmationKind!,
                                  session.review_version,
                                ),
                              action.confirmationSuccess ??
                                "试卷页面与当前版本已完成核对",
                            )
                          }
                        >
                          {bundleConfirmations.has(action.confirmationKind!) &&
                          !openCodes.has("PAPER_VARIANT_REVIEW")
                            ? "✓ 页面已核对"
                            : action.secondaryActionLabel}
                        </Button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
          {blockerMessages.length > 0 && (
            <details className="mt-3 rounded-lg border border-red-200 bg-white p-3 text-sm">
              <summary className="cursor-pointer text-red-900">
                查看涉及内容（{blockerMessages.length} 类）
              </summary>
              <ul className="mt-2 list-inside list-disc space-y-1 text-red-800">
                {blockerMessages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      ) : publicationBlocked ? (
        <section
          className="rounded-xl border border-sky-200 bg-sky-50 p-4"
          aria-label="自动发布检查"
        >
          <h3 className="font-semibold text-sky-900">正在自动完成发布检查</h3>
          <p className="mt-1 text-sm text-sky-800">
            无需逐项确认；检查完成后将直接开放“确认发布”。
          </p>
        </section>
      ) : (
        <section
          className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"
          aria-label="可以发布说明"
        >
          <h3 className="font-semibold text-emerald-900">✓ 已满足发布条件</h3>
          <p className="mt-1 text-sm text-emerald-800">
            所有影响发布的问题和需要确认的项目均已处理。剩余提示仅供留档，不会阻止发布。
          </p>
          <p className="mt-2 text-sm text-emerald-900">
            {readiness?.status === "ready"
              ? "系统已完成发布状态核对。请核对班级、截止时间和总分，然后点击“确认发布”。"
              : "系统正在自动核对发布状态；完成后只需由教师确认发布。"}
          </p>
        </section>
      )}
      <div className="flex justify-end">
        <Button
          variant="outline"
          disabled={busy || automating}
          onClick={() => {
            autoConfirmationAttempt.current = "";
            autoBindingAttempt.current = "";
            void act(
              () =>
                assignmentReviewApi.refresh(session.id, session.review_version),
              "审查已刷新",
            );
          }}
        >
          重新扫描最新状态
        </Button>
      </div>
      {(visible.length > 0 || teacherVisibleBlockers.length > 0) && (
        <details id="review-audit" className="rounded-xl border">
          <summary className="cursor-pointer rounded-xl p-4 font-semibold hover:bg-slate-50">
            查看检查记录（{unresolved.length + resolved.length} 条）
          </summary>
          <div className="space-y-3 border-t p-3">
            <div className="grid grid-cols-3 gap-3" aria-label="风险汇总">
              <div className="rounded bg-emerald-50 p-3">
                提示{" "}
                {visible.filter((entry) => entry.severity === "info").length}
              </div>
              <div className="rounded bg-amber-50 p-3">
                警告 {bundleWarningCount}
              </div>
              <div className="rounded bg-red-50 p-3">
                阻塞 {bundleBlockingCount}
                <span className="sr-only">红色 {bundleBlockingCount}</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-3">
              <Select
                aria-label="按风险过滤"
                value={severity}
                onChange={(event) => setSeverity(event.target.value)}
              >
                <option value="all">全部问题</option>
                <option value="blocking">影响发布</option>
                <option value="warning">警告</option>
                <option value="info">提示</option>
              </Select>
              <Select
                aria-label="按分区过滤"
                value={section}
                onChange={(event) => setSection(event.target.value)}
              >
                <option value="all">全部分区</option>
                {sections.map((value) => (
                  <option key={value} value={value}>
                    {sectionLabels[value] ?? value}
                  </option>
                ))}
              </Select>
            </div>
            {unresolved.length > 0 && (
              <details className="rounded-xl border">
                <summary className="cursor-pointer rounded-xl p-4 font-semibold hover:bg-slate-50">
                  查看全部待处理明细（{unresolved.length} 项）
                </summary>
                <ul className="space-y-2 border-t p-3">
                  {unresolved.map(renderReview)}
                </ul>
              </details>
            )}
            {resolved.length > 0 && (
              <section className="rounded-xl border">
                <button
                  type="button"
                  className="flex w-full items-center justify-between p-4 text-left font-semibold"
                  aria-expanded={resolvedOpen}
                  onClick={() => setResolvedOpen((open) => !open)}
                >
                  <span>已解决 {resolved.length} 项</span>
                  <span aria-hidden="true">
                    {resolvedOpen ? "收起" : "展开"}
                  </span>
                </button>
                {resolvedOpen && (
                  <ul className="space-y-2 border-t p-3">
                    {resolved.map(renderReview)}
                  </ul>
                )}
              </section>
            )}
            {teacherVisibleBlockers.length > 0 && (
              <details className="rounded-xl border p-3 text-sm">
                <summary className="cursor-pointer text-slate-600">
                  查看发布检查技术详情
                </summary>
                <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs text-slate-100">
                  {JSON.stringify(teacherVisibleBlockers, null, 2)}
                </pre>
              </details>
            )}
          </div>
        </details>
      )}
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
        <strong>
          {automating
            ? "正在自动核对…"
            : requiredConfirmationsComplete
              ? "常规内容已自动核对"
              : "仍有内容需要处理"}
        </strong>
        <p className="mt-1">
          班级、时间、分值、文件和版本由系统核对；只有内容不完整或存在冲突时才需要处理。
        </p>
      </div>
      <details
        className="rounded-xl border"
        open={bindingIsStale || bindingHasUnknownLoss || bindingHasKnownLosses}
      >
        <summary className="cursor-pointer rounded-xl p-4 font-semibold hover:bg-slate-50">
          评分标准兼容说明
        </summary>
        <div className="space-y-2 border-t p-4">
          <p className="text-sm text-slate-600">
            完整评分标准始终保留；兼容版本只供现有批改流程读取，不会修改原规则。
          </p>
          {bindingIsStale ? (
            <div
              data-testid="rubric-binding-stale"
              className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
            >
              <strong>需要重新生成兼容版本</strong>
              <p className="mt-1">
                答案或评分标准已经变化，已有兼容版本不再适用于当前内容。
              </p>
            </div>
          ) : bindingHasUnknownLoss ? (
            <div
              data-testid="rubric-binding-compatibility-summary"
              className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900"
            >
              <strong>存在尚无法安全说明的兼容差异</strong>
              <p className="mt-1">
                当前不能确认或发布此兼容版本。请修改评分标准，或联系支持人员核查。
              </p>
            </div>
          ) : bindingHasKnownLosses ? (
            <div
              data-testid="rubric-binding-compatibility-summary"
              className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
            >
              <strong>需要教师确认的具体业务损失</strong>
              <p className="mt-1">
                完整评分标准仍会保留；以下规则在兼容版本中需要改由教师人工核查。
              </p>
              <ul
                data-testid="rubric-binding-loss-list"
                className="mt-3 space-y-2"
              >
                {bindingLosses.map((loss, index) => {
                  const question = bundle?.questions.find(
                    (entry) => entry.id === loss.question_id,
                  );
                  const criterion =
                    question?.rubric.selected?.criteria.find(
                      (entry) => entry.key === loss.criterion_key,
                    ) ??
                    question?.rubric.materialized?.criteria.find(
                      (entry) => entry.key === loss.criterion_key,
                    );
                  return (
                    <li
                      key={`${loss.question_id}-${loss.criterion_key}-${index}`}
                      data-testid="rubric-binding-loss-item"
                      className="rounded-lg border border-amber-200 bg-white p-3"
                    >
                      <strong>
                        第 {question?.number ?? loss.question_number} 题 ·{" "}
                        {criterion?.title ?? "评分项"}
                      </strong>
                      <p className="mt-1">{bindingLossMessages[loss.code]}</p>
                    </li>
                  );
                })}
              </ul>
              {bindingLossesConfirmed ? (
                <p className="mt-3 font-medium text-emerald-800">
                  ✓ 已确认按上述人工核查方式使用兼容版
                </p>
              ) : (
                <div data-testid="confirm-rubric-publication-binding">
                  <Button
                    className="mt-3"
                    data-testid="rubric-binding-loss-confirm"
                    disabled={busy || !bindingCanBeConfirmed}
                    onClick={() =>
                      act(
                        () =>
                          assignmentReviewApi.confirmBinding(
                            bundle!.binding!.id,
                            session.review_version,
                          ),
                        "已确认按人工核查方式发布兼容版",
                      )
                    }
                  >
                    确认按上述人工核查方式发布兼容版
                  </Button>
                </div>
              )}
            </div>
          ) : bindingIsAutoCompatible ? (
            <div
              data-testid="rubric-binding-automatic"
              className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900"
            >
              <strong>✓ 可自动兼容</strong>
              <p className="mt-1">
                当前评分规则可完整用于兼容版本，无需教师再次确认。
                此兼容版本为本次重新生成，不会沿用旧发布版本的确认。
              </p>
            </div>
          ) : bundle?.binding ? (
            <div
              data-testid="rubric-binding-compatibility-summary"
              className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900"
            >
              <strong>兼容版本状态尚未完成</strong>
              <p className="mt-1">请重新生成当前评分标准的兼容版本。</p>
            </div>
          ) : (
            <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
              尚未生成评分标准兼容版本。
            </p>
          )}
          {(!bundle?.binding ||
            bindingIsStale ||
            (!bindingHasUnknownLoss && !bindingProjectionIsFresh) ||
            (bindingIsLossless && !bindingIsAutoCompatible)) && (
            <Button
              data-testid="prepare-rubric-publication-binding"
              disabled={busy || !bindingPrerequisitesComplete}
              onClick={() =>
                act(
                  () =>
                    assignmentReviewApi.createBinding(
                      session.id,
                      session.review_version,
                    ),
                  "评分标准兼容版本已生成",
                )
              }
            >
              {bundle?.binding ? "重新生成兼容版本" : "生成兼容版本"}
            </Button>
          )}
          {!bindingPrerequisitesComplete && !bundle?.binding && (
            <p className="text-sm text-amber-700">
              请先完成上方仍影响发布的参考答案和评分标准；完成后系统会自动生成兼容版本。
            </p>
          )}
          {bundle?.binding && (
            <details
              data-testid="rubric-binding-technical-details"
              className="rounded-lg border p-3 text-sm"
            >
              <summary className="cursor-pointer text-slate-600">
                查看技术详情
              </summary>
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs text-slate-100">
                {JSON.stringify(
                  {
                    binding_id: bundle.binding.id,
                    binding_version: bundle.binding.binding_version,
                    target_legacy_hash: bundle.binding.target_legacy_hash,
                    projection_profile: bundle.binding.projection_profile,
                    projection_version: bundle.binding.projection_version,
                    projection_current: bundle.binding.projection_current,
                    projection_reason: bundle.binding.projection_reason,
                    loss_report_hash: bundle.binding.loss_report_hash,
                    loss_report: bundle.binding.loss_report,
                    mapping: bundle.binding.mapping,
                    legacy_confirmation: legacyBindingConfirmation
                      ? {
                          id: legacyBindingConfirmation.id,
                          origin: legacyBindingConfirmation.origin,
                          binding_id: legacyBindingConfirmation.binding_id,
                          fingerprint_schema_version:
                            legacyBindingConfirmation.fingerprint_schema_version,
                        }
                      : null,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
        </div>
      </details>
      <div className="rounded-xl border p-4">
        <h3 className="font-semibold">发布门禁</h3>
        <p>
          班级 {item.classes.length} · 截止时间 {item.due_at ?? "无截止时间"} ·
          总分 {item.total_score ?? "未设置"}
        </p>
        <div className="mt-3 flex gap-2">
          <Button
            disabled={
              publicationBlocked ||
              !readiness ||
              readiness.status !== "ready" ||
              busy
            }
            onClick={() => {
              if (
                !readiness ||
                !window.confirm(
                  `确认由教师发布？\n班级：${readiness.class_ids.length}\n截止：${readiness.due_at ?? "无截止时间"}\n总分：${readiness.total_score}`,
                )
              )
                return;
              void act(async () => {
                await assignmentReviewApi.publish(
                  item.id,
                  readiness,
                  item.updated_at,
                );
                onPublished();
              }, "作业已由教师发布");
            }}
          >
            {preparingReadiness ? "正在核对发布状态…" : "确认发布"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

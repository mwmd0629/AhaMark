"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  gradingApi,
  type ConfirmResultsReadiness,
  type ConfirmResultsResult,
  type ReviewWorkspace,
  type SafeAcceptPreview,
} from "@/lib/api";
import { AnswerRecognitionWorkspace } from "@/components/answer-recognition-workspace";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

type Decision = "accepted" | "modified" | "manual_scored";

const statusLabels: Record<string, string> = {
  pending: "待复核",
  review_required: "需教师复核",
  reviewed: "已复核",
  finalized: "已定稿",
  complete: "完整",
  incomplete: "不完整",
  stale: "已失效",
  suggested: "待确认建议",
  confirmed: "已确认",
  manual: "人工标注",
  accepted: "已接受",
  modified: "已修改",
  rejected: "已拒绝",
  manual_scored: "人工评分",
  unavailable: "不可用",
};

const finalizeProblemLabels: Record<string, string> = {
  ANSWER_NOT_REVIEWED: "仍有题目未检查",
  SUBMISSION_MISSING: "没有可确认的作业",
  SUBMISSION_VERSION_INCOMPLETE: "作业信息不完整",
  RUBRIC_VERSION_NOT_CONFIRMED: "评分标准未确认",
  QUESTION_SCORE_REQUIRED: "题目缺少有效满分",
  ANSWER_MISSING: "提交缺少对应题目的答案",
  SCORE_MISSING: "最终分数缺失",
  SCORE_OUT_OF_RANGE: "最终分数超出题目满分范围",
  RUBRIC_VERSION_STALE: "评分标准版本已变化",
  STALE_RUBRIC: "评分标准已变化，请重新批改",
  ANSWER_SNAPSHOT_STALE: "答案已修改，请重新批改",
  GRADING_RESULT_MISSING: "缺少评分建议",
  REGION_NOT_CONFIRMED: "答题区域未确认",
  RESULT_REQUIRED: "尚无建议评分",
  RESULT_NOT_SUGGESTED: "该建议需要检查",
  REVIEW_REQUIRED: "需要逐题检查",
  CONFIDENCE_LOW: "评分建议置信度不足，需要教师逐题核对",
  REQUIRES_REVIEW: "评分建议仍在待复核状态",
  CRITERION_INCOMPLETE: "评分项尚未完成教师确认",
  EVIDENCE_REQUIRED: "缺少评分证据",
  SCORE_REQUIRED: "建议中没有有效分数",
  SUBMISSION_FINALIZED: "该批次结果已经确认",
  FINALIZED_SNAPSHOT_NOT_REUSABLE: "已有结果需要重新确认",
  SNAPSHOT_REUSE_MISMATCH: "已有结果已变化，请重新确认",
};

function problemLabel(code: string, fallback?: string | null) {
  return finalizeProblemLabels[code] ?? fallback ?? "请检查未完成项";
}

function resultSummary(result: ConfirmResultsResult) {
  const created = result.new_snapshot_count ?? result.snapshot_ids.length;
  const reused = result.reused_snapshot_count ?? 0;
  return `更新 ${created} 份，保留 ${reused} 份`;
}

type ReviewFilter = "pending" | "completed" | "all" | "needs_review" | "stale";

function statusLabel(value?: string) {
  return value ? (statusLabels[value] ?? value) : "未提供";
}

type ReviewAnswer = ReviewWorkspace["items"][number]["answers"][number];
type ReviewDraft = {
  score: string;
  feedback: string;
  criteria: Record<string, string>;
  decision: "modified" | "manual_scored";
};

function hasManualOrIncompleteCriteria(answer: ReviewAnswer) {
  return answer.criteria.some((criterion) =>
    ["manual", "incomplete"].includes(criterion.status),
  );
}

function needsTeacherReview(answer: ReviewAnswer) {
  const stale = answer.status === "stale" || answer.result?.status === "stale";
  if (answer.review && !stale) return false;
  return (
    stale ||
    answer.requires_review ||
    Boolean(answer.result?.requires_review) ||
    Boolean(answer.result?.quality_flags?.length) ||
    answer.result?.score == null ||
    hasManualOrIncompleteCriteria(answer)
  );
}

function isCompleted(answer: ReviewAnswer) {
  return Boolean(
    answer.review?.final_score != null &&
    answer.review.decision !== "reopened" &&
    answer.status !== "stale" &&
    answer.result?.status !== "stale",
  );
}

function isAnomaly(answer: ReviewAnswer) {
  return (
    needsTeacherReview(answer) ||
    Number(answer.confidence ?? 0) < 0.9 ||
    (answer.regions ?? []).length === 0 ||
    Boolean(answer.result?.quality_flags?.length)
  );
}

type ReviewTarget = {
  submissionIndex: number;
  answerIndex: number;
  answerId: string;
};

function reviewTargets(
  data: ReviewWorkspace,
  filter: ReviewFilter,
): ReviewTarget[] {
  const targets = data.items.flatMap((submission, submissionIndex) =>
    submission.answers.flatMap((answer, answerIndex) =>
      matchesReviewFilter(answer, filter)
        ? [{ submissionIndex, answerIndex, answerId: answer.id }]
        : [],
    ),
  );
  return filter === "pending"
    ? targets.sort((left, right) => {
        const leftAnswer =
          data.items[left.submissionIndex].answers[left.answerIndex];
        const rightAnswer =
          data.items[right.submissionIndex].answers[right.answerIndex];
        return Number(isAnomaly(rightAnswer)) - Number(isAnomaly(leftAnswer));
      })
    : targets;
}

function nextReviewTarget(
  data: ReviewWorkspace,
  filter: ReviewFilter,
  currentSubmissionIndex: number,
  currentAnswerIndex: number,
  currentAnswerId: string,
) {
  const targets = reviewTargets(data, filter).filter(
    (target) => target.answerId !== currentAnswerId,
  );
  return (
    targets.find(
      (target) =>
        target.submissionIndex > currentSubmissionIndex ||
        (target.submissionIndex === currentSubmissionIndex &&
          target.answerIndex > currentAnswerIndex),
    ) ?? targets[0]
  );
}

function matchesReviewFilter(answer: ReviewAnswer, filter: ReviewFilter) {
  return (
    filter === "all" ||
    (filter === "pending" && !isCompleted(answer)) ||
    (filter === "completed" && isCompleted(answer)) ||
    (filter === "needs_review" && needsTeacherReview(answer)) ||
    (filter === "stale" &&
      (answer.status === "stale" || answer.result?.status === "stale"))
  );
}

export default function ReviewPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const jointQuestionId = searchParams.get("questionId") ?? undefined;
  const jointMode =
    searchParams.get("joint") === "1" && Boolean(jointQuestionId);
  const [data, setData] = useState<ReviewWorkspace>();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [submissionIndex, setSubmissionIndex] = useState(0);
  const [answerIndex, setAnswerIndex] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [processed, setProcessed] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [activeEvidence, setActiveEvidence] = useState<string>();
  const [scoreDraft, setScoreDraft] = useState("");
  const [feedbackDraft, setFeedbackDraft] = useState("");
  const [criterionDrafts, setCriterionDrafts] = useState<
    Record<string, string>
  >({});
  const [scoringDecision, setScoringDecision] = useState<
    "modified" | "manual_scored" | null
  >(null);
  const [readiness, setReadiness] = useState<ConfirmResultsReadiness>();
  const [confirmedResult, setConfirmedResult] =
    useState<ConfirmResultsResult>();
  const confirmCommand = useRef<
    | {
        reviewHash: string;
        idempotencyKey: string;
      }
    | undefined
  >(undefined);
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("pending");
  const [safeAcceptPreview, setSafeAcceptPreview] =
    useState<SafeAcceptPreview>();
  const [showSafeAccept, setShowSafeAccept] = useState(false);
  const safeAcceptCommand = useRef<
    { reviewHash: string; idempotencyKey: string } | undefined
  >(undefined);
  const [lastAccepted, setLastAccepted] = useState<{
    answerId: string;
    questionNumber: string;
    reviewVersion: number;
    until: number;
  }>();
  const [draftOwnerAnswerId, setDraftOwnerAnswerId] = useState<string>();
  const scoringFormRef = useRef<HTMLElement>(null);
  const scoreInputRef = useRef<HTMLInputElement>(null);
  const keyboardActionRef = useRef<(key: string) => void>(() => undefined);
  const fetchWorkspace = () =>
    jointQuestionId
      ? gradingApi.reviewWorkspace(batchId, jointQuestionId)
      : gradingApi.reviewWorkspace(batchId);

  const load = async () => {
    const next = await fetchWorkspace();
    setData(next);
    if (next.collaboration?.can_confirm_results === false) {
      setReadiness(undefined);
    } else {
      try {
        const nextReadiness = await gradingApi.confirmResultsReadiness(batchId);
        setReadiness(nextReadiness);
        setConfirmedResult(
          (current) => nextReadiness.confirmed_result ?? current,
        );
      } catch {
        // The saved review and refreshed workspace are authoritative. A temporary
        // readiness failure must not strand the teacher on the completed answer.
      }
    }
    return next;
  };
  useSmartRefresh(load, {
    enabled: !saving && scoringDecision === null,
    intervalMs: 45_000,
  });
  useEffect(() => {
    setConfirmedResult(undefined);
    confirmCommand.current = undefined;
    gradingApi
      .reviewWorkspace(batchId, ...(jointQuestionId ? [jointQuestionId] : []))
      .then(async (workspace) => {
        const nextReadiness =
          workspace.collaboration?.can_confirm_results === false
            ? undefined
            : await gradingApi.confirmResultsReadiness(batchId);
        const preferredFilter = reviewTargets(workspace, "pending").length
          ? "pending"
          : "completed";
        const firstTarget = reviewTargets(workspace, preferredFilter)[0];
        setData(workspace);
        setReadiness(nextReadiness);
        setConfirmedResult(nextReadiness?.confirmed_result ?? undefined);
        setReviewFilter(preferredFilter);
        if (firstTarget) {
          setSubmissionIndex(firstTarget.submissionIndex);
          setAnswerIndex(firstTarget.answerIndex);
        }
      })
      .catch(() => setError("无法加载复核工作台"));
  }, [batchId, jointQuestionId]);

  useEffect(() => {
    if (!data || !jointMode || reviewTargets(data, "all").length) return;
    const batches = data.joint_navigation?.batches ?? [];
    const currentIndex = batches.findIndex((item) => item.id === batchId);
    const nextBatch = currentIndex >= 0 ? batches[currentIndex + 1] : undefined;
    if (nextBatch && jointQuestionId) {
      router.replace(
        `/grading/${nextBatch.id}/review?questionId=${jointQuestionId}&joint=1`,
      );
    }
  }, [batchId, data, jointMode, jointQuestionId, router]);

  const submission = data?.items[submissionIndex];
  const selectedAnswer = submission?.answers[answerIndex];
  const answer =
    selectedAnswer && matchesReviewFilter(selectedAnswer, reviewFilter)
      ? selectedAnswer
      : undefined;
  const page = submission?.pages[pageIndex];
  const evidence = useMemo(
    () => answer?.evidence.find((item) => item.id === activeEvidence),
    [answer, activeEvidence],
  );
  const draftKey =
    answer && submission
      ? `ahamark:review-draft:${batchId}:${submission.submission_id}:${answer.id}:${answer.result?.id ?? "manual"}:${answer.result?.structured_rubric_version_id ?? "none"}`
      : undefined;

  useEffect(() => {
    if (!answer) return;
    const fallbackCriteria = Object.fromEntries(
      answer.criteria.map((criterion) => [
        criterion.criterion_id,
        criterion.awarded_points ?? "",
      ]),
    );
    let saved: ReviewDraft | undefined;
    if (draftKey) {
      try {
        saved =
          JSON.parse(localStorage.getItem(draftKey) ?? "null") ?? undefined;
      } catch {
        localStorage.removeItem(draftKey);
      }
    }
    setScoreDraft(
      saved?.score ?? answer.review?.final_score ?? answer.result?.score ?? "",
    );
    setFeedbackDraft(saved?.feedback ?? answer.review?.feedback ?? "");
    setCriterionDrafts(saved?.criteria ?? fallbackCriteria);
    setScoringDecision(saved?.decision ?? null);
    setDraftOwnerAnswerId(answer.id);
  }, [answer, draftKey]);

  useEffect(() => {
    if (!data || answer) return;
    const firstTarget = reviewTargets(data, reviewFilter)[0];
    if (firstTarget) {
      setSubmissionIndex(firstTarget.submissionIndex);
      setAnswerIndex(firstTarget.answerIndex);
      setActiveEvidence(undefined);
    }
  }, [answer, data, reviewFilter]);

  const maxScore = Number(
    answer?.question.max_score ?? answer?.result?.score ?? 0,
  );
  const criterionTotal = Object.values(criterionDrafts).reduce(
    (total, value) => total + (value.trim() === "" ? 0 : Number(value)),
    0,
  );
  const hasInvalidCriterion = Boolean(
    answer?.criteria.some((criterion) => {
      const value = criterionDrafts[criterion.criterion_id] ?? "";
      const numeric = Number(value);
      return (
        value.trim() === "" ||
        Number.isNaN(numeric) ||
        numeric < 0 ||
        numeric > Number(criterion.max_points)
      );
    }),
  );
  const canAcceptSuggestion = Boolean(
    answer?.result?.status === "suggested" &&
    answer.result.score != null &&
    answer.status !== "stale" &&
    !answer.result.quality_flags?.length &&
    answer.recognized_text !== undefined &&
    (answer.regions ?? []).length > 0 &&
    !hasManualOrIncompleteCriteria(answer),
  );
  const needsManualScoring = Boolean(
    answer &&
    (!answer.result ||
      answer.result.score == null ||
      hasManualOrIncompleteCriteria(answer)),
  );
  const scoreIsInvalid =
    scoreDraft.trim() === "" ||
    Number.isNaN(Number(scoreDraft)) ||
    Number(scoreDraft) < 0 ||
    Number(scoreDraft) > maxScore;
  const criterionTotalMismatch = Boolean(
    answer?.criteria.length &&
    !scoreIsInvalid &&
    !hasInvalidCriterion &&
    Math.abs(criterionTotal - Number(scoreDraft)) > 0.0001,
  );
  const scoringValidationMessage = scoreIsInvalid
    ? `最终分数必须在 0–${maxScore} 范围内`
    : hasInvalidCriterion
      ? "请填写全部评分项，并确保每项分值不超过该项满分"
      : criterionTotalMismatch
        ? `分项合计 ${criterionTotal} 分，必须等于最终分 ${Number(scoreDraft)} 分`
        : "";
  const draftDirty = Boolean(
    answer &&
    scoringDecision &&
    draftOwnerAnswerId === answer.id &&
    (scoreDraft !==
      (answer.review?.final_score ?? answer.result?.score ?? "") ||
      feedbackDraft !== (answer.review?.feedback ?? "") ||
      answer.criteria.some(
        (criterion) =>
          (criterionDrafts[criterion.criterion_id] ?? "") !==
          (criterion.awarded_points ?? ""),
      )),
  );
  const remainingCount = data ? reviewTargets(data, "pending").length : 0;

  useEffect(() => {
    if (!draftKey || draftOwnerAnswerId !== answer?.id || !scoringDecision)
      return;
    const draft: ReviewDraft = {
      score: scoreDraft,
      feedback: feedbackDraft,
      criteria: criterionDrafts,
      decision: scoringDecision,
    };
    localStorage.setItem(draftKey, JSON.stringify(draft));
  }, [
    answer?.id,
    criterionDrafts,
    draftKey,
    draftOwnerAnswerId,
    feedbackDraft,
    scoreDraft,
    scoringDecision,
  ]);

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (!draftDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [draftDirty]);

  useEffect(() => {
    setShowSafeAccept(false);
    safeAcceptCommand.current = undefined;
    if (!submission) {
      setSafeAcceptPreview(undefined);
      return;
    }
    gradingApi
      .safeAcceptPreview(batchId, submission.submission_id)
      .then(setSafeAcceptPreview)
      .catch(() => setSafeAcceptPreview(undefined));
  }, [batchId, submission]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        target?.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(
          target?.tagName ?? "",
        )
      ) {
        return;
      }
      if (!["a", "e", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      keyboardActionRef.current(event.key);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  const readinessNewSnapshotCount =
    readiness?.new_snapshot_count ??
    readiness?.plan?.filter((item) => item.action === "create_snapshot")
      .length ??
    readiness?.submission_count ??
    0;
  const readinessReusedSnapshotCount =
    readiness?.reused_snapshot_count ??
    readiness?.plan?.filter((item) => item.action === "reuse_snapshot")
      .length ??
    0;
  const hasReadinessPlan =
    readiness?.submission_count !== undefined ||
    readiness?.new_snapshot_count !== undefined ||
    readiness?.reused_snapshot_count !== undefined ||
    readiness?.plan !== undefined;
  const changedSubmissionPlans =
    readiness?.plan?.filter((item) => item.action === "create_snapshot") ?? [];
  const readinessProblems = Array.from(
    new Set(
      readiness?.blockers.map((blocker) =>
        problemLabel(blocker.code, blocker.message),
      ) ?? [],
    ),
  );

  function clearCurrentDraft() {
    if (draftKey) localStorage.removeItem(draftKey);
    setScoringDecision(null);
  }

  function canLeaveCurrentEdit() {
    if (!draftDirty) return true;
    if (
      !window.confirm("当前评分还未保存，离开本题将清除这份草稿。确定离开吗？")
    ) {
      return false;
    }
    clearCurrentDraft();
    return true;
  }

  function selectTarget(target: ReviewTarget) {
    if (!canLeaveCurrentEdit()) return;
    setSubmissionIndex(target.submissionIndex);
    setAnswerIndex(target.answerIndex);
    setActiveEvidence(undefined);
  }

  function changeReviewFilter(filter: ReviewFilter) {
    if (!canLeaveCurrentEdit()) return;
    setReviewFilter(filter);
  }

  function moveReviewTarget(offset: number) {
    if (!data || !answer) return;
    const targets = reviewTargets(data, reviewFilter);
    const index = targets.findIndex((target) => target.answerId === answer.id);
    const target = targets[index + offset];
    if (target) selectTarget(target);
  }

  function beginScoring(decision: "modified" | "manual_scored") {
    setScoringDecision(decision);
    setMessage("");
    window.requestAnimationFrame(() => {
      scoringFormRef.current?.scrollIntoView?.({
        behavior: "smooth",
        block: "nearest",
      });
      scoreInputRef.current?.focus({ preventScroll: true });
      scoreInputRef.current?.select();
    });
  }

  async function submitReview(decision: Decision) {
    if (!answer || saving) return;
    const payload: Record<string, unknown> = { decision };
    if (answer.review) {
      payload.expected_review_version = answer.review.review_version;
    }
    if (decision === "modified" || decision === "manual_scored") {
      const score = Number(scoreDraft);
      if (
        scoreDraft.trim() === "" ||
        Number.isNaN(score) ||
        score < 0 ||
        score > maxScore
      ) {
        setMessage(`最终分数必须在 0–${maxScore} 范围内`);
        return;
      }
      payload.final_score = scoreDraft;
      payload.final_feedback = feedbackDraft;
      if (answer.criteria.length) {
        if (hasInvalidCriterion) {
          setMessage("请填写全部评分项，并确保每项分值不超过该项满分");
          return;
        }
        if (Math.abs(criterionTotal - score) > 0.0001) {
          setMessage(
            `分项合计 ${criterionTotal} 分，必须等于最终分 ${score} 分`,
          );
          return;
        }
        payload.criterion_scores = criterionDrafts;
      }
      payload.reason =
        decision === "modified" ? "教师修改 AI 建议" : "教师手动评分";
    }
    setSaving(true);
    setMessage("");
    try {
      const currentAnswerId = answer.id;
      const savedReview = await gradingApi.review(currentAnswerId, payload);
      clearCurrentDraft();
      if (decision === "accepted") {
        setLastAccepted({
          answerId: currentAnswerId,
          questionNumber: answer.question.number,
          reviewVersion: savedReview.review_version,
          until: Date.now() + 5 * 60 * 1000,
        });
      }
      const next = await load();
      const target = nextReviewTarget(
        next,
        "pending",
        submissionIndex,
        answerIndex,
        currentAnswerId,
      );
      if (target) {
        setReviewFilter("pending");
        setSubmissionIndex(target.submissionIndex);
        setAnswerIndex(target.answerIndex);
        setActiveEvidence(undefined);
      }
      const batches = next.joint_navigation?.batches ?? [];
      const currentBatchIndex = batches.findIndex(
        (item) => item.id === batchId,
      );
      const nextBatch =
        currentBatchIndex >= 0 ? batches[currentBatchIndex + 1] : undefined;
      if (!target && jointMode && jointQuestionId && nextBatch) {
        router.push(
          `/grading/${nextBatch.id}/review?questionId=${jointQuestionId}&joint=1`,
        );
        setMessage("本班该题已处理完，正在进入下一班");
        return;
      }
      setScoringDecision(null);
      setMessage(
        target
          ? "已保存，已进入下一题"
          : reviewFilter === "needs_review"
            ? "异常已处理完"
            : "复核结果已保存",
      );
    } catch (reason) {
      const body =
        typeof reason === "object" && reason !== null && "body" in reason
          ? (reason.body as { code?: string })
          : undefined;
      if (body?.code === "REVIEW_CONFLICT") {
        try {
          await load();
        } catch {
          // Keep the conflict message visible if refreshing also fails.
        }
        setMessage("这道题已由其他老师更新，已刷新为最新结果，请重新检查");
        return;
      }
      setMessage(
        reason instanceof Error && reason.message.trim()
          ? `保存失败：${reason.message}`
          : "保存失败，请检查分数范围后重试",
      );
    } finally {
      setSaving(false);
    }
  }

  async function reopenLastAccepted() {
    if (!lastAccepted || saving || Date.now() > lastAccepted.until) return;
    setSaving(true);
    try {
      await gradingApi.reopenReview(lastAccepted.answerId, {
        expected_review_version: lastAccepted.reviewVersion,
        reason: "教师撤回刚确认的建议分",
      });
      const next = await load();
      const target = reviewTargets(next, "pending").find(
        (item) => item.answerId === lastAccepted.answerId,
      );
      setReviewFilter("pending");
      if (target) selectTarget(target);
      setLastAccepted(undefined);
      setMessage("已撤回本题确认，题目已回到待处理；没有发布或释放成绩");
    } catch {
      await load().catch(() => undefined);
      setMessage("无法撤回：题目已变化或撤回时间已过，请检查最新状态");
    } finally {
      setSaving(false);
    }
  }

  async function confirmSafeSubmission() {
    if (!submission || !safeAcceptPreview?.eligible_count || saving) return;
    if (
      safeAcceptCommand.current?.reviewHash !== safeAcceptPreview.review_hash
    ) {
      safeAcceptCommand.current = {
        reviewHash: safeAcceptPreview.review_hash,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    setSaving(true);
    try {
      const result = await gradingApi.safeAcceptSubmission(
        batchId,
        submission.submission_id,
        {
          answer_ids: safeAcceptPreview.answer_ids,
          expected_review_hash: safeAcceptPreview.review_hash,
          idempotency_key: safeAcceptCommand.current.idempotencyKey,
        },
      );
      await load();
      setShowSafeAccept(false);
      setMessage(
        `已确认本份作业 ${result.accepted_count} 道无异常建议，共 ${result.suggested_total} 分；未发布成绩`,
      );
    } catch {
      await load().catch(() => undefined);
      setMessage("无法批量确认：内容已变化或出现异常，已刷新，请重新检查");
    } finally {
      setSaving(false);
    }
  }

  async function addCollaborator() {
    if (!data?.collaboration?.is_owner || saving) return;
    const email = window.prompt("输入协作老师的登录邮箱");
    if (!email?.trim()) return;
    setSaving(true);
    setMessage("");
    try {
      await gradingApi.addCollaborator(batchId, email.trim());
      await load();
      setMessage("协作老师已添加");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "添加协作老师失败");
    } finally {
      setSaving(false);
    }
  }

  async function removeCollaborator(userId: string) {
    if (!data?.collaboration?.is_owner || saving) return;
    if (
      !window.confirm(
        "移除这位协作老师并清除其题目分配？已保存的批改记录会保留。",
      )
    ) {
      return;
    }
    setSaving(true);
    try {
      await gradingApi.removeCollaborator(batchId, userId);
      await load();
      setMessage("协作老师已移除");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "移除协作老师失败");
    } finally {
      setSaving(false);
    }
  }

  async function assignQuestion(questionId: string, assigneeId?: string) {
    if (!data?.collaboration?.is_owner || saving) return;
    setSaving(true);
    try {
      if (data.joint_navigation) {
        await gradingApi.assignJointQuestion(
          data.joint_navigation.assignment_id,
          questionId,
          assigneeId,
        );
      } else {
        await gradingApi.assignQuestion(batchId, questionId, assigneeId);
      }
      await load();
      setMessage(
        assigneeId
          ? data.joint_navigation
            ? "该题已分配到全部联考班级"
            : "题目已分配"
          : data.joint_navigation
            ? "已从全部联考班级收回该题"
            : "题目已收回",
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "题目分配失败");
    } finally {
      setSaving(false);
    }
  }

  async function confirmResults() {
    if (!readiness || saving) return;
    if (confirmCommand.current?.reviewHash !== readiness.review_hash) {
      confirmCommand.current = {
        reviewHash: readiness.review_hash,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    setSaving(true);
    setMessage("");
    try {
      const result = await gradingApi.confirmResults(batchId, {
        idempotency_key: confirmCommand.current.idempotencyKey,
        expected_review_hash: readiness.review_hash,
      });
      setConfirmedResult(result);
      const successMessage = `已确认 ${result.submission_count} 份作业，${resultSummary(result)}`;
      try {
        await load();
        setMessage(successMessage);
      } catch {
        setMessage(`${successMessage}。页面刷新失败，请手动刷新`);
      }
    } catch (reason) {
      const body =
        typeof reason === "object" && reason !== null && "body" in reason
          ? (reason.body as {
              code?: string;
              message?: string;
              details?: {
                current_review_hash?: string;
                blockers?: Array<{ code?: string }>;
              };
            })
          : undefined;
      const blockerText = body?.details?.blockers
        ?.map((item) => problemLabel(item.code ?? "UNKNOWN"))
        .join("；");
      const staleText = body?.details?.current_review_hash
        ? "内容已变化，请重新检查"
        : "";
      setMessage(
        `无法确认：${blockerText || staleText || body?.message || (reason instanceof Error ? reason.message : "请刷新后重试")}`,
      );
      try {
        await load();
      } catch {
        // Preserve the actionable command error when the follow-up refresh fails.
      }
    } finally {
      setSaving(false);
    }
  }

  async function correctCurrentAnswer() {
    if (!answer || saving) return;
    const corrected = window.prompt(
      "请输入教师修正后的答案",
      answer.corrected_text ?? answer.recognized_text ?? "",
    );
    if (corrected === null) return;
    setSaving(true);
    try {
      await gradingApi.correctAnswer(answer.id, { corrected_text: corrected });
    } catch {
      setMessage("答案修正失败");
      setSaving(false);
      return;
    }
    try {
      await gradingApi.grade(answer.id);
      await load();
      setMessage("答案已修改并重新批改");
    } catch {
      try {
        await load();
      } catch {
        // Keep the actionable grading failure visible.
      }
      setMessage("答案已修改，自动批改失败，请检查评分标准");
    } finally {
      setSaving(false);
    }
  }

  async function regradeCurrentAnswer() {
    if (!answer || saving) return;
    setSaving(true);
    try {
      await gradingApi.grade(answer.id);
      await load();
      setMessage("已重新批改");
    } catch {
      setMessage("重新批改失败，请检查评分标准");
    } finally {
      setSaving(false);
    }
  }

  keyboardActionRef.current = (key) => {
    if (saving) return;
    if (key === "a" && canAcceptSuggestion && answer && !isCompleted(answer)) {
      void submitReview("accepted");
    } else if (key === "e" && answer && !answer.review) {
      beginScoring(needsManualScoring ? "manual_scored" : "modified");
    } else if (key === "ArrowLeft") {
      moveReviewTarget(-1);
    } else if (key === "ArrowRight") {
      moveReviewTarget(1);
    }
  };

  if (error)
    return (
      <div role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
        {error}
      </div>
    );
  if (!data) return <div role="status">正在加载复核工作台…</div>;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-white p-4">
        <div>
          <h1 className="text-xl font-bold">教师评分复核</h1>
        </div>
        <div className="flex items-center gap-3 text-sm font-medium">
          <span>
            已检查 {data.progress.reviewed}/{data.progress.total}
          </span>
          <span className="rounded-full bg-amber-100 px-3 py-1 text-amber-900">
            还剩 {remainingCount} 题
          </span>
          {data.collaboration?.can_confirm_results !== false && (
            <button
              className="rounded bg-indigo-700 px-3 py-2 text-white disabled:opacity-50"
              disabled={saving || !readiness?.ready || Boolean(confirmedResult)}
              onClick={() => void confirmResults()}
            >
              {confirmedResult
                ? "结果已确认"
                : saving
                  ? "正在确认…"
                  : "确认结果"}
            </button>
          )}
          <Link
            className="rounded border px-3 py-2"
            href={`/grading/${batchId}`}
            onClick={(event) => {
              if (!canLeaveCurrentEdit()) event.preventDefault();
            }}
          >
            返回批次工作台
          </Link>
        </div>
      </header>
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
        <span>快捷键：A 确认建议 · E 打开评分 · ← 上一题 · → 下一题</span>
        {lastAccepted && Date.now() <= lastAccepted.until && (
          <button
            className="rounded border bg-white px-3 py-1 font-medium text-slate-900"
            disabled={saving}
            onClick={() => void reopenLastAccepted()}
          >
            撤回第 {lastAccepted.questionNumber} 题确认
          </button>
        )}
      </div>
      {data.collaboration && (
        <section
          className="rounded-xl border bg-white p-4"
          aria-label="协作批改"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">协作批改</h2>
              <p className="text-sm text-slate-600">
                {data.collaboration.is_owner
                  ? "按题分配给协作老师，最终结果仍由你统一确认。"
                  : `主责老师：${data.collaboration.owner.display_name}。这里只显示分配给你的题目。`}
              </p>
            </div>
            {data.collaboration.is_owner && (
              <button
                className="rounded border px-3 py-2 text-sm"
                disabled={saving}
                onClick={() => void addCollaborator()}
              >
                添加协作老师
              </button>
            )}
          </div>
          {data.collaboration.collaborators.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 text-sm">
              {data.collaboration.collaborators.map((teacher) => (
                <span
                  key={teacher.id}
                  className="rounded-full bg-slate-100 px-3 py-1"
                >
                  {teacher.display_name}
                  {data.collaboration.is_owner && (
                    <button
                      className="ml-2 text-slate-500 hover:text-red-700"
                      aria-label={`移除${teacher.display_name}`}
                      onClick={() => void removeCollaborator(teacher.id)}
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
          {data.collaboration.is_owner &&
            data.collaboration.questions.length > 0 && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {data.collaboration.questions.map((question) => (
                  <label
                    key={question.id}
                    className="flex items-center gap-2 rounded border p-2 text-sm"
                  >
                    <span className="min-w-14">第 {question.number} 题</span>
                    <select
                      className="min-w-0 flex-1 rounded border px-2 py-1"
                      value={question.assignee_id ?? ""}
                      disabled={saving}
                      onChange={(event) =>
                        void assignQuestion(
                          question.id,
                          event.target.value || undefined,
                        )
                      }
                    >
                      <option value="">主责老师</option>
                      {data.collaboration.collaborators.map((teacher) => (
                        <option key={teacher.id} value={teacher.id}>
                          {teacher.display_name}
                        </option>
                      ))}
                    </select>
                    <span className="text-slate-500">
                      {question.reviewed}/{question.total}
                    </span>
                  </label>
                ))}
              </div>
            )}
        </section>
      )}
      <section
        aria-label="集中审查概览"
        className="rounded-xl border bg-white p-4"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold">检查结果</h2>
            <p className="text-sm text-slate-600">系统已筛出需要检查的答案。</p>
          </div>
        </div>
        {readiness && !readiness.ready && !confirmedResult && (
          <div
            role="alert"
            className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="confirm-results-blockers"
          >
            <p className="font-medium">暂不能确认结果，请先处理：</p>
            <ul className="mt-1 list-disc pl-5">
              {readinessProblems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </div>
        )}
        {readiness?.ready && !confirmedResult && hasReadinessPlan && (
          <div
            className="mt-3 rounded border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-900"
            data-testid="confirm-results-plan"
          >
            <p className="font-medium">
              本次更新 {readinessNewSnapshotCount} 份，保留{" "}
              {readinessReusedSnapshotCount} 份
            </p>
            {readinessReusedSnapshotCount > 0 && (
              <p className="mt-1">未修改的结果保持不变。</p>
            )}
            {readiness.previous_grade_release_id &&
              changedSubmissionPlans.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {changedSubmissionPlans.map((item) => {
                    const student = item.student_name
                      ? `${item.student_name}${item.student_number ? `（${item.student_number}）` : ""}`
                      : item.student_number || "学生";
                    const questions = item.changed_questions
                      ?.map((question) => question.question_number)
                      .join("、");
                    return (
                      <li key={item.submission_id}>
                        {student}：
                        {questions
                          ? `第 ${questions} 题有变化`
                          : "需要重新确认"}
                      </li>
                    );
                  })}
                </ul>
              )}
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-2" aria-label="答案筛选">
          {(
            [
              ["pending", "待处理"],
              ["completed", "已完成"],
            ] as Array<[ReviewFilter, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={reviewFilter === value}
              className={`rounded border px-3 py-1 text-sm ${
                reviewFilter === value ? "bg-indigo-700 text-white" : ""
              }`}
              onClick={() => changeReviewFilter(value)}
            >
              {label}
            </button>
          ))}
          <details className="relative">
            <summary className="cursor-pointer rounded border px-3 py-1 text-sm">
              更多筛选
            </summary>
            <div className="absolute right-0 z-10 mt-1 grid min-w-32 gap-1 rounded border bg-white p-2 shadow">
              {(
                [
                  ["all", "全部"],
                  ["needs_review", "需检查"],
                  ["stale", "已失效"],
                ] as Array<[ReviewFilter, string]>
              ).map(([value, label]) => (
                <button
                  key={value}
                  className="rounded px-3 py-1 text-left text-sm hover:bg-slate-100"
                  onClick={() => changeReviewFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </details>
        </div>
        {safeAcceptPreview && safeAcceptPreview.eligible_count > 0 && (
          <div className="mt-3 rounded border border-emerald-200 bg-emerald-50 p-3 text-sm">
            {!showSafeAccept ? (
              <button
                className="rounded bg-emerald-700 px-3 py-2 font-medium text-white"
                disabled={saving}
                onClick={() => setShowSafeAccept(true)}
              >
                确认本份作业的无异常建议
              </button>
            ) : (
              <div
                className="space-y-2"
                role="dialog"
                aria-label="批量确认预览"
              >
                <p className="font-medium">
                  将确认 {safeAcceptPreview.eligible_count} 题，建议总分{" "}
                  {safeAcceptPreview.suggested_total}
                  分。
                </p>
                <p className="text-slate-600">
                  异常题不会包含在内；本操作只保存教师复核，不发布或释放成绩。
                </p>
                <div className="flex gap-2">
                  <button
                    className="rounded bg-emerald-700 px-3 py-2 text-white"
                    disabled={saving}
                    onClick={() => void confirmSafeSubmission()}
                  >
                    确认这 {safeAcceptPreview.eligible_count} 题
                  </button>
                  <button
                    className="rounded border bg-white px-3 py-2"
                    disabled={saving}
                    onClick={() => setShowSafeAccept(false)}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </section>
      {confirmedResult && (
        <div
          className="rounded-xl border border-emerald-300 bg-emerald-50 p-4"
          data-testid="confirmed-results"
          data-release-id={confirmedResult.grade_release_id}
          data-previous-release-id={
            confirmedResult.previous_grade_release_id ?? undefined
          }
        >
          <p className="font-medium">结果已确认</p>
          <p className="text-sm">
            已确认 {confirmedResult.submission_count} 份作业，
            {resultSummary(confirmedResult)}。
          </p>
        </div>
      )}
      <div className="grid min-h-[70vh] gap-4 xl:grid-cols-[minmax(0,1.4fr)_220px_minmax(320px,1fr)]">
        <section
          aria-label="原卷与证据"
          className="overflow-hidden rounded-xl border bg-slate-100"
        >
          <div className="flex flex-wrap gap-2 border-b bg-white p-3">
            <button
              className="rounded border px-3 py-1"
              onClick={() => setProcessed(!processed)}
            >
              {processed ? "查看原图" : "查看处理图"}
            </button>
            <button
              aria-label="缩小"
              className="rounded border px-3 py-1"
              onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
            >
              −
            </button>
            <button
              aria-label="放大"
              className="rounded border px-3 py-1"
              onClick={() => setZoom(Math.min(3, zoom + 0.25))}
            >
              ＋
            </button>
            <span className="py-1 text-sm">{Math.round(zoom * 100)}%</span>
          </div>
          <div className="relative overflow-auto p-4">
            {page && (processed ? page.processed_url : page.original_url) ? (
              <div
                className="relative origin-top-left"
                style={{ width: `${zoom * 100}%` }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  alt={`学生作业第 ${page.page_number} 页`}
                  src={
                    (processed ? page.processed_url : page.original_url) ?? ""
                  }
                  className="h-auto w-full"
                />
                {evidence?.x && (
                  <span
                    aria-label="当前证据区域"
                    className="absolute border-2 border-red-500 bg-red-200/20"
                    style={{
                      left: `${Number(evidence.x) * 100}%`,
                      top: `${Number(evidence.y) * 100}%`,
                      width: `${Number(evidence.width) * 100}%`,
                      height: `${Number(evidence.height) * 100}%`,
                    }}
                  />
                )}
              </div>
            ) : (
              <div className="p-12 text-center text-slate-500">
                原卷图暂不可用
              </div>
            )}
          </div>
        </section>
        <nav aria-label="复核导航" className="rounded-xl border bg-white p-3">
          <h2 className="mb-2 font-semibold">学生 / 题目</h2>
          {data.items.map((item, index) =>
            !item.answers.some((answer) =>
              matchesReviewFilter(answer, reviewFilter),
            ) ? null : (
              <button
                key={item.submission_id}
                onClick={() => {
                  const firstVisibleAnswer = item.answers.findIndex((answer) =>
                    matchesReviewFilter(answer, reviewFilter),
                  );
                  selectTarget({
                    submissionIndex: index,
                    answerIndex: Math.max(firstVisibleAnswer, 0),
                    answerId:
                      item.answers[Math.max(firstVisibleAnswer, 0)]?.id ?? "",
                  });
                }}
                className={`mb-2 block w-full rounded-lg p-2 text-left text-sm ${index === submissionIndex ? "bg-indigo-50 text-indigo-700" : "hover:bg-slate-50"}`}
              >
                学生 {index + 1}
              </button>
            ),
          )}
          <hr className="my-3" />
          {submission?.answers.map((item, index) => {
            if (!matchesReviewFilter(item, reviewFilter)) return null;
            return (
              <button
                key={item.id}
                data-answer-id={item.id}
                onClick={() =>
                  selectTarget({
                    submissionIndex,
                    answerIndex: index,
                    answerId: item.id,
                  })
                }
                className={`mb-1 block w-full rounded p-2 text-left text-sm ${index === answerIndex ? "bg-amber-50" : ""}`}
              >
                第 {item.question.number} 题
              </button>
            );
          })}
          <div className="mt-3 flex gap-1 overflow-x-auto">
            {submission?.pages.map((item, index) => (
              <button
                key={item.id}
                onClick={() => setPageIndex(index)}
                className="rounded border px-2 py-1 text-xs"
              >
                P{item.page_number}
              </button>
            ))}
          </div>
        </nav>
        <section
          aria-label="评分复核详情"
          data-testid="review-answer"
          data-answer-id={answer?.id}
          data-answer-status={answer?.status}
          data-question-type={answer?.question.type}
          data-provider={answer?.result?.provider ?? "manual"}
          data-result-status={answer?.result?.status}
          data-rubric-version-id={answer?.result?.structured_rubric_version_id}
          data-suggested-score={answer?.result?.score}
          data-final-score={answer?.review?.final_score}
          className="space-y-4 overflow-auto rounded-xl border bg-white p-4"
        >
          {answer && submission ? (
            <>
              <div>
                <span className="text-xs text-slate-500">题目</span>
                <h2 className="text-lg font-bold">
                  第 {answer.question.number} 题
                </h2>
                <p>{answer.question.content}</p>
              </div>
              <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
                <span className="text-sm text-indigo-800">AI 建议得分</span>
                <div className="mt-1 flex items-end gap-2">
                  <strong className="text-3xl text-indigo-950">
                    {answer.result?.score ?? "—"}
                  </strong>
                  <span className="pb-1 text-slate-600">
                    / {answer.question.max_score ?? "—"} 分
                  </span>
                </div>
                {!answer.review && canAcceptSuggestion && (
                  <p className="mt-1 text-sm text-slate-600">
                    核对原卷后，可直接确认或修改。
                  </p>
                )}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Info
                  label="学生答案"
                  value={answer.corrected_text ?? answer.recognized_text}
                />
                <Info label="教师最终分" value={answer.review?.final_score} />
              </div>
              {answer.result?.reasoning && (
                <div className="rounded border border-indigo-200 bg-indigo-50 p-3 text-sm">
                  <h3 className="font-semibold">建议评分理由</h3>
                  <p className="mt-1 whitespace-pre-wrap">
                    {answer.result.reasoning}
                  </p>
                </div>
              )}
              {!isCompleted(answer) && !scoringDecision && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {canAcceptSuggestion && (
                    <Action
                      label="确认建议分"
                      primary
                      onClick={() => void submitReview("accepted")}
                      disabled={saving}
                    />
                  )}
                  <Action
                    label={needsManualScoring ? "手动评分" : "修改分数"}
                    onClick={() =>
                      beginScoring(
                        needsManualScoring ? "manual_scored" : "modified",
                      )
                    }
                    disabled={saving}
                  />
                </div>
              )}
              {answer.result?.quality_flags?.includes(
                "CONSISTENCY_REVIEW_REQUIRED",
              ) && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                >
                  <p>相同答案出现不同评分，请先查看差异再决定。</p>
                  <div className="mt-2 flex gap-2">
                    <Link
                      className="rounded border bg-white px-3 py-1"
                      href={`/grading/${batchId}/review/${answer.id}/validation`}
                    >
                      查看差异
                    </Link>
                    <button
                      className="rounded border bg-white px-3 py-1"
                      onClick={() => beginScoring("modified")}
                    >
                      处理评分差异
                    </button>
                  </div>
                </div>
              )}
              {answer.result?.quality_flags?.includes(
                "BOUNDARY_RECHECK_DISAGREEMENT",
              ) && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                >
                  <p>两次评分结果不同，请查看差异或直接修改分数。</p>
                  <div className="mt-2 flex gap-2">
                    <Link
                      className="rounded border bg-white px-3 py-1"
                      href={`/grading/${batchId}/review/${answer.id}/validation`}
                    >
                      查看差异
                    </Link>
                    <button
                      className="rounded border bg-white px-3 py-1"
                      onClick={() => beginScoring("modified")}
                    >
                      处理评分差异
                    </button>
                  </div>
                </div>
              )}
              <details className="rounded border p-3 text-sm">
                <summary className="cursor-pointer font-semibold">
                  更多工具
                </summary>
                <Link
                  href={`/grading/${batchId}/review/${answer.id}/validation`}
                  className="mt-2 inline-flex rounded border px-3 py-2 font-medium"
                >
                  查看详细依据
                </Link>
              </details>
              <div id="answer-recognition-workspace">
                <AnswerRecognitionWorkspace
                  submissionId={submission.submission_id}
                  answerId={answer.id}
                  regionIds={(answer.regions ?? []).map((region) => region.id)}
                  readOnly={submission.status === "finalized"}
                  attentionRequired={
                    answer.recognized_text === undefined ||
                    Number(answer.confidence ?? 0) < 0.9
                  }
                />
              </div>
              {(answer.status === "stale" ||
                answer.result?.status === "stale") && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                  data-testid="regrade-required"
                >
                  <p>内容已变化，旧建议不能继续使用。</p>
                  <button
                    className="mt-2 rounded border bg-white px-3 py-1"
                    disabled={saving}
                    onClick={() => void regradeCurrentAnswer()}
                  >
                    重新批改
                  </button>
                </div>
              )}
              {(answer.status === "manual_segmentation_required" ||
                (answer.regions ?? []).length === 0) && (
                <div
                  role="alert"
                  className="rounded border border-red-300 bg-red-50 p-3 text-sm"
                >
                  <p>未找到清晰的答题区域。</p>
                  <Link
                    className="mt-2 inline-flex rounded border bg-white px-3 py-1"
                    href={`/grading/${batchId}#submission-${submission.submission_id}`}
                  >
                    重新框选答案
                  </Link>
                </div>
              )}
              {(answer.recognized_text === undefined ||
                Number(answer.confidence ?? 0) < 0.9) && (
                <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
                  <p>答案识别不清，请先修正识别文字或重新框选答案。</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      className="rounded border bg-white px-3 py-1"
                      onClick={() => {
                        const details =
                          document.querySelector<HTMLDetailsElement>(
                            "[data-testid='answer-recognition-details']",
                          );
                        if (details) {
                          details.open = true;
                          details.scrollIntoView({
                            behavior: "smooth",
                            block: "start",
                          });
                        }
                      }}
                    >
                      修正识别文字
                    </button>
                    <Link
                      className="rounded border bg-white px-3 py-1"
                      href={`/grading/${batchId}#submission-${submission.submission_id}`}
                    >
                      重新框选答案
                    </Link>
                  </div>
                </div>
              )}
              {(answer.result?.score == null ||
                hasManualOrIncompleteCriteria(answer)) && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                >
                  <p>
                    建议分缺失或包含需要教师判断的评分项，请由教师手动评分。
                  </p>
                </div>
              )}
              <details className="rounded border p-3 text-sm">
                <summary className="cursor-pointer font-semibold">
                  识别调整
                </summary>
                <Link
                  className="mt-2 inline-flex rounded border px-3 py-2 text-sm"
                  href={`/grading/${batchId}#submission-${submission.submission_id}`}
                >
                  重新框选答案
                </Link>
                <div className="mt-2">
                  {(answer.regions ?? []).map((region) => (
                    <div
                      key={region.id}
                      id={`answer-region-${region.id}`}
                      className="mt-2 flex items-center gap-2 text-sm"
                    >
                      <span>已标记答题区域 · {statusLabel(region.status)}</span>
                      <button
                        className="text-red-700 underline"
                        disabled={saving}
                        onClick={() =>
                          void (async () => {
                            setSaving(true);
                            try {
                              await gradingApi.deleteAnswerRegion(
                                answer.id,
                                region.id,
                              );
                              await load();
                              setMessage(
                                "区域已删除；旧文字识别和评分已标记失效",
                              );
                            } finally {
                              setSaving(false);
                            }
                          })()
                        }
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              </details>
              <details
                className="rounded border p-3"
                open={hasManualOrIncompleteCriteria(answer) || undefined}
              >
                <summary className="cursor-pointer font-semibold">
                  评分项 · {answer.criteria.length} 项 · 建议合计{" "}
                  {answer.criteria.reduce(
                    (total, item) => total + Number(item.awarded_points ?? 0),
                    0,
                  )}
                  /{answer.question.max_score ?? "—"}
                </summary>
                {answer.criteria.map((item) => (
                  <div
                    key={item.criterion_id}
                    className="mt-2 rounded border p-2 text-sm"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">
                        {item.title || "评分项"}
                      </span>
                      <span>
                        {item.awarded_points ?? "—"} / {item.max_points}
                      </span>
                    </div>
                    {item.reason && (
                      <p className="mt-1 text-slate-700">{item.reason}</p>
                    )}
                    {item.description && (
                      <p className="mt-1 text-slate-500">{item.description}</p>
                    )}
                    {item.evidence_quotes?.[0] && (
                      <p className="mt-1 text-slate-500">
                        依据：{item.evidence_quotes[0]}
                      </p>
                    )}
                  </div>
                ))}
              </details>
              {answer.evidence.length > 0 && (
                <details className="rounded border p-3 text-sm">
                  <summary className="cursor-pointer font-semibold">
                    查看原图依据
                  </summary>
                  {answer.evidence.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => {
                        setActiveEvidence(item.id);
                        const index = submission.pages.findIndex(
                          (candidate) =>
                            candidate.id === item.submission_page_id,
                        );
                        if (index >= 0) setPageIndex(index);
                      }}
                      className="mt-2 block w-full rounded border p-2 text-left hover:bg-indigo-50"
                    >
                      {item.quote || "证据区域"}
                    </button>
                  ))}
                </details>
              )}
              {scoringDecision && (
                <section
                  ref={scoringFormRef}
                  aria-label="教师评分表单"
                  data-testid="teacher-scoring-form"
                  className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">教师最终评分</h3>
                      <p className="text-sm text-slate-600">
                        请确认或修改分数。
                      </p>
                    </div>
                    <span
                      className={`rounded-full px-3 py-1 text-sm font-medium ${
                        scoringValidationMessage
                          ? "bg-amber-100 text-amber-900"
                          : "bg-emerald-100 text-emerald-900"
                      }`}
                    >
                      分项合计 {criterionTotal} · 最终分 {scoreDraft || "—"} ·
                      满分 {maxScore || "—"}
                    </span>
                  </div>
                  <p
                    aria-live="polite"
                    className={`mt-3 rounded border px-3 py-2 text-sm ${
                      scoringValidationMessage
                        ? "border-amber-300 bg-amber-50 text-amber-900"
                        : "border-emerald-200 bg-emerald-50 text-emerald-800"
                    }`}
                    data-testid="scoring-validation"
                  >
                    {scoringValidationMessage ||
                      "分数与评分项已核对，可以保存。"}
                  </p>
                  <label className="mt-3 block text-sm">
                    最终分数
                    <input
                      ref={scoreInputRef}
                      aria-label="教师最终分数"
                      aria-invalid={scoreIsInvalid}
                      type="number"
                      min="0"
                      max={maxScore || undefined}
                      step="any"
                      value={scoreDraft}
                      onChange={(event) => setScoreDraft(event.target.value)}
                      className="mt-1 w-full rounded border bg-white px-3 py-2"
                    />
                  </label>
                  {answer.criteria.length > 0 && (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      {answer.criteria.map((criterion, index) => (
                        <label key={criterion.criterion_id} className="text-sm">
                          {criterion.title || `评分项 ${index + 1}`}（满分{" "}
                          {criterion.max_points}）
                          <input
                            aria-label={`${criterion.title || `评分项 ${index + 1}`} 得分`}
                            aria-invalid={
                              (criterionDrafts[criterion.criterion_id] ??
                                "") === "" ||
                              Number(criterionDrafts[criterion.criterion_id]) <
                                0 ||
                              Number(criterionDrafts[criterion.criterion_id]) >
                                Number(criterion.max_points)
                            }
                            type="number"
                            min="0"
                            max={criterion.max_points}
                            step="any"
                            value={
                              criterionDrafts[criterion.criterion_id] ?? ""
                            }
                            onChange={(event) =>
                              setCriterionDrafts((current) => ({
                                ...current,
                                [criterion.criterion_id]: event.target.value,
                              }))
                            }
                            className="mt-1 w-full rounded border bg-white px-3 py-2"
                          />
                        </label>
                      ))}
                    </div>
                  )}
                  <label className="mt-3 block text-sm">
                    教师反馈（可选）
                    <textarea
                      aria-label="教师反馈"
                      value={feedbackDraft}
                      onChange={(event) => setFeedbackDraft(event.target.value)}
                      className="mt-1 min-h-20 w-full rounded border bg-white p-2"
                    />
                  </label>
                  <div className="mt-3 flex gap-2">
                    <Action
                      label="保存最终评分"
                      primary
                      disabled={saving || Boolean(scoringValidationMessage)}
                      onClick={() => void submitReview(scoringDecision)}
                    />
                    <Action
                      label="取消"
                      disabled={saving}
                      onClick={clearCurrentDraft}
                    />
                  </div>
                </section>
              )}
              <details className="rounded border p-3 text-sm">
                <summary className="cursor-pointer font-semibold">
                  更多操作
                </summary>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Action
                    label="修正答案"
                    onClick={() => void correctCurrentAnswer()}
                    disabled={saving}
                  />
                  <Action
                    label="重新批改"
                    onClick={() => void regradeCurrentAnswer()}
                    disabled={saving}
                  />
                </div>
              </details>
              {message && (
                <p role="status" className="text-sm text-slate-600">
                  {message}
                </p>
              )}
            </>
          ) : (
            <div className="space-y-3 text-slate-500">
              <p>
                {reviewFilter === "pending"
                  ? "本轮题目已处理完。请检查“已完成”，确认无误后再由主责教师确认结果；系统不会自动发布。"
                  : reviewFilter === "needs_review"
                    ? "当前没有需要检查的答案"
                    : "当前筛选下没有题目"}
              </p>
              {message && <p role="status">{message}</p>}
              {(reviewFilter === "needs_review" ||
                reviewFilter === "stale") && (
                <button
                  className="rounded border px-3 py-2 text-sm text-slate-900"
                  onClick={() => changeReviewFilter("pending")}
                >
                  返回待处理
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <span className="block text-xs text-slate-500">{label}</span>
      <span className="break-words text-sm">{value || "—"}</span>
    </div>
  );
}

function Action({
  label,
  primary = false,
  ...props
}: {
  label: string;
  primary?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={
        primary
          ? "rounded bg-emerald-600 px-3 py-2 text-white disabled:opacity-50"
          : "rounded border px-3 py-2 disabled:opacity-50"
      }
      {...props}
    >
      {label}
    </button>
  );
}

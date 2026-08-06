"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  gradingApi,
  type ConfirmResultsReadiness,
  type ConfirmResultsResult,
  type ReviewWorkspace,
} from "@/lib/api";
import { AnswerRecognitionWorkspace } from "@/components/answer-recognition-workspace";

type Decision = "modified" | "manual_scored";

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

type ReviewFilter = "all" | "suggested" | "needs_review" | "reviewed" | "stale";

function statusLabel(value?: string) {
  return value ? (statusLabels[value] ?? value) : "未提供";
}

type ReviewAnswer = ReviewWorkspace["items"][number]["answers"][number];

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

type ReviewTarget = {
  submissionIndex: number;
  answerIndex: number;
  answerId: string;
};

function reviewTargets(
  data: ReviewWorkspace,
  filter: ReviewFilter,
): ReviewTarget[] {
  return data.items.flatMap((submission, submissionIndex) =>
    submission.answers.flatMap((answer, answerIndex) =>
      matchesReviewFilter(answer, filter)
        ? [{ submissionIndex, answerIndex, answerId: answer.id }]
        : [],
    ),
  );
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
    (filter === "suggested" && answer.result?.status === "suggested") ||
    (filter === "needs_review" && needsTeacherReview(answer)) ||
    (filter === "reviewed" && Boolean(answer.review)) ||
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
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const fetchWorkspace = () =>
    jointQuestionId
      ? gradingApi.reviewWorkspace(batchId, jointQuestionId)
      : gradingApi.reviewWorkspace(batchId);

  const load = async () => {
    const next = await fetchWorkspace();
    const nextReadiness =
      next.collaboration?.can_confirm_results === false
        ? undefined
        : await gradingApi.confirmResultsReadiness(batchId);
    setData(next);
    setReadiness(nextReadiness);
    setConfirmedResult((current) => nextReadiness?.confirmed_result ?? current);
    return next;
  };
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
        const preferredFilter = reviewTargets(workspace, "needs_review").length
          ? "needs_review"
          : "all";
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

  useEffect(() => {
    if (!answer) return;
    setScoreDraft(answer.review?.final_score ?? answer.result?.score ?? "");
    setFeedbackDraft(answer.review?.feedback ?? "");
    setCriterionDrafts(
      Object.fromEntries(
        answer.criteria.map((criterion) => [
          criterion.criterion_id,
          criterion.awarded_points ?? "",
        ]),
      ),
    );
    setScoringDecision(null);
  }, [answer]);

  useEffect(() => {
    if (!data || answer) return;
    const firstTarget = reviewTargets(data, reviewFilter)[0];
    if (firstTarget) {
      setSubmissionIndex(firstTarget.submissionIndex);
      setAnswerIndex(firstTarget.answerIndex);
      setPageIndex(0);
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
      await gradingApi.review(currentAnswerId, payload);
      const next = await load();
      const target = nextReviewTarget(
        next,
        reviewFilter,
        submissionIndex,
        answerIndex,
        currentAnswerId,
      );
      if (target) {
        setSubmissionIndex(target.submissionIndex);
        setAnswerIndex(target.answerIndex);
        setPageIndex(0);
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

  async function addRegion() {
    if (!answer || !page || saving) return;
    const raw = window.prompt(
      "输入答题区域 x,y,width,height（0–1 坐标）",
      "0,0,1,1",
    );
    if (raw === null) return;
    const values = raw.split(",").map(Number);
    if (
      values.length !== 4 ||
      values.some((value) => Number.isNaN(value)) ||
      values[0] < 0 ||
      values[1] < 0 ||
      values[2] <= 0 ||
      values[3] <= 0 ||
      values[0] + values[2] > 1 ||
      values[1] + values[3] > 1
    ) {
      setMessage("区域坐标无效");
      return;
    }
    setSaving(true);
    try {
      await gradingApi.createAnswerRegion(answer.id, {
        submission_page_id: page.id,
        x: values[0],
        y: values[1],
        width: values[2],
        height: values[3],
        source: "manual",
        confirmed: true,
      });
      await load();
      setMessage("答题区域已更新，请重新识别");
    } catch {
      setMessage("答题区域保存失败");
    } finally {
      setSaving(false);
    }
  }

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
          {data.collaboration?.can_confirm_results !== false && (
            <button
              className="rounded bg-indigo-700 px-3 py-2 text-white disabled:opacity-50"
              disabled={saving || !readiness?.ready || Boolean(confirmedResult)}
              onClick={() => void confirmResults()}
            >
              {confirmedResult ? "结果已确认" : "确认结果"}
            </button>
          )}
          <Link
            className="rounded border px-3 py-2"
            href={`/grading/${batchId}`}
          >
            返回批次工作台
          </Link>
        </div>
      </header>
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
              ["all", "全部"],
              ["suggested", "待确认"],
              ["needs_review", "需检查"],
              ["reviewed", "已复核"],
              ["stale", "已失效"],
            ] as Array<[ReviewFilter, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={reviewFilter === value}
              className={`rounded border px-3 py-1 text-sm ${
                reviewFilter === value ? "bg-indigo-700 text-white" : ""
              }`}
              onClick={() => setReviewFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
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
                  setSubmissionIndex(index);
                  setAnswerIndex(Math.max(firstVisibleAnswer, 0));
                  setPageIndex(0);
                  setActiveEvidence(undefined);
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
                onClick={() => setAnswerIndex(index)}
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
              <div className="grid gap-3 sm:grid-cols-2">
                <Info
                  label="学生答案"
                  value={answer.corrected_text ?? answer.recognized_text}
                />
                <Info
                  label="建议分 / 满分"
                  value={`${answer.result?.score ?? "—"} / ${answer.question.max_score ?? "—"}`}
                />
                <Info label="教师最终分" value={answer.review?.final_score} />
              </div>
              {answer.result?.reasoning && (
                <div className="rounded border border-indigo-200 bg-indigo-50 p-3 text-sm">
                  <h3 className="font-semibold">建议评分理由</h3>
                  <p className="mt-1 whitespace-pre-wrap">
                    {answer.result.reasoning}
                  </p>
                  <p className="mt-2 text-xs text-slate-600">仅供参考。</p>
                </div>
              )}
              {answer.result?.quality_flags?.includes(
                "CONSISTENCY_REVIEW_REQUIRED",
              ) && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                >
                  相同答案出现不同评分，请检查。
                </div>
              )}
              {answer.result?.quality_flags?.includes(
                "BOUNDARY_RECHECK_DISAGREEMENT",
              ) && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                >
                  两次评分结果不同，请教师判断。
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
                  查看验证
                </Link>
              </details>
              <div id="answer-recognition-workspace">
                <AnswerRecognitionWorkspace
                  submissionId={submission.submission_id}
                  answerId={answer.id}
                  regionIds={(answer.regions ?? []).map((region) => region.id)}
                  readOnly={submission.status === "finalized"}
                />
              </div>
              {(answer.status === "stale" ||
                answer.result?.status === "stale") && (
                <div
                  role="alert"
                  className="rounded border border-amber-300 bg-amber-50 p-3 text-sm"
                  data-testid="regrade-required"
                >
                  内容已变化，请重新批改。
                </div>
              )}
              {(answer.status === "manual_segmentation_required" ||
                (answer.regions ?? []).length === 0) && (
                <div
                  role="alert"
                  className="rounded border border-red-300 bg-red-50 p-3 text-sm"
                >
                  未找到答题区域，请手动标记。
                </div>
              )}
              {(answer.recognized_text === undefined ||
                Number(answer.confidence ?? 0) < 0.9) && (
                <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
                  答案识别不清，请检查。
                </div>
              )}
              <details className="rounded border p-3 text-sm">
                <summary className="cursor-pointer font-semibold">
                  识别调整
                </summary>
                <button
                  className="mt-2 rounded border px-3 py-2 text-sm"
                  disabled={saving || !page}
                  onClick={() => void addRegion()}
                >
                  在当前页增加区域
                </button>
                <div className="mt-2">
                  {(answer.regions ?? []).map((region) => (
                    <div
                      key={region.id}
                      id={`answer-region-${region.id}`}
                      className="mt-2 flex items-center gap-2 text-sm"
                    >
                      <span>
                        {statusLabel(region.source)} /{" "}
                        {statusLabel(region.status)} · 坐标 {region.x},
                        {region.y},{region.width},{region.height}
                      </span>
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
              <div>
                <h3 className="font-semibold">评分依据</h3>
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
                    {item.evidence_quotes?.[0] && (
                      <p className="mt-1 text-slate-500">
                        依据：{item.evidence_quotes[0]}
                      </p>
                    )}
                  </div>
                ))}
              </div>
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
                  aria-label="教师评分表单"
                  className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">教师最终评分</h3>
                      <p className="text-sm text-slate-600">
                        请确认或修改分数。
                      </p>
                    </div>
                    <span className="text-sm font-medium">
                      分项合计 {criterionTotal} · 最终分 {scoreDraft || "—"} ·
                      满分 {maxScore || "—"}
                    </span>
                  </div>
                  <label className="mt-3 block text-sm">
                    最终分数
                    <input
                      aria-label="教师最终分数"
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
                          评分项 {index + 1}（满分 {criterion.max_points}）
                          <input
                            aria-label={`评分项 ${index + 1} 得分`}
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
                      disabled={saving}
                      onClick={() => void submitReview(scoringDecision)}
                    />
                    <Action
                      label="取消"
                      disabled={saving}
                      onClick={() => setScoringDecision(null)}
                    />
                  </div>
                </section>
              )}
              <div className="grid grid-cols-2 gap-2">
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
                <Action
                  label="修改"
                  onClick={() => setScoringDecision("modified")}
                  disabled={saving}
                />
                <Action
                  label="手动评分"
                  onClick={() => setScoringDecision("manual_scored")}
                  disabled={saving}
                />
              </div>
              {message && (
                <p role="status" className="text-sm text-slate-600">
                  {message}
                </p>
              )}
            </>
          ) : (
            <div className="space-y-3 text-slate-500">
              <p>
                {reviewFilter === "needs_review"
                  ? "当前没有需要检查的答案"
                  : "请选择答案"}
              </p>
              {message && <p role="status">{message}</p>}
              {reviewFilter === "needs_review" && (
                <button
                  className="rounded border px-3 py-2 text-sm text-slate-900"
                  onClick={() => setReviewFilter("all")}
                >
                  查看全部
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

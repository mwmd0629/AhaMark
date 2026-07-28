"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  aiGradingApi,
  type AIScoringJob,
  type AISuggestion,
} from "@/lib/api";

const terminal = new Set([
  "completed",
  "partially_completed",
  "abstained",
  "failed",
  "cancelled",
  "stale",
  "review_pending",
]);

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "已完成",
  partially_completed: "部分完成",
  abstained: "已放弃判断",
  cancelled: "已取消",
  review_pending: "待教师复核",
  confirmed: "已确认",
  scored: "可评分建议",
  abstain: "AI 放弃判断",
  manual: "需人工评分",
  conflict: "与确定性验证冲突",
  insufficient: "证据不足",
  failed: "处理失败",
  stale: "版本已失效",
  verified: "确定性验证通过",
  indeterminate: "验证不确定",
  unsupported: "自动验证不支持",
  manual_required: "需人工验证",
};

const providerLabels: Record<string, string> = {
  unavailable: "未配置评分服务",
  fake: "本地占位服务",
  codex: "本地 Codex",
  manual: "教师人工评分",
};

function providerLabel(value: string) {
  return providerLabels[value] ?? value;
}

function badge(status: string) {
  if (["conflict", "failed", "stale"].includes(status))
    return "bg-red-100 text-red-800";
  if (
    [
      "abstain",
      "manual",
      "insufficient",
      "indeterminate",
      "unsupported",
    ].includes(status)
  )
    return "bg-amber-100 text-amber-900";
  if (["scored", "verified"].includes(status))
    return "bg-emerald-100 text-emerald-800";
  return "bg-slate-100 text-slate-800";
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.body.code === "AI_SUGGESTION_STALE")
      return "建议已失效，请刷新后重跑 AI 任务或改用人工评分。";
    if (error.body.code === "AI_SUGGESTION_ALREADY_REVIEWED")
      return "该建议已经处置，请刷新查看结果，避免重复提交。";
    if (error.body.code === "VALIDATION_STALE")
      return "数学验证版本已变化，请刷新或重新验证后再处理。";
    if (error.body.code === "SUBMISSION_FINALIZED")
      return "提交已定稿，当前页面只能查看审计记录。";
    return `${error.body.message}（${error.body.code}）`;
  }
  return "操作失败，请刷新后重试；仍失败时改用人工评分。";
}

function SuggestionCard({
  job,
  suggestion,
  readOnly,
  busy,
  onBusy,
  onDone,
  onRefresh,
}: {
  job: AIScoringJob;
  suggestion: AISuggestion;
  readOnly: boolean;
  busy: boolean;
  onBusy: (busy: boolean) => void;
  onDone: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [points, setPoints] = useState(suggestion.suggested_points ?? "");
  const [reason, setReason] = useState("");
  const validationResults = useMemo(
    () =>
      job.validation?.results.filter((item) =>
        suggestion.validation_refs.includes(item.id),
      ) ?? [],
    [job.validation?.results, suggestion.validation_refs],
  );
  const evidence = useMemo(
    () =>
      job.evidence.filter((item) => suggestion.evidence_ids.includes(item.id)),
    [job.evidence, suggestion.evidence_ids],
  );
  const disabled = readOnly || busy || Boolean(suggestion.review);
  const adoptable =
    !disabled &&
    suggestion.status === "scored" &&
    suggestion.suggested_points != null &&
    suggestion.requires_review;

  const review = async (action: "accepted" | "modified" | "rejected") => {
    if (!reason.trim()) {
      onDone("请填写教师处置原因。");
      return;
    }
    if (action === "modified") {
      const numeric = Number(points);
      if (
        points.trim() === "" ||
        Number.isNaN(numeric) ||
        numeric < 0 ||
        numeric > Number(suggestion.max_points)
      ) {
        onDone(`请输入 0–${suggestion.max_points} 范围内的教师分值。`);
        return;
      }
    }
    onBusy(true);
    onDone("");
    try {
      await aiGradingApi.review(suggestion.id, {
        action,
        selected_points: action === "modified" ? Number(points) : undefined,
        reason,
      });
      await onRefresh();
      onDone(
        action === "rejected"
          ? "AI 建议已拒绝；可继续人工评分。"
          : "AI 处置草稿已保存；请在本页教师复核区确认最终总分。",
      );
    } catch (error) {
      onDone(errorMessage(error));
    } finally {
      onBusy(false);
    }
  };

  return (
    <article className="rounded-lg border border-violet-200 bg-violet-50/40 p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <strong>{suggestion.criterion_stable_key}</strong>
        <span
          className={`rounded px-2 py-1 text-xs ${badge(suggestion.status)}`}
        >
          {statusLabels[suggestion.status] ?? suggestion.status}
        </span>
      </header>
      <p className="mt-2 text-sm">
        AI 建议：{suggestion.suggested_points ?? "不评分"} /{" "}
        {suggestion.max_points}；置信度：
        {suggestion.confidence ?? "未提供"}
      </p>
      <p className="mt-1 text-sm text-slate-700">
        {suggestion.reason ?? "评分服务未提供理由；不得据此自动定分。"}
      </p>
      {suggestion.error_codes.length > 0 && (
        <p className="mt-1 text-sm text-red-800">
          错误代码：{suggestion.error_codes.join("、")}
        </p>
      )}

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <section
          className="rounded border bg-white p-2"
          aria-label="AI 证据引用"
        >
          <h4 className="text-xs font-semibold text-slate-600">证据链</h4>
          {evidence.length ? (
            evidence.map((item) => (
              <a
                key={item.id}
                href={`#${item.target_id}`}
                className="mt-1 block text-sm text-indigo-700 underline"
              >
                {item.kind === "recognition" ? "识别证据" : "答题区域"} · 版本
                {item.version} · {statusLabels[item.status] ?? item.status}
                {item.stale ? " · 已失效" : ""}
              </a>
            ))
          ) : (
            <p className="mt-1 text-sm text-amber-800">
              当前建议未提供可定位证据引用。
            </p>
          )}
        </section>
        <section
          className="rounded border bg-white p-2"
          aria-label="数学验证引用"
        >
          <h4 className="text-xs font-semibold text-slate-600">
            数学验证（确定性）
          </h4>
          <p className="mt-1 text-xs text-slate-500">
            评分服务的自述不视为确定性验证。
          </p>
          {validationResults.length ? (
            validationResults.map((item) => (
              <p key={item.id} className="mt-1 text-sm">
                <span className={`rounded px-1 ${badge(item.result)}`}>
                  {statusLabels[item.result] ?? item.result}
                </span>{" "}
                第 {item.generation} 代 · {item.comparison_method}
                {item.stale ? " · 已失效" : ""}
              </p>
            ))
          ) : (
            <p className="mt-1 text-sm text-slate-600">
              无当前数学验证引用；评分服务的自述不视为确定性验证。
            </p>
          )}
        </section>
      </div>

      {suggestion.missing_steps.length > 0 && (
        <p className="mt-2 text-sm">
          缺失步骤：{suggestion.missing_steps.join("；")}
        </p>
      )}
      {suggestion.detected_errors.length > 0 && (
        <p className="mt-1 text-sm text-red-800">
          错误分类：{suggestion.detected_errors.join("、")}
        </p>
      )}
      {suggestion.review ? (
        <div className="mt-3 rounded border border-emerald-300 bg-emerald-50 p-3 text-sm">
          教师已处置：{suggestion.review.action}
          {suggestion.review.selected_points != null
            ? ` · ${suggestion.review.selected_points} 分`
            : ""}
          <span className="block">原因：{suggestion.review.reason}</span>
        </div>
      ) : (
        <div className="mt-3 rounded border bg-white p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            教师处置（AI 草稿，不是最终成绩）
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <input
              aria-label={`${suggestion.criterion_stable_key} 教师分值`}
              type="number"
              min="0"
              max={suggestion.max_points}
              step="any"
              value={points}
              disabled={disabled}
              onChange={(event) => setPoints(event.target.value)}
              className="w-28 rounded border px-2 py-1"
            />
            <input
              aria-label={`${suggestion.criterion_stable_key} 修改原因`}
              value={reason}
              disabled={disabled}
              onChange={(event) => setReason(event.target.value)}
              placeholder="必填：采纳/修改/拒绝原因"
              className="min-w-64 flex-1 rounded border px-2 py-1"
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!adoptable}
              onClick={() => void review("accepted")}
              className="rounded border px-3 py-1 disabled:opacity-50"
            >
              采纳 AI 分项建议
            </button>
            <button
              type="button"
              disabled={disabled || !points}
              onClick={() => void review("modified")}
              className="rounded border px-3 py-1 disabled:opacity-50"
            >
              教师修改后采用
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => void review("rejected")}
              className="rounded border px-3 py-1 disabled:opacity-50"
            >
              拒绝并转人工
            </button>
          </div>
          {!adoptable && suggestion.status !== "scored" && (
            <p className="mt-2 text-xs text-amber-800">
              当前状态不能直接采纳；教师仍可独立修改分值或拒绝后人工评分。
            </p>
          )}
        </div>
      )}
    </article>
  );
}

export function AIGradingReview({
  answerId,
  rubricVersionId,
  finalized = false,
}: {
  answerId: string;
  rubricVersionId?: string;
  finalized?: boolean;
}) {
  const [jobs, setJobs] = useState<AIScoringJob[]>([]);
  const [message, setMessage] = useState("");
  const [busySuggestion, setBusySuggestion] = useState<string>();
  const [creating, setCreating] = useState(false);
  const load = useCallback(
    () => aiGradingApi.listForAnswer(answerId).then(setJobs),
    [answerId],
  );
  useEffect(() => {
    setMessage("");
    load().catch((error) => setMessage(errorMessage(error)));
  }, [load]);
  const current = jobs.find((job) => !job.stale) ?? jobs[0];
  useEffect(() => {
    if (!current || terminal.has(current.status)) return;
    const timer = window.setInterval(() => load().catch(() => undefined), 1500);
    return () => window.clearInterval(timer);
  }, [current, load]);
  const [studentFeedback, setStudentFeedback] = useState("");
  const [teacherSummary, setTeacherSummary] = useState("");
  useEffect(() => {
    setStudentFeedback(current?.feedback?.student_feedback ?? "");
    setTeacherSummary(current?.feedback?.teacher_summary ?? "");
  }, [current?.id, current?.feedback]);
  const validationMismatch = Boolean(
    current?.validation &&
    (current.validation.rubric_version_id !== current.rubric_version_id ||
      current.validation.reference_answer_version_id !==
        current.reference_answer_version_id),
  );
  const readOnly = finalized || Boolean(current?.stale) || validationMismatch;
  const effectiveRubricVersionId =
    rubricVersionId ?? current?.rubric_version_id;

  const create = async () => {
    if (!effectiveRubricVersionId) {
      setMessage(
        "当前页没有可用的结构化评分标准版本，请先打开数学验证页创建任务。",
      );
      return;
    }
    setCreating(true);
    setMessage("");
    try {
      await aiGradingApi.create(answerId, effectiveRubricVersionId);
      await load();
      setMessage("AI 建议任务已创建；结果仍需教师确认。");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  return (
    <section
      aria-label="AI 分项评分建议"
      className="rounded-xl border-2 border-violet-300 bg-white p-4"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-violet-950">
            本地 Codex / AI 建议，需教师确认
          </h2>
          <p className="text-sm text-slate-600">
            当前采用离线占位工作流，不调用外部 API、不上传文件。AI
            处置仅保存审计草稿；最终总分仍由教师复核。
          </p>
        </div>
        <button
          type="button"
          disabled={finalized || creating || !effectiveRubricVersionId}
          onClick={() => void create()}
          className="rounded bg-violet-700 px-3 py-2 text-white disabled:opacity-50"
        >
          {creating ? "正在创建…" : "生成新建议"}
        </button>
      </header>
      {message && (
        <p role="status" className="mt-2 rounded bg-slate-50 p-2 text-sm">
          {message}
        </p>
      )}
      {finalized && (
        <p className="mt-2 rounded bg-slate-100 p-2 text-sm">
          已定稿：本区只读，不能创建或采纳建议。
        </p>
      )}
      {current ? (
        <>
          <div className="mt-3 grid gap-2 rounded bg-violet-50 p-3 text-sm md:grid-cols-3">
            <span>状态：{statusLabels[current.status] ?? current.status}</span>
            <span>评分来源：{providerLabel(current.provider)}</span>
            <span>生成代次：第 {current.generation} 代</span>
            <span>评分标准版本：{current.rubric_version_id}</span>
            <span>标准答案：{current.reference_answer_version_id}</span>
            <span>
              验证代次：第 {current.validation?.generation ?? "无"} 代
            </span>
          </div>
          {(current.provider === "unavailable" ||
            current.error_code === "PROVIDER_UNAVAILABLE") && (
            <p className="mt-3 rounded bg-amber-100 p-3">
              评分服务未配置：AI 未给出有效评分，人工批改流程仍可正常使用。
            </p>
          )}
          {current.status === "failed" && (
            <p className="mt-3 rounded bg-red-100 p-3">
              AI 任务失败：{current.error_code ?? "未知错误"}
              。请刷新、重跑或直接人工评分。
            </p>
          )}
          {current.stale && (
            <p className="mt-3 rounded bg-amber-100 p-3">
              已失效：版本或证据已变化，此次生成结果仅供审计，不可处置。
            </p>
          )}
          {current.validation?.stale && (
            <p className="mt-3 rounded bg-amber-100 p-3">
              数学验证已失效：不可将旧验证结果用于采纳，请重新验证。
            </p>
          )}
          {validationMismatch && (
            <p className="mt-3 rounded bg-red-100 p-3">
              评分标准/标准答案版本与数学验证引用不一致：已禁用 AI
              处置，请刷新、重新验证或改用人工评分。
            </p>
          )}
          <div className="mt-3 space-y-3">
            {current.suggestions.length ? (
              current.suggestions.map((suggestion) => (
                <SuggestionCard
                  key={suggestion.id}
                  job={current}
                  suggestion={suggestion}
                  readOnly={readOnly}
                  busy={busySuggestion === suggestion.id}
                  onBusy={(busy) =>
                    setBusySuggestion(busy ? suggestion.id : undefined)
                  }
                  onDone={setMessage}
                  onRefresh={load}
                />
              ))
            ) : (
              <p className="rounded bg-slate-50 p-3 text-sm">
                当前任务没有可展示的 AI 分项建议；请继续人工评分。
              </p>
            )}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              学生反馈草稿
              <textarea
                value={studentFeedback}
                disabled={readOnly}
                onChange={(event) => setStudentFeedback(event.target.value)}
                className="mt-1 min-h-24 w-full rounded border p-2"
              />
            </label>
            <label className="text-sm">
              教师内部摘要
              <textarea
                value={teacherSummary}
                disabled={readOnly}
                onChange={(event) => setTeacherSummary(event.target.value)}
                className="mt-1 min-h-24 w-full rounded border p-2"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={readOnly}
            onClick={() =>
              aiGradingApi
                .editFeedback(current.id, {
                  student_feedback: studentFeedback,
                  teacher_summary: teacherSummary,
                })
                .then(() => setMessage("反馈草稿已保存，未发布。"))
                .catch((error) => setMessage(errorMessage(error)))
            }
            className="mt-2 rounded border px-3 py-2 disabled:opacity-50"
          >
            保存反馈草稿
          </button>
          {jobs.length > 1 && (
            <details className="mt-4 rounded border p-3">
              <summary>历史生成记录</summary>
              {jobs.map((job) => (
                <p key={job.id} className="mt-1 text-sm">
                  第 {job.generation} 代 ·{" "}
                  {statusLabels[job.status] ?? job.status} ·{" "}
                  {job.stale ? "已失效" : "当前版本"}
                </p>
              ))}
            </details>
          )}
        </>
      ) : (
        <p className="mt-3 text-sm">
          尚无 AI 建议。人工批改不受影响；可在确认识别与结构化评分标准
          后创建任务。
        </p>
      )}
    </section>
  );
}

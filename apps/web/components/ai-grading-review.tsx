"use client";

import { useCallback, useEffect, useState } from "react";
import { aiGradingApi, type AIScoringJob, type AISuggestion } from "@/lib/api";

const terminal = new Set([
  "completed",
  "partially_completed",
  "abstained",
  "failed",
  "cancelled",
  "stale",
  "review_pending",
]);

function badge(status: string) {
  if (status === "deterministic_conflict") return "bg-red-100 text-red-800";
  if (["abstain", "manual_required", "insufficient_evidence"].includes(status))
    return "bg-amber-100 text-amber-900";
  if (status === "suggested_pass") return "bg-emerald-100 text-emerald-800";
  return "bg-slate-100 text-slate-800";
}

function SuggestionCard({
  job,
  suggestion,
  readOnly,
  onDone,
}: {
  job: AIScoringJob;
  suggestion: AISuggestion;
  readOnly: boolean;
  onDone: (message: string) => void;
}) {
  const [points, setPoints] = useState(suggestion.suggested_points ?? "");
  const [reason, setReason] = useState("");
  const review = async (action: "accepted" | "modified" | "rejected") => {
    if (!reason.trim()) {
      onDone("请填写教师处置原因。");
      return;
    }
    await aiGradingApi.review(suggestion.id, {
      action,
      selected_points: action === "modified" ? Number(points) : undefined,
      reason,
    });
    onDone("教师评分草稿已保存；尚未确认、Finalize 或发布。");
  };
  return (
    <article className="rounded-lg border border-violet-200 bg-violet-50/40 p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <strong>{suggestion.criterion_stable_key}</strong>
        <span
          className={`rounded px-2 py-1 text-xs ${badge(suggestion.status)}`}
        >
          {suggestion.status}
        </span>
      </header>
      <p className="mt-2 text-sm">
        AI 建议：{suggestion.suggested_points ?? "不评分"} /{" "}
        {suggestion.max_points}；置信度：
        {suggestion.confidence ?? "未提供"}
      </p>
      {suggestion.evidence_refs.length > 0 && (
        <p className="mt-1 text-sm">
          证据：
          {suggestion.evidence_refs.map((ref) => (
            <a key={ref} href={`#evidence-${ref}`} className="ml-1 underline">
              {ref}
            </a>
          ))}
        </p>
      )}
      {suggestion.missing_steps.length > 0 && (
        <p className="mt-1 text-sm">
          缺失步骤：{suggestion.missing_steps.join("；")}
        </p>
      )}
      {suggestion.detected_errors.length > 0 && (
        <p className="mt-1 text-sm text-red-800">
          错误分类：{suggestion.detected_errors.join("、")}
        </p>
      )}
      {suggestion.manual_review_reason && (
        <p className="mt-1 text-sm text-amber-900">
          人工复核：{suggestion.manual_review_reason}
        </p>
      )}
      <div className="mt-3 rounded border bg-white p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          教师决定（仅草稿）
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            aria-label={`${suggestion.criterion_stable_key} 教师分值`}
            type="number"
            min="0"
            max={suggestion.max_points}
            step="0.5"
            value={points}
            disabled={readOnly}
            onChange={(event) => setPoints(event.target.value)}
            className="w-28 rounded border px-2 py-1"
          />
          <input
            aria-label={`${suggestion.criterion_stable_key} 修改原因`}
            value={reason}
            disabled={readOnly}
            onChange={(event) => setReason(event.target.value)}
            placeholder="必填：采纳/修改/拒绝原因"
            className="min-w-64 flex-1 rounded border px-2 py-1"
          />
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={readOnly || suggestion.suggested_points == null}
            onClick={() => review("accepted").catch(() => onDone("保存失败"))}
            className="rounded border px-3 py-1"
          >
            采纳建议
          </button>
          <button
            type="button"
            disabled={readOnly || !points}
            onClick={() => review("modified").catch(() => onDone("保存失败"))}
            className="rounded border px-3 py-1"
          >
            修改后保存
          </button>
          <button
            type="button"
            disabled={readOnly}
            onClick={() => review("rejected").catch(() => onDone("保存失败"))}
            className="rounded border px-3 py-1"
          >
            拒绝建议
          </button>
          <button
            type="button"
            disabled={readOnly}
            onClick={() =>
              aiGradingApi
                .retryCriterion(job.id, suggestion.criterion_stable_key)
                .then(() => onDone("已创建新的单项 generation。"))
                .catch(() => onDone("单项重试失败"))
            }
            className="rounded border px-3 py-1"
          >
            单项重试
          </button>
        </div>
      </div>
    </article>
  );
}

export function AIGradingReview({
  answerId,
  rubricVersionId,
  finalized = false,
}: {
  answerId: string;
  rubricVersionId: string;
  finalized?: boolean;
}) {
  const [jobs, setJobs] = useState<AIScoringJob[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(
    () => aiGradingApi.listForAnswer(answerId).then(setJobs),
    [answerId],
  );
  useEffect(() => {
    load().catch(() => setMessage("无法加载 AI 建议。"));
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
  const readOnly = finalized || Boolean(current?.stale);

  return (
    <section
      aria-label="AI 分项评分建议"
      className="rounded-xl border-2 border-violet-300 bg-white p-4"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-violet-950">AI 分项建议</h2>
          <p className="text-sm text-slate-600">
            非正式成绩。确定性验证优先，教师决定与正式发布保持独立。
          </p>
        </div>
        <button
          type="button"
          disabled={finalized}
          onClick={() =>
            aiGradingApi
              .create(answerId, rubricVersionId)
              .then(load)
              .catch(() =>
                setMessage("无法创建 AI 任务，请检查 confirmed 输入。"),
              )
          }
          className="rounded bg-violet-700 px-3 py-2 text-white disabled:opacity-50"
        >
          生成新建议
        </button>
      </header>
      {message && (
        <p role="status" className="mt-2 text-sm">
          {message}
        </p>
      )}
      {finalized && (
        <p className="mt-2 rounded bg-slate-100 p-2 text-sm">
          已 Finalize：本区只读，不能创建或采纳建议。
        </p>
      )}
      {current ? (
        <>
          <div className="mt-3 grid gap-2 rounded bg-violet-50 p-3 text-sm md:grid-cols-3">
            <span>状态：{current.status}</span>
            <span>Provider：{current.provider}</span>
            <span>Generation：{current.generation}</span>
            <span>模型：{current.model ?? "未配置"}</span>
            <span>
              Token：{current.usage.input_tokens ?? 0} +{" "}
              {current.usage.output_tokens ?? 0}
            </span>
            <span>成本：{current.usage.estimated_cost ?? "未估算"}</span>
          </div>
          {current.provider === "unavailable" && (
            <p className="mt-3 rounded bg-amber-100 p-3">
              Provider unavailable：已安全转入教师人工评分。
            </p>
          )}
          {current.stale && (
            <p className="mt-3 rounded bg-amber-100 p-3">
              stale：此 generation 仅供审计，不可采纳。
            </p>
          )}
          <div className="mt-3 space-y-3">
            {current.suggestions.map((suggestion) => (
              <SuggestionCard
                key={suggestion.id}
                job={current}
                suggestion={suggestion}
                readOnly={readOnly}
                onDone={setMessage}
              />
            ))}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              学生反馈草稿
              <textarea
                value={studentFeedback}
                disabled={readOnly}
                onChange={(event) => setStudentFeedback(event.target.value)}
                className="mt-1 min-h-32 w-full rounded border p-2"
              />
            </label>
            <label className="text-sm">
              教师内部摘要
              <textarea
                value={teacherSummary}
                disabled={readOnly}
                onChange={(event) => setTeacherSummary(event.target.value)}
                className="mt-1 min-h-32 w-full rounded border p-2"
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
                .catch(() => setMessage("反馈保存失败"))
            }
            className="mt-2 rounded border px-3 py-2"
          >
            保存反馈草稿
          </button>
          {jobs.length > 1 && (
            <details className="mt-4 rounded border p-3">
              <summary>历史 generation</summary>
              {jobs.map((job) => (
                <p key={job.id} className="mt-1 text-sm">
                  #{job.generation} · {job.status} ·{" "}
                  {job.stale ? "stale" : "current"}
                </p>
              ))}
            </details>
          )}
        </>
      ) : (
        <p className="mt-3 text-sm">尚无 AI 建议。</p>
      )}
    </section>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Card } from "@/components/ui";
import { QuestionExtractionReview } from "@/components/question-extraction-review";
import {
  ApiError,
  assignmentGenerationApi,
  type AssignmentFieldSuggestion,
  type AssignmentGenerationCapabilities,
  type AssignmentFileAnalysis,
  type AssignmentPageAnalysis,
  type AssignmentRecord,
  type AssignmentDraftRevision,
  type AssignmentGenerationJob,
  type AssignmentGenerationStage,
} from "@/lib/api";

const ACTIVE = new Set([
  "queued",
  "analyzing",
  "processing_pages",
  "extracting_questions",
  "generating_rubrics",
  "validating",
]);
const TERMINAL = new Set([
  "review_required",
  "ready",
  "partial",
  "failed",
  "cancelled",
  "stale",
  "unavailable",
  "completed",
  "discarded",
]);
const STAGES: { key: AssignmentGenerationStage; label: string }[] = [
  { key: "analyzing", label: "分析输入" },
  { key: "processing_pages", label: "检查页面" },
  { key: "extracting_questions", label: "页面整理与题目抽取" },
  { key: "generating_rubrics", label: "生成答案与评分标准" },
  { key: "validating", label: "结构验证" },
];
const STATUS_LABEL: Record<string, string> = {
  queued: "等待 Worker",
  analyzing: "正在分析",
  processing_pages: "正在检查页面",
  extracting_questions: "正在编排题目抽取",
  generating_rubrics: "正在编排 Rubric",
  validating: "正在验证",
  review_required: "需要教师复核",
  ready: "草稿就绪",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  stale: "输入已变化",
  unavailable: "能力不可用",
  completed: "已完成",
  discarded: "结果已丢弃",
};
const FILE_ROLE_LABEL: Record<string, string> = {
  question_paper: "试卷",
  reference_answer: "参考答案",
  rubric: "评分标准",
  instructions: "作业说明",
  attachment: "其他附件",
  unknown: "尚未确定",
};
export function AssignmentGenerationPanel({
  assignmentId,
  assignment,
  onAssignmentChanged,
  onReviewInputsChanged,
}: {
  assignmentId: string;
  assignment?: AssignmentRecord;
  onAssignmentChanged?: () => Promise<void> | void;
  onReviewInputsChanged?: () => Promise<void> | void;
}) {
  const [jobs, setJobs] = useState<AssignmentGenerationJob[]>([]);
  const [capabilities, setCapabilities] =
    useState<AssignmentGenerationCapabilities | null>(null);
  const [revisions, setRevisions] = useState<AssignmentDraftRevision[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<AssignmentFieldSuggestion[]>(
    [],
  );
  const [fileAnalyses, setFileAnalyses] = useState<AssignmentFileAnalysis[]>(
    [],
  );
  const [pageAnalyses, setPageAnalyses] = useState<
    Record<string, AssignmentPageAnalysis[]>
  >({});
  const [fileChoices, setFileChoices] = useState<
    Record<string, { role: string; source: string }>
  >({});
  const observedJobStatus = useRef<{ id: string; status: string } | null>(null);
  const mountedRef = useRef(false);
  const current = jobs[0];
  const activeFileAnalyses = fileAnalyses.filter(
    (row) => row.analysis_status !== "superseded",
  );
  const needsRoleReview = (row: AssignmentFileAnalysis) =>
    row.analysis_status === "suggested" &&
    (row.suggested_role === "unknown" ||
      row.role_confidence < 0.7 ||
      row.warning_codes.includes("FILE_ROLE_CONFLICT_REVIEW_REQUIRED"));
  const fileAnalysisCounts = {
    confirmed: activeFileAnalyses.filter(
      (row) => row.analysis_status === "confirmed",
    ).length,
    automatic: activeFileAnalyses.filter(
      (row) => row.analysis_status === "suggested" && !needsRoleReview(row),
    ).length,
    needsReview: activeFileAnalyses.filter((row) => needsRoleReview(row))
      .length,
    stale: activeFileAnalyses.filter((row) => row.analysis_status === "stale")
      .length,
  };

  const load = useCallback(async () => {
    try {
      const [nextCapabilities, nextJobs, nextRevisions] = await Promise.all([
        assignmentGenerationApi.capabilities(),
        assignmentGenerationApi.listJobs(assignmentId),
        assignmentGenerationApi.listRevisions(assignmentId),
      ]);
      if (!mountedRef.current) return [];
      setCapabilities(nextCapabilities);
      setJobs(nextJobs);
      setRevisions(nextRevisions);
      const revisionId = nextJobs[0]?.revision?.id ?? nextRevisions[0]?.id;
      if (revisionId) {
        const [nextSuggestions, nextFiles] = await Promise.all([
          assignmentGenerationApi.listFieldSuggestions(revisionId),
          assignmentGenerationApi.listFileAnalyses(revisionId),
        ]);
        if (!mountedRef.current) return [];
        setSuggestions(nextSuggestions);
        setFileAnalyses(nextFiles);
        setFileChoices((old) => {
          const next = { ...old };
          for (const file of nextFiles) {
            next[file.id] ??= {
              role: file.teacher_confirmed_role ?? file.suggested_role,
              source:
                file.teacher_confirmed_answer_source ??
                (file.suggested_role === "reference_answer"
                  ? file.suggested_answer_source
                  : "not_applicable"),
            };
          }
          return next;
        });
        const pages = await Promise.all(
          nextFiles.map(
            async (file) =>
              [
                file.id,
                await assignmentGenerationApi.listPageAnalyses(file.id),
              ] as const,
          ),
        );
        if (!mountedRef.current) return [];
        setPageAnalyses(Object.fromEntries(pages));
      } else {
        setSuggestions([]);
        setFileAnalyses([]);
        setPageAnalyses({});
      }
      setError("");
      return nextJobs;
    } catch (reason) {
      if (!mountedRef.current) return [];
      setError(
        reason instanceof ApiError
          ? reason.message
          : "无法恢复 Codex 草稿生成任务",
      );
      return [];
    }
  }, [assignmentId]);

  const notifyReviewInputsChanged = useCallback(async () => {
    if (onReviewInputsChanged) await onReviewInputsChanged();
    else await onAssignmentChanged?.();
  }, [onAssignmentChanged, onReviewInputsChanged]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const next = current ? { id: current.id, status: current.status } : null;
    const previous = observedJobStatus.current;
    observedJobStatus.current = next;
    if (
      next &&
      previous?.id === next.id &&
      ACTIVE.has(previous.status) &&
      TERMINAL.has(next.status)
    ) {
      void notifyReviewInputsChanged();
    }
  }, [current, notifyReviewInputsChanged]);

  useEffect(() => {
    if (!current || !ACTIVE.has(current.status) || error) return;
    const timer = window.setTimeout(() => void load(), 2000);
    return () => window.clearTimeout(timer);
  }, [current, error, load]);

  const latestStages = useMemo(() => {
    const byStage = new Map<
      AssignmentGenerationStage,
      AssignmentGenerationJob["stages"][number]
    >();
    for (const row of current?.stages ?? []) {
      const previous = byStage.get(row.stage);
      if (!previous || previous.stage_generation < row.stage_generation) {
        byStage.set(row.stage, row);
      }
    }
    return byStage;
  }, [current]);
  const codexQuestionDraftReady =
    latestStages.get("extracting_questions")?.result_payload?.capability ===
    "codex_local";

  const act = async (
    operation: () => Promise<AssignmentGenerationJob>,
    notifyReviewInputs = false,
  ) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      if (notifyReviewInputs) await notifyReviewInputsChanged();
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "任务操作失败");
    } finally {
      setBusy(false);
    }
  };

  const review = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      await notifyReviewInputsChanged();
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "教师确认操作失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const valueText = (value: unknown) => {
    if (value == null) return "无法判断";
    return typeof value === "string" ? value : JSON.stringify(value);
  };

  const start = () =>
    act(
      () =>
        assignmentGenerationApi.start(assignmentId, {
          idempotency_key:
            globalThis.crypto?.randomUUID?.() ??
            `generation-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        }),
      true,
    );

  const risk = current?.revision?.risk_summary ?? {
    info: 0,
    warning: 0,
    blocking: 0,
  };

  return (
    <Card className="space-y-4 border-[var(--brand-200)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-bold">Codex 草稿生成</h2>
          <p className="mt-1 text-sm text-[var(--neutral-600)]">
            由 Codex
            一次生成题目、参考答案和评分标准草稿，不会直接发布。系统自动核对完整性，仅在异常时提示处理。
          </p>
        </div>
        <Button
          onClick={start}
          disabled={
            busy ||
            capabilities === null ||
            !capabilities.enabled ||
            !capabilities.teacher_start_allowed ||
            !capabilities.suggestion_only ||
            Boolean(current && ACTIVE.has(current.status))
          }
        >
          {current ? "重新生成完整草稿" : "生成完整草稿"}
        </Button>
      </div>

      {capabilities && (
        <div
          role="status"
          className="rounded-lg border border-[var(--neutral-200)] bg-[var(--neutral-50)] p-3 text-sm"
        >
          <div className="font-medium">
            当前草稿生成方式：Codex（由当前 Codex 任务执行）
          </div>
          <div className="mt-1 text-[var(--neutral-600)]">
            外部
            Provider：不使用；系统会先完成识别，再连续生成题目、答案和评分标准，不会自动发布。
          </div>
          <div className="mt-1 text-amber-700">
            教师只需处理异常并在最后确认发布；页面不会把未完成阶段显示为成功。
          </div>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-700"
        >
          {error}（轮询已停止，可点击重试）
          <Button
            className="ml-3"
            variant="outline"
            onClick={() => void load()}
          >
            重试
          </Button>
        </div>
      )}

      {current ? (
        <>
          <div className="grid gap-3 text-sm sm:grid-cols-4">
            <div>
              <span className="block text-[var(--neutral-500)]">
                Generation
              </span>
              {current.generation}
            </div>
            <div>
              <span className="block text-[var(--neutral-500)]">
                草稿 Revision
              </span>
              {current.revision?.revision ?? "—"}
            </div>
            <div aria-label="生成状态">
              <span className="block text-[var(--neutral-500)]">
                状态 / 进度
              </span>
              {STATUS_LABEL[current.status] ?? current.status} ·{" "}
              {current.progress}%
            </div>
            <div>
              <span className="block text-[var(--neutral-500)]">输入快照</span>
              <code>{current.source_snapshot_hash.slice(0, 12)}</code>
            </div>
          </div>
          <div
            aria-label="Codex 草稿生成进度"
            className="h-2 overflow-hidden rounded bg-[var(--neutral-100)]"
          >
            <div
              className="h-full bg-[var(--brand-600)]"
              style={{ width: `${current.progress}%` }}
            />
          </div>
          <div className="grid gap-2 md:grid-cols-5">
            {STAGES.map(({ key, label }) => {
              const row = latestStages.get(key);
              const canRetry =
                ["failed", "partial"].includes(current.status) &&
                Boolean(
                  row &&
                  ["failed", "unavailable", "discarded"].includes(row.status),
                );
              return (
                <div key={key} className="rounded-lg border p-3 text-sm">
                  <strong className="block">{label}</strong>
                  <span>
                    {row ? (STATUS_LABEL[row.status] ?? row.status) : "未开始"}
                  </span>
                  {row && (
                    <small className="block">尝试 {row.stage_generation}</small>
                  )}
                  {canRetry && (
                    <Button
                      className="mt-2"
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        act(() =>
                          assignmentGenerationApi.retryStage(current.id, key),
                        )
                      }
                    >
                      重试此阶段
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {current.provider_mode === "unavailable" &&
              (codexQuestionDraftReady ? (
                <strong className="text-emerald-700">
                  Codex 题目草稿已生成，等待教师确认
                </strong>
              ) : (
                <strong className="text-amber-700">等待 Codex 代生成</strong>
              ))}
            {ACTIVE.has(current.status) && (
              <Button
                variant="danger"
                disabled={busy}
                onClick={() =>
                  act(() => assignmentGenerationApi.cancel(current.id))
                }
              >
                请求取消
              </Button>
            )}
          </div>
          {current.issues.length > 0 && (
            <details className="rounded-lg border text-sm">
              <summary className="cursor-pointer rounded-lg px-3 py-3 font-medium hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
                生成记录/技术详情（{current.issues.length}）
              </summary>
              <p className="px-3 pb-2 text-[var(--neutral-600)]">
                历史记录：信息 {risk.info} · 警告 {risk.warning} · 阻断{" "}
                {risk.blocking}。这些数量不代表当前发布待办。
              </p>
              <ul className="grid gap-1 px-3 pb-3">
                {current.issues.map((item) => (
                  <li key={item.id}>
                    [{item.severity}] {item.code}：{item.message}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <details
            className="space-y-3 border-t pt-4"
            aria-label="基本信息建议"
          >
            <summary className="cursor-pointer rounded-lg px-3 py-3 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
              第一步 · 基本信息建议（
              {suggestions.filter((row) => row.status !== "superseded").length}
              ）
            </summary>
            <div className="mt-3">
              <p className="text-sm text-[var(--neutral-600)]">
                AI 建议不会自动覆盖教师内容；不会推荐班级，也不会设置截止时间。
              </p>
            </div>
            {suggestions.filter((row) => row.status !== "superseded").length ? (
              <div className="grid gap-3">
                {suggestions
                  .filter((row) => row.status !== "superseded")
                  .map((row) => (
                    <article
                      key={row.id}
                      className="rounded-lg border p-3 text-sm"
                    >
                      <div className="flex flex-wrap justify-between gap-2">
                        <strong>{row.field_name}</strong>
                        <span>
                          {Math.round(row.confidence * 100)}% · {row.status}
                        </span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap break-words">
                        AI 建议：{valueText(row.suggested_value)}
                      </p>
                      <ul className="mt-1 text-xs text-[var(--neutral-600)]">
                        {row.evidence.map((evidence, index) => (
                          <li key={`${evidence.reference_id}-${index}`}>
                            {evidence.summary}
                          </li>
                        ))}
                      </ul>
                      {row.status === "suggested" && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {row.field_name === "total_score" ? (
                            <Button
                              variant="outline"
                              disabled={busy || !assignment}
                              onClick={() => {
                                const raw = window.prompt(
                                  "请核对证据并输入确认后的总分",
                                  valueText(row.normalized_value),
                                );
                                if (raw == null) return;
                                const score = Number(raw);
                                if (!Number.isFinite(score) || score <= 0)
                                  return;
                                if (!window.confirm("确认将该值写入草稿总分？"))
                                  return;
                                void review(() =>
                                  assignmentGenerationApi.confirmTotalScore(
                                    row.id,
                                    {
                                      expected_teacher_edit_version:
                                        row.teacher_edit_version,
                                      expected_assignment_updated_at:
                                        assignment!.updated_at,
                                      confirmed_value: score,
                                      explicit_confirmation: true,
                                    },
                                  ),
                                );
                              }}
                            >
                              确认总分
                            </Button>
                          ) : (
                            <Button
                              variant="outline"
                              disabled={busy || row.normalized_value == null}
                              onClick={() =>
                                void review(() =>
                                  assignmentGenerationApi.dispositionField(
                                    row.id,
                                    {
                                      action: "accept",
                                      expected_teacher_edit_version:
                                        row.teacher_edit_version,
                                      expected_assignment_updated_at:
                                        assignment?.updated_at,
                                    },
                                  ),
                                )
                              }
                            >
                              接受
                            </Button>
                          )}
                          {row.field_name !== "total_score" && (
                            <Button
                              variant="outline"
                              disabled={busy}
                              onClick={() => {
                                const value = window.prompt(
                                  "请输入教师修改值",
                                  valueText(row.normalized_value),
                                );
                                if (value == null) return;
                                void review(() =>
                                  assignmentGenerationApi.dispositionField(
                                    row.id,
                                    {
                                      action: "modify",
                                      expected_teacher_edit_version:
                                        row.teacher_edit_version,
                                      expected_assignment_updated_at:
                                        assignment?.updated_at,
                                      teacher_value: value,
                                    },
                                  ),
                                );
                              }}
                            >
                              修改
                            </Button>
                          )}
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void review(() =>
                                assignmentGenerationApi.dispositionField(
                                  row.id,
                                  {
                                    action: "reject",
                                    expected_teacher_edit_version:
                                      row.teacher_edit_version,
                                  },
                                ),
                              )
                            }
                          >
                            拒绝
                          </Button>
                        </div>
                      )}
                    </article>
                  ))}
              </div>
            ) : (
              <p className="text-sm">当前没有可用字段建议，请由教师填写。</p>
            )}
          </details>

          <details
            id="generation-file-analysis"
            className="scroll-mt-6 space-y-3 border-t pt-4"
            aria-label="文件分析"
          >
            <summary className="cursor-pointer rounded-lg px-3 py-3 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
              文件与页面（{activeFileAnalyses.length} 个文件：自动识别{" "}
              {fileAnalysisCounts.automatic}，需要选择{" "}
              {fileAnalysisCounts.needsReview}
              {fileAnalysisCounts.confirmed > 0
                ? `，已修改 ${fileAnalysisCounts.confirmed}`
                : ""}
              {fileAnalysisCounts.stale > 0
                ? `，已过期 ${fileAnalysisCounts.stale}`
                : ""}
              ）
            </summary>
            <div className="mt-3">
              <p className="text-sm text-[var(--neutral-600)]">
                系统会自动识别文件用途并直接生成。仅在无法判断或用途冲突时需要选择；识别结果仍可修改。
              </p>
              {fileAnalysisCounts.stale > 0 && (
                <div
                  role="alert"
                  className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3"
                >
                  <div>
                    <strong className="text-sm text-red-900">
                      旧分析已过期，不能算作已确认
                    </strong>
                    <p className="text-xs text-red-700">
                      作业总分、页面或其他生成输入已修改，请基于最新内容重新分析后再确认。
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    disabled={
                      busy ||
                      capabilities === null ||
                      !capabilities.enabled ||
                      !capabilities.teacher_start_allowed ||
                      !capabilities.suggestion_only ||
                      Boolean(current && ACTIVE.has(current.status))
                    }
                    onClick={start}
                  >
                    重新分析最新内容
                  </Button>
                </div>
              )}
            </div>
            {activeFileAnalyses.map((file) => {
              const choice = fileChoices[file.id] ?? {
                role: file.suggested_role,
                source: "not_applicable",
              };
              const requiresRoleReview = needsRoleReview(file);
              const pages = pageAnalyses[file.id] ?? [];
              const visibleWarningCodes = file.warning_codes.filter(
                (code) =>
                  !code.includes("ANSWER_SOURCE") &&
                  !code.includes("FILE_ROLE_") &&
                  !code.includes("ROLE_REVIEW_REQUIRED"),
              );
              return (
                <article
                  key={file.id}
                  className="space-y-2 rounded-lg border p-3 text-sm"
                >
                  <div className="flex flex-wrap justify-between gap-2">
                    <strong>{file.file_name ?? file.stored_file_id}</strong>
                    <span>
                      {file.detected_mime_type} · {file.file_size ?? "—"} bytes
                      · {file.page_count ?? "—"} 页
                    </span>
                  </div>
                  <p>
                    checksum：<code>{file.checksum.slice(0, 12)}</code> · 状态{" "}
                    {file.analysis_status === "suggested"
                      ? requiresRoleReview
                        ? "需要选择用途"
                        : "已自动识别"
                      : file.analysis_status === "confirmed"
                        ? "已确认"
                        : file.analysis_status === "stale"
                          ? "已过期"
                          : file.analysis_status}
                  </p>
                  {file.analysis_status === "stale" && (
                    <p className="rounded-lg bg-red-50 p-2 text-red-800">
                      此文件的旧分析已过期。请点击上方“重新分析最新内容”，生成最新分析后再确认。
                    </p>
                  )}
                  <p>
                    文件用途：
                    {FILE_ROLE_LABEL[file.suggested_role] ??
                      file.suggested_role}
                    （{Math.round(file.role_confidence * 100)}%）
                  </p>
                  {file.analysis_status !== "suggested" &&
                    file.teacher_confirmed_role && (
                      <p className="rounded-lg bg-emerald-50 p-2 font-medium text-emerald-800">
                        ✓ 已确认：此文件是
                        {FILE_ROLE_LABEL[file.teacher_confirmed_role] ??
                          file.teacher_confirmed_role}
                      </p>
                    )}
                  {file.duplicate_of_file_id && (
                    <p className="text-amber-700">
                      重复关系：{file.duplicate_of_file_id}
                    </p>
                  )}
                  {!!visibleWarningCodes.length && (
                    <p className="text-amber-700">
                      风险：{visibleWarningCodes.join("、")}
                    </p>
                  )}
                  {!!pages.length && (
                    <p>
                      页面：
                      {pages
                        .map(
                          (page) =>
                            `#${page.paper_page_id.slice(0, 6)} ${page.warning_codes.join("/") || page.status}`,
                        )
                        .join("；")}
                    </p>
                  )}
                  {file.analysis_status === "suggested" &&
                    (requiresRoleReview ? (
                      <div className="grid gap-2 rounded-lg bg-amber-50 p-3 sm:grid-cols-2">
                        <label>
                          选择文件用途
                          <select
                            aria-label={`${file.file_name ?? file.id} 文件角色`}
                            className="mt-1 w-full rounded border p-2"
                            value={choice.role}
                            onChange={(event) =>
                              setFileChoices((old) => ({
                                ...old,
                                [file.id]: {
                                  ...choice,
                                  role: event.target.value,
                                  source:
                                    event.target.value === "reference_answer"
                                      ? choice.source === "not_applicable"
                                        ? file.suggested_answer_source ===
                                          "not_applicable"
                                          ? "unknown"
                                          : file.suggested_answer_source
                                        : choice.source
                                      : "not_applicable",
                                },
                              }))
                            }
                          >
                            {[
                              "question_paper",
                              "reference_answer",
                              "rubric",
                              "instructions",
                              "attachment",
                              "unknown",
                            ].map((role) => (
                              <option key={role} value={role}>
                                {FILE_ROLE_LABEL[role]}
                              </option>
                            ))}
                          </select>
                        </label>
                        <Button
                          className="self-end"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            void review(() =>
                              assignmentGenerationApi.confirmFileAnalysis(
                                file.id,
                                {
                                  expected_teacher_edit_version:
                                    file.teacher_edit_version,
                                  confirmed_role: choice.role,
                                  confirmed_answer_source: choice.source,
                                },
                              ),
                            )
                          }
                        >
                          保存文件用途
                        </Button>
                      </div>
                    ) : (
                      <details>
                        <summary className="cursor-pointer text-sm text-[var(--brand-700)]">
                          修改用途
                        </summary>
                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                          <label>
                            文件用途
                            <select
                              aria-label={`${file.file_name ?? file.id} 文件角色`}
                              className="mt-1 w-full rounded border p-2"
                              value={choice.role}
                              onChange={(event) =>
                                setFileChoices((old) => ({
                                  ...old,
                                  [file.id]: {
                                    ...choice,
                                    role: event.target.value,
                                    source:
                                      event.target.value === "reference_answer"
                                        ? file.suggested_answer_source ===
                                          "not_applicable"
                                          ? "unknown"
                                          : file.suggested_answer_source
                                        : "not_applicable",
                                  },
                                }))
                              }
                            >
                              {[
                                "question_paper",
                                "reference_answer",
                                "rubric",
                                "instructions",
                                "attachment",
                                "unknown",
                              ].map((role) => (
                                <option key={role} value={role}>
                                  {FILE_ROLE_LABEL[role]}
                                </option>
                              ))}
                            </select>
                          </label>
                          <Button
                            className="self-end"
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void review(() =>
                                assignmentGenerationApi.confirmFileAnalysis(
                                  file.id,
                                  {
                                    expected_teacher_edit_version:
                                      file.teacher_edit_version,
                                    confirmed_role: choice.role,
                                    confirmed_answer_source: choice.source,
                                  },
                                ),
                              )
                            }
                          >
                            保存修改
                          </Button>
                        </div>
                      </details>
                    ))}
                </article>
              );
            })}
          </details>
        </>
      ) : (
        <p className="text-sm text-[var(--neutral-600)]">尚未创建生成任务。</p>
      )}

      {current?.revision && (
        <QuestionExtractionReview
          revision={current.revision}
          onChanged={() =>
            void (async () => {
              await notifyReviewInputsChanged();
              await load();
            })()
          }
        />
      )}

      <details>
        <summary className="cursor-pointer rounded-lg px-3 py-3 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
          草稿历史版本（{revisions.length}）
        </summary>
        <ol className="mt-2 grid gap-2 text-sm">
          {revisions.map((revision) => (
            <li key={revision.id} className="rounded-lg border p-3">
              Revision {revision.revision} · {revision.status} · Generation{" "}
              {String(revision.draft_payload.generation ?? "—")} · 快照{" "}
              <code>{revision.source_snapshot_hash.slice(0, 12)}</code>
            </li>
          ))}
        </ol>
      </details>
    </Card>
  );
}

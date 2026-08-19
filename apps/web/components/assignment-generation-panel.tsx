"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Card } from "@/components/ui";
import { QuestionExtractionReview } from "@/components/question-extraction-review";
import { QuestionStructureReviewPanel } from "@/components/question-structure-review";
import {
  ApiError,
  assignmentGenerationApi,
  assignmentsApi,
  type AssignmentFieldSuggestion,
  type AssignmentGenerationCapabilities,
  type AssignmentFileAnalysis,
  type AssignmentRecord,
  type AssignmentDraftRevision,
  type AssignmentGenerationJob,
  type AssignmentGenerationStage,
  type TextbookLibrary,
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
  { key: "extracting_questions", label: "可选：AI 整理页面与抽取题目" },
  { key: "generating_rubrics", label: "可选：AI 生成答案与评分标准" },
  { key: "validating", label: "结构验证" },
];
const STATUS_LABEL: Record<string, string> = {
  queued: "等待处理",
  analyzing: "正在分析",
  processing_pages: "正在检查页面",
  extracting_questions: "正在生成题目",
  generating_rubrics: "正在生成答案与评分标准",
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
  question_paper: "题目",
  reference_answer: "答案",
  question_and_answer: "题目和答案",
  textbook: "教材",
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
  onFieldSuggestionsChanged,
  onContinueManually,
}: {
  assignmentId: string;
  assignment?: AssignmentRecord;
  onAssignmentChanged?: () => Promise<void> | void;
  onReviewInputsChanged?: () => Promise<void> | void;
  onFieldSuggestionsChanged?: (
    suggestions: AssignmentFieldSuggestion[],
  ) => void;
  onContinueManually?: () => void;
}) {
  const [jobs, setJobs] = useState<AssignmentGenerationJob[]>([]);
  const [capabilities, setCapabilities] =
    useState<AssignmentGenerationCapabilities | null>(null);
  const [revisions, setRevisions] = useState<AssignmentDraftRevision[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [deletingFileId, setDeletingFileId] = useState("");
  const [fileAnalyses, setFileAnalyses] = useState<AssignmentFileAnalysis[]>(
    [],
  );
  const [fileChoices, setFileChoices] = useState<
    Record<string, { role: string; source: string }>
  >({});
  const [textbookLibraries, setTextbookLibraries] = useState<TextbookLibrary[]>(
    [],
  );
  const [selectedTextbookLibraryIds, setSelectedTextbookLibraryIds] = useState<
    string[]
  >([]);
  const observedJobStatus = useRef<{ id: string; status: string } | null>(null);
  const mountedRef = useRef(false);
  const current = jobs[0];
  const hasConfirmedTotalScore = Number(assignment?.total_score ?? 0) > 0;
  const visibleIssues = (current?.issues ?? []).filter((item) => {
    if (item.resolution_status !== "open") return false;
    if (["PROVIDER_UNAVAILABLE", "CODEX_DRAFT_PENDING"].includes(item.code))
      return false;
    if (
      hasConfirmedTotalScore &&
      ["TOTAL_SCORE_UNCONFIRMED", "TOTAL_SCORE_CONFLICT"].includes(item.code)
    )
      return false;
    if (
      item.code === "MANUAL_REVIEW_REQUIRED" &&
      item.message === "教师必须检查并确认所有草稿内容；Worker 不能发布作业"
    )
      return false;
    return item.code !== "BASIC_INFO_LOW_CONFIDENCE";
  });
  const activeFileAnalyses = fileAnalyses.filter(
    (row) => row.analysis_status !== "superseded",
  );
  const needsRoleReview = (row: AssignmentFileAnalysis) =>
    row.analysis_status === "suggested";
  const fileAnalysisCounts = {
    confirmed: activeFileAnalyses.filter(
      (row) => row.analysis_status === "confirmed",
    ).length,
    needsReview: activeFileAnalyses.filter((row) => needsRoleReview(row))
      .length,
    stale: activeFileAnalyses.filter((row) => row.analysis_status === "stale")
      .length,
  };

  const load = useCallback(async () => {
    try {
      const [
        nextCapabilities,
        nextJobs,
        nextRevisions,
        nextTextbookLibraries,
        nextTextbookSelections,
      ] = await Promise.all([
        assignmentGenerationApi.capabilities(),
        assignmentGenerationApi.listJobs(assignmentId),
        assignmentGenerationApi.listRevisions(assignmentId),
        assignmentGenerationApi.listTextbookLibraries(),
        assignmentGenerationApi.listTextbookLibrarySelections(assignmentId),
      ]);
      if (!mountedRef.current) return [];
      setCapabilities(nextCapabilities);
      setJobs(nextJobs);
      setRevisions(nextRevisions);
      setTextbookLibraries(nextTextbookLibraries);
      setSelectedTextbookLibraryIds(nextTextbookSelections);
      const revisionId = nextJobs[0]?.revision?.id ?? nextRevisions[0]?.id;
      if (revisionId) {
        const [nextSuggestions, nextFiles] = await Promise.all([
          assignmentGenerationApi.listFieldSuggestions(revisionId),
          assignmentGenerationApi.listFileAnalyses(revisionId),
        ]);
        if (!mountedRef.current) return [];
        onFieldSuggestionsChanged?.(
          hasConfirmedTotalScore
            ? nextSuggestions.filter((row) => row.field_name !== "total_score")
            : nextSuggestions,
        );
        setFileAnalyses(nextFiles);
        setFileChoices((old) => {
          const next = { ...old };
          for (const file of nextFiles) {
            next[file.id] ??= {
              role: file.teacher_confirmed_role ?? file.suggested_role,
              source:
                file.teacher_confirmed_answer_source ??
                (["reference_answer", "question_and_answer"].includes(
                  file.suggested_role,
                )
                  ? file.suggested_answer_source
                  : "not_applicable"),
            };
          }
          return next;
        });
      } else {
        onFieldSuggestionsChanged?.([]);
        setFileAnalyses([]);
      }
      setError("");
      return nextJobs;
    } catch (reason) {
      if (!mountedRef.current) return [];
      setError(
        reason instanceof ApiError ? reason.message : "无法恢复草稿生成任务",
      );
      return [];
    }
  }, [assignmentId, hasConfirmedTotalScore, onFieldSuggestionsChanged]);

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
  const codexUnavailable = (
    ["extracting_questions", "generating_rubrics"] as const
  ).some((stage) => latestStages.get(stage)?.status === "unavailable");
  const configuredProviderReady =
    capabilities?.provider_status === "available" &&
    capabilities.provider !== "unavailable";
  const providerChanged = Boolean(
    current &&
    configuredProviderReady &&
    current.provider_mode !== capabilities?.provider,
  );
  const codexStageStatus = (
    stage: AssignmentGenerationStage,
    row: AssignmentGenerationJob["stages"][number] | undefined,
  ) => {
    if (!row) return "未开始";
    if (
      ["extracting_questions", "generating_rubrics"].includes(stage) &&
      row.status === "unavailable"
    )
      return providerChanged ? "旧任务未启用 AI" : "可跳过（AI 辅助暂不可用）";
    return STATUS_LABEL[row.status] ?? row.status;
  };

  const act = async (
    operation: () => Promise<AssignmentGenerationJob>,
    notifyReviewInputs = false,
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
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

  const saveTextbookLibraries = async () => {
    const revision = current?.revision ?? revisions[0];
    if (!revision) return;
    await review(() =>
      assignmentGenerationApi.replaceTextbookLibrarySelections(assignmentId, {
        draft_revision_id: revision.id,
        expected_draft_revision_edit_version: revision.teacher_edit_version,
        expected_source_snapshot: revision.source_snapshot_hash,
        library_ids: selectedTextbookLibraryIds,
      }),
    );
  };

  const deleteSourceFile = async (file: AssignmentFileAnalysis) => {
    const name = file.file_name ?? "此文件";
    if (
      busy ||
      !window.confirm(
        `确定删除“${name}”吗？对应页面也会删除，之后需要重新整理。`,
      )
    )
      return;
    setBusy(true);
    setDeletingFileId(file.stored_file_id);
    setError("");
    setNotice("");
    try {
      await assignmentsApi.removeFile(assignmentId, file.stored_file_id);
      await notifyReviewInputsChanged();
      await load();
      setNotice("文件已删除，请重新整理。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "文件删除失败");
    } finally {
      setDeletingFileId("");
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-bold">整理试卷</h2>
        {current ? (
          ACTIVE.has(current.status) ? (
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                act(() => assignmentGenerationApi.cancel(current.id))
              }
            >
              停止整理
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={start}
              disabled={
                busy ||
                capabilities === null ||
                !capabilities.enabled ||
                !capabilities.teacher_start_allowed ||
                !capabilities.suggestion_only
              }
            >
              重新整理
            </Button>
          )
        ) : (
          <Button
            onClick={start}
            disabled={
              busy ||
              capabilities === null ||
              !capabilities.enabled ||
              !capabilities.teacher_start_allowed ||
              !capabilities.suggestion_only
            }
          >
            开始整理
          </Button>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
          <Button
            className="ml-3"
            variant="outline"
            onClick={() => void load()}
          >
            重试
          </Button>
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          {notice}
        </div>
      )}

      {current ? (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <strong>
              {codexQuestionDraftReady
                ? "题目已整理，等待核对"
                : providerChanged
                  ? "本地 AI 已可用，请重新整理"
                  : current.status === "partial" && codexUnavailable
                    ? "可继续手动核对"
                    : (STATUS_LABEL[current.status] ?? current.status)}
            </strong>
            <div
              aria-label="草稿生成进度"
              className="h-1.5 min-w-28 flex-1 overflow-hidden rounded bg-[var(--neutral-100)]"
            >
              <div
                className="h-full bg-[var(--brand-600)]"
                style={{ width: `${current.progress}%` }}
              />
            </div>
            <span className="text-xs text-[var(--neutral-600)]">
              {current.progress}%
            </span>
          </div>
          {codexUnavailable && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-950">
              <p>
                {providerChanged
                  ? "本地 AI 已经可用。当前记录来自旧配置，请重新整理以生成新的题目、答案和评分标准建议。"
                  : "AI 辅助不会阻塞作业编辑。你可以先手动整理题目、答案和评分标准，需要时再回来重试。"}
              </p>
              <div className="flex flex-wrap gap-2">
                {providerChanged && (
                  <Button disabled={busy} onClick={start}>
                    使用本地 AI 重新整理
                  </Button>
                )}
                {onContinueManually && (
                  <Button variant="outline" onClick={onContinueManually}>
                    不等 AI，手动核对
                  </Button>
                )}
              </div>
            </div>
          )}
          <section aria-label="处理详情" className="text-sm">
            <span className="sr-only">
              {STATUS_LABEL[current.status] ?? current.status}
            </span>
            <div className="rounded-lg border border-[var(--neutral-300)] bg-[var(--neutral-50)] p-3">
              <div className="grid gap-2 md:grid-cols-5">
                {STAGES.map(({ key, label }) => {
                  const row = latestStages.get(key);
                  const canRetry =
                    !providerChanged &&
                    ["failed", "partial"].includes(current.status) &&
                    Boolean(
                      row &&
                      ["failed", "unavailable", "discarded"].includes(
                        row.status,
                      ),
                    );
                  return (
                    <div key={key} className="rounded-lg border p-2 text-sm">
                      <strong className="block">{label}</strong>
                      <span>{codexStageStatus(key, row)}</span>
                      {canRetry && (
                        <Button
                          className="mt-2"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            act(() =>
                              assignmentGenerationApi.retryStage(
                                current.id,
                                key,
                              ),
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
            </div>
          </section>
          {visibleIssues.length > 0 && (
            <details className="rounded-lg border text-sm">
              <summary className="cursor-pointer rounded-lg px-3 py-2 font-medium hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
                需要处理的问题（{visibleIssues.length}）
              </summary>
              <ul className="grid gap-1 px-3 pb-3">
                {visibleIssues.map((item) => (
                  <li key={item.id}>
                    {item.code === "TOTAL_SCORE_UNCONFIRMED"
                      ? "请确认作业总分"
                      : item.message}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {textbookLibraries.length > 0 && (
            <details className="border-t pt-3">
              <summary className="cursor-pointer font-semibold">
                教材来源
                {selectedTextbookLibraryIds.length > 0
                  ? `（已选 ${selectedTextbookLibraryIds.length} 册）`
                  : ""}
              </summary>
              <div className="mt-3 space-y-3 rounded-lg bg-[var(--neutral-50)] p-3">
                <div className="space-y-2">
                  {textbookLibraries.map((library) => (
                    <label key={library.id} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={selectedTextbookLibraryIds.includes(
                          library.id,
                        )}
                        onChange={(event) =>
                          setSelectedTextbookLibraryIds((old) =>
                            event.target.checked
                              ? [...old, library.id]
                              : old.filter((id) => id !== library.id),
                          )
                        }
                      />
                      <span>
                        {library.title}
                        {library.volume_label ? ` ${library.volume_label}` : ""}
                      </span>
                    </label>
                  ))}
                </div>
                <Button
                  variant="outline"
                  disabled={busy || !(current?.revision ?? revisions[0])}
                  onClick={() => void saveTextbookLibraries()}
                >
                  保存教材来源
                </Button>
                <p className="text-xs text-[var(--neutral-600)]">
                  系统会在解答可用时自动给出最可信的一处，仍需教师确认。
                </p>
              </div>
            </details>
          )}

          <details
            id="generation-file-analysis"
            className="scroll-mt-6 space-y-3 border-t pt-1 open:pt-3"
            aria-label="文件分析"
            open={
              fileAnalysisCounts.needsReview > 0 || fileAnalysisCounts.stale > 0
            }
          >
            <summary className="cursor-pointer rounded-lg py-2 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
              上传文件用途确认（{activeFileAnalyses.length} 个文件：需要确认{" "}
              {fileAnalysisCounts.needsReview}
              {fileAnalysisCounts.confirmed > 0
                ? `，已确认 ${fileAnalysisCounts.confirmed}`
                : ""}
              {fileAnalysisCounts.stale > 0
                ? `，已过期 ${fileAnalysisCounts.stale}`
                : ""}
              ）
            </summary>
            <div className="mt-3">
              <p className="text-sm text-[var(--neutral-600)]">
                每个文件只需确认是题目还是答案。系统建议仅供参考，未经教师确认不会用于生成题目。
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
              const suggestedRole = [
                "question_paper",
                "reference_answer",
                "question_and_answer",
              ].includes(file.suggested_role)
                ? file.suggested_role
                : "";
              const choice = fileChoices[file.id] ?? {
                role: suggestedRole,
                source: "not_applicable",
              };
              const requiresRoleReview = needsRoleReview(file);
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
                    <div className="flex items-center gap-2">
                      <span>{file.page_count ?? "—"} 页</span>
                      {file.analysis_status !== "stale" && (
                        <Button
                          variant="danger"
                          className="min-h-8 px-3 py-1 text-xs"
                          loading={deletingFileId === file.stored_file_id}
                          disabled={busy}
                          aria-label={`删除 ${file.file_name ?? "文件"}`}
                          onClick={() => void deleteSourceFile(file)}
                        >
                          删除文件
                        </Button>
                      )}
                    </div>
                  </div>
                  {file.analysis_status === "stale" && (
                    <p>内容已变化，请重新处理</p>
                  )}
                  {file.analysis_status === "stale" && (
                    <p className="rounded-lg bg-red-50 p-2 text-red-800">
                      此文件的旧分析已过期。请点击上方“重新分析最新内容”，生成最新分析后再确认。
                    </p>
                  )}
                  <p>
                    {requiresRoleReview && "需要选择文件用途 · "}
                    用途：
                    {FILE_ROLE_LABEL[
                      file.teacher_confirmed_role ?? file.suggested_role
                    ] ??
                      file.teacher_confirmed_role ??
                      file.suggested_role}
                    {requiresRoleReview &&
                      `（${Math.round(file.role_confidence * 100)}%）`}
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
                    <p className="text-amber-700">检测到重复文件</p>
                  )}
                  {!!visibleWarningCodes.length && (
                    <p className="text-amber-700">
                      发现 {visibleWarningCodes.length} 项需要核对的问题
                    </p>
                  )}
                  {file.analysis_status !== "stale" &&
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
                                  source: [
                                    "reference_answer",
                                    "question_and_answer",
                                  ].includes(event.target.value)
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
                            <option value="" disabled>
                              请选择题目、答案或二者都有
                            </option>
                            {[
                              "question_paper",
                              "reference_answer",
                              "question_and_answer",
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
                          disabled={busy || !choice.role}
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
                        <p className="mt-2 text-xs text-[var(--neutral-600)]">
                          如用途选错，可在这里更正；更正后需重新整理，旧结果不会继续使用。
                        </p>
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
                                    source: [
                                      "reference_answer",
                                      "question_and_answer",
                                    ].includes(event.target.value)
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
                                "question_and_answer",
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
      ) : null}

      {current?.revision && (
        <>
          <QuestionExtractionReview
            revision={current.revision}
            onChanged={() =>
              void (async () => {
                await notifyReviewInputsChanged();
                await load();
              })()
            }
          />
          <QuestionStructureReviewPanel assignmentId={assignmentId} />
        </>
      )}

      <details className="border-t pt-1 open:pt-3">
        <summary className="cursor-pointer rounded-lg px-3 py-2 font-semibold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
          历史记录（{revisions.length}）
        </summary>
        <ol className="mt-2 grid gap-2 text-sm">
          {revisions.map((revision) => (
            <li key={revision.id} className="rounded-lg border p-3">
              版本 {revision.revision} ·
              {STATUS_LABEL[revision.status] ?? revision.status}
            </li>
          ))}
        </ol>
      </details>
    </Card>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
const STAGES: { key: AssignmentGenerationStage; label: string }[] = [
  { key: "analyzing", label: "分析输入" },
  { key: "processing_pages", label: "检查页面" },
  { key: "extracting_questions", label: "页面整理与题目抽取" },
  { key: "generating_rubrics", label: "生成 Rubric（占位）" },
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

export function AssignmentGenerationPanel({
  assignmentId,
  assignment,
  onAssignmentChanged,
}: {
  assignmentId: string;
  assignment?: AssignmentRecord;
  onAssignmentChanged?: () => Promise<void> | void;
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
  const current = jobs[0];

  const load = useCallback(async () => {
    try {
      const [nextCapabilities, nextJobs, nextRevisions] = await Promise.all([
        assignmentGenerationApi.capabilities(),
        assignmentGenerationApi.listJobs(assignmentId),
        assignmentGenerationApi.listRevisions(assignmentId),
      ]);
      setCapabilities(nextCapabilities);
      setJobs(nextJobs);
      setRevisions(nextRevisions);
      const revisionId = nextJobs[0]?.revision?.id ?? nextRevisions[0]?.id;
      if (revisionId) {
        const [nextSuggestions, nextFiles] = await Promise.all([
          assignmentGenerationApi.listFieldSuggestions(revisionId),
          assignmentGenerationApi.listFileAnalyses(revisionId),
        ]);
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
        setPageAnalyses(Object.fromEntries(pages));
      } else {
        setSuggestions([]);
        setFileAnalyses([]);
        setPageAnalyses({});
      }
      setError("");
      return nextJobs;
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "无法恢复 Codex 草稿生成任务",
      );
      return [];
    }
  }, [assignmentId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const act = async (operation: () => Promise<AssignmentGenerationJob>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
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
      await onAssignmentChanged?.();
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
    act(() =>
      assignmentGenerationApi.start(assignmentId, {
        idempotency_key:
          globalThis.crypto?.randomUUID?.() ??
          `generation-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      }),
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
            生成可编辑草稿，不能直接发布作业。班级、截止时间、总分、答案与各版本仍须教师确认。
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
          {current ? "重新生成新版本" : "启动生成任务"}
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
            外部 Provider：不使用；仅生成建议草稿，不会自动发布。
          </div>
          <div className="mt-1 text-amber-700">
            Codex 生成结果仍需教师逐项确认；页面不会伪造 Provider 已完成。
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
            <span>风险：信息 {risk.info}</span>
            <span>警告 {risk.warning}</span>
            <span>阻断 {risk.blocking}</span>
            {current.provider_mode === "unavailable" && (
              <strong className="text-amber-700">等待 Codex 代生成</strong>
            )}
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
            <ul className="grid gap-1 text-sm">
              {current.issues.map((item) => (
                <li key={item.id}>
                  [{item.severity}] {item.code}：{item.message}
                </li>
              ))}
            </ul>
          )}

          <section
            className="space-y-3 border-t pt-4"
            aria-label="基本信息建议"
          >
            <div>
              <h3 className="font-semibold">第一步 · 基本信息建议</h3>
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
          </section>

          <section className="space-y-3 border-t pt-4" aria-label="文件分析">
            <div>
              <h3 className="font-semibold">第二步 · 文件与页面异常分析</h3>
              <p className="text-sm text-[var(--neutral-600)]">
                文件角色需由教师确认。AI/第三方答案不会被标记为官方答案。分析不会删除或发布文件。
              </p>
            </div>
            {fileAnalyses
              .filter((row) => row.analysis_status !== "superseded")
              .map((file) => {
                const choice = fileChoices[file.id] ?? {
                  role: file.suggested_role,
                  source: "not_applicable",
                };
                const pages = pageAnalyses[file.id] ?? [];
                return (
                  <article
                    key={file.id}
                    className="space-y-2 rounded-lg border p-3 text-sm"
                  >
                    <div className="flex flex-wrap justify-between gap-2">
                      <strong>{file.file_name ?? file.stored_file_id}</strong>
                      <span>
                        {file.detected_mime_type} · {file.file_size ?? "—"}{" "}
                        bytes · {file.page_count ?? "—"} 页
                      </span>
                    </div>
                    <p>
                      checksum：<code>{file.checksum.slice(0, 12)}</code> · 状态{" "}
                      {file.analysis_status}
                    </p>
                    <p>
                      建议角色：{file.suggested_role}（
                      {Math.round(file.role_confidence * 100)}%） ·
                      建议答案来源：{file.suggested_answer_source}
                    </p>
                    {file.duplicate_of_file_id && (
                      <p className="text-amber-700">
                        重复关系：{file.duplicate_of_file_id}
                      </p>
                    )}
                    {!!file.warning_codes.length && (
                      <p className="text-amber-700">
                        风险：{file.warning_codes.join("、")}
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
                    {file.analysis_status === "suggested" && (
                      <div className="grid gap-2 sm:grid-cols-3">
                        <label>
                          确认文件角色
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
                                      ? choice.source
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
                              <option key={role}>{role}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          确认答案来源
                          <select
                            aria-label={`${file.file_name ?? file.id} 答案来源`}
                            className="mt-1 w-full rounded border p-2"
                            value={choice.source}
                            disabled={choice.role !== "reference_answer"}
                            onChange={(event) =>
                              setFileChoices((old) => ({
                                ...old,
                                [file.id]: {
                                  ...choice,
                                  source: event.target.value,
                                },
                              }))
                            }
                          >
                            {[
                              "teacher_official",
                              "publisher_official",
                              "teacher_provided",
                              "third_party",
                              "ai_generated",
                              "unknown",
                              "not_applicable",
                            ].map((source) => (
                              <option key={source}>{source}</option>
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
                          确认文件分析
                        </Button>
                      </div>
                    )}
                  </article>
                );
              })}
          </section>
        </>
      ) : (
        <p className="text-sm text-[var(--neutral-600)]">尚未创建生成任务。</p>
      )}

      {current?.revision && (
        <QuestionExtractionReview
          revision={current.revision}
          onChanged={() => void load()}
        />
      )}

      <details>
        <summary className="cursor-pointer font-semibold">
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

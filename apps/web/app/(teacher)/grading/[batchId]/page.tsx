"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  analyticsApi,
  gradingApi,
  type GradeRelease,
  type GradingBatch,
  type ProcessingRun,
  type ReportJob,
  type ReviewWorkspace,
  type SubmissionRecognitionJob,
  type SubmissionRecord,
} from "@/lib/api";
import { Button, Card, PageHeader } from "@/components/ui";
import { SubmissionSegmentationWorkspace } from "@/components/submission-segmentation-workspace";
import { useSmartRefresh } from "@/lib/use-smart-refresh";

const terminal = new Set(["completed", "partially_completed", "failed"]);
const terminalSubmissionStatuses = new Set(["finalized", "merged", "voided"]);
const isActiveSubmission = (submission: SubmissionRecord) =>
  !terminalSubmissionStatuses.has(submission.status);
const releaseStatusLabels: Record<string, string> = {
  draft: "草稿",
  released: "已发布",
  superseded: "已被新版本替代",
};
const releaseMeaningLabels: Record<string, string> = {
  score_only: "仅发布分数",
  score_and_feedback: "发布分数与评语",
};
const reportTypeLabels: Record<string, string> = {
  gradebook_xlsx: "成绩表",
  student_report_pdf: "学生报告",
};
const reportStatusLabels: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  partially_completed: "部分完成",
  failed: "失败",
  expired: "已过期",
};
const matchReasonLabels: Record<string, string> = {
  MULTIPLE_CANDIDATES: "文件名对应多个学生，请选择正确学生。",
  NO_CANDIDATE: "暂时无法从文件名确定学生，请手动选择。",
  STUDENT_NOT_FOUND: "未找到对应学生，请手动选择。",
};
const processingStatusCopy: Record<
  ProcessingRun["status"],
  { label: string; description: string }
> = {
  queued: { label: "已排队", description: "即将开始。" },
  running: { label: "处理中", description: "请稍候。" },
  waiting_input: {
    label: "等待教师补充",
    description: "请处理下方问题。",
  },
  waiting_codex: {
    label: "正在评分",
    description: "正在生成评分建议。",
  },
  awaiting_teacher_review: {
    label: "等待教师复核",
    description: "评分建议已就绪。",
  },
  partially_failed: {
    label: "部分失败",
    description: "请重试失败步骤。",
  },
  failed: { label: "处理失败", description: "请重试。" },
  stale: {
    label: "输入已变化",
    description: "内容已变化，请重新处理。",
  },
  cancelled: { label: "已取消", description: "处理已停止。" },
};
const unknownProcessingStatusCopy = {
  label: "处理中",
  description: "请稍候。",
};
const getProcessingStatusCopy = (status: string) =>
  (
    processingStatusCopy as Record<
      string,
      { label: string; description: string }
    >
  )[status] ?? unknownProcessingStatusCopy;
const processingPollStatuses = new Set<ProcessingRun["status"]>([
  "queued",
  "running",
  "waiting_codex",
]);

export default function GradingBatchPage({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = use(params);
  const [batch, setBatch] = useState<GradingBatch>();
  const [submissions, setSubmissions] = useState<SubmissionRecord[]>([]);
  const [jobs, setJobs] = useState<Record<string, SubmissionRecognitionJob>>(
    {},
  );
  const [workspace, setWorkspace] = useState<ReviewWorkspace>();
  const [releases, setReleases] = useState<GradeRelease[]>([]);
  const [release, setRelease] = useState<GradeRelease>();
  const [matchSelections, setMatchSelections] = useState<
    Record<string, string>
  >({});
  const [reports, setReports] = useState<ReportJob[]>([]);
  const [retriedReportIds, setRetriedReportIds] = useState<Set<string>>(
    new Set(),
  );
  const [download, setDownload] = useState<{ jobId: string; url: string }>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedUploadFiles, setSelectedUploadFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState<{
    kind: "loading" | "success" | "error";
    message: string;
  }>();
  const [processingRun, setProcessingRun] = useState<ProcessingRun>();
  const [processingBusy, setProcessingBusy] = useState(false);

  const load = useCallback(async () => {
    const nextBatch = await gradingApi.getBatch(batchId);
    const [nextSubmissions, releases, latestProcessingRun] = await Promise.all([
      gradingApi.submissions(batchId),
      analyticsApi.releases(nextBatch.assignment_id),
      gradingApi.latestProcessingRun(batchId),
    ]);
    setBatch(nextBatch);
    setSubmissions(nextSubmissions);
    setProcessingRun(latestProcessingRun ?? undefined);
    const classReleases = releases.filter(
      (item) => item.class_id === nextBatch.class_id,
    );
    setReleases(classReleases);
    setRelease((current) => {
      if (current) {
        const same = classReleases.find((item) => item.id === current.id);
        if (same) return same;
      }
      return classReleases[0];
    });
    if (nextSubmissions.length) {
      setWorkspace(await gradingApi.reviewWorkspace(batchId));
    }
  }, [batchId]);

  useEffect(() => {
    load().catch(() => setError("无法加载批次工作台"));
  }, [load]);
  useSmartRefresh(load, { intervalMs: 30_000 });

  useEffect(() => {
    if (!release) {
      setReports([]);
      return;
    }
    analyticsApi
      .reports(release.id)
      .then(setReports)
      .catch(() => setError("无法加载该发布版本的报告历史"));
  }, [release]);

  useEffect(() => {
    if (!processingRun || !processingPollStatuses.has(processingRun.status)) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      timer = setTimeout(async () => {
        try {
          const next = await gradingApi.reconcileProcessing(
            batchId,
            processingRun.id,
            {
              idempotency_key: crypto.randomUUID(),
              expected_generation: processingRun.generation,
            },
          );
          if (cancelled) return;
          setProcessingRun(next);
          if (processingPollStatuses.has(next.status)) poll();
        } catch (reason) {
          if (!cancelled) {
            setError(
              reason instanceof Error ? reason.message : "无法刷新处理状态",
            );
          }
        }
      }, 1500);
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [batchId, processingRun]);

  async function upload(form: FormData) {
    const submittedFiles = form
      .getAll("files")
      .filter((item): item is File => item instanceof File && item.size > 0);
    const files = submittedFiles.length ? submittedFiles : selectedUploadFiles;
    if (!files.length) {
      setUploadStatus({ kind: "error", message: "请先选择作业文件" });
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    setUploadStatus({ kind: "loading", message: "正在上传并匹配…" });
    try {
      await gradingApi.upload(batchId, files);
      await load();
      setMessage("学生作业已上传并完成确定性文件名匹配");
      setUploadStatus({
        kind: "success",
        message: "上传完成，已刷新匹配结果",
      });
      setSelectedUploadFiles([]);
    } catch (reason) {
      const uploadError = reason instanceof Error ? reason.message : "上传失败";
      setError(uploadError);
      setUploadStatus({ kind: "error", message: uploadError });
    } finally {
      setBusy(false);
    }
  }

  async function startOcr() {
    await act("Submission OCR 已完成，可继续规则初批", async () => {
      const nextJobs: Record<string, SubmissionRecognitionJob> = {};
      for (const submission of submissions.filter(isActiveSubmission)) {
        let job = await gradingApi.startRecognition(submission.id);
        nextJobs[submission.id] = job;
        setJobs({ ...nextJobs });
        const deadline = Date.now() + 90_000;
        while (!terminal.has(job.status) && Date.now() < deadline) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          job = await gradingApi.recognition(submission.id, job.id);
          nextJobs[submission.id] = job;
          setJobs({ ...nextJobs });
        }
        if (!terminal.has(job.status))
          throw new Error("SUBMISSION_OCR_TIMEOUT");
        if (job.status === "failed")
          throw new Error(job.error_code || "SUBMISSION_OCR_FAILED");
      }
      setWorkspace(await gradingApi.reviewWorkspace(batchId));
      await load();
    });
  }

  async function confirmMatch(matchId: string) {
    const studentId = matchSelections[matchId];
    if (!studentId) {
      setError("请选择要匹配的班级学生");
      return;
    }
    await act("匹配已由教师通过 UI 明确确认", async () => {
      await gradingApi.confirmMatch(batchId, matchId, studentId);
      await load();
    });
  }

  async function undoUpload(matchId: string) {
    if (
      !window.confirm(
        "确认撤销这次错误上传？已完成或已进入批改的数据不会被直接删除。",
      )
    )
      return;
    await act("错误上传已安全撤销", async () => {
      await gradingApi.undoUpload(batchId, matchId);
      await load();
    });
  }

  async function reversePages(submissionId: string, pageIds: string[]) {
    await act("页面已重排，OCR 与评分状态已标记为 stale", async () => {
      await gradingApi.reorderPages(submissionId, [...pageIds].reverse());
      await load();
    });
  }

  async function splitSubmission(submissionId: string, pageId: string) {
    await act("Submission 已拆分且原始上传文件保持不变", async () => {
      await gradingApi.splitSubmission(submissionId, [pageId]);
      await load();
    });
  }

  async function mergeSubmission(targetId: string, sourceId: string) {
    await act("Submission 已合并且页码重新连续编号", async () => {
      await gradingApi.mergeSubmission(targetId, sourceId);
      await load();
    });
  }

  async function gradeAll() {
    await act(
      "评分输入已准备完成；客观题已规则初批，主观题等待 Codex 生成建议",
      async () => {
        const current = await gradingApi.reviewWorkspace(batchId);
        for (const item of current.items) {
          for (const answer of item.answers) await gradingApi.grade(answer.id);
        }
        setWorkspace(await gradingApi.reviewWorkspace(batchId));
      },
    );
  }

  async function continueToTeacherReview() {
    setProcessingBusy(true);
    setError("");
    setMessage("");
    try {
      const run = await gradingApi.continueProcessing(
        batchId,
        crypto.randomUUID(),
      );
      setProcessingRun(run);
      setMessage("处理计划已保存；所有 Codex 结果仍只是待教师复核的建议。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法继续处理");
    } finally {
      setProcessingBusy(false);
    }
  }

  async function retryFailedProcessing() {
    if (!processingRun) return;
    const stepIds = processingRun.steps
      .filter((step) => step.status === "retryable_failed" && step.retryable)
      .map((step) => step.id)
      .sort();
    if (!stepIds.length) return;
    setProcessingBusy(true);
    setError("");
    try {
      setProcessingRun(
        await gradingApi.retryProcessing(batchId, processingRun.id, {
          idempotency_key: crypto.randomUUID(),
          expected_generation: processingRun.generation,
          step_ids: stepIds,
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法重试处理");
    } finally {
      setProcessingBusy(false);
    }
  }

  async function createReport(
    type: ReportJob["report_type"],
    studentId?: string,
  ) {
    if (!release) return;
    await act(`${type} 报告任务已完成`, async () => {
      let job = await analyticsApi.createReport(release.id, type, studentId);
      const deadline = Date.now() + 120_000;
      while (!terminal.has(job.status) && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        job = await analyticsApi.report(job.id);
      }
      setReports((old) => [...old.filter((item) => item.id !== job.id), job]);
      if (job.status !== "completed" && job.status !== "partially_completed") {
        throw new Error(job.error_code || "REPORT_NOT_COMPLETED");
      }
    });
  }

  async function publishToStudents() {
    if (!release || release.student_visible) return;
    await act("该版本已向学生开放", async () => {
      const next = await analyticsApi.publishToStudents(release.id);
      setRelease(next);
      setReleases((items) =>
        items.map((item) => (item.id === next.id ? next : item)),
      );
    });
  }

  async function requestDownload(job: ReportJob) {
    await act("已通过 UI 获取新的 15 分钟短期签名下载地址", async () => {
      const value = await analyticsApi.reportDownload(job.id);
      setDownload({ jobId: job.id, url: value.url });
    });
  }

  async function retryReport(job: ReportJob) {
    await act("已创建新的 ReportJob；旧任务终态保持不变", async () => {
      const replacement = (await analyticsApi.retryReport(job.id)) as ReportJob;
      setReports((old) => [
        replacement,
        ...old.filter((item) => item.id !== replacement.id),
      ]);
      setRetriedReportIds((old) => new Set(old).add(job.id));
    });
  }

  async function act(success: string, action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  if (!batch) return <Card className="p-8">正在加载批次…</Card>;
  const workflow = batch.workflow ?? {
    stage_counts: {},
    blocked: [],
    completed_count: batch.reviewed_count ?? 0,
    blocked_count: Math.max(
      0,
      (batch.submission_count ?? submissions.length) -
        (batch.reviewed_count ?? 0),
    ),
  };
  const submissionCount = batch.submission_count ?? submissions.length;
  const currentProcessingStatusCopy = processingRun
    ? getProcessingStatusCopy(processingRun.status)
    : null;
  const processingStepBlockers = processingRun
    ? Array.from(
        new Map(
          processingRun.steps
            .filter(
              (step) =>
                step.status === "blocked_review" ||
                step.status === "retryable_failed" ||
                step.status === "terminal_failed",
            )
            .map((step) => [
              `${step.submission_id}:${step.error_code ?? ""}:${step.error_message ?? ""}`,
              step,
            ]),
        ).values(),
      )
    : [];
  const needsAttentionCount = workflow.blocked_count;
  const latestRelease = releases.reduce<GradeRelease | undefined>(
    (latest, candidate) =>
      !latest || candidate.version > latest.version ? candidate : latest,
    undefined,
  );
  const confirmedResultCount = latestRelease?.items.length ?? 0;
  const primaryBlocker = workflow.blocked[0];
  const isReadyForReview =
    submissionCount > 0 &&
    (processingRun
      ? processingRun.status === "awaiting_teacher_review"
      : workflow.blocked.length === 0);
  return (
    <div
      className="space-y-6"
      data-testid="batch-workspace"
      data-batch-id={batch.id}
    >
      <PageHeader
        title={batch.name || "批改批次"}
        description={`共 ${submissionCount} 份学生作业`}
        actions={
          <Link
            href="/grading"
            className="inline-flex min-h-10 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border)] bg-white px-4 text-sm font-semibold transition hover:bg-slate-50"
          >
            返回批次列表
          </Link>
        }
      />
      {message && (
        <Card role="status" className="border-emerald-300 p-4 text-emerald-800">
          {message}
        </Card>
      )}
      {error && (
        <Card role="alert" className="border-red-300 p-4 text-red-700">
          {error}
        </Card>
      )}

      <Card className="space-y-4 p-5" data-testid="batch-progress-overview">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-bold">当前情况</h2>
            <p className="mt-1 text-sm text-slate-600">
              {submissionCount === 0
                ? "上传学生作业后即可开始处理。"
                : needsAttentionCount > 0
                  ? `${needsAttentionCount} 份需要处理。`
                  : confirmedResultCount > 0
                    ? `已确认 ${confirmedResultCount} 份结果。`
                    : "可以检查结果。"}
            </p>
          </div>
          <span className="rounded-full bg-indigo-50 px-3 py-1 text-sm text-indigo-700">
            {workflow.completed_count}/{submissionCount} 份已完成处理
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            ["全部作业", submissionCount],
            ["需要处理", needsAttentionCount],
            ["已确认结果", confirmedResultCount],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border bg-slate-50 p-3">
              <div className="text-xs text-slate-500">{label}</div>
              <div className="mt-1 text-2xl font-bold">{value}</div>
            </div>
          ))}
        </div>
        {workflow.blocked.length ? (
          <div className="space-y-2" data-testid="batch-blockers">
            <h3 className="font-semibold">需要注意</h3>
            {workflow.blocked.map((item) => (
              <div
                key={item.stage}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3"
                data-testid="batch-blocker"
                data-stage={item.stage}
              >
                <div>
                  <p className="font-medium">
                    {item.stage_label} · {item.count} 份
                  </p>
                  <p className="text-sm text-slate-600">{item.reason}</p>
                  <p className="text-xs text-slate-500">{item.action}</p>
                </div>
                {item.stage === "matching" ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      document
                        .getElementById("pending-matches")
                        ?.scrollIntoView({ behavior: "smooth" })
                    }
                  >
                    查看待匹配文件
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">
            当前没有需要处理的问题。
          </p>
        )}
        <div
          className="flex flex-wrap items-center gap-3 border-t pt-4"
          data-testid="processing-orchestrator"
        >
          {confirmedResultCount > 0 && isReadyForReview ? (
            <Button
              data-testid="primary-batch-action"
              onClick={() =>
                document
                  .getElementById("confirmed-results")
                  ?.scrollIntoView({ behavior: "smooth" })
              }
            >
              查看已确认结果
            </Button>
          ) : isReadyForReview ? (
            <Link
              href={`/grading/${batch.id}/review`}
              className="inline-flex min-h-10 items-center justify-center rounded-[var(--radius-md)] bg-[var(--brand-600)] px-4 text-sm font-semibold text-white transition hover:bg-[var(--brand-700)]"
              data-testid="open-teacher-review"
              data-primary-action="true"
            >
              检查结果
            </Link>
          ) : primaryBlocker?.stage === "matching" ? (
            <Button
              data-testid="primary-batch-action"
              onClick={() =>
                document
                  .getElementById("pending-matches")
                  ?.scrollIntoView({ behavior: "smooth" })
              }
            >
              处理待匹配文件
            </Button>
          ) : submissionCount === 0 ? (
            <Button
              data-testid="primary-batch-action"
              onClick={() =>
                document
                  .getElementById("submission-upload-panel")
                  ?.scrollIntoView({ behavior: "smooth" })
              }
            >
              上传学生作业
            </Button>
          ) : (
            <Button
              data-testid="continue-processing-to-teacher-review"
              disabled={processingBusy}
              onClick={() => void continueToTeacherReview()}
            >
              继续处理
            </Button>
          )}
          <span className="text-sm text-slate-600">
            {currentProcessingStatusCopy?.description ??
              "系统会自动处理，异常项需要检查。"}
          </span>
        </div>
        {processingRun ? (
          <div className="space-y-3" data-testid="processing-run-status">
            {processingStepBlockers.length > 0 && (
              <div className="space-y-2" data-testid="processing-step-blockers">
                {processingStepBlockers.map((step) => (
                  <p
                    key={step.id}
                    className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-sm"
                  >
                    {step.error_message || "此步骤需要教师处理"}
                  </p>
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {processingRun.steps.some(
                (step) => step.status === "retryable_failed" && step.retryable,
              ) && (
                <Button
                  variant="outline"
                  disabled={processingBusy}
                  onClick={() => void retryFailedProcessing()}
                >
                  重试失败步骤
                </Button>
              )}
            </div>
            <details className="rounded-lg border p-3 text-xs text-slate-600">
              <summary className="cursor-pointer font-medium">技术详情</summary>
              <dl className="mt-2 grid gap-1">
                <div>处理状态：{currentProcessingStatusCopy?.label}</div>
                <div>说明：{currentProcessingStatusCopy?.description}</div>
                <div>
                  处理进度：{processingRun.completed_step_count}/
                  {processingRun.step_count}
                </div>
                <div>
                  评分方式：{processingRun.provider_label} · suggestion-only
                </div>
                <div>Run ID：{processingRun.id}</div>
                <div>Generation：{processingRun.generation}</div>
                <div>Input version：{processingRun.input_version}</div>
                <div>Request hash：{processingRun.request_hash}</div>
                {processingRun.error_code && (
                  <div>Error code：{processingRun.error_code}</div>
                )}
              </dl>
            </details>
          </div>
        ) : null}
      </Card>

      <Card className="space-y-4 p-5" id="upload-and-pages">
        <div>
          <h2 className="font-bold">学生作业</h2>
        </div>
        <details
          className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 shadow-sm"
          data-testid="submission-upload-section"
          data-section-kind="upload"
          open={submissions.length === 0}
        >
          <summary className="flex cursor-pointer list-none items-center gap-3 font-semibold text-indigo-950 [&::-webkit-details-marker]:hidden">
            <span
              aria-hidden="true"
              className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-100 text-lg text-indigo-700"
            >
              ＋
            </span>
            <span className="flex flex-wrap items-center gap-2">
              <span>{submissions.length ? "补充上传" : "上传学生作业"}</span>
              {batch.matching.items.some(
                (item) => item.status === "confirmed",
              ) && (
                <span className="rounded-full bg-cyan-100 px-2.5 py-0.5 text-xs font-medium text-cyan-800">
                  已匹配{" "}
                  {
                    batch.matching.items.filter(
                      (item) => item.status === "confirmed",
                    ).length
                  }
                  个文件
                </span>
              )}
            </span>
          </summary>
          <form
            action={upload}
            className="mt-4 flex flex-wrap items-end gap-4 border-t border-dashed border-slate-300 pt-4"
            id="submission-upload-panel"
            data-testid="submission-upload-panel"
          >
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-slate-900">上传学生作业</p>
              <p className="mt-1 text-xs text-slate-500">
                支持 PNG、JPG 或 PDF，可一次选择多个文件。
              </p>
              <label
                htmlFor="submission-files"
                className="mt-3 inline-flex cursor-pointer items-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-400 hover:bg-slate-100"
                data-testid="submission-file-picker"
              >
                选择文件
              </label>
              <input
                id="submission-files"
                name="files"
                aria-label="选择学生作业"
                type="file"
                className="sr-only"
                multiple
                accept=".png,.jpg,.jpeg,.pdf"
                onChange={(event) => {
                  setSelectedUploadFiles(
                    Array.from(event.currentTarget.files ?? []),
                  );
                  setUploadStatus(undefined);
                }}
              />
              {selectedUploadFiles.length > 0 && (
                <div
                  className="mt-3 text-sm text-slate-700"
                  data-testid="submission-file-selection"
                  role="status"
                >
                  <p className="font-medium">
                    已选择 {selectedUploadFiles.length} 个文件
                  </p>
                  <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
                    {selectedUploadFiles.slice(0, 3).map((file, index) => (
                      <li className="break-all" key={`${file.name}-${index}`}>
                        {file.name}
                      </li>
                    ))}
                    {selectedUploadFiles.length > 3 && (
                      <li>另有 {selectedUploadFiles.length - 3} 个文件</li>
                    )}
                  </ul>
                </div>
              )}
              {uploadStatus && (
                <p
                  className={`mt-3 text-sm ${
                    uploadStatus.kind === "error"
                      ? "text-red-700"
                      : uploadStatus.kind === "success"
                        ? "text-emerald-700"
                        : "text-slate-600"
                  }`}
                  data-testid="submission-upload-status"
                  role={uploadStatus.kind === "error" ? "alert" : "status"}
                >
                  {uploadStatus.message}
                </p>
              )}
            </div>
            <Button loading={busy}>上传并自动匹配</Button>
          </form>
          {batch.matching.items.some((item) => item.status === "confirmed") && (
            <section
              className="mt-4 border-t border-indigo-200 pt-4"
              data-testid="matched-upload-section"
              data-section-kind="matched-files"
            >
              <h3 className="text-sm font-semibold text-cyan-950">
                已匹配上传
              </h3>
              <div className="mt-2 space-y-2 rounded-lg border border-cyan-200 bg-cyan-50/80 p-3">
                {batch.matching.items
                  .filter((item) => item.status === "confirmed")
                  .map((item) => (
                    <div
                      key={item.id}
                      className="flex items-center justify-between gap-3 text-sm"
                    >
                      <span className="min-w-0 truncate">{item.filename}</span>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void undoUpload(item.id)}
                      >
                        撤销错误上传
                      </Button>
                    </div>
                  ))}
              </div>
            </section>
          )}
        </details>
        <div className="grid gap-3" data-testid="submission-cards">
          {submissions.map((item, index) => {
            const job = jobs[item.id];
            const isActive = isActiveSubmission(item);
            const workspaceSubmission = workspace?.items.find(
              (candidate) => candidate.submission_id === item.id,
            );
            const reviewedScores = (workspaceSubmission?.answers ?? []).map(
              (answer) => answer.review?.final_score,
            );
            const hasConfirmedScore =
              reviewedScores.length > 0 &&
              reviewedScores.every(
                (score): score is string =>
                  typeof score === "string" && score.trim().length > 0,
              );
            const confirmedScore = hasConfirmedScore
              ? reviewedScores.reduce((sum, score) => sum + Number(score), 0)
              : undefined;
            const compactStatus =
              confirmedScore === undefined
                ? "待批改"
                : `${Number.isInteger(confirmedScore) ? confirmedScore : confirmedScore.toFixed(2)} 分`;
            return (
              <article
                key={item.id}
                className={`overflow-hidden rounded-xl border border-l-4 shadow-sm transition-colors ${
                  confirmedScore === undefined
                    ? "border-amber-200 border-l-amber-400 bg-amber-50/30"
                    : "border-emerald-200 border-l-emerald-500 bg-emerald-50/30"
                }`}
                data-testid="submission"
                data-section-kind="student-submission"
                data-submission-id={item.id}
                data-student-id={item.student_id}
                data-status={item.status}
              >
                <details className="group" data-testid="submission-details">
                  <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        aria-hidden="true"
                        className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-bold ${
                          confirmedScore === undefined
                            ? "bg-amber-100 text-amber-800"
                            : "bg-emerald-100 text-emerald-800"
                        }`}
                      >
                        生
                      </span>
                      <span
                        aria-hidden="true"
                        className="text-xs text-slate-500 transition-transform group-open:rotate-90"
                      >
                        ▶
                      </span>
                      <h3 className="truncate font-semibold">
                        {item.student_name || `学生作业 ${index + 1}`}
                      </h3>
                    </span>
                    <span
                      className={`shrink-0 rounded-full px-3 py-1 text-sm font-medium ${
                        confirmedScore === undefined
                          ? "bg-amber-50 text-amber-800"
                          : "bg-emerald-50 text-emerald-800"
                      }`}
                      data-testid="submission-compact-status"
                    >
                      {compactStatus}
                    </span>
                  </summary>
                  <div className="border-t p-4">
                    <p className="text-xs text-slate-500">
                      {item.student_number
                        ? `学号 ${item.student_number} · `
                        : ""}
                      {item.page_count} 页
                    </p>
                    <div
                      className={`mt-3 rounded-lg border p-3 text-sm ${
                        item.workflow.stage === "completed"
                          ? "border-emerald-200 bg-emerald-50"
                          : item.workflow.stage === "failed"
                            ? "border-red-200 bg-red-50"
                            : "border-amber-200 bg-amber-50"
                      }`}
                      data-testid="submission-workflow"
                      data-stage={item.workflow.stage}
                    >
                      <p className="font-medium">{item.workflow.stage_label}</p>
                      <p className="text-slate-600">{item.workflow.reason}</p>
                      <p className="text-xs text-slate-500">
                        {item.workflow.action}
                      </p>
                    </div>
                    <details className="mt-3 rounded-lg border p-3 text-sm">
                      <summary className="cursor-pointer font-medium">
                        页面与高级操作
                      </summary>
                      <p className="mt-2 text-xs text-slate-500">
                        作业记录：{item.id}
                      </p>
                      {(() => {
                        const pageIds =
                          workspace?.items
                            .find(
                              (candidate) =>
                                candidate.submission_id === item.id,
                            )
                            ?.pages.map((page) => page.id) ?? [];
                        const primary = submissions.find(
                          (candidate) =>
                            candidate.attempt_number === 1 &&
                            isActiveSubmission(candidate),
                        );
                        return (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {isActive && pageIds.length > 1 && (
                              <>
                                <Button
                                  variant="outline"
                                  onClick={() =>
                                    void reversePages(item.id, pageIds)
                                  }
                                >
                                  反转页面顺序
                                </Button>
                                <Button
                                  variant="outline"
                                  onClick={() =>
                                    void splitSubmission(
                                      item.id,
                                      pageIds[pageIds.length - 1],
                                    )
                                  }
                                >
                                  拆出末页
                                </Button>
                              </>
                            )}
                            {item.attempt_number > 1 && primary && isActive && (
                              <Button
                                variant="outline"
                                onClick={() =>
                                  void mergeSubmission(primary.id, item.id)
                                }
                              >
                                合并回首次 Submission
                              </Button>
                            )}
                          </div>
                        );
                      })()}
                      {job && (
                        <div
                          className="mt-2 text-sm"
                          data-testid="submission-ocr"
                          data-job-id={job.id}
                          data-provider={job.provider}
                          data-status={job.status}
                        >
                          OCR：{job.status} · provider={job.provider} · 页面{" "}
                          {job.pages
                            .map((page) => `${page.page_number}:${page.status}`)
                            .join("，")}
                          {job.pages.length > 0 && (
                            <Button
                              className="mt-2"
                              variant="outline"
                              onClick={() =>
                                void act("页面顺序已通过 UI 保存", () =>
                                  gradingApi
                                    .reorderPages(
                                      item.id,
                                      job.pages.map((page) => page.id),
                                    )
                                    .then(() => undefined),
                                )
                              }
                            >
                              保存当前页面顺序
                            </Button>
                          )}
                        </div>
                      )}
                      {isActive && (
                        <div className="mt-3">
                          <SubmissionSegmentationWorkspace
                            submissionId={item.id}
                          />
                        </div>
                      )}
                    </details>
                  </div>
                </details>
              </article>
            );
          })}
        </div>
        {batch.matching.items.some((item) => item.status === "pending") && (
          <div
            id="pending-matches"
            className="space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-4"
            data-testid="pending-matches"
          >
            <h3 className="font-semibold">需要教师确认的歧义/未知匹配</h3>
            {batch.matching.items
              .filter((item) => item.status === "pending")
              .map((item) => (
                <div
                  key={item.id}
                  className="flex flex-wrap items-end gap-2"
                  data-testid="pending-match"
                  data-match-id={item.id}
                  data-match-method={item.method}
                >
                  <label className="grid gap-1 text-sm">
                    {item.filename} ·
                    {item.reason
                      ? (matchReasonLabels[item.reason] ?? "需要确认所属学生。")
                      : "需要确认所属学生。"}
                    <select
                      aria-label={`为 ${item.filename} 选择学生`}
                      value={matchSelections[item.id] ?? ""}
                      onChange={(event) =>
                        setMatchSelections((old) => ({
                          ...old,
                          [item.id]: event.target.value,
                        }))
                      }
                    >
                      <option value="">请选择学生</option>
                      {batch.matching.student_options.map((student) => (
                        <option key={student.id} value={student.id}>
                          {student.student_number} · {student.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Button
                    variant="outline"
                    onClick={() => void confirmMatch(item.id)}
                  >
                    人工确认匹配
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void undoUpload(item.id)}
                  >
                    撤销错误上传
                  </Button>
                </div>
              ))}
          </div>
        )}
        <details
          className="rounded-xl border border-dashed border-slate-300 bg-slate-50/80 p-4 text-sm"
          data-testid="advanced-processing-section"
          data-section-kind="advanced-tools"
        >
          <summary className="flex cursor-pointer list-none items-center gap-3 font-semibold text-slate-700 [&::-webkit-details-marker]:hidden">
            <span
              aria-hidden="true"
              className="grid h-8 w-8 place-items-center rounded-lg bg-slate-200 text-xs font-bold text-slate-600"
            >
              工
            </span>
            <span>高级处理工具</span>
          </summary>
          <p className="mt-2 text-slate-600">仅在自动处理未解决问题时使用。</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              data-testid="submission-ocr-start"
              variant="outline"
              disabled={!submissions.some(isActiveSubmission) || busy}
              onClick={() => void startOcr()}
            >
              重新识别全部作业
            </Button>
            <Button
              data-testid="prepare-grading-inputs"
              variant="outline"
              disabled={
                !workspace?.items.some((item) => item.answers.length) || busy
              }
              onClick={() => void gradeAll()}
            >
              重新准备评分建议
            </Button>
            {submissions.some((item) => item.status === "stale") && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act("已为失效答案创建新的评分结果", async () => {
                    await gradingApi.regrade(batch.id, true);
                    setWorkspace(await gradingApi.reviewWorkspace(batch.id));
                  })
                }
              >
                仅处理已失效答案
              </Button>
            )}
          </div>
          <p className="mt-3 text-xs text-slate-500" data-testid="answer-count">
            当前已形成答案记录：
            {workspace?.items.reduce(
              (sum, item) => sum + item.answers.length,
              0,
            ) ?? 0}
          </p>
        </details>
      </Card>

      <Card className="space-y-4 p-5" id="confirmed-results">
        <h2 className="font-bold">结果与报告</h2>
        <p className="text-sm text-slate-600">
          {releases.length
            ? "这里保留教师已确认的结果版本和报告。"
            : "尚未确认正式结果。完成上方处理后，进入复核并确认结果。"}
        </p>
        {releases.length > 0 && (
          <div className="space-y-2" data-testid="grade-release-versions">
            <h3 className="font-semibold">已确认版本</h3>
            {releases.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`block w-full rounded border p-3 text-left ${
                  item.id === release?.id
                    ? "border-indigo-500 bg-indigo-50"
                    : ""
                }`}
                data-testid="grade-release-version"
                data-release-id={item.id}
                data-release-version={item.version}
                aria-pressed={item.id === release?.id}
                onClick={() => setRelease(item)}
              >
                第 {item.version} 版 ·{" "}
                {releaseStatusLabels[item.status] ?? item.status} · 已确认{" "}
                {item.items.length} 份
                <span className="sr-only">
                  · 成绩快照{" "}
                  {item.items
                    .map((releaseItem) => releaseItem.score_snapshot_id)
                    .join("，")}
                </span>
              </button>
            ))}
          </div>
        )}
        {release && (
          <div
            className="space-y-3"
            data-testid="grade-release"
            data-release-id={release.id}
          >
            <p>
              当前查看第 {release.version} 版 ·{" "}
              {releaseMeaningLabels[release.meaning] ?? release.meaning}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => void createReport("gradebook_xlsx")}
                disabled={busy}
              >
                生成 Excel 成绩表
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  void createReport(
                    "student_report_pdf",
                    release.items[0]?.student_id,
                  )
                }
                disabled={busy || !release.items.length}
              >
                生成首名学生中文 PDF
              </Button>
              <Button
                onClick={() => void publishToStudents()}
                disabled={busy || release.student_visible}
              >
                {release.student_visible ? "已向学生开放" : "向学生开放此版本"}
              </Button>
            </div>
            <p className="text-xs text-slate-500">
              开放后，已关联账号的学生可查看本人成绩；此操作不会公开班级排名或其他学生信息。
            </p>
            <details className="rounded-lg border p-3 text-xs text-slate-500">
              <summary className="cursor-pointer font-medium">技术详情</summary>
              <p className="mt-2 break-all">
                固定成绩快照：
                {release.items.map((item) => item.score_snapshot_id).join("，")}
              </p>
            </details>
          </div>
        )}
        {reports.map((job) => (
          <div
            key={job.id}
            className="rounded border p-3"
            data-testid="report-job"
            data-report-id={job.id}
            data-report-type={job.report_type}
            data-report-status={job.status}
            data-report-release-id={job.grade_release_id}
            data-report-student-id={job.student_id ?? ""}
            data-report-error-code={job.error_code ?? ""}
            data-report-created-at={job.created_at ?? ""}
            data-report-assignment-id={batch.assignment_id}
            data-report-class-id={batch.class_id}
          >
            {reportTypeLabels[job.report_type] ?? job.report_type} ·{" "}
            {reportStatusLabels[job.status] ?? job.status} · {job.progress}%
            {["failed", "expired", "partially_completed"].includes(
              job.status,
            ) ? (
              <Button
                className="ml-3"
                variant="outline"
                disabled={busy || retriedReportIds.has(job.id)}
                onClick={() => void retryReport(job)}
              >
                {retriedReportIds.has(job.id)
                  ? "已创建重试任务"
                  : "创建新任务重试"}
              </Button>
            ) : (
              <Button
                className="ml-3"
                variant="outline"
                disabled={job.status !== "completed"}
                onClick={() => void requestDownload(job)}
              >
                请求短期下载地址
              </Button>
            )}
          </div>
        ))}
        {download && (
          <a
            data-testid="signed-download"
            data-job-id={download.jobId}
            className="block text-blue-700 underline"
            href={download.url}
            target="_blank"
            rel="noreferrer"
          >
            打开刚生成的短期签名下载
          </a>
        )}
      </Card>
    </div>
  );
}

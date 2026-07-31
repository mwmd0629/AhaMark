"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  analyticsApi,
  gradingApi,
  type GradeReadiness,
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

const terminal = new Set(["completed", "partially_completed", "failed"]);
const terminalSubmissionStatuses = new Set(["finalized", "merged", "voided"]);
const isActiveSubmission = (submission: SubmissionRecord) =>
  !terminalSubmissionStatuses.has(submission.status);
const submissionStatusLabels: Record<string, string> = {
  uploaded: "已上传",
  processing: "处理中",
  recognized: "已识别",
  grading: "批改中",
  finalized: "已定稿",
  failed: "处理失败",
  stale: "结果已失效",
  merged: "已合并",
  voided: "已撤销",
};
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
const processingStatusCopy: Record<
  ProcessingRun["status"],
  { label: string; description: string }
> = {
  queued: { label: "已排队", description: "服务端已保存处理计划。" },
  running: { label: "处理中", description: "服务端正在推进可执行步骤。" },
  waiting_input: {
    label: "等待教师补充",
    description: "部分答案仍需识别、分割确认或正式评分标准。",
  },
  waiting_codex: {
    label: "等待 Codex-assisted",
    description: "正在等待本地 Codex 受控生成评分建议。",
  },
  awaiting_teacher_review: {
    label: "等待教师复核",
    description: "评分建议已就绪，仍需教师明确复核；这不是正式成绩。",
  },
  partially_failed: {
    label: "部分失败",
    description: "部分步骤可安全重试，其余结果保持不变。",
  },
  failed: { label: "处理失败", description: "当前处理计划未能完成。" },
  stale: {
    label: "输入已变化",
    description: "答案或评分标准已更新，请重新继续处理。",
  },
  cancelled: { label: "已取消", description: "当前处理计划已停止。" },
};
const unknownProcessingStatusCopy = {
  label: "处理中",
  description: "服务端正在推进处理计划，请稍候查看最新状态。",
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
  const [readiness, setReadiness] = useState<GradeReadiness>();
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
    const [nextSubmissions, releases] = await Promise.all([
      gradingApi.submissions(batchId),
      analyticsApi.releases(nextBatch.assignment_id),
    ]);
    setBatch(nextBatch);
    setSubmissions(nextSubmissions);
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

  async function checkReadiness() {
    if (!batch) return;
    await act("成绩就绪检查完成", async () => {
      setReadiness(
        await analyticsApi.readiness(batch.assignment_id, batch.class_id),
      );
    });
  }

  async function createRelease() {
    if (!batch) return;
    await act("成绩发布版本已创建，并固定当前完整成绩快照", async () => {
      const value = await analyticsApi.createRelease({
        assignment_id: batch.assignment_id,
        class_id: batch.class_id,
        release_mode: "score_and_feedback",
        idempotency_key: crypto.randomUUID(),
      });
      setRelease(value);
      setReleases((old) => [
        value,
        ...old.filter((item) => item.id !== value.id),
      ]);
    });
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
  return (
    <div
      className="space-y-6"
      data-testid="batch-workspace"
      data-batch-id={batch.id}
    >
      <PageHeader
        title={batch.name || "批改批次"}
        description="此页面只编排真实 Submission、OCR、评分、快照、发布和报告状态。"
        actions={
          <Link href="/grading">
            <Button variant="outline">返回批次列表</Button>
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
            <h2 className="font-bold">批改进度总览</h2>
            <p className="mt-1 text-sm text-slate-600">
              每份作业只显示当前最先需要处理的环节，完成后会自动进入下一阶段。
            </p>
          </div>
          <span className="rounded-full bg-indigo-50 px-3 py-1 text-sm text-indigo-700">
            已完成 {workflow.completed_count}/{submissionCount}
          </span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ["已上传", submissionCount],
            ["已识别", batch.recognized_count ?? 0],
            ["已有建议分", batch.graded_count ?? 0],
            ["教师已确认", batch.reviewed_count ?? 0],
            ["处理失败", batch.failed_count ?? 0],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border bg-slate-50 p-3">
              <div className="text-xs text-slate-500">{label}</div>
              <div className="mt-1 text-2xl font-bold">{value}</div>
            </div>
          ))}
        </div>
        {workflow.blocked.length ? (
          <div className="space-y-2" data-testid="batch-blockers">
            <h3 className="font-semibold">
              当前待处理 {workflow.blocked_count} 份
            </h3>
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
                  <p className="text-xs text-slate-500">
                    下一步：{item.action}
                  </p>
                </div>
                {["recognition", "failed", "pages"].includes(item.stage) ? (
                  <Button
                    variant="outline"
                    disabled={!submissions.length || busy}
                    onClick={() => void startOcr()}
                  >
                    启动或重试识别
                  </Button>
                ) : item.stage === "grading" ? (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void gradeAll()}
                  >
                    准备 Codex 批改
                  </Button>
                ) : item.stage === "matching" ? (
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
                ) : (
                  <Link href={`/grading/${batch.id}/review`}>
                    <Button variant="outline">进入教师复核</Button>
                  </Link>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">
            当前没有阻塞项，可以进行成绩就绪检查。
          </p>
        )}
      </Card>

      <Card className="space-y-4 p-5" data-testid="processing-orchestrator">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-bold">服务端连续处理</h2>
            <p className="mt-1 text-sm text-slate-600">
              识别与 Codex-assisted 结果只形成评分建议，必须经过教师复核。
            </p>
          </div>
          <Button
            data-testid="continue-processing-to-teacher-review"
            disabled={processingBusy}
            onClick={() => void continueToTeacherReview()}
          >
            继续处理至教师复核
          </Button>
        </div>
        {processingRun ? (
          <div className="space-y-3" data-testid="processing-run-status">
            <div className="rounded-xl border bg-slate-50 p-3">
              <p className="font-medium">
                {currentProcessingStatusCopy?.label}
              </p>
              <p className="text-sm text-slate-600">
                {currentProcessingStatusCopy?.description}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {processingRun.provider_label} · suggestion-only · 已完成{" "}
                {processingRun.completed_step_count}/{processingRun.step_count}
              </p>
            </div>
            {processingRun.steps.some(
              (step) =>
                step.status === "blocked_review" ||
                step.status === "retryable_failed" ||
                step.status === "terminal_failed",
            ) && (
              <div className="space-y-2" data-testid="processing-step-blockers">
                {processingRun.steps
                  .filter(
                    (step) =>
                      step.status === "blocked_review" ||
                      step.status === "retryable_failed" ||
                      step.status === "terminal_failed",
                  )
                  .map((step) => (
                    <p
                      key={step.id}
                      className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-sm"
                    >
                      {step.error_message || "此步骤需要教师处理"}
                      {step.error_code ? `（${step.error_code}）` : ""}
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
              {processingRun.status === "awaiting_teacher_review" && (
                <Link href={`/grading/${batch.id}/review`}>
                  <Button>进入教师复核</Button>
                </Link>
              )}
            </div>
            <details className="rounded-lg border p-3 text-xs text-slate-600">
              <summary className="cursor-pointer font-medium">技术详情</summary>
              <dl className="mt-2 grid gap-1">
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
        ) : (
          <p className="text-sm text-slate-600">尚未启动新的服务端处理计划。</p>
        )}
      </Card>

      <Card className="space-y-4 p-5" id="upload-and-pages">
        <div>
          <h2 className="font-bold">1. 上传、匹配与页面整理</h2>
          <p className="mt-1 text-sm text-slate-600">
            先上传学生作业，系统将自动匹配学生；随后可在下方核对并整理页面。
          </p>
        </div>
        <form
          action={upload}
          className="flex flex-wrap items-end gap-4 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4"
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
        <div className="grid gap-3" data-testid="submission-cards">
          {submissions.map((item) => {
            const job = jobs[item.id];
            const isActive = isActiveSubmission(item);
            return (
              <article
                key={item.id}
                className="rounded-xl border p-4"
                data-testid="submission"
                data-submission-id={item.id}
                data-student-id={item.student_id}
                data-status={item.status}
              >
                <div className="flex justify-between">
                  <strong>学生作业</strong>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs">
                    {submissionStatusLabels[item.status] ?? item.status}
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  页面 {item.page_count} · 作业记录 {item.id}
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
                    下一步：{item.workflow.action}
                  </p>
                </div>
                {(() => {
                  const pageIds =
                    workspace?.items
                      .find((candidate) => candidate.submission_id === item.id)
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
                            onClick={() => void reversePages(item.id, pageIds)}
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
                    <SubmissionSegmentationWorkspace submissionId={item.id} />
                  </div>
                )}
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
                    {item.filename} · {item.reason}
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
        {batch.matching.items.some((item) => item.status === "confirmed") && (
          <div className="space-y-2 rounded-xl border p-4">
            <h3 className="font-semibold">已匹配上传</h3>
            {batch.matching.items
              .filter((item) => item.status === "confirmed")
              .map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span>
                    {item.filename} · {item.method}
                  </span>
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
        <Button
          data-testid="submission-ocr-start"
          disabled={!submissions.some(isActiveSubmission) || busy}
          onClick={() => void startOcr()}
        >
          启动全部 Submission OCR
        </Button>
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-bold">2. 初批与教师复核</h2>
        <p className="text-sm text-slate-600">
          客观题先由规则引擎核对；主观题由 Codex
          根据已确认的答题内容、参考答案和评分标准生成逐项建议。教师只需复核，不必从零人工评分。
        </p>
        <p data-testid="answer-count">
          已形成 StudentAnswer：
          {workspace?.items.reduce(
            (sum, item) => sum + item.answers.length,
            0,
          ) ?? 0}
        </p>
        <Button
          data-testid="prepare-grading-inputs"
          disabled={
            !workspace?.items.some((item) => item.answers.length) || busy
          }
          onClick={() => void gradeAll()}
        >
          准备并检查评分输入
        </Button>{" "}
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void act("已为 stale 答案创建新的评分结果", async () => {
              await gradingApi.regrade(batch.id, true);
              setWorkspace(await gradingApi.reviewWorkspace(batch.id));
            })
          }
        >
          仅重新批改 stale 答案
        </Button>{" "}
        <Link href={`/grading/${batch.id}/review`}>
          <Button data-testid="open-teacher-review" variant="secondary">
            进入三栏教师复核
          </Button>
        </Link>
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-bold">3. 成绩发布与报告</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void checkReadiness()}
            disabled={busy}
          >
            检查成绩是否可发布
          </Button>
          <Button
            onClick={() => void createRelease()}
            disabled={!readiness?.releasable_count || busy}
          >
            创建新的成绩发布版本
          </Button>
        </div>
        {readiness && (
          <p data-testid="grade-readiness">
            可发布 {readiness.releasable_count} · 未完成{" "}
            {readiness.unreleasable_count}（未完成不会记零分）
          </p>
        )}
        {releases.length > 0 && (
          <div className="space-y-2" data-testid="grade-release-versions">
            <h3 className="font-semibold">历史发布版本（固定成绩快照）</h3>
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
                onClick={() => setRelease(item)}
              >
                第 {item.version} 版 ·{" "}
                {releaseStatusLabels[item.status] ?? item.status} · 成绩快照{" "}
                {item.items
                  .map((releaseItem) => releaseItem.score_snapshot_id)
                  .join("，")}
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
              发布第 {release.version} 版 ·{" "}
              {releaseMeaningLabels[release.meaning] ?? release.meaning} ·
              固定成绩快照{" "}
              {release.items.map((item) => item.score_snapshot_id).join("，")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => void createReport("gradebook_xlsx")}
                disabled={busy}
              >
                生成 Excel 成绩表
              </Button>
              <Button
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
            </div>
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

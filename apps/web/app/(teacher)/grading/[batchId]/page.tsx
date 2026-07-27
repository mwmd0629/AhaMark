"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  analyticsApi,
  gradingApi,
  type GradeReadiness,
  type GradeRelease,
  type GradingBatch,
  type ReportJob,
  type ReviewWorkspace,
  type SubmissionRecognitionJob,
  type SubmissionRecord,
} from "@/lib/api";
import { Button, Card, PageHeader } from "@/components/ui";
import { SubmissionSegmentationWorkspace } from "@/components/submission-segmentation-workspace";

const terminal = new Set(["completed", "partially_completed", "failed"]);

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

  async function upload(form: FormData) {
    const files = form
      .getAll("files")
      .filter((item): item is File => item instanceof File && item.size > 0);
    if (!files.length) return;
    await act("学生作业已上传并完成确定性文件名匹配", async () => {
      await gradingApi.upload(batchId, files);
      await load();
    });
  }

  async function startOcr() {
    await act("Submission OCR 已完成，可继续规则初批", async () => {
      const nextJobs: Record<string, SubmissionRecognitionJob> = {};
      for (const submission of submissions.filter(
        (item) => item.status !== "finalized",
      )) {
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
      "客观题规则初批完成；主观题保持 Provider unavailable，等待教师人工评分",
      async () => {
        const current = await gradingApi.reviewWorkspace(batchId);
        for (const item of current.items) {
          for (const answer of item.answers) await gradingApi.grade(answer.id);
        }
        setWorkspace(await gradingApi.reviewWorkspace(batchId));
      },
    );
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
    await act(
      "GradeRelease 已创建并固定具体 complete ScoreSnapshot",
      async () => {
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
      },
    );
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

      <Card className="space-y-4 p-5">
        <h2 className="font-bold">1. 上传、匹配与页面整理</h2>
        <form action={upload} className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-sm font-medium">
            合成学生作业（PNG/PDF）
            <input
              name="files"
              aria-label="选择学生作业"
              type="file"
              multiple
              accept=".png,.jpg,.jpeg,.pdf"
            />
          </label>
          <Button loading={busy}>上传并自动匹配</Button>
        </form>
        <div className="grid gap-3 md:grid-cols-2">
          {submissions.map((item) => {
            const job = jobs[item.id];
            return (
              <article
                key={item.id}
                className="rounded-xl border p-4"
                data-testid="submission"
                data-submission-id={item.id}
                data-student-id={item.student_id}
              >
                <div className="flex justify-between">
                  <strong>合成学生提交</strong>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs">
                    {item.status}
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  页面 {item.page_count} · Submission {item.id}
                </p>
                {(() => {
                  const pageIds =
                    workspace?.items
                      .find((candidate) => candidate.submission_id === item.id)
                      ?.pages.map((page) => page.id) ?? [];
                  const primary = submissions.find(
                    (candidate) => candidate.attempt_number === 1,
                  );
                  return (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {pageIds.length > 1 && (
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
                      {item.attempt_number > 1 &&
                        primary &&
                        item.status !== "merged" && (
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
                {item.status !== "finalized" && (
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
          disabled={!submissions.length || busy}
          onClick={() => void startOcr()}
        >
          启动全部 Submission OCR
        </Button>
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-bold">2. 初批与教师复核</h2>
        <p className="text-sm text-slate-600">
          工作流测试 OCR 适配器只证明编排；客观题使用 objective-rule，主观题真实
          Provider unavailable。
        </p>
        <p data-testid="answer-count">
          已形成 StudentAnswer：
          {workspace?.items.reduce(
            (sum, item) => sum + item.answers.length,
            0,
          ) ?? 0}
        </p>
        <Button
          disabled={
            !workspace?.items.some((item) => item.answers.length) || busy
          }
          onClick={() => void gradeAll()}
        >
          运行确定性初批
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
          <Button variant="secondary">进入三栏教师复核</Button>
        </Link>
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="font-bold">3. GradeRelease 与报告</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void checkReadiness()}
            disabled={busy}
          >
            查看 grade readiness
          </Button>
          <Button
            onClick={() => void createRelease()}
            disabled={!readiness?.releasable_count || busy}
          >
            创建新的 GradeRelease 版本
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
            <h3 className="font-semibold">历史发布版本（固定 Snapshot）</h3>
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
                v{item.version} · {item.status} · Snapshot{" "}
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
              发布 v{release.version} · {release.meaning} · 固定快照{" "}
              {release.items.map((item) => item.score_snapshot_id).join("，")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => void createReport("gradebook_xlsx")}
                disabled={busy}
              >
                生成 XLSX
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
            {job.report_type} · {job.status} · {job.progress}%
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

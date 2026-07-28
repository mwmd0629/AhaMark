"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { gradingApi, type ReviewWorkspace } from "@/lib/api";
import { AnswerRecognitionWorkspace } from "@/components/answer-recognition-workspace";
import { AIGradingReview } from "@/components/ai-grading-review";

type Decision = "accepted" | "modified" | "rejected" | "manual_scored";

export default function ReviewPage() {
  const { batchId } = useParams<{ batchId: string }>();
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
  const [snapshots, setSnapshots] = useState<
    Array<{
      id: string;
      submission_id: string;
      status: string;
      total_score?: string;
      problems?: Array<{ code: string; question_id: string }>;
    }>
  >([]);

  const load = async () => setData(await gradingApi.reviewWorkspace(batchId));
  useEffect(() => {
    gradingApi
      .reviewWorkspace(batchId)
      .then(setData)
      .catch(() => setError("无法加载复核工作台"));
  }, [batchId]);

  const submission = data?.items[submissionIndex];
  const answer = submission?.answers[answerIndex];
  const page = submission?.pages[pageIndex];
  const evidence = useMemo(
    () => answer?.evidence.find((item) => item.id === activeEvidence),
    [answer, activeEvidence],
  );

  async function submitReview(decision: Decision) {
    if (!answer || saving) return;
    const payload: Record<string, unknown> = { decision };
    if (decision === "modified" || decision === "manual_scored") {
      const score = window.prompt("请输入最终分数", answer.result?.score ?? "");
      if (score === null) return;
      if (score.trim() === "" || Number.isNaN(Number(score))) {
        setMessage("请输入有效分数");
        return;
      }
      payload.final_score = score;
      const feedback = window.prompt(
        "请输入反馈（可留空）",
        answer.review?.feedback ?? "",
      );
      if (feedback === null) return;
      payload.final_feedback = feedback;
      if (answer.criteria.length) {
        const criterionScores: Record<string, string> = {};
        for (const criterion of answer.criteria) {
          const awarded = window.prompt(
            `评分项 ${criterion.rubric_item_id}（满分 ${criterion.max_points}）`,
            criterion.awarded_points ?? "",
          );
          if (awarded === null) return;
          if (
            awarded.trim() === "" ||
            Number.isNaN(Number(awarded)) ||
            Number(awarded) < 0 ||
            Number(awarded) > Number(criterion.max_points)
          ) {
            setMessage("评分项得分无效");
            return;
          }
          criterionScores[criterion.rubric_item_id] = awarded;
        }
        payload.criterion_scores = criterionScores;
      }
      payload.reason =
        decision === "modified" ? "教师修改 AI 建议" : "教师手动评分";
    }
    setSaving(true);
    setMessage("");
    try {
      await gradingApi.review(answer.id, payload);
      await load();
      setMessage("复核结果已保存");
    } catch {
      setMessage("保存失败，请检查分数范围后重试");
    } finally {
      setSaving(false);
    }
  }

  async function finalizeAll() {
    if (!data || saving) return;
    setSaving(true);
    setMessage("");
    try {
      const values = [];
      for (const item of data.items) {
        values.push(
          (await gradingApi.finalize(item.submission_id)) as {
            id: string;
            submission_id: string;
            status: string;
            total_score?: string;
            problems?: Array<{ code: string; question_id: string }>;
          },
        );
      }
      setSnapshots(values);
      await load();
      const blocked = values.filter((item) => item.status !== "complete");
      setMessage(
        blocked.length
          ? `finalize 已阻止 ${blocked.length} 份未完成 Submission：${blocked
              .flatMap(
                (item) => item.problems?.map((problem) => problem.code) ?? [],
              )
              .join("、")}`
          : "全部 Submission 已 finalize，并生成新的 complete ScoreSnapshot 版本",
      );
    } catch {
      setMessage("finalize 失败：仍有题目未完成教师复核");
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
      await load();
      setMessage("答案已修正；旧评分建议已失效，请重新批改或人工评分");
    } catch {
      setMessage("答案修正失败");
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
      setMessage("已创建新的评分 Job/Result；旧结果保持 superseded/stale");
    } catch {
      setMessage("重新批改失败，请检查当前 Rubric 是否完整");
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
      setMessage("答题区域已确认；旧 OCR 和评分已标记 stale，请重新识别");
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
          <p className="text-sm text-slate-500">{data.provider_notice}</p>
        </div>
        <div className="flex items-center gap-3 text-sm font-medium">
          <span>
            进度 {data.progress.reviewed}/{data.progress.total}
          </span>
          <button
            className="rounded bg-indigo-700 px-3 py-2 text-white disabled:opacity-50"
            disabled={
              saving ||
              data.progress.reviewed !== data.progress.total ||
              data.progress.total === 0
            }
            onClick={() => void finalizeAll()}
          >
            完成全部 finalize
          </button>
          <Link
            className="rounded border px-3 py-2"
            href={`/grading/${batchId}`}
          >
            返回批次工作台
          </Link>
        </div>
      </header>
      {snapshots.length > 0 && (
        <div
          className="rounded-xl border border-emerald-300 bg-emerald-50 p-4"
          data-testid="score-snapshots"
        >
          {snapshots.map((snapshot) => (
            <p
              key={snapshot.id}
              data-testid="score-snapshot"
              data-snapshot-id={snapshot.id}
              data-submission-id={snapshot.submission_id}
              data-status={snapshot.status}
              data-total-score={snapshot.total_score}
            >
              {snapshot.status} Snapshot {snapshot.id} · 总分{" "}
              {snapshot.total_score ?? "未生成"}{" "}
              {snapshot.problems?.length
                ? `· 阻塞 ${snapshot.problems.map((item) => item.code).join("、")}`
                : ""}
            </p>
          ))}
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
          {data.items.map((item, index) => (
            <button
              key={item.submission_id}
              onClick={() => {
                setSubmissionIndex(index);
                setAnswerIndex(0);
                setPageIndex(0);
              }}
              className={`mb-2 block w-full rounded-lg p-2 text-left text-sm ${index === submissionIndex ? "bg-indigo-50 text-indigo-700" : "hover:bg-slate-50"}`}
            >
              提交 {index + 1}
              <span className="block text-xs">{item.status}</span>
            </button>
          ))}
          <hr className="my-3" />
          {submission?.answers.map((item, index) => (
            <button
              key={item.id}
              data-answer-id={item.id}
              onClick={() => setAnswerIndex(index)}
              className={`mb-1 block w-full rounded p-2 text-left text-sm ${index === answerIndex ? "bg-amber-50" : ""}`}
            >
              第 {item.question.number} 题 · {item.status}
            </button>
          ))}
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
          data-rubric-version-id={answer?.result?.rubric_version_id}
          data-suggested-score={answer?.result?.score}
          data-final-score={answer?.review?.final_score}
          className="space-y-4 overflow-auto rounded-xl border bg-white p-4"
        >
          {answer ? (
            <>
              <div>
                <span className="text-xs text-slate-500">Question</span>
                <h2 className="text-lg font-bold">
                  第 {answer.question.number} 题 · {answer.question.type}
                </h2>
                <p>{answer.question.content}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Info label="OCR 原始值" value={answer.recognized_text} />
                <Info label="教师修正值" value={answer.corrected_text} />
                <Info label="建议分" value={answer.result?.score} />
                <Info label="置信度" value={answer.result?.confidence} />
                <Info
                  label="Provider"
                  value={
                    answer.result
                      ? `${answer.result.provider}/${answer.result.provider_version}`
                      : "人工评分"
                  }
                />
                <Info
                  label="状态"
                  value={answer.requires_review ? "强制复核" : answer.status}
                />
                <Info label="评分结果状态" value={answer.result?.status} />
              </div>
              <Link
                href={`/grading/${batchId}/review/${answer.id}/validation`}
                className="inline-flex rounded border px-3 py-2 text-sm font-medium"
              >
                查看数学验证证据
              </Link>
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
                  答案或 Rubric
                  已变化，旧建议不能继续接受。请重新批改或由教师重新人工评分。
                </div>
              )}
              {(answer.status === "manual_segmentation_required" ||
                (answer.regions ?? []).length === 0) && (
                <div
                  role="alert"
                  className="rounded border border-red-300 bg-red-50 p-3 text-sm"
                >
                  未找到可靠题号区域，必须人工切题后才能继续。系统不会用页码猜测题号。
                </div>
              )}
              {(answer.recognized_text === undefined ||
                Number(answer.confidence ?? 0) < 0.9) && (
                <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
                  OCR 为空或置信度不足，必须人工复核。公式识别未配置时不可用。
                </div>
              )}
              <div>
                <h3 className="font-semibold">答题区域</h3>
                <button
                  className="mt-2 rounded border px-3 py-2 text-sm"
                  disabled={saving || !page}
                  onClick={() => void addRegion()}
                >
                  在当前页增加区域
                </button>
                {(answer.regions ?? []).map((region) => (
                  <div
                    key={region.id}
                    id={`answer-region-${region.id}`}
                    className="mt-2 flex items-center gap-2 text-sm"
                  >
                    <span>
                      {region.source}/{region.status} · {region.x},{region.y},
                      {region.width},{region.height}
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
                            setMessage("区域已删除；旧 OCR 和评分已标记 stale");
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
              <AIGradingReview
                key={answer.id}
                answerId={answer.id}
                finalized={submission.status === "finalized"}
              />
              <div>
                <h3 className="font-semibold">Criterion</h3>
                {answer.criteria.map((item) => (
                  <div
                    key={item.rubric_item_id}
                    className="mt-2 rounded border p-2 text-sm"
                  >
                    {item.awarded_points ?? "—"} / {item.max_points} ·{" "}
                    {item.reason}
                  </div>
                ))}
              </div>
              <div>
                <h3 className="font-semibold">Evidence</h3>
                {answer.evidence.length ? (
                  answer.evidence.map((item) => (
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
                      className="mt-2 block w-full rounded border p-2 text-left text-sm hover:bg-indigo-50"
                    >
                      {item.quote || "证据区域"}
                    </button>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">没有伪造证据框</p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Action
                  label="修正 OCR 答案"
                  onClick={() => void correctCurrentAnswer()}
                  disabled={saving}
                />
                <Action
                  label="重新批改"
                  onClick={() => void regradeCurrentAnswer()}
                  disabled={saving}
                />
                <Action
                  label="接受"
                  primary
                  onClick={() => submitReview("accepted")}
                  disabled={saving || answer.result?.status !== "suggested"}
                />
                <Action
                  label="修改"
                  onClick={() => submitReview("modified")}
                  disabled={saving}
                />
                <Action
                  label="拒绝"
                  onClick={() => submitReview("rejected")}
                  disabled={saving}
                />
                <Action
                  label="手动评分"
                  onClick={() => submitReview("manual_scored")}
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
            <p className="text-slate-500">请选择待复核答案</p>
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

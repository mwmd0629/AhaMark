"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import {
  ApiError,
  recognitionApi,
  type RecognitionCandidate,
  type RecognitionJob,
  type RecognitionPage,
  type RecognitionProviderStatus,
} from "@/lib/api";
import { Badge, Button, Card, Input } from "@/components/ui";

export function RecognitionWorkspace({
  assignmentId,
  paperVersionId,
}: {
  assignmentId: string;
  paperVersionId: string;
}) {
  const [provider, setProvider] = useState<RecognitionProviderStatus>();
  const [job, setJob] = useState<RecognitionJob>();
  const [pages, setPages] = useState<RecognitionPage[]>([]);
  const [candidates, setCandidates] = useState<RecognitionCandidate[]>([]);
  const [selected, setSelected] = useState("");
  const [showProcessed, setShowProcessed] = useState(true);
  const [error, setError] = useState("");
  useEffect(
    () =>
      void recognitionApi
        .providers(assignmentId)
        .then(setProvider)
        .catch(() => setError("无法读取识别器状态")),
    [assignmentId],
  );
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      const next = await recognitionApi.job(assignmentId, job.id);
      setJob(next);
      if (!["queued", "running"].includes(next.status)) {
        setPages(await recognitionApi.pages(assignmentId, next.id));
        const nextCandidates = await recognitionApi.candidates(
          assignmentId,
          next.id,
        );
        setCandidates(nextCandidates);
        setSelected(nextCandidates[0]?.id ?? "");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [assignmentId, job]);
  const current = candidates.find((candidate) => candidate.id === selected);
  const region = current?.regions[0];
  const page =
    pages.find((item) => item.paper_page_id === region?.paper_page_id) ??
    pages[0];
  return (
    <Card
      className="space-y-4 p-6"
      data-testid="recognition-workspace"
      data-provider={provider?.provider}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-bold">图片处理与 OCR</h2>
          <p className="text-sm text-slate-600">
            原图、处理图和候选数据分离保存；候选确认前不会修改正式题目。fake
            仅是非 production 工作流测试适配器。
          </p>
        </div>
        <Button
          disabled={
            !provider?.available ||
            (!!job && ["queued", "running"].includes(job.status))
          }
          onClick={async () => {
            try {
              setError("");
              setJob(await recognitionApi.start(assignmentId, paperVersionId));
            } catch (reason) {
              setError(
                reason instanceof ApiError ? reason.message : "无法启动识别",
              );
            }
          }}
        >
          开始识别
        </Button>
      </div>
      <div className="rounded-xl border p-3 text-sm">
        文字识别：{provider?.provider ?? "检查中"}{" "}
        {provider?.demo ? "（测试 provider）" : ""} ·{" "}
        {provider?.available
          ? "可用"
          : `不可用：${provider?.reason ?? "未知原因"}`}
        <br />
        公式识别：
        {provider?.formula.available
          ? "可用"
          : `不可用：${provider?.formula.reason ?? "未配置"}`}
      </div>
      {error && (
        <p
          role="alert"
          className="rounded-xl bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </p>
      )}
      {job && (
        <div
          aria-label="识别任务进度"
          data-testid="recognition-job"
          data-job-id={job.id}
          data-status={job.status}
          className="space-y-2 rounded-xl bg-slate-50 p-3"
        >
          <div className="flex justify-between">
            <span>
              <Badge status={job.status} /> · {job.stage}
            </span>
            <span>{job.progress}%</span>
          </div>
          <progress className="w-full" max={100} value={job.progress} />
          <p className="text-xs">
            成功 {job.page_summary.completed} · 失败 {job.page_summary.failed} ·
            待重新识别 {job.page_summary.stale}
          </p>
          {job.error_message && (
            <p className="text-red-700">{job.error_message}</p>
          )}
        </div>
      )}
      {!!pages.length && (
        <div className="grid gap-4 lg:grid-cols-[10rem_1fr_20rem]">
          <nav aria-label="页面缩略图" className="space-y-2">
            {pages.map((item, index) => (
              <button
                key={item.id}
                onClick={() =>
                  setSelected(
                    candidates.find((candidate) =>
                      candidate.regions.some(
                        (r) => r.paper_page_id === item.paper_page_id,
                      ),
                    )?.id ?? "",
                  )
                }
                className="w-full rounded-xl border p-2 text-left text-xs"
              >
                第 {index + 1} 页 · {item.status}
                {item.thumbnail_url && (
                  <Image
                    unoptimized
                    width={160}
                    height={220}
                    alt={`第 ${index + 1} 页缩略图`}
                    className="mt-2 h-auto w-full"
                    src={item.thumbnail_url}
                  />
                )}
                {item.status === "failed" && (
                  <Button
                    variant="outline"
                    onClick={() =>
                      recognitionApi.retryPage(
                        assignmentId,
                        job!.id,
                        item.paper_page_id,
                      )
                    }
                  >
                    重试
                  </Button>
                )}
              </button>
            ))}
          </nav>
          <section>
            <div className="mb-2 flex gap-2">
              <Button variant="outline" onClick={() => setShowProcessed(false)}>
                原图
              </Button>
              <Button variant="outline" onClick={() => setShowProcessed(true)}>
                处理图
              </Button>
            </div>
            <div className="relative overflow-hidden rounded-xl border bg-slate-100">
              {page && (
                <Image
                  unoptimized
                  width={1200}
                  height={1600}
                  alt={showProcessed ? "处理后页面" : "原始页面"}
                  className="h-auto w-full"
                  src={
                    (showProcessed ? page.processed_url : page.rendered_url) ??
                    page.thumbnail_url ??
                    ""
                  }
                />
              )}
              {region && (
                <button
                  aria-label="候选题目区域"
                  className="absolute border-2 border-blue-600 bg-blue-300/20"
                  style={{
                    left: `${Number(region.x) * 100}%`,
                    top: `${Number(region.y) * 100}%`,
                    width: `${Number(region.width) * 100}%`,
                    height: `${Number(region.height) * 100}%`,
                  }}
                />
              )}
            </div>
          </section>
          <aside
            className="space-y-3"
            data-testid="recognition-candidate"
            data-candidate-id={current?.id}
          >
            <h3 className="font-semibold">候选题目</h3>
            <select
              aria-label="候选题目"
              className="w-full rounded-xl border p-2"
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {candidates.map((candidate) => (
                <option value={candidate.id} key={candidate.id}>
                  第 {candidate.temporary_number} 题 · {candidate.status}
                </option>
              ))}
            </select>
            {current && (
              <>
                <Input
                  label="OCR 文字"
                  value={current.content_text ?? ""}
                  onChange={(event) =>
                    setCandidates((old) =>
                      old.map((item) =>
                        item.id === current.id
                          ? { ...item, content_text: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
                <Input
                  label="LaTeX（公式 provider 不可用时为空）"
                  value={current.content_latex ?? ""}
                  onChange={(event) =>
                    setCandidates((old) =>
                      old.map((item) =>
                        item.id === current.id
                          ? { ...item, content_latex: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
                <Input
                  label="分值"
                  type="number"
                  min="0.01"
                  step="0.01"
                  placeholder="未设置"
                  value={current.suggested_score ?? ""}
                  onChange={(event) =>
                    setCandidates((old) =>
                      old.map((item) =>
                        item.id === current.id
                          ? {
                              ...item,
                              suggested_score: event.target.value || undefined,
                            }
                          : item,
                      ),
                    )
                  }
                />
                <p className="text-xs">
                  {current.suggested_score == null
                    ? "分值未设置；可先确认为待完善题目，但无法设置评分标准或发布作业。"
                    : `建议分值：${current.suggested_score} 分`}
                </p>
                <p className="text-xs">
                  置信度：
                  {current.confidence == null
                    ? "未提供"
                    : `${Math.round(Number(current.confidence) * 100)}%`}
                  （不代表准确率） · 来源 {current.source}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={() =>
                      recognitionApi.patchCandidate(
                        assignmentId,
                        job!.id,
                        current.id,
                        {
                          content_text: current.content_text,
                          content_latex: current.content_latex,
                          suggested_score: current.suggested_score
                            ? Number(current.suggested_score)
                            : null,
                          status: "edited",
                        },
                      )
                    }
                  >
                    保存修正
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() =>
                      recognitionApi
                        .patchCandidate(assignmentId, job!.id, current.id, {
                          status: "rejected",
                        })
                        .then((next) =>
                          setCandidates((old) =>
                            old.map((item) =>
                              item.id === next.id ? next : item,
                            ),
                          ),
                        )
                    }
                  >
                    拒绝
                  </Button>
                  <Button
                    onClick={() =>
                      recognitionApi.confirm(assignmentId, job!.id, [
                        current.id,
                      ])
                    }
                  >
                    确认生成题目
                  </Button>
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </Card>
  );
}

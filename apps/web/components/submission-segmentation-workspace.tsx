"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  submissionProcessingApi,
  type SubmissionProcessingJob,
  type SubmissionProcessingPage,
  type SubmissionRegionCandidate,
} from "@/lib/api";
import { Badge, Button, Card } from "@/components/ui";

const terminal = new Set([
  "completed",
  "partially_completed",
  "failed",
  "cancelled",
]);

export function SubmissionSegmentationWorkspace({
  submissionId,
}: {
  submissionId: string;
}) {
  const [job, setJob] = useState<SubmissionProcessingJob>();
  const [pages, setPages] = useState<SubmissionProcessingPage[]>([]);
  const [regions, setRegions] = useState<SubmissionRegionCandidate[]>([]);
  const [currentPageId, setCurrentPageId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [showProcessed, setShowProcessed] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [draft, setDraft] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
  }>();
  const startPoint = useRef<{ x: number; y: number } | undefined>(undefined);
  const canvas = useRef<HTMLDivElement>(null);

  const reload = useCallback(async () => {
    const [nextPages, nextRegions, incomplete] = await Promise.all([
      submissionProcessingApi.pages(submissionId),
      submissionProcessingApi.regions(submissionId),
      submissionProcessingApi.incomplete(submissionId),
    ]);
    setPages(nextPages);
    setRegions(nextRegions);
    setCurrentPageId((old) => old || nextPages[0]?.id || "");
    setQuestionId(
      (old) =>
        old || incomplete.question_ids[0] || nextRegions[0]?.question_id || "",
    );
  }, [submissionId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!job || terminal.has(job.status)) return;
    const timer = window.setInterval(async () => {
      const next = await submissionProcessingApi.job(submissionId, job.id);
      setJob(next);
      if (terminal.has(next.status)) await reload();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job, reload, submissionId]);

  const page = pages.find((item) => item.id === currentPageId) ?? pages[0];
  const pageRegions = useMemo(
    () => regions.filter((item) => item.submission_page_id === page?.id),
    [page?.id, regions],
  );
  const questionIds = Array.from(
    new Set(regions.map((item) => item.question_id)),
  );
  if (questionId && !questionIds.includes(questionId))
    questionIds.push(questionId);

  function point(event: React.PointerEvent<HTMLDivElement>) {
    const rect = canvas.current!.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  }

  return (
    <Card
      className="space-y-3 p-4"
      data-testid="submission-segmentation-workspace"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold">答卷页面处理与题目切分</h3>
          <p className="text-xs text-slate-600">
            只对教师确认区域运行后续 OCR；不会按页码推断题号。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={!!job && !terminal.has(job.status)}
            onClick={async () =>
              setJob(await submissionProcessingApi.start(submissionId))
            }
          >
            {job && !terminal.has(job.status) ? "自动切题中" : "处理并自动切题"}
          </Button>
          <Button
            variant="outline"
            onClick={async () => {
              await submissionProcessingApi.confirmHighConfidence(submissionId);
              await reload();
            }}
          >
            确认高置信度区域
          </Button>
        </div>
      </div>
      {job && (
        <div className="rounded-lg bg-slate-50 p-2 text-sm">
          <Badge status={job.status} /> {job.stage} · {job.progress}%
          {job.error_code && (
            <span className="ml-2 text-red-700">{job.error_code}</span>
          )}
        </div>
      )}
      {!!pages.length && (
        <div className="grid gap-3 lg:grid-cols-[9rem_minmax(0,1fr)_15rem]">
          <nav aria-label="答卷页面缩略图" className="space-y-2">
            {pages.map((item) => (
              <div
                key={item.id}
                className={`w-full rounded-lg border p-2 text-left text-xs ${
                  item.id === page?.id ? "border-blue-500" : ""
                }`}
              >
                <button
                  className="w-full text-left"
                  onClick={() => setCurrentPageId(item.id)}
                >
                  第 {item.page_number} 页 · {item.processing_status}
                  {item.thumbnail_url && (
                    // Signed object URLs are runtime-generated and intentionally unoptimized.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      className="mt-1 w-full"
                      alt={`第 ${item.page_number} 页`}
                      src={item.thumbnail_url}
                    />
                  )}
                  {item.quality.warnings.map((warning) => (
                    <span key={warning} className="mt-1 block text-amber-700">
                      {warning}
                    </span>
                  ))}
                </button>
                {item.processing_status === "failed" &&
                  item.retryable &&
                  job && (
                    <Button
                      variant="outline"
                      onClick={async (event) => {
                        event.stopPropagation();
                        setJob(
                          await submissionProcessingApi.retryPage(
                            submissionId,
                            job.id,
                            item.id,
                          ),
                        );
                      }}
                    >
                      重新处理
                    </Button>
                  )}
              </div>
            ))}
          </nav>
          <section className="min-w-0">
            <div className="mb-2 flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setShowProcessed(false)}>
                原图
              </Button>
              <Button variant="outline" onClick={() => setShowProcessed(true)}>
                处理图
              </Button>
              <Button
                variant="outline"
                onClick={() => setZoom((value) => Math.min(2, value + 0.25))}
              >
                放大
              </Button>
              <Button
                variant="outline"
                onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
              >
                缩小
              </Button>
            </div>
            <div className="overflow-auto rounded-lg border bg-slate-100">
              <div
                ref={canvas}
                aria-label="框选题目区域"
                className="relative touch-none select-none"
                style={{ width: `${zoom * 100}%` }}
                onPointerDown={(event) => {
                  if (!page || !questionId) return;
                  startPoint.current = point(event);
                  setDraft({ ...startPoint.current, width: 0, height: 0 });
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onPointerMove={(event) => {
                  if (!startPoint.current) return;
                  const end = point(event);
                  setDraft({
                    x: Math.min(startPoint.current.x, end.x),
                    y: Math.min(startPoint.current.y, end.y),
                    width: Math.abs(end.x - startPoint.current.x),
                    height: Math.abs(end.y - startPoint.current.y),
                  });
                }}
                onPointerUp={async (event) => {
                  const target = event.currentTarget;
                  const pointerId = event.pointerId;
                  if (
                    !draft ||
                    !page ||
                    draft.width < 0.01 ||
                    draft.height < 0.01
                  ) {
                    startPoint.current = undefined;
                    setDraft(undefined);
                    return;
                  }
                  await submissionProcessingApi.addRegion(submissionId, {
                    question_id: questionId,
                    submission_page_id: page.id,
                    ...draft,
                    source: "manual",
                    status: "confirmed",
                    reason: "TEACHER_DRAWN",
                  });
                  startPoint.current = undefined;
                  setDraft(undefined);
                  await reload();
                  target.releasePointerCapture(pointerId);
                }}
              >
                {page &&
                  (showProcessed ? page.processed_url : page.original_url) && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      draggable={false}
                      className="block h-auto w-full"
                      alt={showProcessed ? "处理后的答卷页面" : "原始答卷页面"}
                      src={
                        showProcessed
                          ? (page.processed_url ?? "")
                          : (page.original_url ?? "")
                      }
                    />
                  )}
                {[
                  ...pageRegions,
                  ...(draft
                    ? [{ ...draft, id: "draft", status: "candidate" as const }]
                    : []),
                ].map((region) => (
                  <div
                    key={region.id}
                    className={`absolute border-2 ${
                      region.status === "confirmed"
                        ? "border-emerald-600 bg-emerald-300/20"
                        : region.status === "manual_required"
                          ? "border-amber-600 bg-amber-300/20"
                          : "border-blue-600 bg-blue-300/20"
                    }`}
                    style={{
                      left: `${Number(region.x) * 100}%`,
                      top: `${Number(region.y) * 100}%`,
                      width: `${Number(region.width) * 100}%`,
                      height: `${Number(region.height) * 100}%`,
                    }}
                  />
                ))}
              </div>
            </div>
          </section>
          <aside className="space-y-2 text-sm">
            <label className="grid gap-1">
              框选后分配给题目
              <select
                className="rounded-lg border p-2"
                value={questionId}
                onChange={(event) => setQuestionId(event.target.value)}
              >
                {questionIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            {pageRegions.map((region) => (
              <div key={region.id} className="rounded-lg border p-2">
                <strong>{region.question_id}</strong>
                <div>
                  {region.source} · {region.status} ·{" "}
                  {region.confidence == null
                    ? "无置信度"
                    : `${Math.round(Number(region.confidence) * 100)}%`}
                </div>
                {region.reason && (
                  <div className="text-amber-700">{region.reason}</div>
                )}
                <div className="mt-1 flex gap-1">
                  {region.status !== "confirmed" && (
                    <Button
                      variant="outline"
                      onClick={async () => {
                        await submissionProcessingApi.updateRegion(
                          submissionId,
                          region.id,
                          {
                            question_id: region.question_id,
                            submission_page_id: region.submission_page_id,
                            x: Number(region.x),
                            y: Number(region.y),
                            width: Number(region.width),
                            height: Number(region.height),
                            source: region.source as
                              "manual" | "template" | "ocr" | "alignment",
                            confidence:
                              region.confidence == null
                                ? undefined
                                : Number(region.confidence),
                            status: "confirmed",
                            reason: region.reason,
                          },
                        );
                        await reload();
                      }}
                    >
                      确认
                    </Button>
                  )}
                  <Button
                    variant="danger"
                    onClick={async () => {
                      await submissionProcessingApi.removeRegion(
                        submissionId,
                        region.id,
                      );
                      await reload();
                    }}
                  >
                    删除
                  </Button>
                </div>
              </div>
            ))}
            {!pageRegions.length && (
              <p className="text-amber-700">需要人工切题</p>
            )}
          </aside>
        </div>
      )}
    </Card>
  );
}

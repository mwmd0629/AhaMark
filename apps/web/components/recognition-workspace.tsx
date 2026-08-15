"use client";

import Image from "next/image";
import { type PointerEvent, useEffect, useState } from "react";
import {
  ApiError,
  recognitionApi,
  type RecognitionCandidate,
  type FormulaRegion,
  type FormulaUnreadableReason,
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
  const [formulaRegions, setFormulaRegions] = useState<FormulaRegion[]>([]);
  const [selectedFormula, setSelectedFormula] = useState("");
  const [formulaDrawing, setFormulaDrawing] = useState(false);
  const [formulaRedrawing, setFormulaRedrawing] = useState(false);
  const [formulaStart, setFormulaStart] = useState<{
    x: number;
    y: number;
  }>();
  const [formulaDraft, setFormulaDraft] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
  }>();
  const [formulaLatex, setFormulaLatex] = useState("");
  const [showFormulaAlternatives, setShowFormulaAlternatives] = useState(false);
  const [showUnreadableReasons, setShowUnreadableReasons] = useState(false);
  const [unreadableReason, setUnreadableReason] =
    useState<FormulaUnreadableReason>("severe_overwriting_or_occlusion");
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
        setFormulaRegions(
          await recognitionApi.formulaRegions(assignmentId, next.id),
        );
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [assignmentId, job]);
  const current = candidates.find((candidate) => candidate.id === selected);
  const region = current?.regions[0];
  const page =
    pages.find((item) => item.paper_page_id === region?.paper_page_id) ??
    pages[0];
  const sourceMetrics = page?.processing_parameters;
  const currentFormula = formulaRegions.find(
    (item) => item.id === selectedFormula,
  );
  const currentFormulaCandidate = currentFormula?.candidates[0];
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      y: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
    };
  };
  const updateFormulaDraft = (
    start: { x: number; y: number },
    end: { x: number; y: number },
  ) => {
    const normalized = (value: number) => Number(value.toFixed(6));
    return {
      x: normalized(Math.min(start.x, end.x)),
      y: normalized(Math.min(start.y, end.y)),
      width: normalized(Math.abs(end.x - start.x)),
      height: normalized(Math.abs(end.y - start.y)),
    };
  };
  const reloadFormulas = async (includeAlternatives = false) => {
    if (!job) return;
    const next = await recognitionApi.formulaRegions(
      assignmentId,
      job.id,
      includeAlternatives,
    );
    setFormulaRegions(next);
    const selectedItem = next.find((item) => item.id === selectedFormula);
    setFormulaLatex(selectedItem?.candidates[0]?.latex ?? "");
  };
  return (
    <Card className="space-y-4 p-6" data-testid="recognition-workspace">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-bold">图片处理与 OCR</h2>
          <p className="text-sm text-slate-600">
            识别结果确认前不会进入正式题目。
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
              const next = await recognitionApi.start(
                assignmentId,
                paperVersionId,
              );
              setJob(next);
              if (!["queued", "running"].includes(next.status)) {
                setPages(await recognitionApi.pages(assignmentId, next.id));
                const nextCandidates = await recognitionApi.candidates(
                  assignmentId,
                  next.id,
                );
                setCandidates(nextCandidates);
                setSelected(nextCandidates[0]?.id ?? "");
                setFormulaRegions(
                  await recognitionApi.formulaRegions(assignmentId, next.id),
                );
              }
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
        文字识别：
        {provider?.available ? "可用" : "暂不可用，请稍后重试或人工录入"}
        <br />
        公式识别：
        {provider?.formula.available ? "可用" : "暂不可用，请人工核对公式"}
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
          data-status={job.status}
          className="space-y-2 rounded-xl bg-slate-50 p-3"
        >
          <div className="flex justify-between">
            <span>
              {job.status === "completed"
                ? "识别完成"
                : job.status === "partially_completed"
                  ? "部分页面需要重新识别"
                  : job.status === "failed"
                    ? "识别失败，请按页面提示处理"
                    : "正在识别"}
            </span>
            <span>{job.progress}%</span>
          </div>
          <progress className="w-full" max={100} value={job.progress} />
          <p className="text-xs">
            成功 {job.page_summary.completed} · 失败 {job.page_summary.failed} ·
            待重新识别 {job.page_summary.stale}
          </p>
          {job.error_code && (
            <p className="text-red-700">
              {job.error_code === "CHARACTER_ENCODING_CORRUPTION_DETECTED"
                ? "当前结果不能安全生成题目，请重新识别或人工录入。"
                : job.error_code === "RECOGNITION_PROVIDER_UNAVAILABLE" ||
                    job.error_code === "TRUSTED_TEXT_SOURCE_UNAVAILABLE"
                  ? "当前页面没有可靠文字，请重新扫描或人工录入。"
                  : "页面识别失败，请重试或人工录入。"}
            </p>
          )}
        </div>
      )}
      {!!sourceMetrics?.math_symbol_conflict_count && (
        <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
          这一页的数学符号在不同文字来源中不一致，请对照原页核对后再确认。
        </p>
      )}
      {!sourceMetrics?.math_symbol_conflict_count &&
        !!sourceMetrics?.source_conflict_count && (
          <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
            这一页有文字识别不一致，请对照原页核对后再确认。
          </p>
        )}
      {!!sourceMetrics?.missing_region_count && (
        <p className="rounded-xl bg-blue-50 p-3 text-sm text-blue-900">
          这一页有补充识别的文字区域，请重点核对补充内容。
        </p>
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
              <Button
                variant="outline"
                disabled={!job || !page}
                onClick={() => {
                  setFormulaDrawing((value) => !value);
                  setFormulaRedrawing(false);
                  setFormulaStart(undefined);
                  setFormulaDraft(undefined);
                }}
              >
                {formulaDrawing ? "退出公式框选" : "框选公式"}
              </Button>
            </div>
            {formulaDrawing && (
              <p className="mb-2 text-sm text-blue-700">
                {formulaRedrawing
                  ? "重新框选这条公式。新范围保存后，原识别建议会清除。"
                  : "拖出一个公式区域；保存后仍需单独识别和确认。"}
              </p>
            )}
            <div
              className={`relative overflow-hidden rounded-xl border bg-slate-100 ${formulaDrawing ? "cursor-crosshair" : ""}`}
              onPointerDown={(event) => {
                if (!formulaDrawing) return;
                event.currentTarget.setPointerCapture?.(event.pointerId);
                const start = point(event);
                setFormulaStart(start);
                setFormulaDraft({ ...start, width: 0, height: 0 });
              }}
              onPointerMove={(event) => {
                if (!formulaDrawing || !formulaStart) return;
                setFormulaDraft(updateFormulaDraft(formulaStart, point(event)));
              }}
              onPointerUp={async (event) => {
                if (!formulaDrawing || !formulaStart || !job || !page) return;
                const draft = updateFormulaDraft(formulaStart, point(event));
                setFormulaStart(undefined);
                setFormulaDraft(undefined);
                if (draft.width < 0.01 || draft.height < 0.01) return;
                try {
                  const saved = formulaRedrawing
                    ? await recognitionApi.updateFormulaRegion(
                        assignmentId,
                        job.id,
                        selectedFormula,
                        { region_kind: "unknown", ...draft },
                      )
                    : await recognitionApi.createFormulaRegion(
                        assignmentId,
                        job.id,
                        {
                          paper_page_id: page.paper_page_id,
                          region_kind: "unknown",
                          ...draft,
                        },
                      );
                  setFormulaRegions((old) =>
                    formulaRedrawing
                      ? old.map((item) => (item.id === saved.id ? saved : item))
                      : [...old, saved],
                  );
                  setSelectedFormula(saved.id);
                  setFormulaLatex("");
                  setFormulaDrawing(false);
                  setFormulaRedrawing(false);
                } catch (reason) {
                  setError(
                    reason instanceof ApiError
                      ? reason.message
                      : "无法保存公式区域",
                  );
                }
              }}
            >
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
              {formulaRegions
                .filter((item) => item.paper_page_id === page?.paper_page_id)
                .map((item) => (
                  <button
                    key={item.id}
                    aria-label="公式区域"
                    className={`absolute border-2 ${item.id === selectedFormula ? "border-violet-700 bg-violet-300/25" : "border-violet-500 bg-violet-200/15"}`}
                    style={{
                      left: `${Number(item.region.x) * 100}%`,
                      top: `${Number(item.region.y) * 100}%`,
                      width: `${Number(item.region.width) * 100}%`,
                      height: `${Number(item.region.height) * 100}%`,
                    }}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => {
                      setSelectedFormula(item.id);
                      setFormulaLatex(item.candidates[0]?.latex ?? "");
                    }}
                  />
                ))}
              {formulaDraft && (
                <span
                  className="pointer-events-none absolute border-2 border-dashed border-violet-700 bg-violet-200/20"
                  style={{
                    left: `${formulaDraft.x * 100}%`,
                    top: `${formulaDraft.y * 100}%`,
                    width: `${formulaDraft.width * 100}%`,
                    height: `${formulaDraft.height * 100}%`,
                  }}
                />
              )}
            </div>
            {currentFormula && (
              <div className="mt-3 space-y-3 rounded-xl border border-violet-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-semibold">公式识别</h3>
                  <Badge
                    status={
                      currentFormula.status === "confirmed"
                        ? "completed"
                        : currentFormula.status === "rejected"
                          ? "failed"
                          : "pending-review"
                    }
                  />
                </div>
                {currentFormula.status === "rejected" ? (
                  <p className="text-sm text-slate-600">
                    已标记为无法可靠识别，不会采用识别结果。
                  </p>
                ) : !currentFormulaCandidate ? (
                  <Button
                    disabled={!provider?.formula.available}
                    onClick={async () => {
                      try {
                        const next = await recognitionApi.recognizeFormula(
                          assignmentId,
                          job!.id,
                          currentFormula.id,
                        );
                        setFormulaRegions((old) =>
                          old.map((item) =>
                            item.id === next.id ? next : item,
                          ),
                        );
                        setFormulaLatex(next.candidates[0]?.latex ?? "");
                      } catch (reason) {
                        if (
                          reason instanceof ApiError &&
                          reason.body.code === "FORMULA_IMAGE_QUALITY_BLOCKED"
                        ) {
                          setShowUnreadableReasons(false);
                          setError(
                            "这一区域无法可靠识别，请重新框选或标记无法识别。",
                          );
                        } else {
                          setError(
                            reason instanceof ApiError
                              ? reason.message
                              : "公式识别失败",
                          );
                        }
                      }
                    }}
                  >
                    识别公式
                  </Button>
                ) : (
                  <>
                    <Input
                      label="公式"
                      value={formulaLatex}
                      onChange={(event) => setFormulaLatex(event.target.value)}
                    />
                    {currentFormula.has_alternatives && (
                      <button
                        className="text-sm text-blue-700 underline"
                        onClick={async () => {
                          const next = !showFormulaAlternatives;
                          setShowFormulaAlternatives(next);
                          await reloadFormulas(next);
                        }}
                      >
                        {showFormulaAlternatives
                          ? "收起其他结果"
                          : "查看其他结果"}
                      </button>
                    )}
                    {showFormulaAlternatives &&
                      currentFormula.candidates.slice(1).map((candidate) => (
                        <button
                          key={candidate.id}
                          className="block w-full rounded-lg border p-2 text-left text-sm"
                          onClick={() => setFormulaLatex(candidate.latex)}
                        >
                          {candidate.latex}
                        </button>
                      ))}
                    <div className="flex gap-2">
                      <Button
                        onClick={async () => {
                          const next =
                            await recognitionApi.disposeFormulaCandidate(
                              assignmentId,
                              job!.id,
                              currentFormula.id,
                              currentFormulaCandidate.id,
                              {
                                action: "accept",
                                explicit_confirmation: true,
                                edited_latex: formulaLatex,
                              },
                            );
                          setFormulaRegions((old) =>
                            old.map((item) =>
                              item.id === next.id ? next : item,
                            ),
                          );
                        }}
                      >
                        确认公式
                      </Button>
                      <Button
                        variant="danger"
                        onClick={async () => {
                          const next =
                            await recognitionApi.disposeFormulaCandidate(
                              assignmentId,
                              job!.id,
                              currentFormula.id,
                              currentFormulaCandidate.id,
                              {
                                action: "reject",
                                explicit_confirmation: true,
                              },
                            );
                          setFormulaRegions((old) =>
                            old.map((item) =>
                              item.id === next.id ? next : item,
                            ),
                          );
                        }}
                      >
                        不是这个公式
                      </Button>
                    </div>
                  </>
                )}
                {currentFormula.status !== "confirmed" && (
                  <div className="space-y-2 border-t pt-3">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        onClick={() => {
                          setFormulaDrawing(true);
                          setFormulaRedrawing(true);
                          setFormulaStart(undefined);
                          setFormulaDraft(undefined);
                          setShowUnreadableReasons(false);
                          setError("");
                        }}
                      >
                        重新框选
                      </Button>
                      {currentFormula.status !== "rejected" && (
                        <Button
                          variant="outline"
                          onClick={() =>
                            setShowUnreadableReasons((value) => !value)
                          }
                        >
                          标记无法识别
                        </Button>
                      )}
                    </div>
                    {showUnreadableReasons &&
                      currentFormula.status !== "rejected" && (
                        <div className="space-y-2 rounded-lg bg-amber-50 p-3">
                          <label
                            className="block text-sm font-medium"
                            htmlFor="formula-unreadable-reason"
                          >
                            原因
                          </label>
                          <select
                            id="formula-unreadable-reason"
                            className="w-full rounded-lg border bg-white p-2 text-sm"
                            value={unreadableReason}
                            onChange={(event) =>
                              setUnreadableReason(
                                event.target.value as FormulaUnreadableReason,
                              )
                            }
                          >
                            <option value="severe_overwriting_or_occlusion">
                              涂改或遮挡严重
                            </option>
                            <option value="crop_incomplete">
                              公式没有截全
                            </option>
                            <option value="blurred_or_too_faint">
                              模糊或字迹太淡
                            </option>
                            <option value="subscript_ambiguous">
                              上下标无法判断
                            </option>
                            <option value="ruled_paper_line_ambiguous">
                              纸张横线与公式混在一起
                            </option>
                            <option value="other_image_quality_issue">
                              其他图像问题
                            </option>
                          </select>
                          <Button
                            variant="danger"
                            onClick={async () => {
                              try {
                                const next =
                                  await recognitionApi.markFormulaUnreadable(
                                    assignmentId,
                                    job!.id,
                                    currentFormula.id,
                                    unreadableReason,
                                  );
                                setFormulaRegions((old) =>
                                  old.map((item) =>
                                    item.id === next.id ? next : item,
                                  ),
                                );
                                setFormulaLatex("");
                                setShowUnreadableReasons(false);
                                setError("");
                              } catch (reason) {
                                setError(
                                  reason instanceof ApiError
                                    ? reason.message
                                    : "无法标记公式",
                                );
                              }
                            }}
                          >
                            确认标记
                          </Button>
                        </div>
                      )}
                  </div>
                )}
              </div>
            )}
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
                  label="LaTeX（公式识别不可用时为空）"
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

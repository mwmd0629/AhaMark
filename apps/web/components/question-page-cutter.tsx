"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Button, Input, Select, useToast } from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  type AssignmentRecord,
  type QuestionRecord,
} from "@/lib/api";
import {
  displayToOriginal,
  originalToDisplay,
  type Region,
} from "@/lib/region-coordinates";

type PaperPage = NonNullable<
  AssignmentRecord["paper_version"]
>["pages"][number];

type Preview = { url: string; width: number; height: number };

function clamp(value: number) {
  return Math.min(1, Math.max(0, value));
}

function rounded(value: number) {
  return Number(value.toFixed(6));
}

function regionBetween(
  start: { x: number; y: number },
  end: { x: number; y: number },
): Region {
  const left = clamp(Math.min(start.x, end.x));
  const top = clamp(Math.min(start.y, end.y));
  const right = clamp(Math.max(start.x, end.x));
  const bottom = clamp(Math.max(start.y, end.y));
  return {
    x: rounded(left),
    y: rounded(top),
    width: rounded(right - left),
    height: rounded(bottom - top),
  };
}

function normalized(region: Region): Region {
  const x = rounded(clamp(region.x));
  const y = rounded(clamp(region.y));
  return {
    x,
    y,
    width: rounded(Math.min(clamp(region.width), 1 - x)),
    height: rounded(Math.min(clamp(region.height), 1 - y)),
  };
}

function suggestedQuestionNumber(questions: QuestionRecord[]) {
  const numeric = questions
    .map((question) => Number(question.question_number))
    .filter((value) => Number.isInteger(value) && value > 0);
  return String(
    numeric.length ? Math.max(...numeric) + 1 : questions.length + 1,
  );
}

export function QuestionPageCutter({
  assignmentId,
  page,
  questions,
  onCreated,
  onChanged,
}: {
  assignmentId: string;
  page: PaperPage;
  questions: QuestionRecord[];
  onCreated: (question: QuestionRecord) => Promise<void> | void;
  onChanged: () => Promise<void> | void;
}) {
  const toast = useToast();
  const [preview, setPreview] = useState<Preview>();
  const [previewError, setPreviewError] = useState("");
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [imageReady, setImageReady] = useState(false);
  const [selection, setSelection] = useState<Region>();
  const [saving, setSaving] = useState(false);
  const [deletingQuestionId, setDeletingQuestionId] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    number: suggestedQuestionNumber(questions),
    type: "calculation",
    score: "",
    content: "",
    knowledge: "",
  });
  const nextQuestionNumber = useMemo(
    () => suggestedQuestionNumber(questions),
    [questions],
  );
  const dragRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    clientX: number;
    clientY: number;
  } | null>(null);

  useEffect(() => {
    let active = true;
    setPreview(undefined);
    setPreviewError("");
    setImageReady(false);
    setSelection(undefined);
    assignmentsApi
      .pagePreview(assignmentId, page.id)
      .then((result) => {
        if (active) setPreview(result);
      })
      .catch((reason) => {
        if (!active) return;
        setPreviewError(
          reason instanceof ApiError ? reason.message : "页面图片生成失败",
        );
      });
    return () => {
      active = false;
    };
  }, [assignmentId, page.id, page.rotation, previewAttempt]);

  useEffect(() => {
    setForm({
      number: nextQuestionNumber,
      type: "calculation",
      score: "",
      content: "",
      knowledge: "",
    });
  }, [nextQuestionNumber, page.id]);

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        dragRef.current = null;
        setSelection(undefined);
      }
    };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, []);

  const pageRegions = useMemo(
    () =>
      questions.flatMap((question) =>
        question.regions
          .filter((region) => region.paper_page_id === page.id)
          .map((region) => ({
            id: region.id,
            questionId: question.id,
            label: `第 ${question.question_number} 题`,
            region: originalToDisplay(
              {
                x: Number(region.x),
                y: Number(region.y),
                width: Number(region.width),
                height: Number(region.height),
              },
              page.rotation,
            ),
          })),
      ),
    [page.id, page.rotation, questions],
  );

  const point = (event: ReactPointerEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width),
      y: clamp((event.clientY - bounds.top) / bounds.height),
    };
  };

  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (
      saving ||
      !imageReady ||
      page.status !== "ready" ||
      event.button !== 0 ||
      event.isPrimary === false
    )
      return;
    const start = point(event);
    dragRef.current = {
      pointerId: event.pointerId,
      ...start,
      clientX: event.clientX,
      clientY: event.clientY,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setSelection({ x: start.x, y: start.y, width: 0, height: 0 });
  };

  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    setSelection(regionBetween(start, point(event)));
  };

  const finishPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const finalRegion = regionBetween(start, point(event));
    const bounds = event.currentTarget.getBoundingClientRect();
    if (
      finalRegion.width * bounds.width < 8 ||
      finalRegion.height * bounds.height < 8
    ) {
      setSelection(undefined);
      toast("框选范围太小，请重新拖动", "error");
      return;
    }
    setSelection(finalRegion);
  };

  const cancelPointer = () => {
    dragRef.current = null;
    setSelection(undefined);
  };

  const save = async () => {
    const number = form.number.trim();
    const score = Number(form.score);
    if (!selection) {
      setError("请先在页面图片上拖动框选一道题目");
      return;
    }
    if (selection.width <= 0 || selection.height <= 0) {
      setError("框选范围太小，请重新拖动");
      return;
    }
    if (!number || !Number.isFinite(score) || score <= 0) {
      setError("请填写题号和大于 0 的分值");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const region = normalized(displayToOriginal(selection, page.rotation));
      const created = await assignmentsApi.cutQuestion(assignmentId, page.id, {
        question: {
          question_number: number,
          question_type: form.type,
          max_score: score,
          content_text: form.content.trim() || null,
          difficulty: "medium",
          knowledge_points: form.knowledge
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
        },
        region: {
          paper_page_id: page.id,
          ...region,
          region_type: "question",
        },
      });
      await onCreated(created);
      setSelection(undefined);
      setForm({
        number: suggestedQuestionNumber([...questions, created]),
        type: form.type,
        score: "",
        content: "",
        knowledge: "",
      });
      toast(`第 ${created.question_number} 题已切分并保存`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "切题保存失败");
    } finally {
      setSaving(false);
    }
  };

  const removeCutQuestion = async (questionId: string, label: string) => {
    if (!confirm(`确认删除${label}及其所有切题区域？`)) return;
    setDeletingQuestionId(questionId);
    setError("");
    try {
      await assignmentsApi.removeQuestion(assignmentId, questionId);
      await onChanged();
      toast(`${label}已删除`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "题目删除失败");
    } finally {
      setDeletingQuestionId("");
    }
  };

  const ratio = preview ? preview.width / preview.height : 0.72;
  const canvasWidth = Math.max(280, Math.round(560 * ratio));

  return (
    <section className="space-y-4 border-t pt-4" aria-label="试卷切题">
      <div>
        <h3 className="font-semibold">拖拽切分题目</h3>
        <p className="text-sm text-slate-600">
          在页面图片上按住并拖动框选一道题目，再填写题号、题型和分值。蓝框为已保存题目，绿框为当前选择。
        </p>
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid min-h-[420px] place-items-center rounded-xl bg-slate-100 p-3">
          {preview ? (
            <div
              className="relative max-w-full overflow-hidden bg-white shadow"
              style={{
                aspectRatio: `${preview.width} / ${preview.height}`,
                width: `min(100%, ${canvasWidth}px)`,
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={preview.url}
                alt={`第 ${page.page_number} 页切题预览`}
                draggable={false}
                className="absolute inset-0 h-full w-full select-none"
                onLoad={() => {
                  setImageReady(true);
                  setPreviewError("");
                }}
                onError={() => {
                  setImageReady(false);
                  setPreviewError("页面图片加载失败，请重试");
                }}
              />
              <div
                role="application"
                aria-label="题目切割画布"
                aria-disabled={!imageReady || page.status !== "ready"}
                className={`absolute inset-0 touch-none ${imageReady && page.status === "ready" ? "cursor-crosshair" : "cursor-not-allowed"}`}
                onPointerDown={pointerDown}
                onPointerMove={pointerMove}
                onPointerUp={finishPointer}
                onPointerCancel={cancelPointer}
                onLostPointerCapture={() => {
                  dragRef.current = null;
                }}
              >
                {pageRegions.map((item) => (
                  <div
                    key={item.id}
                    className="pointer-events-none absolute border-2 border-sky-500 bg-sky-300/15"
                    style={{
                      left: `${item.region.x * 100}%`,
                      top: `${item.region.y * 100}%`,
                      width: `${item.region.width * 100}%`,
                      height: `${item.region.height * 100}%`,
                    }}
                  >
                    <span className="absolute left-0 top-0 bg-sky-600 px-1.5 py-0.5 text-xs font-semibold text-white">
                      {item.label}
                    </span>
                  </div>
                ))}
                {selection && (
                  <div
                    data-testid="draft-question-region"
                    className="pointer-events-none absolute border-2 border-emerald-500 bg-emerald-300/20"
                    style={{
                      left: `${selection.x * 100}%`,
                      top: `${selection.y * 100}%`,
                      width: `${selection.width * 100}%`,
                      height: `${selection.height * 100}%`,
                    }}
                  />
                )}
              </div>
              {!imageReady && (
                <div className="absolute inset-0 z-20 grid place-items-center bg-slate-100/90 p-4 text-center">
                  <div className="space-y-3">
                    <p
                      role={previewError ? "alert" : undefined}
                      className={
                        previewError
                          ? "text-sm text-red-700"
                          : "text-sm text-slate-500"
                      }
                    >
                      {previewError || "正在加载页面图片…"}
                    </p>
                    {previewError && (
                      <Button
                        variant="outline"
                        onClick={() =>
                          setPreviewAttempt((current) => current + 1)
                        }
                      >
                        重试预览
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : previewError ? (
            <div className="space-y-3 text-center">
              <p role="alert" className="text-sm text-red-700">
                {previewError}
              </p>
              <Button
                variant="outline"
                onClick={() => setPreviewAttempt((current) => current + 1)}
              >
                重试预览
              </Button>
            </div>
          ) : (
            <p className="text-sm text-slate-500">正在生成逐页预览…</p>
          )}
        </div>
        <div className="space-y-3 rounded-xl border bg-white p-4">
          <Input
            label="题号"
            value={form.number}
            onChange={(event) =>
              setForm({ ...form, number: event.target.value })
            }
          />
          <Select
            label="题型"
            value={form.type}
            onChange={(event) => setForm({ ...form, type: event.target.value })}
          >
            <option value="calculation">计算题</option>
            <option value="short_answer">简答题</option>
            <option value="single_choice">单选题</option>
            <option value="multiple_choice">多选题</option>
            <option value="true_false">判断题</option>
            <option value="fill_blank">填空题</option>
            <option value="proof">证明题</option>
            <option value="essay">论述题</option>
            <option value="other">其他</option>
          </Select>
          <Input
            label="分值"
            type="number"
            min="0.01"
            step="0.01"
            value={form.score}
            onChange={(event) =>
              setForm({ ...form, score: event.target.value })
            }
          />
          <Input
            label="题目内容（可选）"
            value={form.content}
            onChange={(event) =>
              setForm({ ...form, content: event.target.value })
            }
          />
          <Input
            label="知识点（逗号分隔，可选）"
            value={form.knowledge}
            onChange={(event) =>
              setForm({ ...form, knowledge: event.target.value })
            }
          />
          {selection && (
            <p className="text-xs text-slate-500">
              已框选页面宽度的 {Math.round(selection.width * 100)}%、高度的{" "}
              {Math.round(selection.height * 100)}%。
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="rounded-lg bg-red-50 p-2 text-sm text-red-700"
            >
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              loading={saving}
              disabled={!imageReady || page.status !== "ready"}
              onClick={() => void save()}
            >
              保存为新题目
            </Button>
            <Button
              variant="outline"
              disabled={saving || !imageReady}
              onClick={() => setSelection({ x: 0, y: 0, width: 1, height: 1 })}
            >
              选择整页
            </Button>
            <Button
              variant="ghost"
              disabled={saving || !selection}
              onClick={() => setSelection(undefined)}
            >
              清除框选
            </Button>
          </div>
          <p className="text-xs text-slate-500">
            本页已保存 {pageRegions.length} 个题目区域。
          </p>
          {pageRegions.length > 0 && (
            <ul className="space-y-2" aria-label="本页已切分题目">
              {pageRegions.map((item) => (
                <li
                  key={item.id}
                  className="flex items-center justify-between gap-3 rounded-lg bg-sky-50 px-3 py-2 text-sm"
                >
                  <span>{item.label}</span>
                  <button
                    type="button"
                    className="font-medium text-red-700 disabled:opacity-50"
                    disabled={Boolean(deletingQuestionId) || saving}
                    onClick={() =>
                      void removeCutQuestion(item.questionId, item.label)
                    }
                  >
                    {deletingQuestionId === item.questionId
                      ? "删除中…"
                      : "删除该题"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

"use client";
/* eslint-disable @next/next/no-img-element */

import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { Button, Input, Select } from "@/components/ui";
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

type Props = {
  assignmentId: string;
  page: PaperPage;
  questions: QuestionRecord[];
  selectedQuestionId?: string;
  onSaved: (question: QuestionRecord) => void | Promise<void>;
};

const emptyDraft = {
  number: "",
  type: "calculation",
  score: "",
  text: "",
  knowledge: "",
};

export function QuestionPageCutter({
  assignmentId,
  page,
  questions,
  selectedQuestionId,
  onSaved,
}: Props) {
  const [enabled, setEnabled] = useState(false);
  const [preview, setPreview] = useState<{
    url: string;
    rotation: 0 | 90 | 180 | 270;
  }>();
  const [mode, setMode] = useState<"new" | "existing">(
    selectedQuestionId &&
      questions.some((question) => question.id === selectedQuestionId)
      ? "existing"
      : "new",
  );
  const [targetId, setTargetId] = useState(
    selectedQuestionId ?? questions[0]?.id ?? "",
  );
  const [draftQuestion, setDraftQuestion] = useState(emptyDraft);
  const [drawn, setDrawn] = useState<Region>();
  const [start, setStart] = useState<{ x: number; y: number }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const overlayRef = useRef<HTMLDivElement>(null);
  const previewRequestRef = useRef(0);

  useEffect(() => {
    previewRequestRef.current += 1;
    setEnabled(false);
    setPreview(undefined);
    setDrawn(undefined);
    setStart(undefined);
    setError("");
  }, [page.id, page.rotation]);

  useEffect(() => {
    if (
      selectedQuestionId &&
      questions.some((question) => question.id === selectedQuestionId)
    ) {
      setTargetId(selectedQuestionId);
    } else if (!questions.some((question) => question.id === targetId)) {
      setTargetId(questions[0]?.id ?? "");
    }
  }, [questions, selectedQuestionId, targetId]);

  const savedRegions = useMemo(
    () =>
      questions.flatMap((question) =>
        question.regions
          .filter((region) => region.paper_page_id === page.id)
          .map((region) => ({
            id: region.id,
            questionNumber: question.question_number,
            area: originalToDisplay(
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

  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = overlayRef.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return undefined;
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    };
  };

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (!enabled || busy) return;
    const current = point(event);
    if (!current) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setStart(current);
    setDrawn({ ...current, width: 0, height: 0 });
    setError("");
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (!start || !event.currentTarget.hasPointerCapture(event.pointerId))
      return;
    const current = point(event);
    if (!current) return;
    setDrawn({
      x: Math.min(start.x, current.x),
      y: Math.min(start.y, current.y),
      width: Math.abs(current.x - start.x),
      height: Math.abs(current.y - start.y),
    });
  };

  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setStart(undefined);
  };

  const enable = async () => {
    if (page.status !== "ready") return;
    const requestId = previewRequestRef.current + 1;
    previewRequestRef.current = requestId;
    setBusy(true);
    setError("");
    try {
      const result = await assignmentsApi.pagePreview(assignmentId, page.id);
      if (previewRequestRef.current !== requestId) return;
      setPreview({ url: result.url, rotation: result.rotation });
      setEnabled(true);
    } catch (reason) {
      if (previewRequestRef.current !== requestId) return;
      setError(
        reason instanceof ApiError ? reason.message : "切题预览加载失败",
      );
    } finally {
      if (previewRequestRef.current === requestId) setBusy(false);
    }
  };

  const save = async () => {
    if (!drawn || drawn.width < 0.01 || drawn.height < 0.01) {
      setError("请先拖拽框选完整题目区域");
      return;
    }
    if (mode === "existing" && !targetId) {
      setError("请选择要追加页面区域的题目");
      return;
    }
    const score = Number(draftQuestion.score);
    if (
      mode === "new" &&
      (!draftQuestion.number.trim() || !Number.isFinite(score) || score <= 0)
    ) {
      setError("新题必须填写题号和大于 0 的分值");
      return;
    }

    setBusy(true);
    setError("");
    try {
      const original = displayToOriginal(
        drawn,
        preview?.rotation ?? page.rotation,
      );
      const region = {
        paper_page_id: page.id,
        x: original.x,
        y: original.y,
        width: original.width,
        height: original.height,
        region_type: "question" as const,
      };
      const payload =
        mode === "existing"
          ? { question_id: targetId, region }
          : {
              question: {
                question_number: draftQuestion.number.trim(),
                question_type: draftQuestion.type,
                max_score: score,
                content_text: draftQuestion.text.trim() || undefined,
                difficulty: "medium" as const,
                knowledge_points: draftQuestion.knowledge
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              },
              region,
            };
      const question = await assignmentsApi.cutQuestion(
        assignmentId,
        page.id,
        payload,
      );
      await onSaved(question);
      setDraftQuestion(emptyDraft);
      setDrawn(undefined);
      setEnabled(false);
      setPreview(undefined);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "题目区域保存失败",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!enabled) {
    return (
      <section className="rounded-xl border border-dashed bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">手动框选题目</h3>
            <p className="mt-1 text-sm text-slate-500">
              默认关闭；需要补题或修正自动识别时再启动，不会自动写入题目。
            </p>
          </div>
          <Button
            loading={busy}
            disabled={page.status !== "ready"}
            onClick={() => void enable()}
          >
            开始手动切题
          </Button>
        </div>
        {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
      </section>
    );
  }

  return (
    <section
      className="space-y-4 rounded-xl border bg-white p-4"
      data-draw-enabled="true"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold">手动框选题目</h3>
          <p className="text-sm text-slate-500">
            在图片上拖拽一个矩形，再选择新建题目或追加到已有题目。
          </p>
        </div>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => {
            previewRequestRef.current += 1;
            setEnabled(false);
            setPreview(undefined);
            setDrawn(undefined);
          }}
        >
          退出手动切题
        </Button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm font-medium">
          保存方式
          <Select
            className="mt-1"
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as "new" | "existing")
            }
          >
            <option value="new">新建题目</option>
            <option value="existing" disabled={!questions.length}>
              追加到已有题目（跨页题）
            </option>
          </Select>
        </label>
        {mode === "existing" && (
          <label className="text-sm font-medium">
            已有题目
            <Select
              className="mt-1"
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
            >
              {questions.map((question) => (
                <option value={question.id} key={question.id}>
                  第 {question.question_number} 题
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>

      {mode === "new" && (
        <div className="grid gap-3 md:grid-cols-2">
          <Input
            aria-label="新题题号"
            placeholder="题号，例如 3"
            value={draftQuestion.number}
            onChange={(event) =>
              setDraftQuestion((value) => ({
                ...value,
                number: event.target.value,
              }))
            }
          />
          <Input
            aria-label="新题分值"
            type="number"
            min="0.01"
            step="0.5"
            placeholder="分值"
            value={draftQuestion.score}
            onChange={(event) =>
              setDraftQuestion((value) => ({
                ...value,
                score: event.target.value,
              }))
            }
          />
          <Select
            aria-label="新题题型"
            value={draftQuestion.type}
            onChange={(event) =>
              setDraftQuestion((value) => ({
                ...value,
                type: event.target.value,
              }))
            }
          >
            <option value="calculation">计算题</option>
            <option value="short_answer">简答题</option>
            <option value="choice">选择题</option>
            <option value="fill_blank">填空题</option>
            <option value="proof">证明题</option>
          </Select>
          <Input
            aria-label="新题知识点"
            placeholder="知识点，逗号分隔"
            value={draftQuestion.knowledge}
            onChange={(event) =>
              setDraftQuestion((value) => ({
                ...value,
                knowledge: event.target.value,
              }))
            }
          />
          <Input
            className="md:col-span-2"
            aria-label="新题题干"
            placeholder="题干（可选）"
            value={draftQuestion.text}
            onChange={(event) =>
              setDraftQuestion((value) => ({
                ...value,
                text: event.target.value,
              }))
            }
          />
        </div>
      )}

      <div className="overflow-auto rounded-xl bg-slate-100 p-3">
        <div className="relative mx-auto w-fit max-w-full select-none touch-none">
          <img
            src={preview?.url}
            alt={`第 ${page.page_number} 页切题预览`}
            className="max-h-[720px] max-w-full"
          />
          <div
            ref={overlayRef}
            className="absolute inset-0 cursor-crosshair"
            aria-label="题目框选画布"
            onPointerDown={begin}
            onPointerMove={move}
            onPointerUp={finish}
            onPointerCancel={finish}
          >
            {savedRegions.map(({ id, questionNumber, area }) => (
              <div
                key={id}
                className="pointer-events-none absolute border-2 border-emerald-600 bg-emerald-300/15"
                style={{
                  left: `${area.x * 100}%`,
                  top: `${area.y * 100}%`,
                  width: `${area.width * 100}%`,
                  height: `${area.height * 100}%`,
                }}
              >
                <span className="bg-emerald-700 px-1 text-xs text-white">
                  第 {questionNumber} 题
                </span>
              </div>
            ))}
            {drawn && (
              <div
                className="pointer-events-none absolute border-2 border-blue-600 bg-blue-300/20"
                style={{
                  left: `${drawn.x * 100}%`,
                  top: `${drawn.y * 100}%`,
                  width: `${drawn.width * 100}%`,
                  height: `${drawn.height * 100}%`,
                }}
              />
            )}
          </div>
        </div>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <Button loading={busy} onClick={() => void save()}>
        保存框选区域
      </Button>
    </section>
  );
}

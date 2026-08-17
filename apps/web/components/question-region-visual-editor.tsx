"use client";
/* eslint-disable @next/next/no-img-element */

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Button } from "@/components/ui";
import { ApiError, assignmentsApi } from "@/lib/api";
import {
  displayToOriginal,
  originalToDisplay,
  type Region,
} from "@/lib/region-coordinates";

export type QuestionRegionEdit = {
  paper_page_id: string;
  x: string;
  y: string;
  width: string;
  height: string;
};

export type QuestionRegionPage = {
  paper_page_id: string;
  current_page_number: number;
  current_status: string;
};

type Preview = { url: string; rotation: 0 | 90 | 180 | 270 };

function rotation(value: number): 0 | 90 | 180 | 270 {
  return value === 90 || value === 180 || value === 270 ? value : 0;
}

function numberRegion(value: QuestionRegionEdit): Region | undefined {
  const region = {
    x: Number(value.x),
    y: Number(value.y),
    width: Number(value.width),
    height: Number(value.height),
  };
  return Object.values(region).every(Number.isFinite) ? region : undefined;
}

function coordinate(value: number) {
  return String(Number(value.toFixed(4)));
}

export function QuestionRegionVisualEditor({
  assignmentId,
  pages,
  regions,
  questionLabel,
  disabled,
  onChange,
}: {
  assignmentId: string;
  pages: QuestionRegionPage[];
  regions: QuestionRegionEdit[];
  questionLabel: string;
  disabled: boolean;
  onChange: (regions: QuestionRegionEdit[]) => void;
}) {
  const [pageId, setPageId] = useState(regions[0]?.paper_page_id ?? "");
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [selectedRegion, setSelectedRegion] = useState<number>();
  const [drawingMode, setDrawingMode] = useState<"add" | "replace">();
  const [start, setStart] = useState<{ x: number; y: number }>();
  const [drawn, setDrawn] = useState<Region>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!pages.some((page) => page.paper_page_id === pageId)) {
      setPageId(regions[0]?.paper_page_id ?? pages[0]?.paper_page_id ?? "");
    }
  }, [pageId, pages, regions]);

  useEffect(() => {
    if (selectedRegion !== undefined && !regions[selectedRegion]) {
      setSelectedRegion(undefined);
    }
  }, [regions, selectedRegion]);

  const page = pages.find((item) => item.paper_page_id === pageId);
  const preview = previews[pageId];
  const visibleRegions = regions.flatMap((item, index) => {
    if (item.paper_page_id !== pageId) return [];
    const area = numberRegion(item);
    return area
      ? [{ index, area: originalToDisplay(area, preview?.rotation ?? 0) }]
      : [];
  });

  const point = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = overlayRef.current?.getBoundingClientRect();
    if (!bounds?.width || !bounds.height) return undefined;
    return {
      x: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      y: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
    };
  };

  const rectangle = (current: { x: number; y: number }) =>
    start
      ? {
          x: Math.min(start.x, current.x),
          y: Math.min(start.y, current.y),
          width: Math.abs(current.x - start.x),
          height: Math.abs(current.y - start.y),
        }
      : undefined;

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (!drawingMode || disabled) return;
    const current = point(event);
    if (!current) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setStart(current);
    setDrawn({ ...current, width: 0, height: 0 });
    setError("");
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const current = point(event);
    if (!current) return;
    const next = rectangle(current);
    if (next) setDrawn(next);
  };

  const finish = (event: PointerEvent<HTMLDivElement>) => {
    const current = point(event);
    const next = current ? rectangle(current) : drawn;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setStart(undefined);
    if (!drawingMode || !next) return;
    if (next.width < 0.005 || next.height < 0.005) {
      setError("框选区域过小，请重新拖拽");
      setDrawn(undefined);
      return;
    }
    const original = displayToOriginal(next, preview?.rotation ?? 0);
    const value: QuestionRegionEdit = {
      paper_page_id: pageId,
      x: coordinate(original.x),
      y: coordinate(original.y),
      width: coordinate(original.width),
      height: coordinate(original.height),
    };
    if (drawingMode === "replace" && selectedRegion !== undefined) {
      onChange(
        regions.map((region, index) =>
          index === selectedRegion ? value : region,
        ),
      );
    } else {
      onChange([...regions, value]);
      setSelectedRegion(regions.length);
    }
    setDrawingMode(undefined);
    setDrawn(undefined);
  };

  const loadPreview = async () => {
    if (!page) return;
    setLoading(true);
    setError("");
    try {
      const result = await assignmentsApi.pagePreview(
        assignmentId,
        page.paper_page_id,
      );
      setPreviews((old) => ({
        ...old,
        [page.paper_page_id]: {
          url: result.url,
          rotation: rotation(result.rotation),
        },
      }));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "页面预览加载失败",
      );
    } finally {
      setLoading(false);
    }
  };

  if (!pages.length) return null;

  return (
    <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">
          可视化页面
          <select
            aria-label={`${questionLabel}可视化页面`}
            className="mt-1 block rounded border bg-white p-2"
            value={pageId}
            onChange={(event) => {
              setPageId(event.target.value);
              setSelectedRegion(undefined);
              setDrawingMode(undefined);
              setDrawn(undefined);
            }}
          >
            {pages.map((item) => (
              <option key={item.paper_page_id} value={item.paper_page_id}>
                第 {item.current_page_number} 页（
                {
                  regions.filter(
                    (region) => region.paper_page_id === item.paper_page_id,
                  ).length
                }
                个区域）
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="outline"
          loading={loading}
          disabled={disabled || page?.current_status !== "ready"}
          onClick={() => void loadPreview()}
        >
          {preview
            ? "刷新页面预览"
            : `加载第 ${page?.current_page_number} 页预览`}
        </Button>
        {preview && (
          <>
            <Button
              variant="outline"
              disabled={disabled}
              aria-pressed={drawingMode === "add"}
              onClick={() => {
                setDrawingMode("add");
                setDrawn(undefined);
              }}
            >
              框选新增区域
            </Button>
            <Button
              variant="outline"
              disabled={disabled || selectedRegion === undefined}
              aria-pressed={drawingMode === "replace"}
              onClick={() => {
                setDrawingMode("replace");
                setDrawn(undefined);
              }}
            >
              重画选中区域
            </Button>
          </>
        )}
        <Button
          variant="outline"
          disabled={disabled || !pageId}
          onClick={() => {
            const wholePage: QuestionRegionEdit = {
              paper_page_id: pageId,
              x: "0",
              y: "0",
              width: "1",
              height: "1",
            };
            if (selectedRegion !== undefined) {
              onChange(
                regions.map((region, index) =>
                  index === selectedRegion ? wholePage : region,
                ),
              );
            } else {
              onChange([...regions, wholePage]);
              setSelectedRegion(regions.length);
            }
          }}
        >
          整页作为区域
        </Button>
        <Button
          variant="outline"
          disabled={
            disabled || selectedRegion === undefined || regions.length <= 1
          }
          onClick={() => {
            if (selectedRegion === undefined) return;
            onChange(
              regions.filter((_region, index) => index !== selectedRegion),
            );
            setSelectedRegion(undefined);
            setDrawingMode(undefined);
          }}
        >
          移除选中区域
        </Button>
      </div>
      <p className="mt-2 text-xs text-[var(--neutral-600)]">
        加载页面后可直接拖框；蓝框属于同一道题，跨页时从页面下拉框切换。无需填写坐标，修改后点击“保存区域”。
      </p>
      {preview && (
        <div className="mt-3 overflow-auto rounded-lg bg-slate-100 p-3">
          <div className="relative mx-auto w-fit max-w-full select-none touch-none">
            <img
              src={preview.url}
              alt={`${questionLabel}第 ${page?.current_page_number} 页区域预览`}
              className="max-h-[720px] max-w-full"
            />
            <div
              ref={overlayRef}
              aria-label={`${questionLabel}区域画布`}
              className={`absolute inset-0 ${drawingMode ? "cursor-crosshair" : ""}`}
              onPointerDown={begin}
              onPointerMove={move}
              onPointerUp={finish}
              onPointerCancel={finish}
            >
              {visibleRegions.map(({ index, area }) => (
                <button
                  type="button"
                  key={`${pageId}-${index}`}
                  aria-label={`${questionLabel}区域${index + 1}`}
                  aria-pressed={selectedRegion === index}
                  className={`absolute border-2 text-left ${
                    selectedRegion === index
                      ? "border-blue-800 bg-blue-400/25"
                      : "border-blue-600 bg-blue-300/15"
                  }`}
                  style={{
                    left: `${area.x * 100}%`,
                    top: `${area.y * 100}%`,
                    width: `${area.width * 100}%`,
                    height: `${area.height * 100}%`,
                  }}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={() => setSelectedRegion(index)}
                >
                  <span className="bg-blue-700 px-1 text-xs text-white">
                    区域 {index + 1}
                  </span>
                </button>
              ))}
              {drawn && (
                <div
                  className="pointer-events-none absolute border-2 border-amber-600 bg-amber-300/25"
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
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

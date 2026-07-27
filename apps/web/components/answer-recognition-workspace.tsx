"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { answerRecognitionApi, type AnswerRecognitionBlock } from "@/lib/api";

export function AnswerRecognitionWorkspace({
  submissionId,
  answerId,
  regionIds,
  readOnly = false,
}: {
  submissionId: string;
  answerId: string;
  regionIds: string[];
  readOnly?: boolean;
}) {
  const [blocks, setBlocks] = useState<AnswerRecognitionBlock[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<AnswerRecognitionBlock>();
  const [draft, setDraft] = useState({
    raw_text: "",
    normalized_text: "",
    latex: "",
  });
  const [message, setMessage] = useState("");

  const reload = useCallback(
    async () => setBlocks(await answerRecognitionApi.blocks(submissionId)),
    [submissionId],
  );
  useEffect(() => void reload(), [reload]);

  const visible = useMemo(
    () =>
      blocks
        .filter(
          (block) => !block.region_id || regionIds.includes(block.region_id),
        )
        .sort((a, b) => a.reading_order - b.reading_order),
    [blocks, regionIds],
  );
  const image = visible.find(
    (block) => block.evidence_image_url,
  )?.evidence_image_url;

  function open(block: AnswerRecognitionBlock) {
    setEditing(block);
    setDraft({
      raw_text: block.raw_text ?? "",
      normalized_text: block.normalized_text ?? "",
      latex: block.latex ?? "",
    });
  }

  async function save() {
    if (!editing) return;
    await answerRecognitionApi.edit(submissionId, editing.id, draft);
    setEditing(undefined);
    setMessage("人工修订已保存");
    await reload();
  }

  async function split(block: AnswerRecognitionBlock) {
    const offset = Math.max(1, Math.floor((block.raw_text?.length ?? 2) / 2));
    await answerRecognitionApi.split(submissionId, block.id, offset);
    await reload();
  }

  async function merge() {
    await answerRecognitionApi.merge(submissionId, selected);
    setSelected([]);
    await reload();
  }

  async function move(index: number, direction: -1 | 1) {
    const next = [...visible];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    await answerRecognitionApi.reorder(
      submissionId,
      next.map((block) => block.id),
    );
    await reload();
  }

  return (
    <section
      className="space-y-3 rounded-lg border p-3"
      aria-label="答案识别证据复核"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">答案识别与结构化证据</h3>
        <span className="text-xs text-slate-500">
          {readOnly ? "finalized · 只读" : `${visible.length} 个识别块`}
        </span>
      </div>
      {image && (
        <div className="relative overflow-hidden rounded border bg-slate-50">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={image} alt="区域证据图像" className="w-full" />
          {visible.map((block) => (
            <button
              key={block.id}
              aria-label={`识别块 ${block.reading_order + 1}`}
              className="absolute border-2 border-indigo-500 bg-indigo-300/10"
              style={{
                left: `${Number(block.bbox.x) * 100}%`,
                top: `${Number(block.bbox.y) * 100}%`,
                width: `${Number(block.bbox.width) * 100}%`,
                height: `${Number(block.bbox.height) * 100}%`,
              }}
              onClick={() => open(block)}
            />
          ))}
        </div>
      )}
      {visible.map((block, index) => (
        <article
          key={block.id}
          className={`rounded border p-2 text-sm ${block.stale ? "border-amber-400 bg-amber-50" : ""}`}
          data-testid="recognition-block"
        >
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input
              aria-label={`选择识别块 ${index + 1}`}
              type="checkbox"
              checked={selected.includes(block.id)}
              disabled={readOnly || block.stale}
              onChange={(event) =>
                setSelected((old) =>
                  event.target.checked
                    ? [...old, block.id]
                    : old.filter((id) => id !== block.id),
                )
              }
            />
            <strong>{block.block_type}</strong>
            <span>
              {block.provider}/{block.provider_version}
            </span>
            <span>置信度 {block.confidence ?? "不可用"}</span>
            {block.stale && <span className="text-amber-800">stale</span>}
            {block.warning_codes.map((warning) => (
              <span key={warning} className="rounded bg-amber-100 px-1">
                {warning}
              </span>
            ))}
          </div>
          <p>原始：{block.raw_text || "（空）"}</p>
          <p>规范化：{block.normalized_text || "（空）"}</p>
          <p>LaTeX：{block.latex || "unavailable"}</p>
          <div className="mt-2 flex gap-2">
            <button
              disabled={readOnly || block.stale}
              onClick={() => open(block)}
            >
              编辑
            </button>
            <button
              disabled={readOnly || block.stale}
              onClick={() => void split(block)}
            >
              拆分
            </button>
            <button
              disabled={readOnly || index === 0}
              onClick={() => void move(index, -1)}
            >
              上移
            </button>
            <button
              disabled={readOnly || index === visible.length - 1}
              onClick={() => void move(index, 1)}
            >
              下移
            </button>
            {block.region_id && (
              <button
                disabled={readOnly}
                onClick={() =>
                  void answerRecognitionApi.retry(
                    submissionId,
                    block.region_id!,
                  )
                }
              >
                重试区域
              </button>
            )}
          </div>
        </article>
      ))}
      {!visible.length && (
        <p className="text-sm text-slate-500">暂无识别证据，需人工复核。</p>
      )}
      <div className="flex gap-2">
        <button
          disabled={readOnly || selected.length < 2}
          onClick={() => void merge()}
        >
          合并所选
        </button>
        <button
          disabled={
            readOnly || !visible.length || visible.some((block) => block.stale)
          }
          onClick={() =>
            void answerRecognitionApi
              .confirm(submissionId, answerId)
              .then(() => {
                setMessage("识别结果已人工确认");
                return reload();
              })
          }
        >
          确认识别结果
        </button>
      </div>
      {message && <p role="status">{message}</p>}
      {editing && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="编辑识别块"
          className="space-y-2 rounded border-2 border-indigo-300 bg-white p-3"
          onClick={(event) => event.stopPropagation()}
        >
          <label className="block">
            原始文本
            <textarea
              value={draft.raw_text}
              onChange={(e) => setDraft({ ...draft, raw_text: e.target.value })}
            />
          </label>
          <label className="block">
            规范化文本
            <textarea
              value={draft.normalized_text}
              onChange={(e) =>
                setDraft({ ...draft, normalized_text: e.target.value })
              }
            />
          </label>
          <label className="block">
            LaTeX
            <textarea
              value={draft.latex}
              onChange={(e) => setDraft({ ...draft, latex: e.target.value })}
            />
          </label>
          <button onClick={() => void save()}>保存</button>
          <button onClick={() => setEditing(undefined)}>取消</button>
        </div>
      )}
    </section>
  );
}

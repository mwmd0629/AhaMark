"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card } from "@/components/ui";
import {
  ApiError,
  assignmentGenerationApi,
  type QuestionStructureItem,
  type QuestionStructureReview,
} from "@/lib/api";

const SCORE_POLICIES: {
  value: QuestionStructureReview["score_policy"];
  label: string;
}[] = [
  { value: "unconfirmed", label: "稍后确认分值" },
  { value: "equal_weight", label: "按作业总分等权分配" },
  { value: "manual", label: "逐题填写分值" },
  { value: "template", label: "使用评分模板后的分值" },
];

export function QuestionStructureReviewPanel({
  assignmentId,
}: {
  assignmentId: string;
}) {
  const [review, setReview] = useState<QuestionStructureReview | null>(null);
  const [items, setItems] = useState<QuestionStructureItem[]>([]);
  const [scorePolicy, setScorePolicy] =
    useState<QuestionStructureReview["score_policy"]>("unconfirmed");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [mergeSelection, setMergeSelection] = useState<Set<string>>(new Set());
  const [mergeNumber, setMergeNumber] = useState("");
  const [splitQuestionId, setSplitQuestionId] = useState<string | null>(null);
  const [splitNumbers, setSplitNumbers] = useState<Record<string, string>>({});
  const editGeneration = useRef(0);

  const load = useCallback(async () => {
    try {
      const value =
        await assignmentGenerationApi.getQuestionStructure(assignmentId);
      setReview(value);
      setItems(value.items);
      setScorePolicy(value.score_policy);
      setDirty(false);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "题目清单加载失败",
      );
    }
  }, [assignmentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const change = useCallback(
    (
      nextItems: QuestionStructureItem[],
      nextPolicy: QuestionStructureReview["score_policy"] = scorePolicy,
    ) => {
      editGeneration.current += 1;
      setItems(nextItems);
      setScorePolicy(nextPolicy);
      setDirty(true);
      setMessage("");
    },
    [scorePolicy],
  );

  const save = useCallback(async () => {
    if (!review || !dirty || saving) return;
    const generation = editGeneration.current;
    setSaving(true);
    setError("");
    try {
      const value = await assignmentGenerationApi.saveQuestionStructure(
        assignmentId,
        {
          expected_content_hash: review.content_hash,
          score_policy: scorePolicy,
          items,
        },
      );
      setReview(value);
      if (editGeneration.current === generation) {
        setItems(value.items);
        setDirty(false);
        setMessage("题目清单已自动保存");
      }
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "自动保存失败");
    } finally {
      setSaving(false);
    }
  }, [assignmentId, dirty, items, review, saving, scorePolicy]);

  useEffect(() => {
    if (!dirty || saving) return;
    const timer = window.setTimeout(() => void save(), 700);
    return () => window.clearTimeout(timer);
  }, [dirty, items, save, saving, scorePolicy]);

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (!dirty && !saving) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, saving]);

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    change(next.map((item, order) => ({ ...item, display_order: order + 1 })));
  }

  function applyOperation(value: QuestionStructureReview, text: string) {
    editGeneration.current += 1;
    setReview(value);
    setItems(value.items);
    setScorePolicy(value.score_policy);
    setDirty(false);
    setMergeSelection(new Set());
    setMergeNumber("");
    setSplitQuestionId(null);
    setSplitNumbers({});
    setMessage(text);
  }

  async function mergeSelected() {
    if (
      !review ||
      dirty ||
      saving ||
      mergeSelection.size < 2 ||
      !mergeNumber.trim()
    )
      return;
    setSaving(true);
    setError("");
    try {
      const value = await assignmentGenerationApi.mergeQuestionStructure(
        assignmentId,
        {
          expected_content_hash: review.content_hash,
          question_ids: [...mergeSelection],
          display_number: mergeNumber.trim(),
          explicit_confirmation: true,
        },
      );
      applyOperation(value, "所选作答单元已合并，请继续确认题号和分值");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "合并失败");
    } finally {
      setSaving(false);
    }
  }

  function startSplit(item: QuestionStructureItem) {
    const base =
      item.parent_number || item.display_number.replace(/[（(].*$/, "");
    setSplitQuestionId(item.question_id);
    setSplitNumbers(
      Object.fromEntries(
        (item.regions ?? []).map((region, index) => [
          region.id,
          base + "(" + (index + 1) + ")",
        ]),
      ),
    );
    setMessage("");
  }

  async function splitSelected() {
    if (!review || !splitQuestionId || dirty || saving) return;
    const item = items.find(
      (current) => current.question_id === splitQuestionId,
    );
    if (!item?.regions || item.regions.length < 2) return;
    setSaving(true);
    setError("");
    try {
      const value = await assignmentGenerationApi.splitQuestionStructure(
        assignmentId,
        {
          expected_content_hash: review.content_hash,
          source_question_id: item.question_id,
          parts: item.regions.map((region) => ({
            display_number: splitNumbers[region.id]?.trim() ?? "",
            region_ids: [region.id],
          })),
          explicit_confirmation: true,
        },
      );
      applyOperation(value, "该题已按区域拆分，请继续确认题号和分值");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "拆分失败");
    } finally {
      setSaving(false);
    }
  }

  async function confirm() {
    if (!review || dirty || saving) return;
    setSaving(true);
    setError("");
    try {
      const value = await assignmentGenerationApi.confirmQuestionStructure(
        assignmentId,
        {
          expected_content_hash: review.content_hash,
          explicit_confirmation: true,
        },
      );
      setReview(value);
      setItems(value.items);
      setMessage("题目清单与分值方式已由教师确认");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "确认失败");
    } finally {
      setSaving(false);
    }
  }

  if (!review) {
    return (
      <Card>
        <p className="text-sm">{error || "正在加载题目清单…"}</p>
      </Card>
    );
  }

  return (
    <Card className="space-y-4">
      <div>
        <h3 className="font-semibold">确认题目清单</h3>
        <p className="mt-1 text-sm text-[var(--neutral-600)]">
          核对层级题号和作答顺序。一个作答单元可以在后续切题时绑定多个页面区域。
        </p>
      </div>
      {error && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-emerald-700">
          {message}
        </p>
      )}
      {review.status !== "confirmed" && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg bg-[var(--neutral-50)] p-3">
          <label className="text-sm font-medium">
            合并后的题号
            <input
              aria-label="合并后的题号"
              className="mt-1 block w-32 rounded-lg border bg-white px-3 py-2"
              value={mergeNumber}
              onChange={(event) => setMergeNumber(event.target.value)}
            />
          </label>
          <Button
            variant="outline"
            disabled={
              dirty || saving || mergeSelection.size < 2 || !mergeNumber.trim()
            }
            onClick={() => void mergeSelected()}
          >
            合并所选（{mergeSelection.size}）
          </Button>
          <span className="text-xs text-[var(--neutral-600)]">
            合并会保留所选题目的全部页面区域，并要求重新确认分值。
          </span>
        </div>
      )}
      <ol className="space-y-2">
        {items.map((item, index) => (
          <li
            key={item.question_id}
            className={`grid gap-2 rounded-lg border p-3 sm:grid-cols-[8rem_1fr_auto] ${item.action === "remove" ? "bg-[var(--neutral-50)] opacity-70" : ""}`}
          >
            <label className="text-sm font-medium">
              题号
              <input
                aria-label={`第 ${index + 1} 个作答单元题号`}
                className="mt-1 w-full rounded-lg border px-3 py-2"
                value={item.display_number}
                disabled={review.status === "confirmed"}
                onChange={(event) =>
                  change(
                    items.map((current, currentIndex) =>
                      currentIndex === index
                        ? {
                            ...current,
                            display_number: event.target.value,
                            source_kind: "manual",
                          }
                        : current,
                    ),
                  )
                }
              />
            </label>
            <div className="flex items-end gap-2 text-sm">
              <label className="flex items-center gap-2 pb-2">
                <input
                  aria-label={"选择合并 " + item.display_number}
                  type="checkbox"
                  checked={mergeSelection.has(item.question_id)}
                  disabled={
                    item.action !== "keep" || review.status === "confirmed"
                  }
                  onChange={(event) =>
                    setMergeSelection((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(item.question_id);
                      else next.delete(item.question_id);
                      return next;
                    })
                  }
                />
                选择合并
              </label>
              <label className="flex items-center gap-2 pb-2">
                <input
                  type="checkbox"
                  checked={item.action === "remove"}
                  disabled={review.status === "confirmed"}
                  onChange={(event) =>
                    change(
                      items.map((current, currentIndex) =>
                        currentIndex === index
                          ? {
                              ...current,
                              action: event.target.checked ? "remove" : "keep",
                            }
                          : current,
                      ),
                    )
                  }
                />
                不作为独立作答单元
              </label>
              {(scorePolicy === "manual" || scorePolicy === "template") &&
                item.action === "keep" && (
                  <label>
                    分值
                    <input
                      aria-label={`${item.display_number} 分值`}
                      type="number"
                      min="0.01"
                      step="0.01"
                      className="ml-2 w-24 rounded-lg border px-2 py-1"
                      value={item.max_score ?? ""}
                      disabled={review.status === "confirmed"}
                      onChange={(event) =>
                        change(
                          items.map((current, currentIndex) =>
                            currentIndex === index
                              ? {
                                  ...current,
                                  max_score: event.target.value || null,
                                }
                              : current,
                          ),
                        )
                      }
                    />
                  </label>
                )}
              {item.action === "keep" && (
                <span className="pb-2 text-xs text-[var(--neutral-600)]">
                  {item.region_count ?? 0} 个区域
                  {(item.page_count ?? 0) > 0
                    ? " / " + item.page_count + " 页"
                    : ""}
                  {item.spans_pages ? " · 跨页延续" : ""}
                </span>
              )}
            </div>
            <div className="flex items-end gap-1">
              <Button
                variant="outline"
                disabled={index === 0 || review.status === "confirmed"}
                onClick={() => move(index, -1)}
              >
                上移
              </Button>
              <Button
                variant="outline"
                disabled={
                  index === items.length - 1 || review.status === "confirmed"
                }
                onClick={() => move(index, 1)}
              >
                下移
              </Button>
              {(item.region_count ?? 0) >= 2 && (
                <Button
                  variant="outline"
                  disabled={dirty || saving || review.status === "confirmed"}
                  onClick={() => startSplit(item)}
                >
                  按区域拆分
                </Button>
              )}
            </div>
          </li>
        ))}
      </ol>
      {splitQuestionId &&
        (() => {
          const item = items.find(
            (current) => current.question_id === splitQuestionId,
          );
          if (!item?.regions) return null;
          return (
            <section className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <div>
                <h4 className="font-semibold">
                  拆分 {item.display_number} 的区域
                </h4>
                <p className="text-sm text-amber-900">
                  每个区域将成为独立作答单元；跨页连续答案无需拆分。
                </p>
              </div>
              {item.regions.map((region) => (
                <label
                  key={region.id}
                  className="flex flex-wrap items-center gap-2 text-sm"
                >
                  第 {region.page_number} 页区域
                  <input
                    aria-label={"区域 " + region.id + " 的新题号"}
                    className="w-32 rounded-lg border bg-white px-3 py-2"
                    value={splitNumbers[region.id] ?? ""}
                    onChange={(event) =>
                      setSplitNumbers((current) => ({
                        ...current,
                        [region.id]: event.target.value,
                      }))
                    }
                  />
                </label>
              ))}
              <div className="flex gap-2">
                <Button
                  disabled={
                    saving ||
                    item.regions.some(
                      (region) => !splitNumbers[region.id]?.trim(),
                    )
                  }
                  onClick={() => void splitSelected()}
                >
                  确认拆分
                </Button>
                <Button
                  variant="outline"
                  disabled={saving}
                  onClick={() => setSplitQuestionId(null)}
                >
                  取消
                </Button>
              </div>
            </section>
          );
        })()}
      <label className="block text-sm font-medium">
        分值处理
        <select
          className="mt-1 block w-full rounded-lg border px-3 py-2 sm:w-80"
          value={scorePolicy}
          disabled={review.status === "confirmed"}
          onChange={(event) =>
            change(
              items,
              event.target.value as QuestionStructureReview["score_policy"],
            )
          }
        >
          {SCORE_POLICIES.map((policy) => (
            <option key={policy.value} value={policy.value}>
              {policy.label}
            </option>
          ))}
        </select>
      </label>
      {scorePolicy === "unconfirmed" && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
          PDF
          未提供可信分值时，系统不会自动生成正式总分。请选择一种处理方式后再确认。
        </p>
      )}
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-[var(--neutral-600)]">
          保留 {items.filter((item) => item.action === "keep").length}{" "}
          个作答单元
          {saving ? " · 正在保存…" : dirty ? " · 等待自动保存" : ""}
        </span>
        <Button
          disabled={
            review.status === "confirmed" ||
            dirty ||
            saving ||
            scorePolicy === "unconfirmed"
          }
          onClick={() => void confirm()}
        >
          {review.status === "confirmed" ? "已确认" : "确认题目清单"}
        </Button>
      </div>
    </Card>
  );
}

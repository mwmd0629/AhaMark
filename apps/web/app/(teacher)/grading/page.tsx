"use client";

import { useEffect, useState } from "react";
import { gradingApi, type GradingBatch } from "@/lib/api";
import { Badge, Card, PageHeader, SectionHeader } from "@/components/ui";

export default function GradingPage() {
  const [assignmentId, setAssignmentId] = useState("");
  const [items, setItems] = useState<GradingBatch[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    if (!assignmentId.trim()) return;
    setLoading(true);
    setError("");
    try {
      setItems((await gradingApi.batches(assignmentId.trim())).items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const saved = window.localStorage.getItem("ahamark-grading-assignment");
    if (saved) setAssignmentId(saved);
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="作业批改"
        description="批量收集学生作业，完成 OCR、规则初批和教师最终复核。主观题 Provider 不可用时不会生成虚假分数。"
      />
      <Card className="p-5">
        <SectionHeader
          title="批改批次"
          description="输入作业 ID 查看真实批次；选择批次后可进入匹配、按学生或按题复核。"
        />
        <form
          className="mt-4 flex gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            window.localStorage.setItem(
              "ahamark-grading-assignment",
              assignmentId,
            );
            void load();
          }}
        >
          <label className="sr-only" htmlFor="assignment-id">
            作业 ID
          </label>
          <input
            id="assignment-id"
            className="min-w-0 flex-1 rounded-lg border px-3 py-2"
            value={assignmentId}
            onChange={(event) => setAssignmentId(event.target.value)}
            placeholder="作业 ID"
          />
          <button
            className="rounded-lg bg-[var(--brand-600)] px-4 py-2 font-semibold text-white"
            type="submit"
          >
            {loading ? "加载中…" : "加载"}
          </button>
        </form>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {error}
          </p>
        )}
        {!loading && !error && items.length === 0 && (
          <p className="mt-4 text-sm text-[var(--text-secondary)]">
            暂无批次。请先通过作业详情创建批改批次。
          </p>
        )}
        <div className="mt-4 grid gap-3">
          {items.map((batch) => (
            <article key={batch.id} className="rounded-xl border p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <strong>{batch.name || "未命名批次"}</strong>
                  <p className="mt-1 text-xs text-[var(--text-secondary)]">
                    提交 {batch.submission_count} · 已识别{" "}
                    {batch.recognized_count} · 已复核 {batch.reviewed_count}
                  </p>
                </div>
                <Badge status={batch.status} />
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <span>未匹配 {batch.matching.unmatched}</span>
                <span>冲突 {batch.matching.ambiguous}</span>
                <span>
                  已确认 {batch.matching.confirmed}/{batch.matching.total}
                </span>
              </div>
              <div className="mt-4 flex gap-2">
                <button className="rounded-lg border px-3 py-2">
                  按学生复核
                </button>
                <button className="rounded-lg border px-3 py-2">
                  按题目横向比较
                </button>
                <button className="rounded-lg border px-3 py-2">
                  上传作业
                </button>
              </div>
            </article>
          ))}
        </div>
      </Card>
      <Card className="p-5">
        <strong>最终成绩边界</strong>
        <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
          AI/规则结果只是建议。低置信度、OCR 异常、公式内容、Provider
          unavailable 和过期 Rubric 均强制人工复核；只有教师确认后生成的
          complete 快照可供成绩与学情分析使用。
        </p>
      </Card>
    </div>
  );
}

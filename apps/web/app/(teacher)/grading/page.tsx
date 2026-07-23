"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  assignmentsApi,
  gradingApi,
  type AssignmentRecord,
  type GradingBatch,
} from "@/lib/api";
import { Button, Card, Input, PageHeader, Select } from "@/components/ui";

export default function GradingPage() {
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [assignmentId, setAssignmentId] = useState("");
  const [items, setItems] = useState<GradingBatch[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const assignment = useMemo(
    () => assignments.find((item) => item.id === assignmentId),
    [assignments, assignmentId],
  );

  useEffect(() => {
    assignmentsApi
      .list("page_size=100")
      .then((page) =>
        setAssignments(page.items.filter((item) => item.status !== "draft")),
      )
      .catch(() => setError("无法加载已发布作业"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!assignmentId) {
      setItems([]);
      return;
    }
    setLoading(true);
    gradingApi
      .batches(assignmentId)
      .then((page) => setItems(page.items))
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "加载批次失败"),
      )
      .finally(() => setLoading(false));
  }, [assignmentId]);

  async function createBatch(form: FormData) {
    if (!assignmentId) return;
    setLoading(true);
    setError("");
    try {
      const batch = await gradingApi.createBatch(assignmentId, {
        class_id: String(form.get("class_id")),
        name: String(form.get("name")),
      });
      setItems((old) => [batch, ...old]);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "创建批次失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="作业批改"
        description="从已发布作业创建批次，经页面整理、工作流测试 OCR、规则初批和教师复核形成最终成绩。"
      />
      <Card className="space-y-4 p-5">
        <Select
          label="已发布作业"
          value={assignmentId}
          onChange={(event) => setAssignmentId(event.target.value)}
        >
          <option value="">请选择作业</option>
          {assignments.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </Select>
        {assignment && (
          <form action={createBatch} className="grid gap-3 md:grid-cols-3">
            <Select name="class_id" label="班级" required defaultValue="">
              <option value="" disabled>
                请选择班级
              </option>
              {assignment.classes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
            <Input name="name" label="批次名称" required />
            <Button className="mt-6" loading={loading}>
              创建批改批次
            </Button>
          </form>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
      </Card>
      <div className="grid gap-3">
        {items.map((batch) => (
          <Card
            key={batch.id}
            className="p-5"
            data-testid="grading-batch"
            data-batch-id={batch.id}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <strong>{batch.name || "未命名批次"}</strong>
                <p className="mt-1 text-xs text-slate-500">
                  提交 {batch.submission_count} · 已识别{" "}
                  {batch.recognized_count} · 已复核 {batch.reviewed_count}
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs">
                {batch.status}
              </span>
            </div>
            <p className="mt-3 text-xs">
              匹配：已确认 {batch.matching.confirmed}/{batch.matching.total} ·
              待匹配 {batch.matching.unmatched}
            </p>
            <Link href={`/grading/${batch.id}`}>
              <Button className="mt-4">进入批次工作台</Button>
            </Link>
          </Card>
        ))}
      </div>
      {!loading && assignmentId && items.length === 0 && (
        <Card className="p-5 text-sm text-slate-500">
          暂无批次，请通过上方表单创建。
        </Card>
      )}
      <Card className="p-5 text-sm leading-6 text-slate-600">
        <strong className="text-slate-900">最终成绩边界：</strong>
        GradingResult 只是建议；只有教师逐题确认、Submission finalize
        后生成的最新 complete ScoreSnapshot 才是正式成绩。
      </Card>
    </div>
  );
}

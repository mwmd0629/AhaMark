"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
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
  const searchParams = useSearchParams();
  const requestedAssignmentId = searchParams.get("assignmentId") ?? "";
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
    if (
      requestedAssignmentId &&
      assignments.some((item) => item.id === requestedAssignmentId)
    ) {
      setAssignmentId(requestedAssignmentId);
    }
  }, [assignments, requestedAssignmentId]);

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
        description="选择作业，创建批次并检查结果。"
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
            <div>
              <strong>{batch.name || "未命名批次"}</strong>
              <p className="mt-1 text-xs text-slate-500">
                共 {batch.submission_count} 份 · 已复核 {batch.reviewed_count}{" "}
                份
              </p>
            </div>
            {batch.matching.unmatched > 0 && (
              <p className="mt-3 text-xs text-amber-700">
                {batch.matching.unmatched} 份待匹配
              </p>
            )}
            <Link href={`/grading/${batch.id}`}>
              <Button className="mt-4">打开批次</Button>
            </Link>
          </Card>
        ))}
      </div>
      {!loading && assignmentId && items.length === 0 && (
        <Card className="p-5 text-sm text-slate-500">
          暂无批次，请通过上方表单创建。
        </Card>
      )}
    </div>
  );
}

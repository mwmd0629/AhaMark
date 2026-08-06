"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Table,
} from "@/components/ui";
import { ApiError, assignmentsApi, type AssignmentRecord } from "@/lib/api";

function displayedWizardStep(nextStep: number) {
  if (nextStep <= 1) return 1;
  if (nextStep <= 3) return 2;
  return 3;
}

export default function AssignmentsPage() {
  const [items, setItems] = useState<AssignmentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ page_size: "100" });
      if (search) query.set("search", search);
      if (status) query.set("status", status);
      setItems((await assignmentsApi.list(query.toString())).items);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "无法连接作业服务");
    } finally {
      setLoading(false);
    }
  }, [search, status]);
  useEffect(() => void load(), [load]);
  async function deleteAssignment(item: AssignmentRecord) {
    if (
      !window.confirm(
        `确定删除“${item.title}”吗？作业会移入归档，可通过状态筛选找回，不会删除历史成绩。`,
      )
    )
      return;
    setDeletingId(item.id);
    setError("");
    try {
      await assignmentsApi.archive(item.id);
      setItems((old) => old.filter((current) => current.id !== item.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "删除作业失败");
    } finally {
      setDeletingId("");
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title="作业管理"
        description="真实作业草稿、试卷结构、评分标准和发布状态。"
        actions={
          <Link href="/assignments/new">
            <Button>创建作业</Button>
          </Link>
        }
      />
      <Card className="grid gap-3 p-4 md:grid-cols-[1fr_200px]">
        <Input
          aria-label="搜索作业"
          placeholder="搜索作业名称"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select
          aria-label="状态筛选"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">全部状态</option>
          <option value="draft">草稿</option>
          <option value="published">已发布</option>
          <option value="archived">已归档</option>
        </Select>
      </Card>
      {loading ? (
        <Card className="space-y-3 p-5" aria-label="正在加载作业">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </Card>
      ) : error ? (
        <ErrorState description={error} retry={load} />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有作业"
          description="先创建一个草稿，选择活动班级并逐步完善试卷与评分标准。"
          action={
            <Link href="/assignments/new">
              <Button>创建第一份作业</Button>
            </Link>
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <thead>
              <tr className="border-b bg-slate-50 text-xs text-slate-500">
                <th className="px-5 py-3">作业</th>
                <th className="px-4 py-3">班级</th>
                <th className="px-4 py-3">题目 / 总分</th>
                <th className="px-4 py-3">完整度</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-5 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b last:border-0">
                  <td className="px-5 py-4 font-semibold">{item.title}</td>
                  <td className="px-4 py-4">
                    {item.classes.map((x) => x.name).join("、") || "未选择"}
                  </td>
                  <td className="px-4 py-4">
                    {item.question_count ?? 0} 题 / {item.total_score ?? "—"} 分
                  </td>
                  <td className="px-4 py-4">
                    {item.completeness.ready
                      ? "可发布"
                      : `待完成步骤 ${displayedWizardStep(item.completeness.next_step)}`}
                  </td>
                  <td className="px-4 py-4">
                    <Badge status={item.status} />
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-3">
                      <Link
                        className="font-semibold text-[var(--brand-700)]"
                        href={`/assignments/${item.id}${item.status === "draft" ? "/edit" : ""}`}
                      >
                        {item.status === "draft" ? "继续编辑" : "查看"}
                      </Link>
                      {item.status !== "archived" && (
                        <Button
                          variant="danger"
                          className="px-2 py-1 text-xs"
                          loading={deletingId === item.id}
                          onClick={() => void deleteAssignment(item)}
                        >
                          删除
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}

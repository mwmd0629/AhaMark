"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import {
  ApiError,
  studentPortalApi,
  type StudentOpenAssignment,
} from "@/lib/api";

export default function StudentSubmissionsPage() {
  const [items, setItems] = useState<StudentOpenAssignment[]>();
  const [saving, setSaving] = useState<string>();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    studentPortalApi
      .openAssignments()
      .then(setItems)
      .catch(() => setError("待提交作业加载失败，请稍后重试。"));
  }, []);
  useEffect(() => load(), [load]);

  const submit = async (
    event: FormEvent<HTMLFormElement>,
    item: StudentOpenAssignment,
  ) => {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem(
      "files",
    ) as HTMLInputElement;
    const files = Array.from(input.files || []);
    if (!files.length) return setError("请至少选择一个 PDF 或图片文件。");
    setSaving(item.assignment_id);
    setError("");
    setMessage("");
    try {
      const result = await studentPortalApi.submitAssignment(
        item.assignment_id,
        files,
      );
      setMessage(
        `第 ${result.attempt_number} 次提交已保存，共 ${result.file_count} 个文件。`,
      );
      event.currentTarget.reset();
      load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "作业提交失败，请稍后重试。",
      );
    } finally {
      setSaving(undefined);
    }
  };

  if (!items) return <Skeleton className="h-72 w-full" />;
  return (
    <div className="space-y-6">
      <PageHeader
        title="提交作业"
        description="使用登录账号直接关联本人档案，无需在文件名中填写姓名或邮箱。"
      />
      {error && (
        <Card className="border-red-300 p-4 text-red-700">{error}</Card>
      )}
      {message && (
        <Card className="border-emerald-300 p-4 text-emerald-700">
          {message}
        </Card>
      )}
      {!items.length ? (
        <EmptyState
          icon="assignments"
          title="暂无可提交作业"
          description="教师正式发布作业后会显示在这里。"
        />
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <Card
              className="p-5"
              key={`${item.assignment_id}-${item.student_id}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-bold">{item.assignment_title}</h2>
                  <p className="mt-1 text-sm text-slate-600">
                    {item.class_name}
                  </p>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs ${item.late ? "bg-amber-50 text-amber-800" : "bg-blue-50 text-blue-700"}`}
                >
                  {item.due_at
                    ? `${item.late ? "已过截止时间 · " : "截止 "}${new Date(item.due_at).toLocaleString("zh-CN")}`
                    : "未设置截止时间"}
                </span>
              </div>
              {item.instructions && (
                <p className="mt-3 text-sm">{item.instructions}</p>
              )}
              {item.attempts.length > 0 && (
                <p className="mt-3 text-sm text-emerald-700">
                  已提交 {item.attempts.length} 次，最近一次为第{" "}
                  {item.attempts[0].attempt_number} 次。
                </p>
              )}
              <form
                className="mt-4 flex flex-wrap items-end gap-3"
                onSubmit={(event) => void submit(event, item)}
              >
                <label className="min-w-64 flex-1 text-sm font-medium">
                  选择答卷文件
                  <input
                    className="mt-2 block w-full rounded-xl border border-slate-300 bg-white p-2.5"
                    name="files"
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg"
                    multiple
                    required
                  />
                </label>
                <Button loading={saving === item.assignment_id}>
                  提交新版本
                </Button>
              </form>
              <p className="mt-2 text-xs text-slate-500">
                支持
                PDF、PNG、JPG；再次提交会保留为新的尝试版本，不覆盖历史文件。
              </p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

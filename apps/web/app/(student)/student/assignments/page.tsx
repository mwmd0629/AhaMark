"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  useToast,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type StudentAssignment,
} from "@/lib/student-api";
import { formatDateTime } from "@/lib/student-format";

function assignmentState(item: StudentAssignment) {
  if (item.submission_id || item.submission_status === "submitted") {
    return { label: "已提交", style: "bg-emerald-50 text-emerald-700" };
  }
  if (item.due_at && new Date(item.due_at).getTime() < Date.now()) {
    return { label: "已截止", style: "bg-red-50 text-red-700" };
  }
  return { label: "待提交", style: "bg-amber-50 text-amber-800" };
}

function AssignmentCard({
  assignment,
  onSubmitted,
}: {
  assignment: StudentAssignment;
  onSubmitted: () => void;
}) {
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const state = assignmentState(assignment);
  const submitted = state.label === "已提交";
  const overdue = state.label === "已截止";
  const maxFiles = assignment.max_files ?? 10;

  const submit = async () => {
    if (!files.length) {
      setError("请先选择至少一个作业文件。");
      return;
    }
    if (!assignment.class_id) {
      setError("这份作业缺少班级信息，无法提交，请联系教师。");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await studentApi.submitAssignment(assignment, files);
      setFiles([]);
      toast("作业已提交");
      onSubmitted();
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "作业提交失败，请重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="p-5">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-bold">{assignment.title}</h2>
            <span
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${state.style}`}
            >
              {state.label}
            </span>
          </div>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {assignment.class_name || assignment.subject || "课程作业"} · 截止{" "}
            {formatDateTime(assignment.due_at)}
          </p>
          {assignment.instructions && (
            <p className="mt-4 whitespace-pre-wrap text-sm leading-6">
              {assignment.instructions}
            </p>
          )}
        </div>
        {assignment.submitted_at && (
          <span className="shrink-0 text-xs text-[var(--text-secondary)]">
            提交于 {formatDateTime(assignment.submitted_at)}
          </span>
        )}
      </div>

      {!submitted && !overdue && (
        <div className="mt-5 rounded-xl border border-dashed border-[var(--border)] bg-slate-50 p-4">
          <label className="grid gap-2 text-sm font-semibold">
            选择作业文件
            <input
              type="file"
              multiple
              accept={assignment.allowed_file_types?.join(",")}
              disabled={submitting}
              onChange={(event) => {
                const nextFiles = Array.from(event.target.files ?? []);
                if (nextFiles.length > maxFiles) {
                  setFiles([]);
                  setError(`一次最多选择 ${maxFiles} 个文件。`);
                  return;
                }
                setFiles(nextFiles);
                setError("");
              }}
              className="block w-full rounded-lg border border-[var(--border)] bg-white p-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--brand-50)] file:px-3 file:py-2 file:font-semibold file:text-[var(--brand-700)]"
            />
          </label>
          <p className="mt-2 text-xs text-[var(--text-secondary)]">
            已选择 {files.length} 个文件，最多 {maxFiles}{" "}
            个。上传过程中请不要关闭页面。
          </p>
          {error && (
            <p role="alert" className="mt-3 text-sm text-red-700">
              {error}
            </p>
          )}
          <Button
            type="button"
            loading={submitting}
            disabled={!files.length}
            onClick={() => void submit()}
            className="mt-4"
          >
            <Icon name="upload" className="h-4 w-4" />
            确认提交
          </Button>
        </div>
      )}

      {overdue && !submitted && (
        <p
          role="status"
          className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700"
        >
          该作业已截止，当前页面不再接受提交。如有特殊情况，请联系教师。
        </p>
      )}
    </Card>
  );
}

export default function StudentAssignmentsPage() {
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setAssignments(collectionItems(await studentApi.assignments()));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "作业列表加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="我的作业"
        description="查看教师已发布的作业并上传答案文件。提交结果以服务器确认为准。"
      />
      {loading ? (
        <div aria-label="正在加载作业" className="grid gap-4">
          {[1, 2, 3].map((item) => (
            <Skeleton key={item} className="h-44 w-full" />
          ))}
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : assignments.length ? (
        <section aria-label="作业列表" className="grid gap-4">
          {assignments.map((assignment) => (
            <AssignmentCard
              key={assignment.id}
              assignment={assignment}
              onSubmitted={() => void load()}
            />
          ))}
        </section>
      ) : (
        <EmptyState
          title="暂无已发布作业"
          description="教师发布作业后会显示在这里。"
        />
      )}
    </div>
  );
}

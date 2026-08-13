"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  teacherReviewRequestsApi,
  type TeacherReviewRequest,
} from "@/lib/student-api";
import { formatDateTime } from "@/lib/student-format";

type ReviewAction = "uphold" | "change_score" | "needs_information" | "reject";

const statusLabels: Record<string, string> = {
  pending: "待处理",
  in_review: "复核中",
  waiting_student: "等待学生补充",
  resolved: "已处理",
  rejected: "已驳回",
};

function ReviewRequestCard({
  request,
  onUpdated,
}: {
  request: TeacherReviewRequest;
  onUpdated: () => void;
}) {
  const toast = useToast();
  const [action, setAction] = useState<ReviewAction>("uphold");
  const [response, setResponse] = useState("");
  const [finalScore, setFinalScore] = useState("");
  const [finalFeedback, setFinalFeedback] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const closed = request.status === "resolved" || request.status === "rejected";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!response.trim()) {
      setError("请填写给学生的复核说明。");
      return;
    }
    const score = finalScore === "" ? undefined : Number(finalScore);
    if (
      action === "change_score" &&
      (score === undefined || !Number.isFinite(score) || score < 0)
    ) {
      setError("修改分数时必须填写有效的新分数。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await teacherReviewRequestsApi.update(request.id, {
        action,
        teacher_response: response.trim(),
        final_score: action === "change_score" ? score : undefined,
        final_feedback:
          action === "change_score"
            ? finalFeedback.trim() || undefined
            : undefined,
      });
      toast(
        action === "change_score"
          ? "已记录改分；请继续生成并发布新成绩版本"
          : "复核请求已处理",
      );
      onUpdated();
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "复核处理失败，请重试。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="p-5">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-bold">学生错题申疑</h2>
            <span
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${closed ? "bg-slate-100 text-slate-700" : "bg-amber-50 text-amber-800"}`}
            >
              {statusLabels[request.status] || request.status}
            </span>
          </div>
          <p className="mt-2 text-xs text-[var(--text-secondary)]">
            学生记录 {request.student_name || request.student_id} · 提交于{" "}
            {formatDateTime(request.submitted_at || request.created_at)}
          </p>
        </div>
        <span className="text-xs text-[var(--text-secondary)]">
          申请编号 {request.id}
        </span>
      </div>

      <section className="mt-5 rounded-xl bg-slate-50 p-4">
        <h3 className="text-sm font-semibold text-[var(--text-secondary)]">
          学生问题
        </h3>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6">
          {request.student_question ||
            request.question ||
            "学生未填写详细问题。"}
        </p>
      </section>
      <section className="mt-3 rounded-xl border border-[var(--border)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">原题与已发布评分证据</h3>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              {request.assignment_title || "未知作业"}
              {request.question_number
                ? ` · 第 ${request.question_number} 题`
                : ""}
              {request.score_snapshot_version
                ? ` · 成绩快照 v${request.score_snapshot_version}`
                : ""}
            </p>
          </div>
          {request.grading_batch_id && (
            <Link
              href={`/grading/${request.grading_batch_id}/review`}
              className="text-sm font-semibold text-[var(--brand-700)]"
            >
              查看原批改证据
            </Link>
          )}
        </div>
        <dl className="mt-4 grid gap-4 text-sm md:grid-cols-2">
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">题目</dt>
            <dd className="mt-1 whitespace-pre-wrap leading-6">
              {request.question || "题目文本暂无"}
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">
              学生答案
            </dt>
            <dd className="mt-1 whitespace-pre-wrap leading-6">
              {request.student_answer || "未记录文本答案，请查看原答卷图像"}
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">
              已发布分数
            </dt>
            <dd className="mt-1">
              {request.published_score != null &&
              request.published_max_score != null
                ? `${request.published_score} / ${request.published_max_score}`
                : "发布模式未展示分数"}
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">
              已发布反馈
            </dt>
            <dd className="mt-1 whitespace-pre-wrap leading-6">
              {request.published_feedback ||
                request.published_error_type ||
                "未填写反馈"}
            </dd>
          </div>
        </dl>
      </section>
      {request.conversation_summary && (
        <details className="mt-3 rounded-xl border border-[var(--border)] p-4">
          <summary className="cursor-pointer text-sm font-semibold">
            查看学生与 AI 的对话摘要
          </summary>
          <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap font-sans text-sm leading-6 text-[var(--text-secondary)]">
            {request.conversation_summary}
          </pre>
        </details>
      )}

      {closed ? (
        <div className="mt-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">
          <strong>
            教师结论：{request.decision || statusLabels[request.status]}
          </strong>
          {request.teacher_response && (
            <p className="mt-2 whitespace-pre-wrap leading-6">
              {request.teacher_response}
            </p>
          )}
        </div>
      ) : (
        <form
          onSubmit={submit}
          className="mt-5 grid gap-4 border-t border-[var(--border)] pt-5"
        >
          <Select
            label="处理结论"
            value={action}
            onChange={(event) => setAction(event.target.value as ReviewAction)}
          >
            <option value="uphold">维持原判</option>
            <option value="change_score">确认误判并修改分数</option>
            <option value="needs_information">请学生补充信息</option>
            <option value="reject">驳回复核请求</option>
          </Select>
          {action === "change_score" && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Input
                label="新分数"
                type="number"
                required
                min="0"
                step="0.01"
                value={finalScore}
                onChange={(event) => setFinalScore(event.target.value)}
              />
              <Input
                label="修订后的题目反馈"
                value={finalFeedback}
                maxLength={4000}
                onChange={(event) => setFinalFeedback(event.target.value)}
              />
            </div>
          )}
          <Textarea
            label="给学生的复核说明"
            required
            maxLength={4000}
            value={response}
            onChange={(event) => setResponse(event.target.value)}
          />
          {action === "change_score" && (
            <p
              role="note"
              className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800"
            >
              此操作会创建成绩修订记录，但学生看到新分数前，仍需按成绩发布流程生成并发布新的成绩快照版本。
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          <div>
            <Button type="submit" loading={saving}>
              确认处理
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

export default function TeacherReviewRequestsPage() {
  const [requests, setRequests] = useState<TeacherReviewRequest[]>([]);
  const [filter, setFilter] = useState("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setRequests(collectionItems(await teacherReviewRequestsApi.list()));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学生复核请求加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);
  const visible = requests.filter((item) => {
    if (filter === "all") return true;
    if (filter === "closed")
      return item.status === "resolved" || item.status === "rejected";
    return item.status !== "resolved" && item.status !== "rejected";
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="学生复核请求"
        description="人工核对学生对错题判定的疑问。AI 对话仅作为线索，最终判断必须依据原题、作答证据和评分规则。"
        actions={
          <Select
            aria-label="筛选复核请求"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="open">待处理</option>
            <option value="closed">已处理</option>
            <option value="all">全部请求</option>
          </Select>
        }
      />
      {loading ? (
        <div className="grid gap-4">
          {[1, 2].map((item) => (
            <Skeleton key={item} className="h-80" />
          ))}
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : visible.length ? (
        <section aria-label="学生复核请求列表" className="grid gap-5">
          {visible.map((request) => (
            <ReviewRequestCard
              key={request.id}
              request={request}
              onUpdated={() => void load()}
            />
          ))}
        </section>
      ) : (
        <EmptyState
          title="当前没有复核请求"
          description={
            filter === "open"
              ? "学生提交新的人工复核申请后会显示在这里。"
              : "当前筛选范围内没有记录。"
          }
          icon="review"
        />
      )}
    </div>
  );
}

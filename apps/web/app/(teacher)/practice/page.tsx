"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  StatCard,
} from "@/components/ui";
import {
  ApiError,
  teacherPracticeApi,
  type TeacherWrongQuestion,
  type TeacherWrongQuestionResponse,
} from "@/lib/api";

function formatScore(score: string, maximum: string) {
  return `${Number(score).toLocaleString("zh-CN")} / ${Number(maximum).toLocaleString("zh-CN")}`;
}

function formatDateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "时间未记录";
}

function WrongQuestionCard({ item }: { item: TeacherWrongQuestion }) {
  return (
    <Card className="p-5">
      <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-bold">
              {item.student_name}（{item.student_number}）
            </h2>
            <span className="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-700">
              得分率 {Math.round(item.score_rate * 100)}%
            </span>
          </div>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {item.class_name} · {item.assignment_title} · 第{" "}
            {item.question_number} 题
          </p>
        </div>
        <div className="text-left lg:text-right">
          <p className="text-lg font-bold text-[var(--brand-700)]">
            {formatScore(item.score, item.max_score)}
          </p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            发布于 {formatDateTime(item.released_at)} · 成绩版本 v
            {item.grade_release_version}
          </p>
        </div>
      </div>

      <dl className="mt-5 grid gap-4 md:grid-cols-2">
        <div className="rounded-xl bg-slate-50 p-4">
          <dt className="text-sm font-semibold text-[var(--text-secondary)]">
            题目
          </dt>
          <dd className="mt-2 whitespace-pre-wrap text-sm leading-6">
            {item.question_content || "题目文本暂不可用，请查看原批改证据。"}
          </dd>
        </div>
        <div className="rounded-xl bg-slate-50 p-4">
          <dt className="text-sm font-semibold text-[var(--text-secondary)]">
            学生答案
          </dt>
          <dd className="mt-2 whitespace-pre-wrap text-sm leading-6">
            {item.student_answer || "未记录文本答案，请查看原答卷图像。"}
          </dd>
        </div>
        <div>
          <dt className="text-sm font-semibold text-[var(--text-secondary)]">
            教师确认反馈
          </dt>
          <dd className="mt-1 whitespace-pre-wrap text-sm leading-6">
            {item.feedback || item.error_type || "未填写反馈"}
          </dd>
        </div>
        <div>
          <dt className="text-sm font-semibold text-[var(--text-secondary)]">
            错误类型与知识点
          </dt>
          <dd className="mt-2 flex flex-wrap gap-2 text-sm">
            {item.error_type && (
              <span className="rounded-full bg-red-50 px-2.5 py-1 text-red-700">
                {item.error_type}
              </span>
            )}
            {item.knowledge_points.map((point) => (
              <span
                key={point.id}
                className="rounded-full bg-blue-50 px-2.5 py-1 text-blue-700"
              >
                {point.name}
              </span>
            ))}
            {!item.error_type && !item.knowledge_points.length && "尚未标注"}
          </dd>
        </div>
      </dl>

      <div className="mt-5 flex flex-wrap gap-4 border-t border-[var(--border)] pt-4 text-sm font-semibold">
        <Link
          href={`/assignments/${item.assignment_id}`}
          className="text-[var(--brand-700)]"
        >
          查看作业
        </Link>
        <Link
          href={`/grading/${item.grading_batch_id}/review`}
          className="text-[var(--brand-700)]"
        >
          查看原批改证据
        </Link>
      </div>
    </Card>
  );
}

export default function PracticePage() {
  const [data, setData] = useState<TeacherWrongQuestionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [classId, setClassId] = useState("");
  const [assignmentId, setAssignmentId] = useState("");
  const [errorType, setErrorType] = useState("");
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [page, setPage] = useState(1);
  const requestSequence = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestSequence.current;
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ page: String(page), page_size: "30" });
    if (classId) query.set("class_id", classId);
    if (assignmentId) query.set("assignment_id", assignmentId);
    if (errorType) query.set("error_type", errorType);
    if (appliedSearch) query.set("search", appliedSearch);
    try {
      const nextData = await teacherPracticeApi.wrongQuestions(
        query.toString(),
      );
      if (requestId === requestSequence.current) setData(nextData);
    } catch (reason) {
      if (requestId === requestSequence.current) {
        setError(
          reason instanceof ApiError
            ? reason.message
            : "教师错题数据加载失败。",
        );
      }
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, [appliedSearch, assignmentId, classId, errorType, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setAppliedSearch(search.trim());
  };

  const resetFilters = () => {
    setClassId("");
    setAssignmentId("");
    setErrorType("");
    setSearch("");
    setAppliedSearch("");
    setPage(1);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="错题与练习"
        description="依据最新正式成绩版本整理教师确认的失分题，并关联原批改证据。本页不调用 AI，也不会自动发布练习。"
        actions={
          <Link href="/assignments/new">
            <Button>创建练习作业</Button>
          </Link>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="已确认错题"
          value={data ? String(data.summary.total_wrong_questions) : "—"}
          note="当前筛选下的最新正式版本"
        />
        <StatCard
          label="涉及学生"
          value={data ? String(data.summary.affected_students) : "—"}
          note="按学生档案去重"
        />
        <StatCard
          label="平均得分率"
          value={
            data?.summary.average_score_rate == null
              ? "—"
              : `${Math.round(data.summary.average_score_rate * 100)}%`
          }
          note="只统计当前失分题"
        />
        <StatCard
          label="覆盖知识点"
          value={data ? String(data.summary.knowledge_point_count) : "—"}
          note="按知识点去重"
        />
      </div>

      <Card className="p-4">
        <form
          onSubmit={applyFilters}
          className="grid gap-3 lg:grid-cols-[1fr_1fr_1fr_1.5fr_auto] lg:items-end"
        >
          <Select
            label="班级"
            value={classId}
            onChange={(event) => {
              setClassId(event.target.value);
              setAssignmentId("");
              setPage(1);
            }}
          >
            <option value="">全部班级</option>
            {(data?.facets.classes || []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
          <Select
            label="作业"
            value={assignmentId}
            onChange={(event) => {
              setAssignmentId(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部作业</option>
            {(data?.facets.assignments || []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </Select>
          <Select
            label="错误类型"
            value={errorType}
            onChange={(event) => {
              setErrorType(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部类型</option>
            {(data?.facets.error_types || []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
          <Input
            label="搜索"
            placeholder="学生、作业、题号、知识点"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="flex gap-2">
            <Button type="submit">筛选</Button>
            <Button type="button" variant="outline" onClick={resetFilters}>
              清除
            </Button>
          </div>
        </form>
      </Card>

      {loading ? (
        <div aria-label="正在加载错题" className="grid gap-4">
          <Skeleton className="h-72" />
          <Skeleton className="h-72" />
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : data?.items.length ? (
        <section aria-label="教师错题列表" className="grid gap-5">
          {data.items.map((item) => (
            <WrongQuestionCard key={item.id} item={item} />
          ))}
        </section>
      ) : (
        <EmptyState
          icon="practice"
          title="当前筛选下没有已确认错题"
          description="错题只来自教师已定稿的完整正式成绩快照。请先完成批改和结果确认，或清除当前筛选条件。"
          action={
            <Link href="/grading">
              <Button variant="outline">前往批改</Button>
            </Link>
          }
        />
      )}

      {(data?.pages || 0) > 1 && (
        <nav
          aria-label="错题分页"
          className="flex items-center justify-center gap-3"
        >
          <Button
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            上一页
          </Button>
          <span className="text-sm text-[var(--text-secondary)]">
            第 {data?.page} / {data?.pages} 页
          </span>
          <Button
            variant="outline"
            disabled={page >= (data?.pages || 0)}
            onClick={() => setPage((value) => value + 1)}
          >
            下一页
          </Button>
        </nav>
      )}
    </div>
  );
}

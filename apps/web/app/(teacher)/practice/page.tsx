"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
  assignmentsApi,
  teacherPracticeApi,
  type TeacherWrongQuestion,
  type TeacherWrongQuestionResponse,
} from "@/lib/api";

const MAX_SELECTED_WRONG_QUESTIONS = 50;

function formatScore(score: string, maximum: string) {
  return `${Number(score).toLocaleString("zh-CN")} / ${Number(maximum).toLocaleString("zh-CN")}`;
}

function formatDateTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "时间未记录";
}

function WrongQuestionCard({
  item,
  selected,
  selectionDisabled,
  onToggle,
}: {
  item: TeacherWrongQuestion;
  selected: boolean;
  selectionDisabled: boolean;
  onToggle: (item: TeacherWrongQuestion, checked: boolean) => void;
}) {
  return (
    <Card className="p-5">
      <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="checkbox"
              aria-label={`选择 ${item.student_name} ${item.assignment_title} 第${item.question_number}题`}
              checked={selected}
              disabled={selectionDisabled && !selected}
              onChange={(event) => onToggle(item, event.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
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
  const router = useRouter();
  const [data, setData] = useState<TeacherWrongQuestionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [classId, setClassId] = useState("");
  const [assignmentId, setAssignmentId] = useState("");
  const [errorType, setErrorType] = useState("");
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedItems, setSelectedItems] = useState<
    Record<string, TeacherWrongQuestion>
  >({});
  const [creatingDraft, setCreatingDraft] = useState(false);
  const [draftError, setDraftError] = useState("");
  const requestSequence = useRef(0);

  const selected = Object.values(selectedItems);
  const uniqueSelectedQuestions = [
    ...new Map(selected.map((item) => [item.question_id, item])).values(),
  ];

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

  const toggleSelection = (item: TeacherWrongQuestion, checked: boolean) => {
    setDraftError("");
    setSelectedItems((current) => {
      if (
        checked &&
        !current[item.id] &&
        Object.keys(current).length >= MAX_SELECTED_WRONG_QUESTIONS
      ) {
        return current;
      }
      const next = { ...current };
      if (checked) next[item.id] = item;
      else delete next[item.id];
      return next;
    });
  };

  const selectVisibleItems = () => {
    setDraftError("");
    setSelectedItems((current) => {
      const next = { ...current };
      for (const item of data?.items || []) {
        if (Object.keys(next).length >= MAX_SELECTED_WRONG_QUESTIONS) break;
        next[item.id] = item;
      }
      return next;
    });
  };

  const createPracticeDraft = async () => {
    if (!selected.length) return;
    setCreatingDraft(true);
    setDraftError("");
    const classIds = [...new Set(selected.map((item) => item.class_id))].sort();
    const lines = uniqueSelectedQuestions.map((item, index) => {
      const points = item.knowledge_points
        .map((point) => point.name)
        .join("、");
      return `${index + 1}. ${item.assignment_title} · 第${item.question_number}题${points ? ` · 知识点：${points}` : ""}`;
    });
    const instructions = [
      "请根据以下正式成绩错题来源重新设计练习题。创建后仍需由教师上传或编辑题目，并完成答案、评分标准和发布确认。",
      ...lines,
    ]
      .join("\n")
      .slice(0, 4000);
    try {
      const draft = await assignmentsApi.create({
        title: `错题巩固练习 ${new Date().toLocaleDateString("zh-CN")}`,
        delivery_mode: "class_assignment",
        class_ids: classIds,
        description: `由教师从正式成绩错题中创建的未发布草稿；选中 ${selected.length} 条失分记录，按原题去重后 ${uniqueSelectedQuestions.length} 道。`,
        instructions,
      });
      router.push(`/assignments/${draft.id}/edit?step=1`);
    } catch (reason) {
      setDraftError(
        reason instanceof ApiError ? reason.message : "练习草稿创建失败。",
      );
    } finally {
      setCreatingDraft(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="错题与练习"
        description="依据最新正式成绩版本整理教师确认的失分题，并关联原批改证据。本页不调用 AI，也不会自动发布练习。"
        actions={
          <Link href="/assignments/new">
            <Button variant="outline">新建空白作业</Button>
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
        {data?.items.length ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-4">
            <p className="text-sm text-[var(--text-secondary)]">
              选择可跨筛选和分页保留，创建草稿时会按原题去重。
            </p>
            <Button
              type="button"
              variant="outline"
              disabled={
                loading || selected.length >= MAX_SELECTED_WRONG_QUESTIONS
              }
              onClick={selectVisibleItems}
            >
              选择本页
            </Button>
          </div>
        ) : null}
      </Card>

      {selected.length > 0 && (
        <Card className="border-blue-200 bg-blue-50 p-4">
          <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center">
            <div>
              <p className="font-bold text-blue-950">
                已选择 {selected.length} 条失分记录，按原题去重后{" "}
                {uniqueSelectedQuestions.length} 道
              </p>
              <p className="mt-1 text-sm text-blue-800">
                最多选择 {MAX_SELECTED_WRONG_QUESTIONS}{" "}
                条。草稿只写入班级、原作业、题号和知识点，不写入学生姓名或答案，也不会自动发布。
              </p>
              {draftError && (
                <p
                  role="alert"
                  className="mt-2 text-sm font-semibold text-red-700"
                >
                  {draftError}
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setSelectedItems({});
                  setDraftError("");
                }}
              >
                清空选择
              </Button>
              <Button loading={creatingDraft} onClick={createPracticeDraft}>
                创建练习草稿
              </Button>
            </div>
          </div>
        </Card>
      )}

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
            <WrongQuestionCard
              key={item.id}
              item={item}
              selected={Boolean(selectedItems[item.id])}
              selectionDisabled={
                selected.length >= MAX_SELECTED_WRONG_QUESTIONS
              }
              onToggle={toggleSelection}
            />
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

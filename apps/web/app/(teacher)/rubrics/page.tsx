"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StructuredRubricEditor } from "@/components/structured-rubric-editor";
import {
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  ApiError,
  assignmentsApi,
  authApi,
  classesApi,
  structuredRubricApi,
  type AssignmentRecord,
  type ClassRecord,
  type Page,
  type ReferenceAnswerVersion,
  type RubricCatalogRecord,
} from "@/lib/api";

const statusLabel = {
  draft: "草稿",
  confirmed: "已确认",
  retired: "已停用",
} as const;

const statusStyle = {
  draft: "bg-slate-100 text-slate-700",
  confirmed: "bg-emerald-50 text-emerald-700",
  retired: "bg-amber-50 text-amber-800",
} as const;

function problemMessage(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}

export default function RubricsPage() {
  const toast = useToast();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [assignments, setAssignments] = useState<AssignmentRecord[]>([]);
  const [catalog, setCatalog] = useState<Page<RubricCatalogRecord>>();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<
    "all" | "draft" | "confirmed" | "retired"
  >("all");
  const [classId, setClassId] = useState("");
  const [pageSize, setPageSize] = useState<10 | 20 | 50>(20);
  const [compactCards, setCompactCards] = useState(false);
  const [page, setPage] = useState(1);
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [editing, setEditing] = useState<RubricCatalogRecord>();
  const [derivingId, setDerivingId] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState("");
  const [selectedAssignment, setSelectedAssignment] =
    useState<AssignmentRecord>();
  const [selectedQuestionId, setSelectedQuestionId] = useState("");
  const [confirmedReference, setConfirmedReference] =
    useState<ReferenceAnswerVersion>();
  const [assignmentLoading, setAssignmentLoading] = useState(false);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const catalogRequest = useRef(0);
  const assignmentRequest = useRef(0);
  const referenceRequest = useRef(0);

  const bootstrap = useCallback(async () => {
    setReady(false);
    setBootError("");
    try {
      const [settings, classPage, assignmentPage] = await Promise.all([
        authApi.preferences(),
        classesApi.list("page=1&page_size=100&status=active&sort=name_asc"),
        assignmentsApi.list("page=1&page_size=100&sort=updated_desc"),
      ]);
      setClasses(classPage.items);
      setAssignments(assignmentPage.items);
      const preferredClassId = settings.preferences.default_class_id;
      setClassId(
        preferredClassId &&
          classPage.items.some((item) => item.id === preferredClassId)
          ? preferredClassId
          : "",
      );
      setStatus(settings.preferences.rubric_status_filter);
      setPageSize(settings.preferences.rubric_page_size);
      setCompactCards(settings.preferences.compact_rubric_cards);
      setPage(1);
      setReady(true);
    } catch (reason) {
      setBootError(
        problemMessage(reason, "评分模板工作台加载失败，请确认后端服务正常。"),
      );
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (!ready) return;
    const requestId = ++catalogRequest.current;
    const query = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (search) query.set("search", search);
    if (status !== "all") query.set("status", status);
    if (classId) query.set("class_id", classId);
    setCatalogLoading(true);
    setCatalogError("");
    structuredRubricApi
      .catalog(query.toString())
      .then((result) => {
        if (requestId === catalogRequest.current) setCatalog(result);
      })
      .catch((reason) => {
        if (requestId === catalogRequest.current) {
          setCatalogError(
            problemMessage(reason, "评分模板加载失败，请稍后重试。"),
          );
        }
      })
      .finally(() => {
        if (requestId === catalogRequest.current) setCatalogLoading(false);
      });
  }, [classId, page, pageSize, ready, refreshVersion, search, status]);

  const selectedQuestion = useMemo(
    () =>
      selectedAssignment?.paper_version?.questions.find(
        (item) => item.id === selectedQuestionId,
      ),
    [selectedAssignment, selectedQuestionId],
  );

  async function chooseAssignment(id: string) {
    const requestId = ++assignmentRequest.current;
    setSelectedAssignmentId(id);
    setSelectedAssignment(undefined);
    setSelectedQuestionId("");
    setConfirmedReference(undefined);
    setCreateError("");
    if (!id) return;
    setAssignmentLoading(true);
    try {
      const result = await assignmentsApi.get(id);
      if (requestId === assignmentRequest.current)
        setSelectedAssignment(result);
    } catch (reason) {
      if (requestId === assignmentRequest.current) {
        setCreateError(problemMessage(reason, "作业题目加载失败。"));
      }
    } finally {
      if (requestId === assignmentRequest.current) setAssignmentLoading(false);
    }
  }

  async function chooseQuestion(id: string) {
    const requestId = ++referenceRequest.current;
    setSelectedQuestionId(id);
    setConfirmedReference(undefined);
    setCreateError("");
    if (!id) return;
    setReferenceLoading(true);
    try {
      const references = await structuredRubricApi.references(id);
      if (requestId === referenceRequest.current) {
        setConfirmedReference(
          references.find((item) => item.status === "confirmed"),
        );
      }
    } catch (reason) {
      if (requestId === referenceRequest.current) {
        setCreateError(problemMessage(reason, "标准答案版本加载失败。"));
      }
    } finally {
      if (requestId === referenceRequest.current) setReferenceLoading(false);
    }
  }

  async function createRubric() {
    if (!selectedAssignment || !selectedQuestion || !confirmedReference) return;
    if (
      !selectedQuestion.max_score ||
      Number(selectedQuestion.max_score) <= 0
    ) {
      setCreateError("请先为题目设置大于 0 的满分。 ");
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const rubric = await structuredRubricApi.create(selectedQuestion.id, {
        reference_answer_version_id: confirmedReference.id,
        title: `${selectedAssignment.title} · 第 ${selectedQuestion.question_number} 题`,
        total_points: selectedQuestion.max_score,
        criteria: [
          {
            stable_key: "final_answer",
            title: "最终答案",
            max_points: selectedQuestion.max_score,
            criterion_type: "final_answer",
            required: true,
            dependencies: [],
            validation_mode: "manual_only",
            validation_rule: {},
          },
        ],
      });
      const record: RubricCatalogRecord = {
        rubric,
        created_at: new Date().toISOString(),
        confirmed_at: null,
        assignment: {
          id: selectedAssignment.id,
          title: selectedAssignment.title,
          subject: selectedAssignment.subject,
          grade: selectedAssignment.grade,
          status: selectedAssignment.status,
        },
        question: {
          id: selectedQuestion.id,
          question_number: selectedQuestion.question_number,
          content_text: selectedQuestion.content_text ?? "",
          max_score: selectedQuestion.max_score,
        },
      };
      setCreateOpen(false);
      setEditing(record);
      setRefreshVersion((value) => value + 1);
      toast("评分模板草稿已创建");
    } catch (reason) {
      setCreateError(problemMessage(reason, "评分模板创建失败。"));
    } finally {
      setCreating(false);
    }
  }

  async function deriveRubric(item: RubricCatalogRecord) {
    setDerivingId(item.rubric.id);
    try {
      const rubric = await structuredRubricApi.derive(item.rubric.id);
      setEditing({
        ...item,
        rubric,
        created_at: new Date().toISOString(),
        confirmed_at: null,
      });
      setRefreshVersion((value) => value + 1);
      toast("已派生新的可编辑草稿");
    } catch (reason) {
      toast(problemMessage(reason, "派生模板失败，请重试。"), "error");
    } finally {
      setDerivingId("");
    }
  }

  const pageCount = catalog?.pages ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="评分模板"
        description="统一管理每道题的真实评分规则、标准答案绑定和历史版本。已确认模板保持只读，需要修改时请派生新草稿。"
        actions={
          <Dialog
            open={createOpen}
            onOpenChange={setCreateOpen}
            trigger={<Button>创建评分模板</Button>}
            title="创建评分模板草稿"
            description="选择已有作业和题目。题目必须已经确认标准答案版本。"
          >
            <div className="space-y-4">
              <Select
                label="作业"
                value={selectedAssignmentId}
                onChange={(event) => void chooseAssignment(event.target.value)}
              >
                <option value="">请选择作业</option>
                {assignments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </Select>
              <Select
                label="题目"
                value={selectedQuestionId}
                disabled={!selectedAssignment || assignmentLoading}
                onChange={(event) => void chooseQuestion(event.target.value)}
              >
                <option value="">
                  {assignmentLoading ? "正在加载题目…" : "请选择题目"}
                </option>
                {selectedAssignment?.paper_version?.questions.map((item) => (
                  <option key={item.id} value={item.id}>
                    第 {item.question_number} 题
                    {item.max_score
                      ? `（${item.max_score} 分）`
                      : "（未设置满分）"}
                  </option>
                ))}
              </Select>
              {selectedQuestionId &&
                !referenceLoading &&
                !confirmedReference && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    此题尚无已确认的标准答案。请先进入题目工作台补充并确认，然后再创建模板。
                  </div>
                )}
              {createError && (
                <p role="alert" className="text-sm text-[var(--danger)]">
                  {createError}
                </p>
              )}
              <div className="flex flex-wrap justify-end gap-3">
                {selectedAssignmentId && selectedQuestionId && (
                  <Link
                    href={`/assignments/${selectedAssignmentId}/rubrics/${selectedQuestionId}`}
                    className="inline-flex min-h-10 items-center rounded-[var(--radius-md)] border border-[var(--border)] px-4 text-sm font-semibold hover:bg-slate-50"
                  >
                    打开题目工作台
                  </Link>
                )}
                <Button
                  loading={creating || referenceLoading}
                  disabled={!selectedQuestion || !confirmedReference}
                  onClick={() => void createRubric()}
                >
                  创建草稿并编辑
                </Button>
              </div>
            </div>
          </Dialog>
        }
      />

      {bootError ? (
        <ErrorState description={bootError} retry={() => void bootstrap()} />
      ) : !ready ? (
        <div className="space-y-4" aria-label="正在加载评分模板工作台">
          <Skeleton className="h-20" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : (
        <>
          <Card className="p-4">
            <form
              className="grid gap-3 lg:grid-cols-[minmax(240px,1fr)_180px_180px_auto]"
              onSubmit={(event) => {
                event.preventDefault();
                setPage(1);
                setSearch(searchInput.trim());
              }}
            >
              <Input
                aria-label="搜索评分模板"
                placeholder="搜索模板、作业、题号或题干"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
              />
              <Select
                aria-label="模板状态筛选"
                value={status}
                onChange={(event) => {
                  setPage(1);
                  setStatus(
                    event.target.value as
                      "all" | "draft" | "confirmed" | "retired",
                  );
                }}
              >
                <option value="all">全部状态</option>
                <option value="draft">草稿</option>
                <option value="confirmed">已确认</option>
                <option value="retired">已停用</option>
              </Select>
              <Select
                aria-label="班级筛选"
                value={classId}
                onChange={(event) => {
                  setPage(1);
                  setClassId(event.target.value);
                }}
              >
                <option value="">全部班级</option>
                {classes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
              <Button type="submit">搜索</Button>
            </form>
          </Card>

          {catalogError ? (
            <ErrorState
              description={catalogError}
              retry={() => setRefreshVersion((value) => value + 1)}
            />
          ) : catalogLoading && !catalog ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Skeleton className="h-44" />
              <Skeleton className="h-44" />
            </div>
          ) : catalog?.items.length ? (
            <>
              <div
                className={
                  compactCards
                    ? "grid gap-3 lg:grid-cols-2"
                    : "grid gap-4 lg:grid-cols-2"
                }
                aria-busy={catalogLoading || undefined}
              >
                {catalog.items.map((item) => (
                  <Card
                    key={item.rubric.id}
                    className={compactCards ? "p-4" : "p-5"}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <h2 className="truncate font-bold">
                          {item.rubric.title}
                        </h2>
                        <p className="mt-1 text-sm text-[var(--text-secondary)]">
                          {item.assignment.title} · 第{" "}
                          {item.question.question_number} 题
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${statusStyle[item.rubric.status]}`}
                      >
                        {statusLabel[item.rubric.status]}
                      </span>
                    </div>
                    {!compactCards && item.question.content_text && (
                      <p className="mt-4 line-clamp-2 text-sm leading-6 text-[var(--text-secondary)]">
                        {item.question.content_text}
                      </p>
                    )}
                    <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-[var(--border)] pt-4 text-sm">
                      <div>
                        <dt className="text-xs text-[var(--text-secondary)]">
                          版本
                        </dt>
                        <dd className="mt-1 font-semibold">
                          v{item.rubric.rubric_version}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-[var(--text-secondary)]">
                          评分项
                        </dt>
                        <dd className="mt-1 font-semibold">
                          {item.rubric.criteria.length} 项
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-[var(--text-secondary)]">
                          总分
                        </dt>
                        <dd className="mt-1 font-semibold">
                          {item.rubric.total_points} 分
                        </dd>
                      </div>
                    </dl>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        onClick={() => setEditing(item)}
                      >
                        {item.rubric.status === "draft"
                          ? "编辑草稿"
                          : "查看详情"}
                      </Button>
                      {item.rubric.status === "confirmed" && (
                        <Button
                          variant="secondary"
                          loading={derivingId === item.rubric.id}
                          onClick={() => void deriveRubric(item)}
                        >
                          派生新草稿
                        </Button>
                      )}
                      <Link
                        href={`/assignments/${item.assignment.id}/rubrics/${item.question.id}`}
                        className="inline-flex min-h-10 items-center rounded-[var(--radius-md)] px-3 text-sm font-semibold text-[var(--brand-700)] hover:bg-[var(--brand-50)]"
                      >
                        标准答案与历史版本
                      </Link>
                    </div>
                  </Card>
                ))}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-[var(--text-secondary)]">
                  共 {catalog.total} 个模板，第 {catalog.page} /{" "}
                  {Math.max(pageCount, 1)} 页
                </p>
                <div className="flex items-center gap-2">
                  <Select
                    aria-label="每页模板数"
                    value={String(pageSize)}
                    onChange={(event) => {
                      setPage(1);
                      setPageSize(Number(event.target.value) as 10 | 20 | 50);
                    }}
                  >
                    <option value="10">每页 10 条</option>
                    <option value="20">每页 20 条</option>
                    <option value="50">每页 50 条</option>
                  </Select>
                  <Button
                    variant="outline"
                    disabled={page <= 1 || catalogLoading}
                    onClick={() => setPage((value) => value - 1)}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    disabled={page >= pageCount || catalogLoading}
                    onClick={() => setPage((value) => value + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <EmptyState
              title="暂无符合条件的评分模板"
              description="可调整筛选条件，或从已有作业题目创建第一个评分模板。"
              action={
                <Button onClick={() => setCreateOpen(true)}>
                  创建评分模板
                </Button>
              }
              icon="rubrics"
            />
          )}
        </>
      )}

      {editing && (
        <StructuredRubricEditor
          initial={editing.rubric}
          onCancel={() => setEditing(undefined)}
          onSave={async (rubric) => {
            try {
              const updated = await structuredRubricApi.update(rubric.id, {
                reference_answer_version_id: rubric.reference_answer_version_id,
                title: rubric.title,
                total_points: rubric.total_points,
                criteria: rubric.criteria,
              });
              setEditing({ ...editing, rubric: updated });
              setRefreshVersion((value) => value + 1);
              toast("评分模板草稿已保存");
            } catch (reason) {
              toast(problemMessage(reason, "评分模板保存失败。"), "error");
            }
          }}
          onConfirm={async () => {
            try {
              const validation = await structuredRubricApi.validate(
                editing.rubric.id,
              );
              if (!validation.valid) {
                toast("模板校验未通过，请检查分值、依赖和验证规则。", "error");
                return;
              }
              await structuredRubricApi.confirm(editing.rubric.id);
              setEditing(undefined);
              setRefreshVersion((value) => value + 1);
              toast("评分模板已确认");
            } catch (reason) {
              toast(problemMessage(reason, "评分模板确认失败。"), "error");
            }
          }}
        />
      )}
    </div>
  );
}

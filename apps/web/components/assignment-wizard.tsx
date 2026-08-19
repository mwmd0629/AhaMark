"use client";
/* eslint-disable @next/next/no-img-element */

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import { useRouter } from "next/navigation";
import {
  Button,
  Card,
  ErrorState,
  Input,
  PageHeader,
  Select,
  useToast,
} from "@/components/ui";
import { AssignmentGenerationPanel } from "@/components/assignment-generation-panel";
import { AnswerRubricGenerationReview } from "@/components/answer-rubric-generation-review";
import { AssignmentCentralReview } from "@/components/assignment-central-review";
import { JointExamTeamPanel } from "@/components/joint-exam-team-panel";
import { QuestionPageCutter } from "@/components/question-page-cutter";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentFieldSuggestion,
  type AssignmentRecord,
  type ClassResource,
  type ClassRecord,
} from "@/lib/api";
import { formatQuestionScore } from "@/lib/question-score";

const steps = ["准备作业", "核对内容", "确认发布"];

function wizardStepForCompleteness(step: number) {
  if (step <= 2) return 1;
  if (step <= 5) return 2;
  return 3;
}

function toLocalDateTimeInput(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function toIsoDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

export function AssignmentWizard({
  assignmentId,
  initialStep,
}: {
  assignmentId: string;
  initialStep?: number;
}) {
  const router = useRouter();
  const toast = useToast();
  const [item, setItem] = useState<AssignmentRecord>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [classResources, setClassResources] = useState<ClassResource[]>([]);
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([]);
  const [step, setStep] = useState(1);
  const [reviewInputsRevision, setReviewInputsRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dueMode, setDueMode] = useState<"none" | "scheduled">("none");
  const [dueValue, setDueValue] = useState("");
  const [selectedClassIds, setSelectedClassIds] = useState<string[]>([]);
  const [classPickerOpen, setClassPickerOpen] = useState(false);
  const [moreSettingsOpen, setMoreSettingsOpen] = useState(false);
  const [classQuery, setClassQuery] = useState("");
  const [deliveryMode, setDeliveryMode] = useState<
    "class_assignment" | "joint_exam"
  >("class_assignment");
  const [assignmentTitle, setAssignmentTitle] = useState("");
  const [assignmentSubject, setAssignmentSubject] = useState("");
  const [assignmentTotalScore, setAssignmentTotalScore] = useState("");
  const [fieldSuggestions, setFieldSuggestions] = useState<
    AssignmentFieldSuggestion[]
  >([]);
  const [uploadFile, setUploadFile] = useState<File>();
  const [uploadState, setUploadState] = useState<
    "idle" | "ready" | "uploading" | "processing" | "success" | "error"
  >("idle");
  const [uploadError, setUploadError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [deletingFileId, setDeletingFileId] = useState("");
  const [selectedPageId, setSelectedPageId] = useState("");
  const [pagePreviewUrls, setPagePreviewUrls] = useState<
    Record<string, string>
  >({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, boolean>>(
    {},
  );
  const pagePreviewRequests = useRef(new Set<string>());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const initializedRef = useRef(false);
  const [question, setQuestion] = useState({
    number: "",
    type: "calculation",
    score: "",
    text: "",
    difficulty: "medium",
    knowledge: "",
  });
  const [selectedQuestion, setSelectedQuestion] = useState("");
  const [questionDirty, setQuestionDirty] = useState(false);
  const [questionSubmitting, setQuestionSubmitting] = useState(false);
  const [questionConflict, setQuestionConflict] = useState(false);
  const questionDirtyRef = useRef(false);
  const load = useCallback(
    async (preferredQuestionId?: string) => {
      try {
        const [assignment, active] = await Promise.all([
          assignmentsApi.get(assignmentId),
          classesApi.list("status=active&page_size=100"),
        ]);
        setItem(assignment);
        setClasses(active.items);
        const initializing = !initializedRef.current;
        if (initializing) {
          setStep(
            initialStep ??
              wizardStepForCompleteness(assignment.completeness.next_step ?? 1),
          );
          initializedRef.current = true;
        }
        setSelectedQuestion((current) => {
          const questions = assignment.paper_version?.questions ?? [];
          if (
            preferredQuestionId &&
            questions.some((entry) => entry.id === preferredQuestionId)
          ) {
            setQuestionConflict(false);
            return preferredQuestionId;
          }
          if (!initializing && current === "") return current;
          if (questions.some((entry) => entry.id === current)) {
            setQuestionConflict(false);
            return current;
          }
          if (current && questionDirtyRef.current) {
            setQuestionConflict(true);
            return current;
          }
          setQuestionConflict(false);
          return questions[0]?.id ?? "";
        });
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "无法加载草稿");
      }
    },
    [assignmentId, initialStep],
  );
  const refreshReviewInputs = useCallback(async () => {
    setReviewInputsRevision((current) => current + 1);
    await load();
  }, [load]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (!item?.classes.length) {
      setClassResources([]);
      return;
    }
    void assignmentsApi
      .availableClassResources(assignmentId)
      .then(setClassResources)
      .catch(() => setClassResources([]));
  }, [assignmentId, item?.classes]);
  useEffect(() => {
    setDueMode(item?.due_at ? "scheduled" : "none");
    setDueValue(item?.due_at ? toLocalDateTimeInput(item.due_at) : "");
  }, [item?.id, item?.due_at]);
  useEffect(() => {
    const ownedClassIds = new Set(classes.map((entry) => entry.id));
    setSelectedClassIds(
      item?.classes
        .filter((entry) => ownedClassIds.has(entry.id))
        .map((entry) => entry.id) ?? [],
    );
  }, [classes, item?.classes]);
  useEffect(() => {
    setDeliveryMode(item?.delivery_mode ?? "class_assignment");
  }, [item?.id, item?.delivery_mode]);
  useEffect(() => {
    setMoreSettingsOpen(Boolean(item?.instructions));
  }, [item?.id, item?.instructions]);
  useEffect(() => {
    setAssignmentTitle(item?.title ?? "");
    setAssignmentSubject(item?.subject ?? "");
    setAssignmentTotalScore(
      item?.total_score == null ? "" : String(item.total_score),
    );
  }, [item?.id, item?.title, item?.subject, item?.total_score]);
  useEffect(() => {
    const pages = item?.paper_version?.pages ?? [];
    if (!pages.length) {
      setSelectedPageId("");
      return;
    }
    if (!pages.some((page) => page.id === selectedPageId)) {
      setSelectedPageId(pages[0].id);
    }
  }, [item?.paper_version?.pages, selectedPageId]);
  useEffect(() => {
    questionDirtyRef.current = questionDirty;
  }, [questionDirty]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (busy || questionDirty || questionSubmitting) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy, questionDirty, questionSubmitting]);
  const selected = useMemo(
    () => item?.paper_version?.questions.find((x) => x.id === selectedQuestion),
    [item, selectedQuestion],
  );
  useEffect(() => {
    if (!selected || questionDirty) return;
    setQuestion({
      number: selected.question_number,
      type: selected.question_type,
      score: selected.max_score ?? "",
      text: selected.content_text ?? "",
      difficulty: selected.difficulty ?? "medium",
      knowledge: selected.knowledge_points.map((point) => point.name).join(","),
    });
  }, [questionDirty, selected]);
  const uploadedFiles = useMemo(() => {
    const files = new Map<
      string,
      { id: string; name: string; pageCount: number }
    >();
    for (const page of item?.paper_version?.pages ?? []) {
      const current = files.get(page.stored_file_id);
      if (current) {
        current.pageCount += 1;
      } else {
        files.set(page.stored_file_id, {
          id: page.stored_file_id,
          name: page.file_name ?? `已上传文件 ${files.size + 1}`,
          pageCount: 1,
        });
      }
    }
    return [...files.values()];
  }, [item?.paper_version?.pages]);
  const loadPagePreview = useCallback(
    async (pageId: string) => {
      if (pagePreviewRequests.current.has(pageId)) return;
      pagePreviewRequests.current.add(pageId);
      try {
        const result = await assignmentsApi.pagePreview(assignmentId, pageId);
        setPagePreviewUrls((current) => ({
          ...current,
          [pageId]: result.url,
        }));
        setPreviewErrors((current) => ({ ...current, [pageId]: false }));
      } catch {
        setPreviewErrors((current) => ({ ...current, [pageId]: true }));
      } finally {
        pagePreviewRequests.current.delete(pageId);
      }
    },
    [assignmentId],
  );
  useEffect(() => {
    if (step !== 2) return;
    (item?.paper_version?.pages ?? []).forEach((page) => {
      if (!pagePreviewUrls[page.id] && !previewErrors[page.id]) {
        void loadPagePreview(page.id);
      }
    });
  }, [
    item?.paper_version?.pages,
    loadPagePreview,
    pagePreviewUrls,
    previewErrors,
    step,
  ]);
  if (error) return <ErrorState description={error} retry={load} />;
  if (!item) return <Card className="p-8">正在恢复后端草稿…</Card>;

  const saveBasics = async (form: FormData) => {
    const subject = String(form.get("subject") ?? "").trim();
    if (!subject || subject === "数学") {
      toast("请填写具体大学课程，例如数学分析或线性代数", "error");
      return;
    }
    if (dueMode === "scheduled" && !dueValue) {
      toast("请选择具体的截止日期和时间", "error");
      return;
    }
    if (deliveryMode !== "joint_exam" && !selectedClassIds.length) {
      toast("请至少选择一个班级", "error");
      return;
    }
    setBusy(true);
    try {
      let next = await assignmentsApi.update(
        item.id,
        {
          title: String(form.get("title")),
          delivery_mode: deliveryMode,
          subject,
          grade: String(form.get("grade")),
          description: String(form.get("description")),
          instructions: String(form.get("instructions")),
          total_score: Number(form.get("total_score")),
          due_at: dueMode === "none" ? null : toIsoDateTime(dueValue),
        },
        item.updated_at,
      );
      const currentClassIds = item.classes.map((entry) => entry.id).sort();
      const nextClassIds = [...selectedClassIds].sort();
      if (currentClassIds.join("|") !== nextClassIds.join("|")) {
        next = await assignmentsApi.setClasses(
          item.id,
          selectedClassIds,
          next.updated_at,
        );
      }
      setItem(next);
      document
        .getElementById("assignment-upload")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
      toast("基本信息已保存");
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "保存失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const suggestedAssignmentTitle = (() => {
    const course =
      assignmentSubject.trim() ||
      classes
        .find((entry) =>
          item.classes.some((selected) => selected.id === entry.id),
        )
        ?.subject?.trim();
    const date = new Intl.DateTimeFormat("zh-CN", {
      month: "numeric",
      day: "numeric",
    }).format(new Date());
    return `${course || "课程"} ${date} 作业`;
  })();
  const latestFieldSuggestion = (fieldName: string) =>
    fieldSuggestions.find(
      (row) =>
        row.field_name === fieldName &&
        !["superseded", "stale", "rejected"].includes(row.status),
    );
  const specificUniversityCourses = [
    "数学分析",
    "线性代数",
    "高等代数",
    "概率论",
    "常微分方程",
    "复变函数",
    "实变函数",
  ];
  const subjectSuggestion = String(
    latestFieldSuggestion("subject")?.normalized_value ?? "",
  ).trim();
  const subjectSuggestionEvidence = fieldSuggestions
    .filter((row) => ["subject", "title"].includes(row.field_name))
    .flatMap((row) => [row.normalized_value, row.suggested_value])
    .filter((value): value is string => typeof value === "string")
    .join(" ");
  const inferredSpecificCourse = specificUniversityCourses.find((course) =>
    subjectSuggestionEvidence.includes(course),
  );
  const subjectSuggestionValue =
    inferredSpecificCourse ??
    (subjectSuggestion === "数学" ? "" : subjectSuggestion);
  const totalScoreSuggestionNumber = Number(
    latestFieldSuggestion("total_score")?.normalized_value,
  );
  const totalScoreSuggestionValue =
    Number.isFinite(totalScoreSuggestionNumber) &&
    totalScoreSuggestionNumber > 0
      ? String(totalScoreSuggestionNumber)
      : "";
  const chooseUpload = (files: File[] = []) => {
    setUploadError("");
    const firstFile = files[0];
    if (!firstFile) {
      setUploadFile(undefined);
      setUploadState("idle");
      return;
    }
    for (const file of files) {
      const extension = file.name.split(".").pop()?.toLowerCase();
      if (!["pdf", "png", "jpg", "jpeg"].includes(extension ?? "")) {
        setUploadFile(file);
        setUploadState("error");
        setUploadError(
          `${file.name} 格式不支持，请选择 PDF、PNG 或 JPG 文件。`,
        );
        return;
      }
      if (file.size === 0) {
        setUploadFile(file);
        setUploadState("error");
        setUploadError(`${file.name} 内容为空，请重新选择完整文件。`);
        return;
      }
      if (file.size > 25 * 1024 * 1024) {
        setUploadFile(file);
        setUploadState("error");
        setUploadError(`${file.name} 超过 25 MB，请压缩后重新上传。`);
        return;
      }
    }
    setUploadFile(firstFile);
    void uploadPapers(files);
  };

  async function uploadPapers(files: File[]) {
    const firstFile = files[0];
    if (!firstFile || busy) return;
    setBusy(true);
    setUploadError("");
    setUploadState("uploading");
    try {
      for (const file of files) {
        await assignmentsApi.upload(assignmentId, file);
      }
      setUploadState("processing");
      await load();
      setUploadState("success");
    } catch (err) {
      setUploadState("error");
      setUploadError(
        err instanceof ApiError
          ? err.message
          : "上传失败，请检查网络后重新上传。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function addSelectedResources() {
    if (!selectedResourceIds.length || busy) return;
    setBusy(true);
    try {
      const result = await assignmentsApi.addClassResources(
        assignmentId,
        selectedResourceIds,
      );
      await load();
      setSelectedResourceIds([]);
      toast(
        `已加入 ${result.files_created} 份资料，共 ${result.pages_created} 页`,
      );
    } catch (error) {
      toast(
        error instanceof ApiError ? error.message : "资料加入失败",
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  const deleteUploadedFile = async (file: { id: string; name: string }) => {
    if (
      busy ||
      !window.confirm(`确定删除“${file.name}”吗？其对应页面也会删除。`)
    ) {
      return;
    }
    setBusy(true);
    setDeletingFileId(file.id);
    try {
      await assignmentsApi.removeFile(item.id, file.id);
      setPagePreviewUrls({});
      setPreviewErrors({});
      await load();
      toast("文件已删除");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "文件删除失败", "error");
    } finally {
      setDeletingFileId("");
      setBusy(false);
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    chooseUpload(Array.from(event.dataTransfer.files));
  };

  const selectedPage = item.paper_version?.pages.find(
    (page) => page.id === selectedPageId,
  );
  const selectedPreviewUrl = selectedPage
    ? pagePreviewUrls[selectedPage.id]
    : undefined;
  const selectedClasses = classes.filter((entry) =>
    selectedClassIds.includes(entry.id),
  );
  const normalizedClassQuery = classQuery.trim().toLocaleLowerCase();
  const visibleClasses = classes
    .filter((entry) =>
      [
        entry.name,
        entry.subject,
        entry.grade,
        entry.academic_year,
        entry.semester,
      ]
        .filter(Boolean)
        .some((value) =>
          value?.toLocaleLowerCase().includes(normalizedClassQuery),
        ),
    )
    .sort((left, right) => {
      const selectedDifference =
        Number(selectedClassIds.includes(right.id)) -
        Number(selectedClassIds.includes(left.id));
      return selectedDifference || left.name.localeCompare(right.name, "zh-CN");
    });

  return (
    <div className="space-y-6">
      <PageHeader
        title={item.title}
        description="准备、核对、发布"
        actions={
          <Link href="/assignments">
            <Button variant="outline">返回列表</Button>
          </Link>
        }
      />
      <ol className="grid grid-cols-3 gap-2" aria-label="创建步骤">
        {steps.map((label, index) => (
          <li key={label}>
            <button
              onClick={() => setStep(index + 1)}
              className={`w-full rounded-xl border p-3 text-left text-xs font-semibold ${step === index + 1 ? "border-[var(--brand-600)] bg-[var(--brand-50)] text-[var(--brand-700)]" : "bg-white"}`}
            >
              <span className="block text-[10px] opacity-60">
                步骤 {index + 1}
              </span>
              {label}
            </button>
          </li>
        ))}
      </ol>
      <div
        role="status"
        aria-live="polite"
        className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm"
      >
        <span className="text-slate-600">你现在在：</span>
        <strong>
          第 {step} 步 · {steps[step - 1]}
        </strong>
        <span className="ml-2 text-slate-600">
          {step === 1
            ? "下一步核对题目、答案和评分标准"
            : step === 2
              ? "下一步检查并确认发布"
              : "完成检查后由你确认发布"}
        </span>
      </div>

      {step === 1 && (
        <Card id="assignment-basics" className="scroll-mt-4 p-6">
          <form action={saveBasics} className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium" htmlFor="assignment-title">
                作业名称
              </label>
              <div
                className="flex h-10 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-white transition focus-within:border-[var(--brand-500)]"
                data-testid="assignment-title-field"
              >
                <input
                  id="assignment-title"
                  name="title"
                  aria-label="作业名称"
                  className="min-w-0 flex-1 bg-transparent px-3 font-normal outline-none"
                  required
                  value={assignmentTitle}
                  onChange={(event) => setAssignmentTitle(event.target.value)}
                />
                <button
                  type="button"
                  className="max-w-1/2 shrink-0 truncate border-l border-slate-100 bg-slate-50/70 px-3 text-left text-xs text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                  onClick={() => setAssignmentTitle(suggestedAssignmentTitle)}
                >
                  {suggestedAssignmentTitle}
                </button>
              </div>
            </div>
            <div className="space-y-1.5">
              <label
                className="text-sm font-medium"
                htmlFor="assignment-total-score"
              >
                总分
              </label>
              <div
                className="flex h-10 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-white transition focus-within:border-[var(--brand-500)]"
                data-testid="assignment-total-score-field"
              >
                <input
                  id="assignment-total-score"
                  name="total_score"
                  aria-label="总分"
                  className="min-w-0 flex-1 bg-transparent px-3 font-normal outline-none"
                  type="number"
                  min="0.01"
                  step="0.01"
                  required
                  value={assignmentTotalScore}
                  onChange={(event) =>
                    setAssignmentTotalScore(event.target.value)
                  }
                />
                {totalScoreSuggestionValue && (
                  <button
                    type="button"
                    className="max-w-1/2 shrink-0 truncate border-l border-slate-100 bg-slate-50/70 px-3 text-left text-xs text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                    onClick={() =>
                      setAssignmentTotalScore(totalScoreSuggestionValue)
                    }
                  >
                    {totalScoreSuggestionValue}
                  </button>
                )}
              </div>
            </div>
            <div className="space-y-1.5 md:col-span-2">
              <label
                className="text-sm font-medium"
                htmlFor="assignment-subject"
              >
                大学课程
              </label>
              <div
                className="flex h-10 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-white transition focus-within:border-[var(--brand-500)]"
                data-testid="assignment-subject-field"
              >
                <input
                  id="assignment-subject"
                  name="subject"
                  aria-label="大学课程"
                  className="min-w-0 flex-1 bg-transparent px-3 font-normal outline-none placeholder:text-slate-400"
                  list="university-course-options"
                  required
                  value={assignmentSubject}
                  placeholder="如：数学分析、线性代数、概率论"
                  onChange={(event) => setAssignmentSubject(event.target.value)}
                />
                {subjectSuggestionValue && (
                  <button
                    type="button"
                    className="max-w-1/2 shrink-0 truncate border-l border-slate-100 bg-slate-50/70 px-3 text-left text-xs text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                    onClick={() => setAssignmentSubject(subjectSuggestionValue)}
                  >
                    {subjectSuggestionValue}
                  </button>
                )}
              </div>
              <datalist id="university-course-options">
                <option value="数学分析" />
                <option value="线性代数" />
                <option value="高等代数" />
                <option value="概率论" />
                <option value="常微分方程" />
                <option value="复变函数" />
                <option value="实变函数" />
              </datalist>
            </div>
            <fieldset className="space-y-3 rounded-xl border p-4 md:col-span-2">
              <legend className="px-1 text-sm font-semibold">截止时间</legend>
              <label className="flex items-start gap-3">
                <input
                  type="radio"
                  name="due_mode"
                  value="none"
                  checked={dueMode === "none"}
                  onChange={() => {
                    setDueMode("none");
                    setDueValue("");
                  }}
                />
                <span>
                  <strong className="block text-sm">无截止时间</strong>
                  <span className="text-xs text-slate-500">
                    学生可以在教师手动关闭提交前继续提交
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-3">
                <input
                  type="radio"
                  name="due_mode"
                  value="scheduled"
                  checked={dueMode === "scheduled"}
                  onChange={() => {
                    setDueMode("scheduled");
                    setDueValue("");
                  }}
                />
                <span className="flex-1">
                  <strong className="block text-sm">设置截止时间</strong>
                  {dueMode === "scheduled" && (
                    <Input
                      className="mt-2"
                      aria-label="具体截止时间"
                      name="due_at"
                      type="datetime-local"
                      value={dueValue}
                      onChange={(event) => setDueValue(event.target.value)}
                      required
                    />
                  )}
                </span>
              </label>
            </fieldset>
            <fieldset className="space-y-3 rounded-xl border p-4 md:col-span-2">
              <legend className="px-1 text-sm font-semibold">发布班级</legend>
              {classes.length ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">
                        已选择 {selectedClasses.length} 个班级
                      </p>
                      {selectedClasses.length ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {selectedClasses.slice(0, 3).map((entry) => (
                            <span
                              key={entry.id}
                              className="rounded-full border bg-white px-2.5 py-1 text-xs"
                            >
                              {entry.name}
                            </span>
                          ))}
                          {selectedClasses.length > 3 && (
                            <span className="rounded-full bg-slate-200 px-2.5 py-1 text-xs">
                              另有 {selectedClasses.length - 3} 个
                            </span>
                          )}
                        </div>
                      ) : (
                        <p className="mt-1 text-xs text-amber-700">
                          尚未选择发布班级
                        </p>
                      )}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      aria-expanded={classPickerOpen}
                      aria-controls="assignment-class-picker"
                      onClick={() => setClassPickerOpen((current) => !current)}
                    >
                      {classPickerOpen ? "收起班级选择" : "选择发布班级"}
                    </Button>
                  </div>
                  {classPickerOpen && (
                    <div
                      id="assignment-class-picker"
                      className="space-y-3 rounded-xl border p-3"
                    >
                      <Input
                        label="搜索班级"
                        value={classQuery}
                        placeholder="输入班级、学科、年级或学年"
                        onChange={(event) => setClassQuery(event.target.value)}
                      />
                      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                        <span>
                          显示 {visibleClasses.length} / {classes.length}{" "}
                          个班级，已选班级置顶
                        </span>
                        <div className="flex gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            disabled={!visibleClasses.length}
                            onClick={() =>
                              setSelectedClassIds((current) => [
                                ...new Set([
                                  ...current,
                                  ...visibleClasses.map((entry) => entry.id),
                                ]),
                              ])
                            }
                          >
                            全选当前结果
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            disabled={!selectedClassIds.length}
                            onClick={() => setSelectedClassIds([])}
                          >
                            清空
                          </Button>
                        </div>
                      </div>
                      <div className="grid max-h-64 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                        {visibleClasses.map((entry) => (
                          <label
                            className="flex items-start gap-2 rounded-lg border p-3 text-sm"
                            key={entry.id}
                          >
                            <input
                              type="checkbox"
                              checked={selectedClassIds.includes(entry.id)}
                              onChange={(event) =>
                                setSelectedClassIds((current) =>
                                  event.target.checked
                                    ? [...new Set([...current, entry.id])]
                                    : current.filter((id) => id !== entry.id),
                                )
                              }
                            />
                            <span>
                              <strong className="block">{entry.name}</strong>
                              <span className="text-xs text-slate-500">
                                {[
                                  entry.subject,
                                  entry.grade,
                                  entry.academic_year,
                                  entry.semester,
                                ]
                                  .filter(Boolean)
                                  .join(" · ") || "未填写课程信息"}
                              </span>
                            </span>
                          </label>
                        ))}
                      </div>
                      {!visibleClasses.length && (
                        <p className="rounded-lg bg-slate-50 p-4 text-center text-sm text-slate-500">
                          没有匹配的班级
                        </p>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <Link
                  className="text-sm text-[var(--brand-700)]"
                  href="/classes"
                >
                  没有可用班级，前往班级管理
                </Link>
              )}
            </fieldset>
            <details
              open={moreSettingsOpen}
              onToggle={(event) =>
                setMoreSettingsOpen(event.currentTarget.open)
              }
              className="md:col-span-2 rounded-xl border p-4"
            >
              <summary className="cursor-pointer font-semibold">
                更多设置
              </summary>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <Input
                  name="grade"
                  label="年级或教学层级"
                  defaultValue={item.grade}
                  placeholder="如：大二、研究生、2026 级"
                />
                <Select
                  label="布置方式"
                  value={deliveryMode}
                  onChange={(event) =>
                    setDeliveryMode(
                      event.target.value as "class_assignment" | "joint_exam",
                    )
                  }
                >
                  <option value="class_assignment">普通作业</option>
                  <option value="joint_exam">联考统批</option>
                </Select>
                <label className="grid gap-1.5 text-sm font-medium md:col-span-2">
                  作业说明
                  <textarea
                    name="description"
                    defaultValue={item.description}
                    className="min-h-24 rounded-xl border p-3 font-normal"
                  />
                </label>
                <label className="grid gap-1.5 text-sm font-medium md:col-span-2">
                  作答要求或复习范围
                  <textarea
                    name="instructions"
                    defaultValue={item.instructions}
                    maxLength={4000}
                    className="min-h-32 rounded-xl border p-3 font-normal"
                    placeholder="填写学生需要遵循的要求、复习范围或练习重点"
                  />
                </label>
              </div>
            </details>
            <Button loading={busy} className="md:col-span-2">
              保存并继续
            </Button>
          </form>
        </Card>
      )}

      {step === 1 && (
        <Card id="assignment-upload" className="scroll-mt-4 space-y-4 p-6">
          <h2 className="font-bold">上传题目与答案</h2>
          {classResources.length > 0 && (
            <section className="space-y-3 rounded-xl border border-slate-200 p-4">
              <div>
                <h3 className="font-semibold">从班级资料选择</h3>
                <p className="text-sm text-slate-600">
                  选择后会复制到此作业，班级原资料保持不变。
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {classResources.map((resource) => (
                  <label
                    className="flex gap-2 rounded-lg border p-3"
                    key={resource.id}
                  >
                    <input
                      type="checkbox"
                      checked={selectedResourceIds.includes(resource.id)}
                      onChange={(event) =>
                        setSelectedResourceIds((current) =>
                          event.target.checked
                            ? [...current, resource.id]
                            : current.filter((id) => id !== resource.id),
                        )
                      }
                    />
                    <span>
                      <strong>{resource.title}</strong>
                      <span className="block text-sm text-slate-600">
                        {resource.file_name} · {resource.page_count} 页
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <Button
                variant="outline"
                disabled={!selectedResourceIds.length || busy}
                onClick={() => void addSelectedResources()}
              >
                加入所选资料
              </Button>
            </section>
          )}
          {uploadedFiles.length > 0 && (
            <section
              aria-label="已上传文件"
              className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"
            >
              <h3 className="font-semibold text-emerald-900">已上传到此作业</h3>
              <ul className="mt-2 space-y-2 text-sm text-emerald-950">
                {uploadedFiles.map((file, index) => (
                  <li
                    key={file.id}
                    className="flex flex-wrap items-center justify-between gap-2"
                  >
                    <span>
                      {index + 1}. {file.name}
                    </span>
                    <Button
                      variant="ghost"
                      className="text-red-600 hover:bg-red-50 hover:text-red-700"
                      loading={deletingFileId === file.id}
                      disabled={busy && deletingFileId !== file.id}
                      onClick={() => void deleteUploadedFile(file)}
                      aria-label={`删除 ${file.name}`}
                    >
                      删除
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div
            className={`grid min-h-44 cursor-pointer place-items-center rounded-2xl border-2 border-dashed p-6 text-center transition ${
              dragging
                ? "border-[var(--brand-600)] bg-[var(--brand-50)]"
                : "border-slate-300 bg-slate-50 hover:border-[var(--brand-600)]"
            }`}
            role="button"
            tabIndex={0}
            aria-label="上传试卷文件"
            onClick={() => !busy && fileInputRef.current?.click()}
            onKeyDown={(event) => {
              if ((event.key === "Enter" || event.key === " ") && !busy) {
                event.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragEnter={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <div>
              <p className="font-semibold">
                将 PDF、PNG 或 JPG 文件拖到这里，也可以点击选择多个文件
              </p>
              <p className="mt-2 text-sm text-slate-500">
                可一次上传多个文件，单个文件不超过 25 MB
              </p>
            </div>
          </div>
          <input
            ref={fileInputRef}
            className="sr-only"
            aria-label="选择试卷文件"
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png"
            disabled={busy}
            onChange={(event) => {
              chooseUpload(Array.from(event.target.files ?? []));
              event.currentTarget.value = "";
            }}
          />
          {uploadFile && uploadState === "error" && (
            <div
              className="rounded-xl border border-red-300 bg-red-50 p-4"
              aria-live="polite"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <strong>{uploadFile.name}</strong>
                <strong className="text-sm text-red-700">上传失败</strong>
              </div>
              {uploadError && (
                <p className="mt-2 text-sm text-red-700">{uploadError}</p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                >
                  重新上传
                </Button>
                <Button variant="ghost" onClick={() => chooseUpload([])}>
                  删除所选文件
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      <AssignmentGenerationPanel
        assignmentId={item.id}
        assignment={item}
        onAssignmentChanged={load}
        onReviewInputsChanged={refreshReviewInputs}
        onFieldSuggestionsChanged={setFieldSuggestions}
      />

      {step === 2 && (
        <Card id="assignment-pages" className="scroll-mt-4 space-y-4 p-6">
          <details>
            <summary className="cursor-pointer rounded-lg px-3 py-2 font-bold hover:bg-[var(--neutral-50)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand-600)]">
              整理页面（{item.paper_version?.pages.length ?? 0} 页）
            </summary>
            <div className="mt-4 space-y-4">
              <div
                className="flex gap-2 overflow-x-auto rounded-xl border bg-slate-50 p-2"
                aria-label="试卷页面缩略图"
                data-testid="assignment-page-thumbnails"
              >
                {item.paper_version?.pages.map((page) => {
                  const url = pagePreviewUrls[page.id];
                  return (
                    <button
                      type="button"
                      key={page.id}
                      onClick={() => setSelectedPageId(page.id)}
                      className={`w-36 shrink-0 rounded-xl border-2 bg-white p-2 text-left ${
                        selectedPageId === page.id
                          ? "border-[var(--brand-600)] shadow-sm"
                          : "border-transparent"
                      }`}
                      aria-current={
                        selectedPageId === page.id ? "page" : undefined
                      }
                    >
                      <div className="grid h-24 place-items-center overflow-hidden rounded bg-slate-100">
                        {url && !previewErrors[page.id] ? (
                          <img
                            src={url}
                            alt={`第 ${page.page_number} 页缩略图`}
                            title={`第 ${page.page_number} 页缩略图`}
                            className="h-full w-full object-contain"
                            loading="lazy"
                            onError={() =>
                              setPreviewErrors((current) => ({
                                ...current,
                                [page.id]: true,
                              }))
                            }
                          />
                        ) : (
                          <span className="text-xs text-slate-500">
                            页面预览
                          </span>
                        )}
                      </div>
                      <strong className="mt-2 block text-sm">
                        第 {page.page_number} 页
                      </strong>
                      <span className="text-xs text-slate-500">
                        {page.rotation}° ·{" "}
                        {page.status === "ready" ? "处理完成" : page.status}
                      </span>
                    </button>
                  );
                })}
              </div>
              {selectedPage ? (
                <div className="min-w-0 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">
                        第 {selectedPage.page_number} 页
                      </h3>
                      <p className="text-sm text-slate-500">
                        旋转 {selectedPage.rotation}° · 状态：
                        {selectedPage.status === "ready"
                          ? "处理完成"
                          : selectedPage.status}
                      </p>
                    </div>
                    {selectedPage.status !== "ready" && (
                      <span className="rounded-full bg-amber-100 px-3 py-1 text-xs text-amber-800">
                        {selectedPage.status === "excluded"
                          ? "已排除"
                          : selectedPage.status === "pending_conversion"
                            ? "等待转换"
                            : "需要检查"}
                      </span>
                    )}
                  </div>
                  <div className="grid min-h-[400px] place-items-center overflow-hidden rounded-xl border bg-slate-100 p-4">
                    {selectedPreviewUrl && !previewErrors[selectedPage.id] ? (
                      <img
                        src={selectedPreviewUrl}
                        alt={`第 ${selectedPage.page_number} 页大图预览`}
                        title={`第 ${selectedPage.page_number} 页大图预览`}
                        className="max-h-[560px] max-w-full object-contain"
                        onError={() =>
                          setPreviewErrors((current) => ({
                            ...current,
                            [selectedPage.id]: true,
                          }))
                        }
                      />
                    ) : previewErrors[selectedPage.id] ? (
                      <div className="text-center">
                        <p className="font-semibold">页面预览加载失败</p>
                        <p className="mt-1 text-sm text-slate-500">
                          文件可能暂时不可用，请重试。
                        </p>
                        <Button
                          className="mt-3"
                          variant="outline"
                          onClick={() => void loadPagePreview(selectedPage.id)}
                        >
                          重试预览
                        </Button>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-500">
                        正在加载页面预览…
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      loading={busy}
                      variant="outline"
                      onClick={async () => {
                        setBusy(true);
                        try {
                          await assignmentsApi.page(item.id, selectedPage.id, {
                            rotation: (selectedPage.rotation + 90) % 360,
                          });
                          await load();
                          toast("页面已旋转");
                        } catch (error) {
                          toast(
                            error instanceof ApiError
                              ? error.message
                              : "页面旋转失败",
                            "error",
                          );
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      旋转 90°
                    </Button>
                    <Button
                      disabled={busy}
                      variant="danger"
                      onClick={async () => {
                        if (!confirm("确认排除此页？")) return;
                        setBusy(true);
                        try {
                          await assignmentsApi.page(item.id, selectedPage.id, {
                            status: "excluded",
                          });
                          await load();
                          toast("页面已排除");
                        } catch (error) {
                          toast(
                            error instanceof ApiError
                              ? error.message
                              : "页面排除失败",
                            "error",
                          );
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      排除此页
                    </Button>
                  </div>
                  <QuestionPageCutter
                    assignmentId={item.id}
                    page={selectedPage}
                    questions={item.paper_version?.questions ?? []}
                    selectedQuestionId={selectedQuestion}
                    onSaved={async (savedQuestion) => {
                      await load(savedQuestion.id);
                      setSelectedQuestion(savedQuestion.id);
                      toast("题目区域已保存");
                    }}
                  />
                </div>
              ) : (
                <p className="grid place-items-center text-sm text-slate-500">
                  暂无可预览页面
                </p>
              )}
            </div>
            <Button
              className="mt-4"
              onClick={() =>
                document
                  .getElementById("assignment-questions")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              继续核对题目
            </Button>
          </details>
        </Card>
      )}

      {step === 2 && (
        <div
          id="assignment-questions"
          className="scroll-mt-4 grid gap-5 lg:grid-cols-[320px_1fr]"
        >
          <Card className="p-5">
            <h2 className="font-bold">题目列表</h2>
            <div className="mt-3 grid gap-2">
              {item.paper_version?.questions.map((q) => (
                <button
                  disabled={questionSubmitting}
                  onClick={() => {
                    if (
                      questionDirty &&
                      !confirm("当前题目有未保存修改，确认放弃并切换吗？")
                    )
                      return;
                    setQuestionDirty(false);
                    setSelectedQuestion(q.id);
                  }}
                  className={`rounded-xl border p-3 text-left ${selectedQuestion === q.id ? "border-[var(--brand-600)] bg-[var(--brand-50)]" : ""}`}
                  key={q.id}
                >
                  第 {q.question_number} 题 · {formatQuestionScore(q.max_score)}
                  <br />
                  <small>{q.regions.length} 个区域</small>
                </button>
              ))}
            </div>
          </Card>
          <Card className="space-y-4 p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-bold">
                {selected
                  ? `编辑第 ${selected.question_number} 题`
                  : "添加题目"}
              </h2>
              {selected && (
                <Button
                  variant="outline"
                  disabled={questionSubmitting}
                  onClick={() => {
                    if (
                      questionDirty &&
                      !confirm("当前题目有未保存修改，确认放弃并新增吗？")
                    )
                      return;
                    setQuestionDirty(false);
                    setSelectedQuestion("");
                    setQuestion({
                      number: "",
                      type: "calculation",
                      score: "",
                      text: "",
                      difficulty: "medium",
                      knowledge: "",
                    });
                  }}
                >
                  新增题目
                </Button>
              )}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {questionConflict && (
                <div className="sm:col-span-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
                  <p className="font-medium">当前题目已被后台更新或移除</p>
                  <p className="mt-1">
                    本地未保存内容已保留，但不能写入其他题目。请放弃本地修改后重新加载。
                  </p>
                  <Button
                    className="mt-2"
                    variant="outline"
                    onClick={() => {
                      questionDirtyRef.current = false;
                      setQuestionDirty(false);
                      setQuestionConflict(false);
                      setSelectedQuestion(
                        item.paper_version?.questions[0]?.id ?? "",
                      );
                    }}
                  >
                    放弃本地修改并重新加载
                  </Button>
                </div>
              )}
              <Input
                label="题号"
                value={question.number}
                onChange={(e) => {
                  setQuestion({ ...question, number: e.target.value });
                  setQuestionDirty(true);
                }}
              />
              <Select
                label="题型"
                value={question.type}
                onChange={(e) => {
                  setQuestion({ ...question, type: e.target.value });
                  setQuestionDirty(true);
                }}
              >
                <option value="calculation">计算题</option>
                <option value="short_answer">简答题</option>
                <option value="single_choice">单选题</option>
                <option value="proof">证明题</option>
                <option value="other">其他</option>
              </Select>
              <Input
                label="分值"
                type="number"
                value={question.score}
                onChange={(e) => {
                  setQuestion({ ...question, score: e.target.value });
                  setQuestionDirty(true);
                }}
              />
              <Input
                label="知识点（逗号分隔）"
                value={question.knowledge}
                onChange={(e) => {
                  setQuestion({ ...question, knowledge: e.target.value });
                  setQuestionDirty(true);
                }}
              />
              <Input
                className="sm:col-span-2"
                label="题目内容"
                value={question.text}
                onChange={(e) => {
                  setQuestion({ ...question, text: e.target.value });
                  setQuestionDirty(true);
                }}
              />
            </div>
            <Button
              loading={questionSubmitting}
              disabled={questionSubmitting || questionConflict}
              onClick={async () => {
                if (questionSubmitting || questionConflict) return;
                setQuestionSubmitting(true);
                try {
                  const payload = {
                    question_number: question.number,
                    question_type: question.type,
                    max_score: Number(question.score),
                    content_text: question.text,
                    difficulty: question.difficulty,
                    knowledge_points: question.knowledge
                      .split(",")
                      .map((point) => point.trim())
                      .filter(Boolean),
                  };
                  const q = selected
                    ? await assignmentsApi.updateQuestion(
                        item.id,
                        selected.id,
                        payload,
                      )
                    : await assignmentsApi.question(item.id, payload);
                  setQuestionDirty(false);
                  await load(q.id);
                  toast(selected ? "题目已保存" : "题目已创建");
                } catch (e) {
                  toast(
                    e instanceof ApiError ? e.message : "保存失败",
                    "error",
                  );
                } finally {
                  setQuestionSubmitting(false);
                }
              }}
            >
              {selected ? "保存题目" : "添加题目"}
            </Button>
            {selected && (
              <div className="border-t pt-4">
                {selected.max_score == null && (
                  <div
                    className="my-3 rounded border border-amber-300 bg-amber-50 p-3"
                    data-testid="missing-question-score"
                  >
                    <p className="text-sm">
                      当前题目分值未知，Rubric 保存和发布会被阻止。
                    </p>
                    <Button
                      className="mt-2"
                      variant="outline"
                      onClick={async () => {
                        const value = window.prompt(
                          `请输入第 ${selected.question_number} 题的正数分值`,
                          "",
                        );
                        if (value === null) return;
                        const score = Number(value);
                        if (!Number.isFinite(score) || score <= 0) {
                          toast("分值必须为正数", "error");
                          return;
                        }
                        try {
                          await assignmentsApi.updateQuestion(
                            item.id,
                            selected.id,
                            {
                              question_number: selected.question_number,
                              question_type: selected.question_type,
                              max_score: score,
                              content_text: selected.content_text,
                              difficulty: selected.difficulty,
                              knowledge_points: selected.knowledge_points.map(
                                (point) => point.name,
                              ),
                            },
                          );
                          await load();
                          toast("题目分值已补齐，可以继续设置 Rubric");
                        } catch (e) {
                          toast(
                            e instanceof ApiError ? e.message : "保存分值失败",
                            "error",
                          );
                        }
                      }}
                    >
                      补齐所选题目分值
                    </Button>
                  </div>
                )}
              </div>
            )}
            <Button
              onClick={() =>
                document
                  .getElementById("assignment-rubrics")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              继续核对评分标准
            </Button>
          </Card>
        </div>
      )}

      {step === 2 && (
        <Card id="assignment-rubrics" className="scroll-mt-4 space-y-4 p-6">
          <h2 className="font-bold">评分标准</h2>
          <AnswerRubricGenerationReview
            assignmentId={item.id}
            questions={item.paper_version?.questions ?? []}
          />
          <Button variant="secondary" onClick={() => setStep(3)}>
            进入确认发布
          </Button>
        </Card>
      )}

      {item.delivery_mode === "joint_exam" && (step === 1 || step === 3) && (
        <JointExamTeamPanel assignmentId={item.id} onChanged={load} />
      )}

      {step === 3 && (
        <AssignmentCentralReview
          item={item}
          reviewInputsRevision={reviewInputsRevision}
          onNavigate={(targetStep) =>
            setStep(wizardStepForCompleteness(targetStep))
          }
          onPublished={() => router.push(`/assignments/${item.id}`)}
        />
      )}
    </div>
  );
}

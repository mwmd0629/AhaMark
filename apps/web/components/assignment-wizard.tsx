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
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";
import { formatQuestionScore } from "@/lib/question-score";

const steps = [
  "基本信息",
  "上传试卷",
  "整理页面",
  "编辑题目",
  "评分标准",
  "集中审查与发布",
];

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

function isPdfPreview(url: string) {
  return /\.pdf(?:$|[?#])/i.test(url);
}

function pagePreviewUrl(url: string, pageNumber: number) {
  return isPdfPreview(url)
    ? `${url}#page=${pageNumber}&toolbar=0&navpanes=0&scrollbar=0`
    : url;
}

export function AssignmentWizard({ assignmentId }: { assignmentId: string }) {
  const router = useRouter();
  const toast = useToast();
  const [item, setItem] = useState<AssignmentRecord>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [step, setStep] = useState(1);
  const [reviewInputsRevision, setReviewInputsRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dueMode, setDueMode] = useState<"none" | "scheduled">("none");
  const [dueValue, setDueValue] = useState("");
  const [selectedClassIds, setSelectedClassIds] = useState<string[]>([]);
  const [deliveryMode, setDeliveryMode] = useState<
    "class_assignment" | "joint_exam"
  >("class_assignment");
  const [uploadFile, setUploadFile] = useState<File>();
  const [uploadState, setUploadState] = useState<
    "idle" | "ready" | "uploading" | "processing" | "success" | "error"
  >("idle");
  const [uploadError, setUploadError] = useState("");
  const [uploadResult, setUploadResult] = useState<{
    name: string;
    pages: number;
  }>();
  const [dragging, setDragging] = useState(false);
  const [deletingFileId, setDeletingFileId] = useState("");
  const [selectedPageId, setSelectedPageId] = useState("");
  const [pagePreviewUrls, setPagePreviewUrls] = useState<
    Record<string, string>
  >({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, boolean>>(
    {},
  );
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
  const [answer, setAnswer] = useState("");
  const [region, setRegion] = useState({
    page: "",
    x: "0.1",
    y: "0.1",
    width: "0.8",
    height: "0.2",
  });
  const load = useCallback(async () => {
    try {
      const [assignment, active] = await Promise.all([
        assignmentsApi.get(assignmentId),
        classesApi.list("status=active&page_size=100"),
      ]);
      setItem(assignment);
      setClasses(active.items);
      if (!initializedRef.current) {
        setStep(assignment.completeness.next_step || 1);
        initializedRef.current = true;
      }
      setSelectedQuestion(assignment.paper_version?.questions[0]?.id ?? "");
      setRegion((old) => ({
        ...old,
        page: assignment.paper_version?.pages[0]?.id ?? "",
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "无法加载草稿");
    }
  }, [assignmentId]);
  const refreshReviewInputs = useCallback(async () => {
    setReviewInputsRevision((current) => current + 1);
    await load();
  }, [load]);
  useEffect(() => void load(), [load]);
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
    const warn = (e: BeforeUnloadEvent) => {
      if (busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);
  const selected = useMemo(
    () => item?.paper_version?.questions.find((x) => x.id === selectedQuestion),
    [item, selectedQuestion],
  );
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
  useEffect(() => {
    const saved = item?.rubric_version?.question_rubrics.find(
      (rubric) => rubric.question_id === selectedQuestion,
    );
    setAnswer(saved?.standard_answer ?? "");
  }, [item, selectedQuestion]);
  const loadPreview = useCallback(
    async (fileId: string) => {
      try {
        const result = await assignmentsApi.preview(assignmentId, fileId);
        setPagePreviewUrls((current) => ({
          ...current,
          [fileId]: result.url,
        }));
        setPreviewErrors((current) => ({ ...current, [fileId]: false }));
      } catch {
        setPreviewErrors((current) => ({ ...current, [fileId]: true }));
      }
    },
    [assignmentId],
  );
  useEffect(() => {
    if (step !== 3) return;
    const fileIds = new Set(
      (item?.paper_version?.pages ?? []).map((page) => page.stored_file_id),
    );
    fileIds.forEach((fileId) => {
      if (!pagePreviewUrls[fileId] && !previewErrors[fileId]) {
        void loadPreview(fileId);
      }
    });
  }, [
    item?.paper_version?.pages,
    loadPreview,
    pagePreviewUrls,
    previewErrors,
    step,
  ]);
  if (error) return <ErrorState description={error} retry={load} />;
  if (!item) return <Card className="p-8">正在恢复后端草稿…</Card>;

  const saveBasics = async (form: FormData) => {
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
          subject: String(form.get("subject")),
          grade: String(form.get("grade")),
          description: String(form.get("description")),
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
      setStep(2);
      toast("基本信息已保存");
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "保存失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const chooseUpload = (file?: File) => {
    setUploadResult(undefined);
    setUploadError("");
    if (!file) {
      setUploadFile(undefined);
      setUploadState("idle");
      return;
    }
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!["pdf", "png", "jpg", "jpeg"].includes(extension ?? "")) {
      setUploadFile(file);
      setUploadState("error");
      setUploadError("文件格式不支持，请选择 PDF、PNG 或 JPG 文件。");
      return;
    }
    if (file.size === 0) {
      setUploadFile(file);
      setUploadState("error");
      setUploadError("文件内容为空，请重新选择完整文件。");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setUploadFile(file);
      setUploadState("error");
      setUploadError("文件超过 25 MB，请压缩后重新上传。");
      return;
    }
    setUploadFile(file);
    setUploadState("ready");
  };

  const uploadPaper = async () => {
    if (!uploadFile || busy || uploadState === "error") return;
    setBusy(true);
    setUploadError("");
    setUploadState("uploading");
    try {
      const result = await assignmentsApi.upload(item.id, uploadFile);
      setUploadState("processing");
      await load();
      setUploadResult({ name: result.name, pages: result.pages_created });
      setUploadState("success");
      toast("试卷上传成功，页面已经可以整理");
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
  };

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
      setPagePreviewUrls((current) => {
        const next = { ...current };
        delete next[file.id];
        return next;
      });
      setPreviewErrors((current) => {
        const next = { ...current };
        delete next[file.id];
        return next;
      });
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
    chooseUpload(event.dataTransfer.files[0]);
  };

  const selectedPage = item.paper_version?.pages.find(
    (page) => page.id === selectedPageId,
  );
  const selectedPreviewUrl = selectedPage
    ? pagePreviewUrls[selectedPage.stored_file_id]
    : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        title={item.title}
        description="六步创建向导；所有已保存内容以后端草稿为准。"
        actions={
          <Link href="/assignments">
            <Button variant="outline">返回列表</Button>
          </Link>
        }
      />
      <AssignmentGenerationPanel
        assignmentId={item.id}
        assignment={item}
        onAssignmentChanged={load}
        onReviewInputsChanged={refreshReviewInputs}
      />
      <ol
        className="grid grid-cols-2 gap-2 md:grid-cols-6"
        aria-label="创建步骤"
      >
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

      {step === 1 && (
        <Card className="p-6">
          <form action={saveBasics} className="grid gap-4 md:grid-cols-2">
            <Input
              name="title"
              label="作业名称"
              required
              defaultValue={item.title}
            />
            <Input name="subject" label="学科" defaultValue={item.subject} />
            <Input
              name="grade"
              label="年级或教学层级"
              defaultValue={item.grade}
              placeholder="如：大二、研究生、2026 级"
            />
            <Input
              name="total_score"
              label="总分"
              type="number"
              min="0.01"
              step="0.01"
              required
              defaultValue={item.total_score}
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
            <p className="self-end pb-2 text-xs text-slate-500">
              {deliveryMode === "joint_exam"
                ? "多班共用试卷与评分标准，统一批改进度，按班发布成绩。"
                : "适合日常作业，可按班建立批改批次。"}
            </p>
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
                <div className="grid gap-2 sm:grid-cols-2">
                  {classes.map((entry) => (
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
                              ? [...current, entry.id]
                              : current.filter((id) => id !== entry.id),
                          )
                        }
                      />
                      <span>
                        <strong className="block">{entry.name}</strong>
                        <span className="text-xs text-slate-500">
                          {[entry.subject, entry.academic_year, entry.semester]
                            .filter(Boolean)
                            .join(" · ") || "未填写课程信息"}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <Link
                  className="text-sm text-[var(--brand-700)]"
                  href="/classes"
                >
                  没有可用班级，前往班级管理
                </Link>
              )}
              <p className="text-xs text-slate-500">
                {deliveryMode === "joint_exam"
                  ? "可先选择自己的班级，再邀请其他老师授权其班级；发布前至少两个班级。"
                  : "发布前可以调整；发布后班级范围将锁定。"}
              </p>
            </fieldset>
            <label className="grid gap-1.5 text-sm font-medium md:col-span-2">
              作业说明
              <textarea
                name="description"
                defaultValue={item.description}
                className="min-h-24 rounded-xl border p-3 font-normal"
              />
            </label>
            <Button loading={busy} className="md:col-span-2">
              保存并继续
            </Button>
          </form>
        </Card>
      )}

      {step === 2 && (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">上传试卷</h2>
          {uploadedFiles.length > 0 && (
            <section
              aria-label="已上传文件"
              className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold text-emerald-900">
                  已上传到此作业（{uploadedFiles.length} 个文件）
                </h3>
                <span className="text-sm text-emerald-800">
                  继续添加不会删除已有文件
                </span>
              </div>
              <ul className="mt-2 space-y-2 text-sm text-emerald-950">
                {uploadedFiles.map((file, index) => (
                  <li
                    key={file.id}
                    className="flex flex-wrap items-center justify-between gap-2"
                  >
                    <span>
                      {index + 1}. {file.name} · {file.pageCount} 页 · 已保留
                    </span>
                    <Button
                      variant="ghost"
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
                将 PDF、PNG 或 JPG 文件拖到这里，也可以点击选择文件
              </p>
              <p className="mt-2 text-sm text-slate-500">
                支持 PDF、PNG、JPG/JPEG，单个文件不超过 25 MB
              </p>
            </div>
          </div>
          <input
            ref={fileInputRef}
            className="sr-only"
            aria-label="选择试卷文件"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            disabled={busy}
            onChange={(event) => {
              chooseUpload(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
          {uploadFile && (
            <div
              className={`rounded-xl border p-4 ${
                uploadState === "error"
                  ? "border-red-300 bg-red-50"
                  : uploadState === "success"
                    ? "border-emerald-300 bg-emerald-50"
                    : "border-slate-200"
              }`}
              aria-live="polite"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <strong>{uploadResult?.name ?? uploadFile.name}</strong>
                  <p className="text-sm text-slate-600">
                    {(uploadFile.size / 1024 / 1024).toFixed(2)} MB
                    {uploadResult ? ` · 共 ${uploadResult.pages} 页` : ""}
                  </p>
                </div>
                <strong className="text-sm">
                  {uploadState === "ready" && "等待上传"}
                  {uploadState === "uploading" && "正在上传"}
                  {uploadState === "processing" && "正在解析"}
                  {uploadState === "success" && "上传成功"}
                  {uploadState === "error" && "上传失败"}
                </strong>
              </div>
              {uploadError && (
                <p className="mt-2 text-sm text-red-700">{uploadError}</p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                {uploadState === "ready" && (
                  <Button loading={busy} onClick={() => void uploadPaper()}>
                    开始上传
                  </Button>
                )}
                {uploadState === "error" && (
                  <Button
                    variant="outline"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    重新上传
                  </Button>
                )}
                {!["uploading", "processing", "success"].includes(
                  uploadState,
                ) && (
                  <Button variant="ghost" onClick={() => chooseUpload()}>
                    删除所选文件
                  </Button>
                )}
              </div>
            </div>
          )}
          <p className="text-sm">
            当前共 {item.paper_version?.pages.length ?? 0}{" "}
            页。选择新的本地文件只会清空当前待选项，已经上传成功的文件仍保留在此作业中。
          </p>
          <Button
            onClick={() => setStep(3)}
            disabled={!item.paper_version?.pages.length}
          >
            继续整理页面
          </Button>
        </Card>
      )}

      {step === 3 && (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">整理页面</h2>
          <div className="grid min-h-[520px] gap-4 lg:grid-cols-[220px_1fr]">
            <div
              className="max-h-[620px] space-y-2 overflow-y-auto rounded-xl border bg-slate-50 p-2"
              aria-label="试卷页面缩略图"
            >
              {item.paper_version?.pages.map((page) => {
                const url = pagePreviewUrls[page.stored_file_id];
                return (
                  <button
                    type="button"
                    key={page.id}
                    onClick={() => setSelectedPageId(page.id)}
                    className={`w-full rounded-xl border-2 bg-white p-2 text-left ${
                      selectedPageId === page.id
                        ? "border-[var(--brand-600)] shadow-sm"
                        : "border-transparent"
                    }`}
                    aria-current={
                      selectedPageId === page.id ? "page" : undefined
                    }
                  >
                    <div className="grid h-28 place-items-center overflow-hidden rounded bg-slate-100">
                      {url && !previewErrors[page.stored_file_id] ? (
                        isPdfPreview(url) ? (
                          <iframe
                            src={pagePreviewUrl(
                              url,
                              page.source_page_number ?? page.page_number,
                            )}
                            title={`第 ${page.page_number} 页缩略图`}
                            className="pointer-events-none h-full w-full border-0"
                            loading="lazy"
                            onError={() =>
                              setPreviewErrors((current) => ({
                                ...current,
                                [page.stored_file_id]: true,
                              }))
                            }
                          />
                        ) : (
                          <img
                            src={url}
                            alt={`第 ${page.page_number} 页缩略图`}
                            className="h-full w-full object-contain"
                            style={{
                              transform: `rotate(${page.rotation}deg)`,
                            }}
                            onError={() =>
                              setPreviewErrors((current) => ({
                                ...current,
                                [page.stored_file_id]: true,
                              }))
                            }
                          />
                        )
                      ) : (
                        <span className="text-xs text-slate-500">页面预览</span>
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
              <div className="space-y-3">
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
                  {selectedPreviewUrl &&
                  !previewErrors[selectedPage.stored_file_id] ? (
                    isPdfPreview(selectedPreviewUrl) ? (
                      <iframe
                        src={pagePreviewUrl(
                          selectedPreviewUrl,
                          selectedPage.source_page_number ??
                            selectedPage.page_number,
                        )}
                        title={`第 ${selectedPage.page_number} 页大图预览`}
                        className="h-[560px] w-full border-0 bg-white"
                        onError={() =>
                          setPreviewErrors((current) => ({
                            ...current,
                            [selectedPage.stored_file_id]: true,
                          }))
                        }
                      />
                    ) : (
                      <img
                        src={selectedPreviewUrl}
                        alt={`第 ${selectedPage.page_number} 页大图预览`}
                        className="max-h-[560px] max-w-full object-contain"
                        style={{
                          transform: `rotate(${selectedPage.rotation}deg)`,
                        }}
                        onError={() =>
                          setPreviewErrors((current) => ({
                            ...current,
                            [selectedPage.stored_file_id]: true,
                          }))
                        }
                      />
                    )
                  ) : previewErrors[selectedPage.stored_file_id] ? (
                    <div className="text-center">
                      <p className="font-semibold">页面预览加载失败</p>
                      <p className="mt-1 text-sm text-slate-500">
                        文件可能暂时不可用，请重试。
                      </p>
                      <Button
                        className="mt-3"
                        variant="outline"
                        onClick={() =>
                          void loadPreview(selectedPage.stored_file_id)
                        }
                      >
                        重试预览
                      </Button>
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">正在加载页面预览…</p>
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
              </div>
            ) : (
              <p className="grid place-items-center text-sm text-slate-500">
                暂无可预览页面
              </p>
            )}
          </div>
          <Button onClick={() => setStep(4)}>继续编辑题目</Button>
        </Card>
      )}

      {step === 4 && (
        <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
          <Card className="p-5">
            <h2 className="font-bold">题目列表</h2>
            <div className="mt-3 grid gap-2">
              {item.paper_version?.questions.map((q) => (
                <button
                  onClick={() => setSelectedQuestion(q.id)}
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
            <h2 className="font-bold">添加题目</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                label="题号"
                value={question.number}
                onChange={(e) =>
                  setQuestion({ ...question, number: e.target.value })
                }
              />
              <Select
                label="题型"
                value={question.type}
                onChange={(e) =>
                  setQuestion({ ...question, type: e.target.value })
                }
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
                onChange={(e) =>
                  setQuestion({ ...question, score: e.target.value })
                }
              />
              <Input
                label="知识点（逗号分隔）"
                value={question.knowledge}
                onChange={(e) =>
                  setQuestion({ ...question, knowledge: e.target.value })
                }
              />
              <Input
                className="sm:col-span-2"
                label="题目内容"
                value={question.text}
                onChange={(e) =>
                  setQuestion({ ...question, text: e.target.value })
                }
              />
            </div>
            <Button
              onClick={async () => {
                try {
                  const q = await assignmentsApi.question(item.id, {
                    question_number: question.number,
                    question_type: question.type,
                    max_score: Number(question.score),
                    content_text: question.text,
                    difficulty: question.difficulty,
                    knowledge_points: question.knowledge
                      .split(",")
                      .filter(Boolean),
                  });
                  await load();
                  setSelectedQuestion(q.id);
                  toast("题目已创建");
                } catch (e) {
                  toast(
                    e instanceof ApiError ? e.message : "创建失败",
                    "error",
                  );
                }
              }}
            >
              添加题目
            </Button>
            {selected && (
              <div className="border-t pt-4">
                <h3 className="font-bold">
                  为第 {selected.question_number} 题添加页面区域
                </h3>
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
                <p className="my-2 text-xs text-slate-500">
                  左上角原点、相对原始页面方向，所有值为
                  0–1；页面旋转仅影响显示。
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <Select
                    label="页面"
                    value={region.page}
                    onChange={(e) =>
                      setRegion({ ...region, page: e.target.value })
                    }
                  >
                    {item.paper_version?.pages.map((p) => (
                      <option key={p.id} value={p.id}>
                        第 {p.page_number} 页
                      </option>
                    ))}
                  </Select>
                  {(["x", "y", "width", "height"] as const).map((key) => (
                    <Input
                      key={key}
                      label={key}
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      value={region[key]}
                      onChange={(e) =>
                        setRegion({ ...region, [key]: e.target.value })
                      }
                    />
                  ))}
                </div>
                <Button
                  className="mt-3"
                  variant="outline"
                  onClick={async () => {
                    try {
                      await assignmentsApi.region(item.id, selected.id, {
                        paper_page_id: region.page,
                        x: Number(region.x),
                        y: Number(region.y),
                        width: Number(region.width),
                        height: Number(region.height),
                      });
                      await load();
                      toast("区域已保存");
                    } catch (e) {
                      toast(
                        e instanceof ApiError ? e.message : "区域无效",
                        "error",
                      );
                    }
                  }}
                >
                  保存区域
                </Button>
              </div>
            )}
            <Button onClick={() => setStep(5)}>继续设置评分标准</Button>
          </Card>
        </div>
      )}

      {step === 5 && (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">评分标准</h2>
          <AnswerRubricGenerationReview
            assignmentId={item.id}
            questions={item.paper_version?.questions ?? []}
            savedRubrics={item.rubric_version?.question_rubrics ?? []}
          />
          <h3 className="border-t pt-4 font-bold">
            手动 legacy Rubric 快捷录入
          </h3>
          <Select
            label="当前题目"
            value={selectedQuestion}
            onChange={(e) => setSelectedQuestion(e.target.value)}
          >
            {item.paper_version?.questions.map((q) => (
              <option key={q.id} value={q.id}>
                第 {q.question_number} 题（
                {formatQuestionScore(q.max_score)}）
              </option>
            ))}
          </Select>
          {selected && (
            <>
              {selected.max_score == null && (
                <div
                  className="rounded border border-amber-300 bg-amber-50 p-3"
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
              <label className="grid gap-1 text-sm font-medium">
                标准答案
                <textarea
                  className="min-h-28 rounded-xl border p-3"
                  data-question-id={selected.id}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                />
              </label>
              <p className="text-sm text-slate-600">
                第一版快捷录入会创建一个与题目满分相等的评分项；可接受解法、单位、格式、精度等字段由
                API 完整保留。
              </p>
              <Button
                key={`save-rubric-${selected.id}`}
                data-question-id={selected.id}
                onClick={async (event) => {
                  try {
                    const questionId = event.currentTarget.dataset.questionId;
                    const currentQuestion = item.paper_version?.questions.find(
                      (candidate) => candidate.id === questionId,
                    );
                    if (!questionId || !currentQuestion)
                      throw new Error("当前题目状态已变化，请重试");
                    const next = await assignmentsApi.rubric(
                      item.id,
                      questionId,
                      {
                        standard_answer: answer,
                        alternative_answers: [],
                        scoring_notes: "",
                        allow_step_score: true,
                        items: [
                          {
                            title: "答案与过程正确",
                            points: Number(currentQuestion.max_score),
                            item_type: "step",
                            required: true,
                          },
                        ],
                      },
                    );
                    setItem(next);
                    toast("评分标准已保存");
                  } catch (e) {
                    toast(
                      e instanceof ApiError ? e.message : "保存失败",
                      "error",
                    );
                  }
                }}
              >
                保存本题评分标准
              </Button>
            </>
          )}
          <Button variant="secondary" onClick={() => setStep(6)}>
            进入发布检查
          </Button>
        </Card>
      )}

      {item.delivery_mode === "joint_exam" && (step === 1 || step === 6) && (
        <JointExamTeamPanel assignmentId={item.id} onChanged={load} />
      )}

      {step === 6 && (
        <AssignmentCentralReview
          item={item}
          reviewInputsRevision={reviewInputsRevision}
          onNavigate={setStep}
          onPublished={() => router.push(`/assignments/${item.id}`)}
        />
      )}
    </div>
  );
}

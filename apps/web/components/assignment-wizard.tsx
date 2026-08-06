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
import {
  Button,
  Card,
  ErrorState,
  Input,
  PageHeader,
  Select,
  useToast,
} from "@/components/ui";
import { AnswerRubricGenerationReview } from "@/components/answer-rubric-generation-review";
import { QuestionPageCutter } from "@/components/question-page-cutter";
import {
  ApiError,
  assignmentsApi,
  classesApi,
  type AssignmentRecord,
  type ClassRecord,
} from "@/lib/api";
import { formatQuestionScore } from "@/lib/question-score";

const steps = ["基本信息", "上传与整理页面", "评分标准"];

function wizardStep(nextStep: number) {
  if (nextStep <= 1) return 1;
  if (nextStep <= 3) return 2;
  return 3;
}

const GRADE_OPTIONS = ["大一", "大二", "大三", "大四"] as const;

function isPdfPreview(url: string) {
  return /\.pdf(?:$|[?#])/i.test(url);
}

function pagePreviewUrl(url: string, pageNumber: number) {
  return isPdfPreview(url)
    ? `${url}#page=${pageNumber}&toolbar=0&navpanes=0&scrollbar=0`
    : url;
}

export function AssignmentWizard({ assignmentId }: { assignmentId: string }) {
  const toast = useToast();
  const [item, setItem] = useState<AssignmentRecord>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [step, setStep] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dueMode, setDueMode] = useState<"none" | "scheduled">("none");
  const [dueValue, setDueValue] = useState("");
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
  const [selectedPageId, setSelectedPageId] = useState("");
  const [pagePreviewUrls, setPagePreviewUrls] = useState<
    Record<string, string>
  >({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, boolean>>(
    {},
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const initializedRef = useRef(false);
  const [selectedQuestion, setSelectedQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const load = useCallback(async () => {
    try {
      const [assignment, active] = await Promise.all([
        assignmentsApi.get(assignmentId),
        classesApi.list("status=active&page_size=100"),
      ]);
      setItem(assignment);
      setClasses(active.items);
      if (!initializedRef.current) {
        setStep(wizardStep(assignment.completeness.next_step || 1));
        initializedRef.current = true;
      }
      setSelectedQuestion(assignment.paper_version?.questions[0]?.id ?? "");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "无法加载草稿");
    }
  }, [assignmentId]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    setDueMode(item?.due_at ? "scheduled" : "none");
    setDueValue(item?.due_at?.slice(0, 16) ?? "");
  }, [item?.id, item?.due_at]);
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
    if (step !== 2) return;
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
    setBusy(true);
    try {
      const next = await assignmentsApi.update(
        item.id,
        {
          title: String(form.get("title")),
          subject: String(form.get("subject")),
          grade: String(form.get("grade")),
          description: String(form.get("description")),
          total_score: Number(form.get("total_score")),
          due_at: dueMode === "none" ? null : dueValue,
        },
        item.updated_at,
      );
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

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    chooseUpload(event.dataTransfer.files[0]);
  };

  const selectedPage = item.paper_version?.pages.find(
    (page) => page.id === selectedPageId,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={item.title}
        description="三步创建向导；所有已保存内容以后端草稿为准。"
        actions={
          <Link href="/assignments">
            <Button variant="outline">返回列表</Button>
          </Link>
        }
      />
      <ol
        className="grid grid-cols-1 gap-2 sm:grid-cols-3"
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
            <Select name="grade" label="年级" defaultValue={item.grade}>
              {GRADE_OPTIONS.map((grade) => (
                <option key={grade} value={grade}>
                  {grade}
                </option>
              ))}
            </Select>
            <Input
              name="total_score"
              label="总分"
              type="number"
              min="0.01"
              step="0.01"
              required
              defaultValue={item.total_score}
            />
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
            <label className="grid gap-1.5 text-sm font-medium md:col-span-2">
              关联班级（创建草稿时已校验）
              <div className="flex flex-wrap gap-2">
                {item.classes.map((x) => (
                  <span
                    className="rounded-full bg-slate-100 px-3 py-1"
                    key={x.id}
                  >
                    {x.name}
                  </span>
                ))}
                {!item.classes.length && (
                  <Link className="text-[var(--brand-700)]" href="/classes">
                    没有班级，前往创建
                  </Link>
                )}
              </div>
              <small className="text-slate-500">
                当前共有 {classes.length} 个可用活动班级。AI
                不会推荐或自动选择班级。
              </small>
            </label>
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
              <ul className="mt-2 space-y-1 text-sm text-emerald-950">
                {uploadedFiles.map((file, index) => (
                  <li key={file.id}>
                    {index + 1}. {file.name} · {file.pageCount} 页 · 已保留
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
                {!["uploading", "processing"].includes(uploadState) && (
                  <Button variant="ghost" onClick={() => chooseUpload()}>
                    {uploadState === "success"
                      ? "继续添加文件"
                      : "删除所选文件"}
                  </Button>
                )}
              </div>
            </div>
          )}
          <p className="text-sm">
            当前共 {item.paper_version?.pages.length ?? 0}{" "}
            页。选择新的本地文件只会清空当前待选项，已经上传成功的文件仍保留在此作业中。
          </p>
        </Card>
      )}

      {step === 2 && (
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
                  onCreated={async (question) => {
                    await load();
                    setSelectedQuestion(question.id);
                  }}
                  onChanged={load}
                />
              </div>
            ) : (
              <p className="grid place-items-center text-sm text-slate-500">
                暂无可预览页面
              </p>
            )}
          </div>
          <Button onClick={() => setStep(3)}>继续设置评分标准</Button>
        </Card>
      )}

      {step === 3 && (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">评分标准</h2>
          <AnswerRubricGenerationReview
            assignmentId={item.id}
            questions={item.paper_version?.questions ?? []}
          />
          <h3 className="border-t pt-4 font-bold">手动评分标准录入</h3>
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
        </Card>
      )}
    </div>
  );
}

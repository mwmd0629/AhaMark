"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  "检查并发布",
];

export function AssignmentWizard({ assignmentId }: { assignmentId: string }) {
  const router = useRouter();
  const toast = useToast();
  const [item, setItem] = useState<AssignmentRecord>();
  const [classes, setClasses] = useState<ClassRecord[]>([]);
  const [step, setStep] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
      setStep(assignment.completeness.next_step || 1);
      setSelectedQuestion(assignment.paper_version?.questions[0]?.id ?? "");
      setRegion((old) => ({
        ...old,
        page: assignment.paper_version?.pages[0]?.id ?? "",
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "无法加载草稿");
    }
  }, [assignmentId]);
  useEffect(() => void load(), [load]);
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
  if (error) return <ErrorState description={error} retry={load} />;
  if (!item) return <Card className="p-8">正在恢复后端草稿…</Card>;

  const saveBasics = async (form: FormData) => {
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
          due_at: String(form.get("due_at")) || undefined,
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
            <Input name="grade" label="年级" defaultValue={item.grade} />
            <Input
              name="total_score"
              label="总分"
              type="number"
              min="0.01"
              step="0.01"
              required
              defaultValue={item.total_score}
            />
            <Input
              name="due_at"
              label="截止时间"
              type="datetime-local"
              defaultValue={item.due_at?.slice(0, 16)}
            />
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
                当前共有 {classes.length} 个可用活动班级。
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
          <p className="text-sm text-slate-600">
            支持 PDF、JPG/JPEG、PNG、DOCX；单文件 25 MB，最多 20
            个。不会自动启动 OCR。
          </p>
          <input
            aria-label="选择试卷文件"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.docx"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setBusy(true);
              try {
                await assignmentsApi.upload(item.id, file);
                await load();
                toast("文件已上传并建立页面记录");
              } catch (err) {
                toast(
                  err instanceof ApiError ? err.message : "上传失败",
                  "error",
                );
              } finally {
                setBusy(false);
              }
            }}
          />
          <p className="text-sm">
            当前页面：{item.paper_version?.pages.length ?? 0}。DOCX
            仅保存文件并显示“待转换”，PDF
            由浏览器通过受控地址预览；服务端页面图将在第五部分生成。
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
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {item.paper_version?.pages.map((page) => (
              <div className="rounded-xl border p-4" key={page.id}>
                <strong>第 {page.page_number} 页</strong>
                <p className="text-xs text-slate-500">
                  状态：{page.status} · 旋转 {page.rotation}°
                </p>
                <div className="mt-3 flex gap-2">
                  <Button
                    variant="outline"
                    onClick={async () => {
                      await assignmentsApi.page(item.id, page.id, {
                        rotation: (page.rotation + 90) % 360,
                      });
                      await load();
                    }}
                  >
                    旋转 90°
                  </Button>
                  <Button
                    variant="danger"
                    onClick={async () => {
                      if (confirm("确认排除此页？")) {
                        await assignmentsApi.page(item.id, page.id, {
                          status: "excluded",
                        });
                        await load();
                      }
                    }}
                  >
                    排除
                  </Button>
                </div>
              </div>
            ))}
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

      {step === 6 && (
        <Card className="space-y-4 p-6">
          <h2 className="font-bold">检查并发布</h2>
          <div className="grid gap-2 sm:grid-cols-3">
            <div>班级：{item.classes.length}</div>
            <div>页面：{item.paper_version?.pages.length ?? 0}</div>
            <div>题目：{item.paper_version?.questions.length ?? 0}</div>
            <div>总分：{item.total_score ?? "未设置"}</div>
            <div>试卷版本：v{item.paper_version?.version ?? "—"}</div>
            <div>评分版本：v{item.rubric_version?.version ?? "—"}</div>
          </div>
          {item.completeness.issues.length ? (
            <ul className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              {item.completeness.issues.map((issue, i) => (
                <li key={`${issue.code}-${i}`}>
                  <button
                    className="underline"
                    onClick={() => setStep(issue.step)}
                  >
                    步骤 {issue.step}：{issue.message}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-xl bg-emerald-50 p-4 text-emerald-800">
              后端检查通过，可以发布。
            </p>
          )}
          <div className="flex gap-3">
            <Button
              variant="outline"
              onClick={() => router.push("/assignments")}
            >
              保存草稿
            </Button>
            <Button
              disabled={!item.completeness.ready}
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await assignmentsApi.publish(item.id);
                  toast("作业已发布");
                  router.push(`/assignments/${item.id}`);
                } catch (e) {
                  toast(
                    e instanceof ApiError ? e.message : "发布失败",
                    "error",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              发布作业
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

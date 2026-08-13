"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type WrongQuestion,
  type WrongQuestionMessage,
} from "@/lib/student-api";
import { formatDateTime, formatScore } from "@/lib/student-format";

function verdictLabel(verdict?: WrongQuestionMessage["verdict"]) {
  if (verdict === "likely_ai_misjudgment") return "可能存在 AI 误判";
  if (verdict === "likely_student_error") return "更可能是知识或作答问题";
  if (verdict === "uncertain") return "暂时无法确定";
  return null;
}

export default function WrongQuestionDetailPage() {
  const params = useParams<{ wrongQuestionId: string }>();
  const toast = useToast();
  const [question, setQuestion] = useState<WrongQuestion | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WrongQuestionMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [reviewQuestion, setReviewQuestion] = useState("");
  const [additionalInformation, setAdditionalInformation] = useState("");
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);
  const [waitingForAI, setWaitingForAI] = useState(false);
  const [aiJobId, setAIJobId] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [addingInformation, setAddingInformation] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");

  const loadMessages = useCallback(async (id: string) => {
    const next = collectionItems(await studentApi.messages(id));
    setMessages(next);
    if (next.at(-1)?.role === "assistant") setWaitingForAI(false);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const all = collectionItems(await studentApi.wrongQuestions());
      const match = all.find((item) => item.id === params.wrongQuestionId);
      if (!match) {
        setError("没有找到这道错题，或它尚未向当前账号发布。");
        return;
      }
      setQuestion(match);
      setThreadId(match.thread_id ?? null);
      if (match.thread_id) await loadMessages(match.thread_id);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "错题详情加载失败。",
      );
    } finally {
      setLoading(false);
    }
  }, [loadMessages, params.wrongQuestionId]);

  useEffect(() => void load(), [load]);

  useEffect(() => {
    if (!waitingForAI || !threadId || !aiJobId) return;
    const timer = window.setInterval(() => {
      void studentApi
        .aiJob(aiJobId)
        .then(async (job) => {
          if (job.status === "completed") {
            await loadMessages(threadId);
            setWaitingForAI(false);
            setAIJobId(null);
          } else if (
            job.status === "failed" ||
            job.status === "cancelled" ||
            job.status === "discarded_late"
          ) {
            setWaitingForAI(false);
            setAIJobId(null);
            setActionError(
              `AI 回复生成失败${job.error_code ? `：${job.error_code}` : "，请稍后重试。"}`,
            );
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [aiJobId, loadMessages, threadId, waitingForAI]);

  const aiAvailable = question?.thread_status
    ? question.thread_status === "open"
    : !question?.review_status || question.review_status === "waiting_student";

  const ensureThread = async () => {
    if (threadId) return threadId;
    if (!question) throw new Error("wrong_question_missing");
    const thread = await studentApi.createWrongQuestionThread(
      question.answer_id,
    );
    setThreadId(thread.id);
    setQuestion((current) =>
      current ? { ...current, thread_status: thread.status } : current,
    );
    return thread.id;
  };

  const ask = async () => {
    if (!aiAvailable) {
      setActionError("当前错题对话已转入教师复核，暂时不能继续询问 AI。");
      return;
    }
    if (!prompt.trim()) {
      setActionError("请输入你想进一步询问的问题。");
      return;
    }
    setAsking(true);
    setActionError("");
    try {
      const id = await ensureThread();
      const response = await studentApi.askAI(id, prompt.trim());
      setPrompt("");
      setAIJobId(response.job.id);
      setWaitingForAI(true);
      await loadMessages(id);
    } catch (reason) {
      setActionError(
        reason instanceof ApiError
          ? reason.message
          : "AI 询问提交失败，请重试。 ",
      );
    } finally {
      setAsking(false);
    }
  };

  const submitReview = async () => {
    if (!reviewQuestion.trim()) {
      setActionError("请说明希望教师复核的疑问或理由。");
      return;
    }
    setReviewing(true);
    setActionError("");
    try {
      const id = await ensureThread();
      const request = await studentApi.requestTeacherReview(
        id,
        reviewQuestion.trim(),
      );
      setReviewQuestion("");
      setQuestion((current) =>
        current
          ? {
              ...current,
              review_status: request.status,
              review_request_id: request.id,
              thread_status: "teacher_review",
            }
          : current,
      );
      toast("已提交教师人工复核");
    } catch (reason) {
      setActionError(
        reason instanceof ApiError
          ? reason.message
          : "教师复核请求提交失败，请重试。 ",
      );
    } finally {
      setReviewing(false);
    }
  };

  const submitAdditionalInformation = async () => {
    const currentQuestion = question;
    if (!currentQuestion?.review_request_id || !additionalInformation.trim()) {
      setActionError("请填写需要补充给教师的信息。");
      return;
    }
    setAddingInformation(true);
    setActionError("");
    try {
      await studentApi.addTeacherReviewInformation(
        currentQuestion.review_request_id,
        additionalInformation.trim(),
      );
      setAdditionalInformation("");
      setQuestion((current) =>
        current
          ? {
              ...current,
              review_status: "pending",
              review_decision: null,
              thread_status: "teacher_review",
            }
          : current,
      );
      toast("补充信息已提交给教师");
    } catch (reason) {
      setActionError(
        reason instanceof ApiError
          ? reason.message
          : "补充信息提交失败，请重试。",
      );
    } finally {
      setAddingInformation(false);
    }
  };

  if (loading) {
    return (
      <div aria-label="正在加载错题详情" className="grid gap-4">
        <Skeleton className="h-48" />
        <Skeleton className="h-80" />
      </div>
    );
  }
  if (error)
    return <ErrorState description={error} retry={() => void load()} />;
  if (!question)
    return (
      <EmptyState title="错题不存在" description="请返回错题本选择其他题目。" />
    );

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          question.question_number
            ? `第 ${question.question_number} 题`
            : "错题详情"
        }
        description={`${question.assignment_title} · 以下内容来自已发布成绩版本。`}
        actions={
          <Link
            href="/student/wrong-questions"
            className="inline-flex min-h-10 items-center rounded-xl border border-[var(--border)] bg-white px-4 text-sm font-semibold"
          >
            返回错题本
          </Link>
        }
      />

      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-bold">题目与批改结果</h2>
          {question.score != null && question.max_score != null && (
            <span className="rounded-full bg-red-50 px-3 py-1 text-sm font-bold text-red-700">
              {formatScore(question.score, question.max_score)}
            </span>
          )}
        </div>
        <p className="mt-4 whitespace-pre-wrap text-sm leading-7">
          {question.question_text}
        </p>
        <dl className="mt-5 grid gap-4 rounded-xl bg-slate-50 p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">
              你的答案
            </dt>
            <dd className="mt-1 whitespace-pre-wrap">
              {question.student_answer || "未记录文本答案"}
            </dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-secondary)]">
              当前错因
            </dt>
            <dd className="mt-1 whitespace-pre-wrap">
              {question.error_reason || "教师暂未填写错因"}
            </dd>
          </div>
        </dl>
      </Card>

      <div
        role="note"
        className="rounded-xl border border-purple-200 bg-purple-50 p-4 text-sm leading-6 text-purple-800"
      >
        <strong>AI 生成内容仅供参考。</strong> AI
        可以帮助你检查推理和补齐知识漏洞，但不能修改成绩。只有教师人工复核后才能形成成绩修订。
      </div>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,.7fr)]">
        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold">继续询问 AI</h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                反复核对题意、解题步骤或当前错因。
              </p>
            </div>
            <span className="rounded-full bg-purple-50 px-2.5 py-1 text-xs font-semibold text-purple-700">
              AI 建议
            </span>
          </div>
          <div
            aria-live="polite"
            className="mt-5 grid max-h-[460px] gap-3 overflow-y-auto rounded-xl bg-slate-50 p-3"
          >
            {messages.length ? (
              messages.map((message) => {
                const ai = message.role === "assistant";
                const verdict = verdictLabel(message.verdict);
                return (
                  <div
                    key={message.id}
                    className={`max-w-[88%] rounded-xl p-3 text-sm leading-6 ${ai ? "justify-self-start border border-purple-100 bg-white" : "justify-self-end bg-[var(--brand-600)] text-white"}`}
                  >
                    <div className="mb-1 flex flex-wrap items-center gap-2 text-xs font-semibold">
                      <span>{ai ? "AI 学习助手" : "你"}</span>
                      {ai && (
                        <span className="rounded-full bg-purple-50 px-2 py-0.5 text-purple-700">
                          AI 生成
                        </span>
                      )}
                      {verdict && (
                        <span className="text-purple-700">{verdict}</span>
                      )}
                    </div>
                    <p className="whitespace-pre-wrap">{message.content}</p>
                    <span
                      className={`mt-1 block text-[10px] ${ai ? "text-[var(--text-secondary)]" : "text-white/75"}`}
                    >
                      {formatDateTime(message.created_at)}
                    </span>
                  </div>
                );
              })
            ) : (
              <p className="p-5 text-center text-sm text-[var(--text-secondary)]">
                还没有对话。你可以从自己的解题思路、具体步骤或评分依据开始问。
              </p>
            )}
            {waitingForAI && (
              <p
                role="status"
                className="rounded-xl border border-dashed border-purple-200 bg-white p-3 text-sm text-purple-700"
              >
                AI 正在分析，请稍候…
              </p>
            )}
          </div>
          <div className="mt-4 grid gap-3">
            <Textarea
              label="你的问题"
              value={prompt}
              maxLength={2000}
              disabled={!aiAvailable}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="例如：我在第二步使用这个公式为什么不成立？请只提示思路，不要直接给出最终答案。"
            />
            {!aiAvailable && (
              <p
                role="status"
                className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800"
              >
                当前对话已转入教师复核或已经结束，暂时不能继续询问 AI。
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                loading={asking}
                disabled={waitingForAI || !aiAvailable}
                onClick={() => void ask()}
              >
                发送给 AI
              </Button>
              {threadId && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void loadMessages(threadId)}
                >
                  刷新对话
                </Button>
              )}
            </div>
          </div>
        </Card>

        <Card className="h-fit p-5">
          <h2 className="font-bold">提交教师人工复核</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
            如果仍认为存在误判，可以把疑问和当前 AI
            对话提交给教师。教师会依据原题、作答证据和评分规则人工判断。
          </p>
          {question.review_status ? (
            <div className="mt-4 grid gap-3">
              <p
                role="status"
                className="rounded-xl bg-amber-50 p-3 text-sm font-semibold text-amber-800"
              >
                当前复核状态：{question.review_status}
              </p>
              {question.teacher_response && (
                <div className="rounded-xl border border-[var(--border)] p-3 text-sm leading-6">
                  <strong className="block">教师回复</strong>
                  <p className="mt-1 whitespace-pre-wrap">
                    {question.teacher_response}
                  </p>
                </div>
              )}
              {question.review_status === "waiting_student" &&
                question.review_request_id && (
                  <div className="grid gap-3">
                    <Textarea
                      label="补充信息"
                      required
                      maxLength={4000}
                      value={additionalInformation}
                      onChange={(event) =>
                        setAdditionalInformation(event.target.value)
                      }
                      placeholder="根据教师的问题补充你的作答依据或疑问。"
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      loading={addingInformation}
                      onClick={() => void submitAdditionalInformation()}
                    >
                      提交补充信息
                    </Button>
                  </div>
                )}
            </div>
          ) : (
            <div className="mt-4 grid gap-3">
              <Textarea
                label="复核理由"
                required
                value={reviewQuestion}
                maxLength={2000}
                onChange={(event) => setReviewQuestion(event.target.value)}
                placeholder="请说明你认为需要复核的位置，以及希望教师重点检查的步骤。"
              />
              <Button
                type="button"
                variant="secondary"
                loading={reviewing}
                onClick={() => void submitReview()}
              >
                提交教师复核
              </Button>
            </div>
          )}
          <p className="mt-4 text-xs leading-5 text-[var(--text-secondary)]">
            提交申请本身不会立即改分。最终结果以教师复核后发布的新成绩版本为准。
          </p>
        </Card>
      </section>
      {actionError && (
        <p
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"
        >
          {actionError}
        </p>
      )}
    </div>
  );
}

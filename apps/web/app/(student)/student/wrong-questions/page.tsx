"use client";

import { useEffect, useState } from "react";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import {
  ApiError,
  studentPortalApi,
  type StudentWrongQuestion,
} from "@/lib/api";

const statusText: Record<string, string> = {
  pending: "等待教师处理",
  needs_information: "教师需要补充说明",
  resolved: "教师已处理",
  cancelled: "已取消",
};

export default function StudentWrongQuestionsPage() {
  const [items, setItems] = useState<StudentWrongQuestion[]>();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [tutorQuestions, setTutorQuestions] = useState<Record<string, string>>(
    {},
  );
  const [tutorResults, setTutorResults] = useState<
    Record<
      string,
      { explanation: string; next_steps: string[]; practice_prompts: string[] }
    >
  >({});
  const [assistantEnabled, setAssistantEnabled] = useState(false);
  const [tutoring, setTutoring] = useState<string>();
  const [submitting, setSubmitting] = useState<string>();
  const [error, setError] = useState("");

  const load = () =>
    Promise.all([
      studentPortalApi.wrongQuestions(),
      studentPortalApi.learningAnalysis(),
    ])
      .then(([nextItems, analysis]) => {
        setItems(nextItems);
        setAssistantEnabled(analysis.assistant_enabled);
      })
      .catch(() => setError("错题记录加载失败，请稍后重试。"));
  useEffect(() => {
    void load();
  }, []);

  const submit = async (item: StudentWrongQuestion) => {
    const message = drafts[item.id]?.trim();
    if (!message) return setError("请先填写需要教师复核的原因。");
    setSubmitting(item.id);
    setError("");
    try {
      await studentPortalApi.createReviewRequest(
        item.grade_release_id,
        item.question_id,
        message,
      );
      await load();
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "复核申请提交失败，请稍后重试。",
      );
    } finally {
      setSubmitting(undefined);
    }
  };

  const askTutor = async (item: StudentWrongQuestion) => {
    setTutoring(item.id);
    setError("");
    try {
      const result = await studentPortalApi.tutor(
        item.grade_release_id,
        item.question_id,
        tutorQuestions[item.id],
      );
      setTutorResults((current) => ({ ...current, [item.id]: result }));
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "本地学习助手暂时不可用。",
      );
    } finally {
      setTutoring(undefined);
    }
  };

  if (!items) return <Skeleton className="h-72 w-full" />;
  return (
    <div className="space-y-6">
      <PageHeader
        title="错题与复核"
        description="仅显示教师已正式发布的错题；复核不会直接改写历史成绩。"
      />
      {error && (
        <Card className="border-red-300 p-4 text-red-700">{error}</Card>
      )}
      {!items.length ? (
        <EmptyState
          icon="practice"
          title="暂无错题"
          description="已发布成绩中的满分题不会出现在这里。"
        />
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <Card className="p-5" key={item.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-bold">
                    {item.assignment_title} · 第 {item.question_number} 题
                  </h2>
                  <p className="mt-1 text-sm text-slate-600">
                    {item.class_name}
                  </p>
                </div>
                <strong className="text-red-700">
                  {Number(item.score).toLocaleString("zh-CN")} /{" "}
                  {Number(item.max_score).toLocaleString("zh-CN")}
                </strong>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <div className="rounded-xl bg-slate-50 p-4 text-sm">
                  <strong>题目</strong>
                  <p className="mt-2 whitespace-pre-wrap">
                    {item.question_content || "暂无题目文本"}
                  </p>
                </div>
                <div className="rounded-xl bg-slate-50 p-4 text-sm">
                  <strong>我的答案</strong>
                  <p className="mt-2 whitespace-pre-wrap">
                    {item.student_answer || "暂无答案文本"}
                  </p>
                </div>
              </div>
              {item.feedback && (
                <p className="mt-3 text-sm">教师反馈：{item.feedback}</p>
              )}
              {assistantEnabled && (
                <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm">
                  <strong>本地学习助手</strong>
                  <p className="mt-1 text-slate-600">
                    只解释错因和给出练习建议，不能评分、改分或发布成绩。
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <input
                      className="min-w-64 flex-1 rounded-lg border border-violet-200 bg-white p-2.5"
                      maxLength={500}
                      placeholder="可选：我想具体了解哪一步？"
                      value={tutorQuestions[item.id] || ""}
                      onChange={(event) =>
                        setTutorQuestions((current) => ({
                          ...current,
                          [item.id]: event.target.value,
                        }))
                      }
                    />
                    <Button
                      variant="outline"
                      loading={tutoring === item.id}
                      onClick={() => void askTutor(item)}
                    >
                      生成学习建议
                    </Button>
                  </div>
                  {tutorResults[item.id] && (
                    <div className="mt-4 space-y-2 border-t border-violet-200 pt-3">
                      <p>{tutorResults[item.id].explanation}</p>
                      <ol className="list-decimal space-y-1 pl-5">
                        {tutorResults[item.id].next_steps.map((step) => (
                          <li key={step}>{step}</li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
              )}
              {item.review_request ? (
                <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm">
                  <strong>
                    {statusText[item.review_request.status] ||
                      item.review_request.status}
                  </strong>
                  {item.review_request.teacher_response && (
                    <p className="mt-2">
                      教师回复：{item.review_request.teacher_response}
                    </p>
                  )}
                  {item.review_request.resolution === "score_changed" && (
                    <p className="mt-2 text-slate-600">
                      新分数将在教师重新发布成绩版本后显示。
                    </p>
                  )}
                </div>
              ) : (
                <div className="mt-4 space-y-3">
                  <label
                    className="block text-sm font-medium"
                    htmlFor={`review-${item.id}`}
                  >
                    申请复核的原因
                  </label>
                  <textarea
                    id={`review-${item.id}`}
                    maxLength={2000}
                    rows={3}
                    value={drafts[item.id] || ""}
                    onChange={(event) =>
                      setDrafts((current) => ({
                        ...current,
                        [item.id]: event.target.value,
                      }))
                    }
                    className="w-full rounded-xl border border-slate-300 p-3 text-sm"
                    placeholder="例如：第二步推导正确，希望复核步骤分。"
                  />
                  <Button
                    loading={submitting === item.id}
                    onClick={() => void submit(item)}
                  >
                    提交复核申请
                  </Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

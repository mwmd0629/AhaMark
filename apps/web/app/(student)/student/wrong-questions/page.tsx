"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Skeleton,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type WrongQuestion,
  type TeacherReviewRequest,
} from "@/lib/student-api";
import { formatScore } from "@/lib/student-format";

export default function WrongQuestionsPage() {
  const [questions, setQuestions] = useState<WrongQuestion[]>([]);
  const [reviews, setReviews] = useState<TeacherReviewRequest[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [questionData, reviewData] = await Promise.all([
        studentApi.wrongQuestions(),
        studentApi.teacherReviewRequests(),
      ]);
      setQuestions(collectionItems(questionData));
      setReviews(collectionItems(reviewData));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "错题本加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);
  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return questions;
    return questions.filter((item) =>
      [
        item.assignment_title,
        item.question_text,
        ...(item.knowledge_points ?? []),
      ]
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [query, questions]);
  const currentQuestionIds = useMemo(
    () => new Set(questions.map((item) => item.id)),
    [questions],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="错题本"
        description="错题来自教师已经发布的成绩。你可以追问 AI 以补齐知识漏洞，也可以提交教师人工复核。"
        actions={
          <div className="w-full sm:w-72">
            <Input
              aria-label="搜索错题"
              placeholder="搜索作业、题目或知识点"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        }
      />
      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[1, 2, 3, 4].map((item) => (
            <Skeleton key={item} className="h-56" />
          ))}
        </div>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <>
          {reviews.length > 0 && (
            <section
              aria-labelledby="student-review-history"
              className="space-y-3"
            >
              <h2 id="student-review-history" className="text-lg font-bold">
                我的人工复核
              </h2>
              <div className="grid gap-3 md:grid-cols-2">
                {reviews.map((review) => (
                  <Card key={review.id} className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <strong className="text-sm">
                          {review.assignment_title || "错题人工复核"}
                        </strong>
                        {review.question_number && (
                          <span className="ml-2 text-xs text-[var(--text-secondary)]">
                            第 {review.question_number} 题
                          </span>
                        )}
                      </div>
                      <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-800">
                        {review.status}
                      </span>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
                      {review.student_question || "已提交人工复核"}
                    </p>
                    {review.teacher_response && (
                      <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm leading-6">
                        <strong>教师回复：</strong>
                        {review.teacher_response}
                      </div>
                    )}
                    {review.student_answer_id &&
                    currentQuestionIds.has(review.student_answer_id) ? (
                      <Link
                        href={`/student/wrong-questions/${review.student_answer_id}`}
                        className="mt-3 inline-block text-sm font-semibold text-[var(--brand-700)]"
                      >
                        查看对应错题
                      </Link>
                    ) : review.student_answer_id ? (
                      <p className="mt-3 text-xs text-[var(--text-secondary)]">
                        该题已不在当前错题本中；上方内容仍保留本次复核记录。
                      </p>
                    ) : null}
                  </Card>
                ))}
              </div>
            </section>
          )}

          {visible.length ? (
            <section
              aria-label="错题列表"
              className="grid gap-4 md:grid-cols-2"
            >
              {visible.map((question) => (
                <Card key={question.id} className="flex flex-col p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <span className="text-xs font-semibold text-[var(--brand-700)]">
                        {question.assignment_title}
                      </span>
                      <h2 className="mt-1 font-bold">
                        {question.question_number
                          ? `第 ${question.question_number} 题`
                          : "错题"}
                      </h2>
                    </div>
                    {question.score != null && question.max_score != null && (
                      <span className="shrink-0 rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-700">
                        {formatScore(question.score, question.max_score)}
                      </span>
                    )}
                  </div>
                  <p className="mt-4 line-clamp-3 flex-1 whitespace-pre-wrap text-sm leading-6">
                    {question.question_text}
                  </p>
                  {question.error_reason && (
                    <p className="mt-3 line-clamp-2 text-sm text-[var(--text-secondary)]">
                      当前错因：{question.error_reason}
                    </p>
                  )}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {question.knowledge_points?.map((point) => (
                      <span
                        key={point}
                        className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700"
                      >
                        {point}
                      </span>
                    ))}
                  </div>
                  <div className="mt-5 flex items-center justify-between gap-3 border-t border-[var(--border)] pt-4">
                    <span className="text-xs text-[var(--text-secondary)]">
                      {question.review_status
                        ? `复核状态：${question.review_status}`
                        : "尚未提交教师复核"}
                    </span>
                    <Link
                      href={`/student/wrong-questions/${question.id}`}
                      className="font-semibold text-[var(--brand-700)]"
                    >
                      查看并询问
                    </Link>
                  </div>
                </Card>
              ))}
            </section>
          ) : (
            <EmptyState
              title={questions.length ? "没有匹配的错题" : "暂无错题"}
              description={
                questions.length
                  ? "请尝试其他搜索关键词。"
                  : "教师发布成绩后，错题会显示在这里。"
              }
              icon="practice"
            />
          )}
        </>
      )}
    </div>
  );
}

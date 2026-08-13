"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useStudent } from "@/components/student-auth-gate";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
  StatCard,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type StudentAssignment,
  type StudentLearningAnalysis,
  type StudentResult,
  type TeachingResource,
} from "@/lib/student-api";
import { formatDateTime, formatScore } from "@/lib/student-format";

export default function StudentHomePage() {
  const student = useStudent();
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  const [results, setResults] = useState<StudentResult[]>([]);
  const [analyses, setAnalyses] = useState<StudentLearningAnalysis[]>([]);
  const [resources, setResources] = useState<TeachingResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [assignmentData, resultData, analysisData, resourceData] =
        await Promise.all([
          studentApi.assignments(),
          studentApi.results(),
          studentApi.learningAnalyses(),
          studentApi.resources(),
        ]);
      setAssignments(collectionItems(assignmentData));
      setResults(collectionItems(resultData));
      setAnalyses(collectionItems(analysisData));
      setResources(collectionItems(resourceData));
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : "学习首页数据加载失败。",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);

  const pending = assignments.filter(
    (item) => !item.submission_id && item.submission_status !== "submitted",
  );
  const latestAnalysis = analyses[0];

  return (
    <div className="space-y-8">
      <PageHeader
        title={`你好，${student?.display_name || student?.name || "同学"}`}
        description="查看真实的待办作业、已发布成绩、学习建议和课程资源。"
      />

      {loading ? (
        <section
          aria-label="正在加载学习首页"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          {[1, 2, 3, 4].map((item) => (
            <Card key={item} className="p-5">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="mt-4 h-9 w-16" />
            </Card>
          ))}
        </section>
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : (
        <>
          <section
            aria-label="学习概览"
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
          >
            <StatCard
              label="待提交作业"
              value={String(pending.length)}
              note="以教师发布数据为准"
            />
            <StatCard
              label="已发布成绩"
              value={String(results.length)}
              note="未发布成绩不会展示"
            />
            <StatCard
              label="学习分析"
              value={String(analyses.length)}
              note="AI 生成，仅供参考"
            />
            <StatCard
              label="学习资源"
              value={String(resources.length)}
              note="教师已发布"
            />
          </section>

          <section className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,.8fr)]">
            <Card className="p-5">
              <SectionHeader
                title="近期作业"
                description="优先显示尚未提交的作业"
                action={
                  <Link
                    className="text-sm font-semibold text-[var(--brand-700)]"
                    href="/student/assignments"
                  >
                    查看全部
                  </Link>
                }
              />
              {assignments.length ? (
                <div className="mt-4 divide-y divide-[var(--border)]">
                  {[
                    ...pending,
                    ...assignments.filter((item) => !pending.includes(item)),
                  ]
                    .slice(0, 5)
                    .map((item) => (
                      <Link
                        key={item.id}
                        href="/student/assignments"
                        className="flex items-center justify-between gap-4 py-4 hover:text-[var(--brand-700)]"
                      >
                        <span>
                          <strong className="block text-sm">
                            {item.title}
                          </strong>
                          <span className="mt-1 block text-xs text-[var(--text-secondary)]">
                            {item.class_name || item.subject || "课程作业"} ·
                            截止 {formatDateTime(item.due_at)}
                          </span>
                        </span>
                        <span className="shrink-0 text-xs font-semibold">
                          {item.submission_id ||
                          item.submission_status === "submitted"
                            ? "已提交"
                            : "待提交"}
                        </span>
                      </Link>
                    ))}
                </div>
              ) : (
                <div className="mt-4">
                  <EmptyState
                    title="暂无作业"
                    description="教师发布的新作业会出现在这里。"
                  />
                </div>
              )}
            </Card>

            <div className="grid content-start gap-6">
              <Card className="p-5">
                <SectionHeader
                  title="最新成绩"
                  action={
                    <Link
                      className="text-sm font-semibold text-[var(--brand-700)]"
                      href="/student/results"
                    >
                      查看成绩
                    </Link>
                  }
                />
                {results[0] ? (
                  <div className="mt-4 rounded-xl bg-slate-50 p-4">
                    <strong className="text-sm">
                      {results[0].assignment_title}
                    </strong>
                    <p className="mt-2 text-3xl font-bold text-[var(--brand-700)]">
                      {formatScore(results[0].score, results[0].total_score)}
                    </p>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      发布于 {formatDateTime(results[0].released_at)}
                    </p>
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-[var(--text-secondary)]">
                    暂无已发布成绩。
                  </p>
                )}
              </Card>

              <Card className="border-purple-200 p-5">
                <SectionHeader
                  title="AI 学习建议"
                  action={
                    <span className="rounded-full bg-purple-50 px-2.5 py-1 text-xs font-semibold text-purple-700">
                      AI 生成
                    </span>
                  }
                />
                <p className="mt-4 text-sm leading-6 text-[var(--text-secondary)]">
                  {latestAnalysis?.summary ||
                    "目前还没有可展示的学习分析。成绩发布后，系统会基于你自己的学习数据生成建议。"}
                </p>
                <p className="mt-3 text-xs font-medium text-purple-700">
                  仅供学习参考，不会自动修改成绩。
                </p>
              </Card>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

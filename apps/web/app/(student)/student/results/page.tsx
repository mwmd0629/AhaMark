"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  Table,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  collectionItems,
  studentApi,
  type StudentResult,
} from "@/lib/student-api";
import { formatDateTime, formatScore } from "@/lib/student-format";

export default function StudentResultsPage() {
  const [results, setResults] = useState<StudentResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setResults(collectionItems(await studentApi.results()));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "成绩加载失败。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => void load(), []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="已发布成绩"
        description="这里只展示教师正式发布的成绩快照；教师修订后会显示新的发布版本。"
      />
      {loading ? (
        <Skeleton className="h-72 w-full" />
      ) : error ? (
        <ErrorState description={error} retry={() => void load()} />
      ) : results.length ? (
        <Card className="overflow-hidden">
          <Table>
            <thead>
              <tr className="border-b border-[var(--border)] bg-slate-50 text-xs text-[var(--text-secondary)]">
                <th className="px-5 py-3">作业</th>
                <th className="px-4 py-3">成绩</th>
                <th className="px-4 py-3">错题</th>
                <th className="px-4 py-3">版本</th>
                <th className="px-5 py-3">发布时间</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <tr
                  key={result.id}
                  className="border-b border-[var(--border)] last:border-0"
                >
                  <td className="px-5 py-4">
                    <strong className="block">{result.assignment_title}</strong>
                    {result.teacher_comment && (
                      <span className="mt-1 block max-w-xl text-xs text-[var(--text-secondary)]">
                        教师评语：{result.teacher_comment}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-4 text-base font-bold text-[var(--brand-700)]">
                    {formatScore(result.score, result.total_score)}
                  </td>
                  <td className="px-4 py-4">
                    {result.wrong_question_count ? (
                      <Link
                        href="/student/wrong-questions"
                        className="font-semibold text-[var(--brand-700)]"
                      >
                        {result.wrong_question_count} 题
                      </Link>
                    ) : (
                      "0 题"
                    )}
                  </td>
                  <td className="px-4 py-4">v{result.release_version ?? 1}</td>
                  <td className="px-5 py-4 text-[var(--text-secondary)]">
                    {formatDateTime(result.released_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      ) : (
        <EmptyState
          title="暂无已发布成绩"
          description="教师正式发布成绩后会显示在这里，批改中的内容不会提前展示。"
        />
      )}
    </div>
  );
}
